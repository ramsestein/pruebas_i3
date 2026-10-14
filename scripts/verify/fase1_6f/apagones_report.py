#!/usr/bin/env python3
"""
fase1_6f/apagones_report.py
===========================
Fase 1.6f, puntos 2 (D-j) y 3 (D-k): informe de apagones del grabador y caídas
de la conexión de datos del ventilador.

Qué mide (con el mismo código que el builder, para que el informe describa el
cohorte que se va a reconstruir):

- **Apagones** por box: intervalos sin NINGUNA muestra de NINGUNA pista
  (``blackout_spans(kind="sample")``), su duración, su tipo (``short``/``long``)
  y si son *benignos* (≤ 2 h con ventilación activa en los 15 min previos y
  posteriores → misma región).
- **Cortes D2 que son apagones**: de las parejas de la cascada de 1.6e
  (``cascada_<cohort>.json``), cuántas caen dentro de un apagón.
- **Caídas de la conexión del ventilador** (D-k): ninguna pista del respirador
  con muestras y monitor presente; cuántas son de ≤ 2 h (dato ausente) y cuántas
  de más de 2 h (se informan sin cambiar la regla).
- **Figuras** de ventana ancha (eje de tiempo continuo, hueco sombreado) de los
  N casos indicados, siempre incluyendo ``box9``.

Uso:
    python scripts/verify/fase1_6f/apagones_report.py --cohort clinic \
        --cascada reports/fase1_6e/cascada_clinic.json --figs 5
    python scripts/verify/fase1_6f/apagones_report.py --cohort vitaldb \
        --cascada reports/fase1_6e/cascada_vitaldb.json --figs 0
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.raw_mirror import resolve_raw_dir  # noqa: E402
from src.common.vital_signals import (  # noqa: E402
    active_vent_span_from_probe,
    probe_vital_file_wide,
    read_track_series,
)
from src.common.episodes import Span, merge_spans  # noqa: E402
from src.create_dataset.build_signal_cases import (  # noqa: E402
    GLOBAL_HOLE_MIN_S,
    SPECS,
    _classify_blackouts,
    _classify_vent_drops,
    _subtract_spans,
    _to_hours,
    blackout_spans,
    scan_source_files,
)
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
MON = ("Intellivue/ECG_HR", "Intellivue/PLETH_SAT_O2")
VEN = ("Intellivue/TV_EXP", "Intellivue/VENT_RR", "Intellivue/PEEP_CMH2O",
       "Intellivue/FIO2")


def _fmt(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%d/%m %H:%M")


def _pct(n: int, total: int) -> str:
    return f"{100.0 * n / total:.1f} %" if total else "—"


def _bucket(dur_h: float) -> str:
    for lo, hi in ((0.0, 0.25), (0.25, 0.5), (0.5, 1.0), (1.0, 2.0),
                   (2.0, 6.0), (6.0, 24.0), (24.0, 1e9)):
        if lo <= dur_h < hi:
            return f"[{lo:g}, {hi:g}) h"
    return "?"


def caidas_md(cohort: str, meta: dict, all_drops: list, cands: list) -> list[str]:
    """Informe del punto 3 (D-k): caídas de la conexión del ventilador."""
    md: list[str] = []
    md.append(f"# Fase 1.6f, punto 3 (D-k) — caídas de la conexión de datos del "
              f"ventilador ({cohort})")
    md.append("")
    md.append(f"- Origen de las muestras: `{meta.get('raw_dir')}` "
              f"(espejo: {'sí' if meta.get('is_mirror') else 'NO'})")
    md.append(f"- Ventanas analizadas: {len(cands)} (una por corte de la cascada)")
    md.append("")
    md.append("Una caída es `vent_feed_dropout` si NINGUNA pista del ventilador")
    md.append("(incluidos los ajustes) tiene muestras y el monitor SÍ está presente.")
    md.append("Un respirador en ESPERA deja los ajustes registrados (TV ≈ 0), así que")
    md.append("conserva muestras y NO se cuenta como caída de la conexión: por eso no")
    md.append("se puede distinguir «respirador apagado» de «caída de la conexión» cuando")
    md.append("no queda ninguna muestra.")
    md.append("")
    short = [d for _b, d in all_drops if d["benign"]]
    long_ = [d for _b, d in all_drops if not d["benign"]]
    md.append(f"- Caídas detectadas: **{len(all_drops)}**")
    md.append(f"  - ≤ 2 h (dato ausente, no corta el intento): **{len(short)}**")
    md.append(f"  - > 2 h (se informan; la regla no cambia): **{len(long_)}**")
    md.append("")
    if all_drops:
        md.append("| Box | inicio | fin | duración (h) | tipo |")
        md.append("|---|---|---|---|---|")
        for box, d in sorted(all_drops, key=lambda x: -x[1]["duration_h"]):
            md.append(f"| {box} | {_fmt(d['start_h'] * 3600.0)} | "
                      f"{_fmt(d['end_h'] * 3600.0)} | {d['duration_h']:.2f} | "
                      f"{d['type']} |")
    else:
        md.append("No se ha detectado ninguna caída en las ventanas analizadas.")
    md.append("")
    return md


def probe_window(config, cohort: str, box: str, lo_h: float, hi_h: float,
                 margin_h: float = 3.0) -> dict:
    """Sondea (todas las pistas) los ficheros de ``box`` que tocan la ventana.

    Se sondea SOLO la ventana: los boxes de Clínic pesan 1-28 GB y leerlos
    enteros con todas las pistas costaría horas; para juzgar un corte basta con
    lo que hay alrededor (y la ventana da los bloques de muestras a los dos
    lados, que es lo que define el hueco).
    """
    raw_dir, is_mirror = resolve_raw_dir(config, cohort)
    all_boxes = scan_source_files(raw_dir, SPECS[cohort])
    sfs = all_boxes.get(box) or []
    sel = [sf for sf in sfs
           if lo_h - margin_h <= sf.dt_unix / 3600.0 <= hi_h + margin_h]
    probes = {str(sf.path): probe_vital_file_wide(sf.path) for sf in sel}
    return {"probes": probes, "raw_dir": str(raw_dir),
            "is_mirror": bool(is_mirror), "n_files": len(sel),
            "n_files_box": len(sfs)}


def window_analysis(probes: dict) -> dict:
    """Apagones y caídas del ventilador en una ventana de ficheros."""
    vent_active = merge_spans(
        [sp for sp in (_to_hours(active_vent_span_from_probe(p))
                       for p in probes.values() if p is not None)
         if sp is not None], 1.0 / 60.0)
    holes = blackout_spans(probes, kind="sample")
    drops = _subtract_spans(blackout_spans(probes, kind="vent"),
                            blackout_spans(probes, kind="monitor"))
    return {
        "blackouts": _classify_blackouts(holes, vent_active),
        "vent_drops": _classify_vent_drops(drops),
    }


def _contains_point(b: dict, point_h: float) -> bool:
    return b["start_h"] <= point_h <= b["end_h"]


def _overlaps_interval(b: dict, lo_h: float, hi_h: float) -> bool:
    return b["start_h"] <= hi_h and b["end_h"] >= lo_h


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cohort", choices=sorted(SPECS), required=True)
    p.add_argument("--cascada", required=True)
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6f"))
    p.add_argument("--figs", type=int, default=5,
                   help="Nº de figuras de ventana ancha (0 = ninguna)")
    p.add_argument("--margen-h", type=float, default=12.0)
    p.add_argument("--config", default=str(CONFIG))
    args = p.parse_args()

    cascade = json.loads(Path(args.cascada).read_text(encoding="utf-8"))
    cands = list(cascade.get("candidatas") or [])
    config = load_config(str(args.config))

    explained: list[dict] = []
    meta: dict = {}
    for c in cands:
        prev_h = float(c["prev_end_epoch"]) / 3600.0
        next_h = float(c["next_start_epoch"]) / 3600.0
        lo, hi = min(prev_h, next_h), max(prev_h, next_h)
        win = probe_window(config, args.cohort, c["box"], lo, hi,
                           args.margen_h)
        meta = {"raw_dir": win["raw_dir"], "is_mirror": win["is_mirror"]}
        bl = window_analysis(win["probes"])
        hit = [b for b in bl["blackouts"]
               if (_overlaps_interval(b, lo, hi) if hi > lo
                   else _contains_point(b, lo))]
        explained.append({
            "box": c["box"], "gap_h": c["gap_h"],
            "cut_lo_h": round(lo, 4), "cut_hi_h": round(hi, 4),
            "n_files_window": win["n_files"], "n_files_box": win["n_files_box"],
            "explicada": bool(hit),
            "apagon": hit[0] if hit else None,
            "blackouts_ventana": bl["blackouts"],
            "vent_drops_ventana": bl["vent_drops"],
        })

    all_blackouts = [(e["box"], b) for e in explained
                     for b in e["blackouts_ventana"]]
    all_drops = [(e["box"], d) for e in explained
                 for d in e["vent_drops_ventana"]]

    n_exp = sum(1 for e in explained if e["explicada"])
    dur_hist = Counter(_bucket(b["duration_h"]) for _box, b in all_blackouts)
    short = [b for _box, b in all_blackouts if b["type"] == "short"]
    benign = [b for b in short if b["benign"]]

    md: list[str] = []
    md.append(f"# Fase 1.6f, puntos 2 y 3 — apagones del grabador y caídas del "
              f"ventilador ({args.cohort})")
    md.append("")
    md.append(f"- Origen de las muestras: `{meta.get('raw_dir')}` "
              f"(espejo: {'sí' if meta.get('is_mirror') else 'NO'})")
    md.append(f"- Ventanas analizadas (una por candidata de la cascada): "
              f"{len(cands)}; ficheros sondeados en total: "
              f"{sum(e['n_files_window'] for e in explained)}")
    md.append(f"- Huecos mínimos considerados: "
              f"{GLOBAL_HOLE_MIN_S / 60.0:.0f} min")
    md.append("")
    md.append("## 1. Apagones del grabador (D-j)")
    md.append("")
    md.append(f"- Apagones detectados: **{len(all_blackouts)}** "
              f"(cortos ≤ 2 h: {len(short)}, largos: {len(all_blackouts) - len(short)})")
    md.append(f"- Cortos **benignos** (ventilación activa a los dos lados → "
              f"misma región): **{len(benign)}**")
    md.append("")
    md.append("| Duración | Apagones |")
    md.append("|---|---|")
    for k in sorted(dur_hist, key=lambda s: float(s.split(",")[0].strip("[ "))):
        md.append(f"| {k} | {dur_hist[k]} |")
    md.append("")
    md.append("## 2. Cortes D2 de la cascada que son apagones")
    md.append("")
    md.append(f"- Candidatas de la cascada medidas: **{len(cands)}**")
    md.append(f"- Explicadas por un apagón (el corte cae dentro del hueco): "
              f"**{n_exp}** ({_pct(n_exp, len(cands))})")
    md.append("")
    md.append("| Box | hueco declarado (h) | corte | apagón | duración (h) | tipo | benigno |")
    md.append("|---|---|---|---|---|---|---|")
    for e in sorted(explained, key=lambda x: (x["box"], x["gap_h"])):
        b = e["apagon"]
        md.append(
            f"| {e['box']} | {e['gap_h']:.2f} | "
            f"{_fmt(e['cut_lo_h'] * 3600.0)} | "
            f"{'sí' if b else 'no'} | "
            f"{b['duration_h'] if b else '—'} | "
            f"{b['type'] if b else '—'} | "
            f"{('sí' if b['benign'] else 'no') if b else '—'} |")
    md.append("")
    md.append("## 3. Caídas de la conexión del ventilador (D-k)")
    md.append("")
    md.append(f"Van en `caidas_vent_{args.cohort}.md` (mismo origen de muestras).")
    md.append("")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"apagones_{args.cohort}.md").write_text(
        "\n".join(md) + "\n", encoding="utf-8")
    # Informe de D-k aparte (punto 3), con la misma fuente de datos.
    (out_dir / f"caidas_vent_{args.cohort}.md").write_text(
        "\n".join(caidas_md(args.cohort, meta, all_drops, cands)) + "\n",
        encoding="utf-8")
    (out_dir / f"apagones_{args.cohort}.json").write_text(
        json.dumps({"cohort": args.cohort, "meta": meta,
                    "explicadas": explained},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] {out_dir / f'apagones_{args.cohort}.md'}")

    if args.figs:
        make_figs(config, args.cohort, explained, out_dir,
                  args.margen_h, args.figs)
    return 0


def make_figs(config, cohort: str, explained: list[dict],
              out_dir: Path, margen_h: float, n_figs: int) -> None:
    """Ventana ancha de los N casos con apagón (siempre incluye box9)."""
    with_bl = [e for e in explained if e["explicada"]]
    sel = [e for e in with_bl if e["box"] == "box9"][:1]
    for e in sorted(with_bl, key=lambda x: -float(x["apagon"]["duration_h"])):
        if len(sel) >= n_figs:
            break
        if e not in sel:
            sel.append(e)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    raw_dir, _ = resolve_raw_dir(config, cohort)
    boxes = scan_source_files(raw_dir, SPECS[cohort])
    fig_dir = out_dir / "figs"
    fig_dir.mkdir(parents=True, exist_ok=True)
    for e in sel:
        box = e["box"]
        c = e["cut_lo_h"] * 3600.0
        bl = e["apagon"]
        a, b = bl["start_h"] * 3600.0, bl["end_h"] * 3600.0
        files = sorted(boxes[box], key=lambda f: f.dt_unix)
        lo = c - (margen_h + 6.0) * 3600.0
        hi = c + margen_h * 3600.0
        sel_files = [f for f in files if lo <= f.dt_unix <= hi]
        series = read_track_series([str(f.path) for f in sel_files],
                                   list(VEN) + list(MON))
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 8), sharex=True)
        for nombre, color in zip(MON, ("tab:red", "tab:blue")):
            t, v = series.get(nombre, (None, None))
            if t is not None and len(t):
                ax1.plot(t / 3600.0, v, ".", ms=1,
                         label=nombre.split("/")[-1], color=color)
        for nombre in VEN:
            t, v = series.get(nombre, (None, None))
            if t is not None and len(t):
                ax2.plot(t / 3600.0, v, ".", ms=1, label=nombre.split("/")[-1])
        for ax in (ax1, ax2):
            ax.axvspan(a / 3600.0, b / 3600.0, color="0.85", zorder=0)
            for x in (a, b):
                ax.axvline(x / 3600.0, color="k", ls="--", lw=1.2)
            ax.grid(alpha=0.25)
            if ax.get_legend_handles_labels()[0]:
                ax.legend(loc="upper right", fontsize=8, ncol=4)
        ax1.set_ylabel("monitor")
        ax2.set_ylabel("ventilador")
        ax2.set_xlabel("hora absoluta (epoch/3600)")
        fig.suptitle(
            f"{cohort} {box}  apagón={bl['duration_h']:.2f} h "
            f"({bl['type']}{', benigno' if bl['benign'] else ''})  "
            f"corte declarado={e['gap_h']:.2f} h  zona gris = sin muestras")
        fig.tight_layout()
        out = fig_dir / f"apagon_{cohort}_{box}_{bl['duration_h']:.2f}h.png"
        fig.savefig(out, dpi=105)
        plt.close(fig)
        print(f"[fig] {out.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
