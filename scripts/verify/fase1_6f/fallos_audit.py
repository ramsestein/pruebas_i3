#!/usr/bin/env python3
"""
fase1_6f/fallos_audit.py
========================
Fase 1.6f, punto 4 (D-l): auditoría visual de TODOS los eventos con al menos una
reintubación, en Clínic y VitalDB. **PARADA obligatoria**: el usuario decide si
cada extubación-reintubación es real, un artefacto o dudosa.

Por cada evento:

- **PNG** de ventana ancha alrededor de la extubación y de la reintubación (eje
  de tiempo CONTINUO, huecos visibles), con dos paneles:
  - FC, SpO2, PAM y FR de impedancia (``Intellivue/RR``);
  - TV_EXP, VENT_RR, PEEP y FiO2;
  y una franja inferior con el estado de las pistas del ventilador por minuto
  (activo / en espera / ausencia total) y el apagón del grabador sombreado.
- **Tabla** por hueco: tipo (desconexión con espera / caída de la conexión /
  apagón / mixto), duración, cambio de ajustes antes/después, cola de monitor
  tras la extubación y horas hasta la reintubación.
- **Hoja** ``hoja.csv`` (``codigo,decision,comentario``) para que el usuario
  rellene ``decision`` con ``real`` / ``artefacto`` / ``dudoso``.

Uso:
    python scripts/verify/fase1_6f/fallos_audit.py --cohort clinic \
        --index datasets/clinic/cases_v0.5.0_eac1eb35/clinic_cases_index.json
    python scripts/verify/fase1_6f/fallos_audit.py --cohort vitaldb \
        --index datasets/vitaldb/cases_v0.4.0_aa142f35/vitaldb_cases_index.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.common.raw_mirror import resolve_raw_dir  # noqa: E402
from src.common.vital_signals import (  # noqa: E402
    ACTIVITY_MV_EXP_MIN_LPM,
    ACTIVITY_TV_EXP_MIN_ML,
    read_track_series,
)
from src.create_dataset.build_signal_cases import (  # noqa: E402
    SPECS,
    scan_source_files,
)
from apagones_report import probe_window, window_analysis  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"

FC = ("Intellivue/ECG_HR", "Intellivue/HR", "Intellivue/PLETH_HR")
SPO2 = ("Intellivue/PLETH_SAT_O2",)
PAM = ("Intellivue/ART_MEAN", "Intellivue/ABP_MEAN", "Intellivue/NIBP_MEAN")
FR = ("Intellivue/RR",)
TV = "Intellivue/TV_EXP"
MV = "Intellivue/MV_EXP"
VRR = "Intellivue/VENT_RR"
PEEP = "Intellivue/PEEP_CMH2O"
FIO2 = "Intellivue/FIO2"
SERIES = list(FC) + list(SPO2) + list(PAM) + list(FR) + [TV, MV, VRR, PEEP, FIO2]


def _fmt(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%d/%m %H:%M")


def _median(series: dict, names, lo_s: float, hi_s: float):
    for n in names:
        t, v = series.get(n, (None, None))
        if t is None or not len(t):
            continue
        t = np.asarray(t, dtype=float)
        v = np.asarray(v, dtype=float)
        sel = (t >= lo_s) & (t <= hi_s) & np.isfinite(v)
        if sel.any():
            return float(np.median(v[sel]))
    return None


def _delta(series: dict, name: str, lo_a: float, hi_a: float,
           lo_b: float, hi_b: float):
    a = _median(series, (name,), lo_a, hi_a)
    b = _median(series, (name,), lo_b, hi_b)
    if a is None or b is None:
        return None
    return round(b - a, 2)


def _monitor_tail_h(series: dict, ext_s: float, reint_s: float) -> float:
    """Horas de FC/SpO2 fisiológica dentro de [extubación, reintubación]."""
    if reint_s <= ext_s:
        return 0.0
    blocks = []
    for names, lo, hi in ((FC, 20.0, 250.0), (SPO2, 50.0, 100.0)):
        for n in names:
            t, v = series.get(n, (None, None))
            if t is None or not len(t):
                continue
            t = np.asarray(t, dtype=float)
            v = np.asarray(v, dtype=float)
            sel = ((t >= ext_s) & (t <= reint_s) & (v >= lo) & (v <= hi)
                   & np.isfinite(v))
            if sel.any():
                blocks.append((float(t[sel].min()), float(t[sel].max())))
                break
    if not blocks:
        return 0.0
    lo = min(a for a, _b in blocks)
    hi = max(b for _a, b in blocks)
    return round((hi - lo) / 3600.0, 2)


def _vent_state_minutes(series: dict, lo_s: float, hi_s: float) -> dict:
    """Estado por minuto de las pistas del ventilador (activo/espera/ausencia)."""
    m_lo, m_hi = int(lo_s // 60), int(hi_s // 60)
    present: set[int] = set()
    active: set[int] = set()
    for n in (TV, MV, VRR, PEEP, FIO2):
        t, v = series.get(n, (None, None))
        if t is None or not len(t):
            continue
        t = np.asarray(t, dtype=float)
        v = np.asarray(v, dtype=float)
        mins = (t // 60.0).astype(np.int64)
        sel = (mins >= m_lo) & (mins <= m_hi)
        present.update(int(x) for x in mins[sel])
        if n == TV:
            active.update(int(x) for x in mins[sel & (v >= ACTIVITY_TV_EXP_MIN_ML)])
        if n == MV:
            active.update(int(x) for x in mins[sel & (v >= ACTIVITY_MV_EXP_MIN_LPM)])
    out = {}
    for m in range(m_lo, m_hi + 1):
        if m in active:
            out[m] = "activo"
        elif m in present:
            out[m] = "espera"
        else:
            out[m] = "ausencia"
    return out


def _coverage_h(spans, lo_h: float, hi_h: float) -> float:
    """Horas de ``[lo_h, hi_h]`` cubiertas por la unión de ``spans``."""
    if hi_h <= lo_h:
        return 0.0
    iv = []
    for s in spans:
        a = max(s["start_h"], lo_h)
        b = min(s["end_h"], hi_h)
        if b > a:
            iv.append((a, b))
    iv.sort()
    total = 0.0
    cur_a = cur_b = None
    for a, b in iv:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += cur_b - cur_a
    return round(total, 3)


def _gap_kind(blackouts, drops, lo_h: float, hi_h: float) -> tuple[str, float]:
    """Tipo de hueco y duración (h) entre dos intentos consecutivos."""
    dur = max(0.0, hi_h - lo_h)
    for b in blackouts:
        if b["start_h"] <= hi_h and b["end_h"] >= lo_h:
            return "apagon", round(dur, 2)
    for d in drops:
        if d["start_h"] <= hi_h and d["end_h"] >= lo_h:
            return "caida_conexion_ventilador", round(dur, 2)
    return "desconexion_con_espera", round(dur, 2)


def audit_event(cohort: str, ev: dict, config, box_files, *, margen_h: float,
                fig_dir: Path, make_png: bool) -> dict:
    t0 = float(ev["t0_unix"])
    atts = ev["attempts"]
    exts = [a["vent_end_h"] for a in atts[:-1]]
    reints = [a["vent_start_h"] for a in atts[1:]]
    lo_h = min(exts) - margen_h
    hi_h = max(reints) + margen_h
    # Todo en horas RELATIVAS a t0 (igual que ``attempts``); para sondear el
    # origen hay que pasar horas absolutas.
    win = probe_window(config, cohort, ev["box"], t0 / 3600.0 + lo_h,
                       t0 / 3600.0 + hi_h, margin_h=2.0)
    ana = window_analysis(win["probes"])
    lo_s, hi_s = lo_h * 3600.0, hi_h * 3600.0
    sel = [f for f in box_files
           if t0 + lo_s - 7200.0 <= f.dt_unix <= t0 + hi_s + 7200.0]
    # ``t0_unix=t0`` → las series vuelven en segundos desde t0.
    series = read_track_series([str(f.path) for f in sel], SERIES, t0_unix=t0)

    gaps = []
    for i, (e_h, r_h) in enumerate(zip(exts, reints)):
        kind, dur = _gap_kind(ana["blackouts"], ana["vent_drops"], e_h, r_h)
        e_s, r_s = e_h * 3600.0, r_h * 3600.0
        cob_dj = _coverage_h([b for b in ana["blackouts"] if b["benign"]],
                             e_h, r_h)
        cob_dk = _coverage_h([d for d in ana["vent_drops"] if d["benign"]],
                             e_h, r_h)
        hueco_efectivo = round(max(0.0, r_h - e_h) - max(cob_dj, cob_dk), 3)
        gaps.append({
            "extubacion_h": round(e_h, 3),
            "reintubacion_h": round(r_h, 3),
            "horas_hasta_reintubacion": round(r_h - e_h, 2),
            "tipo_hueco": kind,
            "duracion_h": dur,
            "cobertura_dj_h": cob_dj,
            "cobertura_dk_h": cob_dk,
            "hueco_efectivo_h": hueco_efectivo,
            # Con el hueco efectivo <= 2 h el intento NO se parte (D1 + D-j/D-k):
            # la reintubación desaparecería.
            "se_une_con_dj_dk": bool(hueco_efectivo <= 2.0),
            "d_peep": _delta(series, PEEP, e_s - 1800, e_s, r_s, r_s + 1800),
            "d_fio2": _delta(series, FIO2, e_s - 1800, e_s, r_s, r_s + 1800),
            "d_tv": _delta(series, TV, e_s - 1800, e_s, r_s, r_s + 1800),
            "d_rr": _delta(series, VRR, e_s - 1800, e_s, r_s, r_s + 1800),
            "cola_monitor_h": _monitor_tail_h(series, e_s, r_s),
        })

    doc = {
        "event_id": ev["event_id"],
        "box": ev["box"],
        "n_attempts": ev["n_attempts"],
        "t0": _fmt(t0),
        "end_reason": ev["end_reason"],
        "n_failed_attempts_48h": ((ev.get("labels") or {}).get("48h") or {}
                                  ).get("n_failed_attempts"),
        "n_blackout_h": ev.get("n_blackout_h"),
        "gaps": gaps,
        "n_files_window": win["n_files"],
    }

    if make_png:
        _png(cohort, ev, t0, series, ana, lo_s, hi_s, fig_dir)
    return doc


def _png(cohort: str, ev: dict, t0: float, series: dict, ana: dict,
         lo_s: float, hi_s: float, fig_dir: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(16, 9))
    gs = fig.add_gridspec(3, 1, height_ratios=[3, 3, 1.1], hspace=0.12)
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax3 = fig.add_subplot(gs[2], sharex=ax1)

    for names, label, color in ((FC, "FC", "tab:red"), (SPO2, "SpO2", "tab:blue"),
                               (PAM, "PAM", "tab:green"), (FR, "FR", "0.35")):
        for n in names:
            t, v = series.get(n, (None, None))
            if t is None or not len(t):
                continue
            t = np.asarray(t, dtype=float) / 3600.0
            ax1.plot(t, np.asarray(v, dtype=float), ".", ms=1, label=label,
                     color=color)
            break
    for n, color in ((TV, "tab:purple"), (VRR, "tab:olive"),
                     (PEEP, "tab:cyan"), (FIO2, "tab:brown")):
        t, v = series.get(n, (None, None))
        if t is None or not len(t):
            continue
        ax2.plot(np.asarray(t, dtype=float) / 3600.0, np.asarray(v, dtype=float),
                 ".", ms=1, label=n.split("/")[-1], color=color)
    for ax in (ax1, ax2):
        ax.grid(alpha=0.25)
        if ax.get_legend_handles_labels()[0]:
            ax.legend(loc="upper right", fontsize=8, ncol=5)
        for a in ev["attempts"]:
            ax.axvspan(a["vent_start_h"], a["vent_end_h"], color="tab:blue",
                       alpha=0.06, zorder=0)
            ax.axvline(a["vent_end_h"], color="k", ls="--", lw=1.1)
    for a in ev["attempts"][1:]:
        ax1.axvline(a["vent_start_h"], color="tab:red", ls="-.", lw=1.1)
    off = t0 / 3600.0
    for b in ana["blackouts"]:
        for ax in (ax1, ax2, ax3):
            ax.axvspan(b["start_h"] - off, b["end_h"] - off, color="0.8",
                       zorder=0)
    for d in ana["vent_drops"]:
        for ax in (ax1, ax2):
            ax.axvspan(d["start_h"] - off, d["end_h"] - off, color="tab:orange",
                       alpha=0.12, zorder=0)

    state = _vent_state_minutes(series, lo_s, hi_s)
    if state:
        m0, m1 = min(state), max(state)
        order = {"ausencia": 0, "espera": 1, "activo": 2}
        arr = np.array([order[state.get(m, "ausencia")]
                        for m in range(m0, m1 + 1)])
        ax3.imshow(arr[None, :], aspect="auto", cmap="viridis", vmin=0, vmax=2,
                   extent=[m0 * 60.0 / 3600.0, (m1 + 1) * 60.0 / 3600.0, 0, 1])
    ax3.set_yticks([])
    ax3.set_ylabel("vent.")
    ax3.set_xlabel("horas desde t0 del evento")
    ax1.set_ylabel("monitor")
    ax2.set_ylabel("ventilador")
    fig.suptitle(
        f"{cohort} {ev['box']} {ev['event_id']}  intentos={ev['n_attempts']}  "
        f"líneas -- = fin de intento (extubación), -. = reintubación  "
        f"gris = apagón, naranja = caída conexión ventilador")
    out = fig_dir / f"{ev['event_id']}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=100)
    plt.close(fig)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cohort", choices=sorted(SPECS), required=True)
    p.add_argument("--index", required=True)
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6f"))
    p.add_argument("--margen-h", type=float, default=6.0)
    p.add_argument("--max-events", type=int, default=None)
    p.add_argument("--no-png", action="store_true")
    p.add_argument("--config", default=str(CONFIG))
    args = p.parse_args()

    idx = json.loads(Path(args.index).read_text(encoding="utf-8"))
    events = [e for e in idx["events"] if int(e["n_attempts"]) >= 2]
    events.sort(key=lambda e: e["event_id"])
    if args.max_events:
        events = events[:args.max_events]
    config = load_config(str(args.config))
    raw_dir, is_mirror = resolve_raw_dir(config, args.cohort)
    boxes = scan_source_files(raw_dir, SPECS[args.cohort])
    out_dir = Path(args.out_dir) / "fallos"
    fig_dir = out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    docs = []
    for ev in events:
        print(f"[audit] {ev['event_id']} (intentos={ev['n_attempts']})",
              flush=True)
        docs.append(audit_event(args.cohort, ev, config,
                                boxes.get(ev["box"]) or [], margen_h=args.margen_h,
                                fig_dir=fig_dir, make_png=not args.no_png))

    (out_dir / f"fallos_{args.cohort}.json").write_text(
        json.dumps({"cohort": args.cohort, "index": args.index,
                    "raw_dir": str(raw_dir), "is_mirror": bool(is_mirror),
                    "events": docs}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    # Hoja para el usuario (misma convención que la adjudicación de 1.6e).
    with open(out_dir / f"hoja_{args.cohort}.csv", "w", encoding="utf-8",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["codigo", "decision", "comentario"])
        for d in docs:
            w.writerow([d["event_id"], "", ""])

    md = [f"# Fase 1.6f, punto 4 (D-l) — auditoría de fallos ({args.cohort})", ""]
    md.append(f"- Índice: `{args.index}`")
    md.append(f"- Origen de las señales: `{raw_dir}` "
              f"(espejo: {'sí' if is_mirror else 'NO'})")
    md.append(f"- Eventos con ≥ 1 reintubación: **{len(docs)}**")
    md.append("")
    md.append("| evento | intentos | extubación (h) | reintubación (h) | "
              "horas hasta reint. | tipo de hueco | duración (h) | "
              "hueco efectivo D-j/D-k (h) | cola monitor (h) | ΔPEEP | ΔFiO2 | "
              "ΔTV | ΔFR |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for d in docs:
        for g in d["gaps"]:
            md.append(
                f"| {d['event_id']} | {d['n_attempts']} | "
                f"{g['extubacion_h']} | {g['reintubacion_h']} | "
                f"{g['horas_hasta_reintubacion']} | {g['tipo_hueco']} | "
                f"{g['duracion_h']} | {g['hueco_efectivo_h']} | "
                f"{g['cola_monitor_h']} | {g['d_peep']} | {g['d_fio2']} | "
                f"{g['d_tv']} | {g['d_rr']} |")
    md.append("")
    md.append("`hueco efectivo D-j/D-k` = duración del hueco menos las horas "
              "cubiertas por un apagón benigno o una caída benigna de la conexión "
              "del ventilador. Si es ≤ 2 h, **el intento no se parte** con el código "
              "de 1.6f: la reintubación desaparecería (candidata clara a `artefacto`).")
    md.append("")
    md.append("Rellena `hoja_"
              f"{args.cohort}.csv` con `decision` = real / artefacto / dudoso.")
    (out_dir / f"tabla_{args.cohort}.md").write_text("\n".join(md) + "\n",
                                                     encoding="utf-8")
    print(f"[ok] {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
