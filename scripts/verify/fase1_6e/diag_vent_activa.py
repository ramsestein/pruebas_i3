#!/usr/bin/env python3
"""
scripts/verify/fase1_6e/diag_vent_activa.py
===========================================
Fase 1.6e — **punto 1**: diagnóstico de la "ventilación activa" (antes de tocar
código).

Para los eventos afectados (los que en la 1.6d pasaron de éxito a
``end_of_record``) y una muestra de controles (éxitos que se mantienen), en las
**12 h finales** de la "ventilación" actual calcula **por minuto**:

  TV_EXP, MV_EXP, VENT_RR, PIP_CMH2O, PEEP_CMH2O, FIO2, amplitud de AWP_WAV
  (p95−p5) y amplitud de FLOW_WAV (y TV_INSP si existe).

Y para cada pista: **última hora con valores activos** y **última hora con
cualquier registro**.

Produce la tabla del punto 1c y los PNG del punto 1b (FC/SpO2/MAP arriba;
TV_EXP, VENT_RR, PIP−PEEP, amplitud AWP y FiO2 abajo; con marcas del fin actual
de la ventilación, el fin de la ventilación activa y el fin del monitor
fisiológico).

Salida: ``reports/fase1_6e/diagnostico_vent_activa.{json,md}``
        ``reports/fase1_6e/figs_diag/<event_id>.png``

Uso:
    python scripts/verify/fase1_6e/diag_vent_activa.py --cohort clinic --png 10
    python scripts/verify/fase1_6e/diag_vent_activa.py --cohort vitaldb --png 10 --control 5
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.common.vital_signals import read_track_series  # noqa: E402
from src.create_dataset.build_signal_cases import SPECS, scan_source_files  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
REPORT = ROOT / "reports" / "fase1_6e"

# ── Criterios de ventilación ACTIVA (D-a: fijos, clínicos) ────────────────────
# Un minuto es ventilación activa si cumple AL MENOS UNO de estos. FIO2 y PEEP
# por sí solas NO cuentan (son ajustes que siguen registrándose con el
# ventilador en espera o con alto flujo).
#
# Estos valores son los MISMOS que `src/common/vital_signals.ACTIVITY_*` (punto
# 2). El diagnóstico los repite a propósito para ser autocontenido: es la
# herramienta con la que se decide si el criterio sirve, y no debe depender del
# código que aún no está commiteado.
TV_EXP_MIN_ML = 100.0        # por debajo no hay volumen tidal entregado
MV_EXP_MIN_LPM = 1.0         # por debajo no hay ventilación minuto efectiva
AWP_AMP_MIN_CMH2O = 5.0      # amplitud p95−p5: la espera no la da

NUM_TRACKS = {
    "TV_EXP": "Intellivue/TV_EXP",
    "MV_EXP": "Intellivue/MV_EXP",
    "VENT_RR": "Intellivue/VENT_RR",
    "PIP": "Intellivue/PIP_CMH2O",
    "PEEP": "Intellivue/PEEP_CMH2O",
    "FIO2": "Intellivue/FIO2",
    "TV_INSP": "Intellivue/TV_INSP",
}
WAVE_TRACKS = {"AWP_WAV": "Intellivue/AWP_WAV", "FLOW_WAV": "Intellivue/FLOW_WAV"}
MONITOR = ("Intellivue/ECG_HR", "Intellivue/PLETH_HR", "Intellivue/HR",
           "Intellivue/PLETH_SAT_O2", "Intellivue/ABP_MEAN",
           "Intellivue/NIBP_MEAN", "Intellivue/ART_MEAN")

TAIL_FILES = 13   # ~12 h


def _minute_median(t_sec: np.ndarray, vals: np.ndarray) -> dict[int, float]:
    if t_sec.size == 0:
        return {}
    keys = np.floor(t_sec / 60.0).astype(np.int64)
    buckets: dict[int, list[float]] = defaultdict(list)
    for k, v in zip(keys, vals):
        if np.isfinite(v):
            buckets[int(k)].append(float(v))
    return {k: float(np.median(v)) for k, v in buckets.items()}


def _minute_amplitude(t_sec: np.ndarray, vals: np.ndarray) -> dict[int, float]:
    if t_sec.size < 5:
        return {}
    keys = np.floor(t_sec / 60.0).astype(np.int64)
    out: dict[int, float] = {}
    order = np.argsort(keys, kind="stable")
    keys, vals = keys[order], vals[order]
    edges = np.flatnonzero(np.diff(keys)) + 1
    for seg in np.split(np.arange(keys.size), edges):
        if seg.size < 5:
            continue
        v = vals[seg]
        out[int(keys[seg[0]])] = float(np.percentile(v, 95)
                                       - np.percentile(v, 5))
    return out


def _last_minute(d: dict[int, float], predicate=None) -> float | None:
    ok = [k for k, v in d.items()
          if predicate is None or predicate(v)]
    return float(max(ok)) / 60.0 if ok else None


def _mask_window(
    series: dict[str, tuple[np.ndarray, np.ndarray]],
    window_h: tuple[float, float] | None,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Recorta las series a la ventana (horas relativas a t0).

    Necesario porque en Clínic los ficheros de origen NO son horarios: hay
    grabaciones de 20-48 h, así que no se puede seleccionar ficheros por su
    hora de nombre (se perderían los que cubren la ventana).
    """
    if window_h is None:
        return series
    lo_s, hi_s = window_h[0] * 3600.0, window_h[1] * 3600.0
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for k, (t, v) in series.items():
        if t.size == 0:
            out[k] = (t, v)
            continue
        m = (t >= lo_s) & (t <= hi_s)
        out[k] = (t[m], v[m])
    return out


def analyse_event(
    event: dict,
    paths: list[str],
    t0_unix: float,
    *,
    window_h: tuple[float, float] | None = None,
) -> dict:
    tracks = {v: v for v in list(NUM_TRACKS.values()) + list(WAVE_TRACKS.values())}
    series = read_track_series(paths, list(tracks) + list(MONITOR), t0_unix=t0_unix)
    series = _mask_window(series, window_h)
    per_min: dict[str, dict[int, float]] = {}
    for name, track in NUM_TRACKS.items():
        t, v = series.get(track, (np.array([]), np.array([])))
        per_min[name] = _minute_median(t, v)
    for name, track in WAVE_TRACKS.items():
        t, v = series.get(track, (np.array([]), np.array([])))
        per_min[name] = _minute_amplitude(t, v)

    # Actividad por minuto: TV_EXP / MV_EXP / amplitud AWP con VENT_RR > 0.
    rr = per_min.get("VENT_RR", {})
    active: set[int] = set()
    for k, v in per_min.get("TV_EXP", {}).items():
        if v >= TV_EXP_MIN_ML:
            active.add(k)
    for k, v in per_min.get("MV_EXP", {}).items():
        if v >= MV_EXP_MIN_LPM:
            active.add(k)
    for k, v in per_min.get("AWP_WAV", {}).items():
        if v >= AWP_AMP_MIN_CMH2O and rr.get(k, 0.0) > 0.0:
            active.add(k)

    vent_end_actual = float(event["attempts"][-1]["vent_end_h"])
    fin_activa = (float(max(active)) / 60.0 if active else None)
    obs_end = event.get("obs_end_h")

    # Qué pistas siguen con registro mucho después del fin activo.
    prolongan = []
    for name, d in per_min.items():
        last_any = _last_minute(d)
        if last_any is None:
            continue
        last_active = None
        if name in ("TV_EXP", "MV_EXP"):
            thr = TV_EXP_MIN_ML if name == "TV_EXP" else MV_EXP_MIN_LPM
            last_active = _last_minute(d, lambda v, t=thr: v >= t)
        elif name == "AWP_WAV":
            last_active = (float(max(active)) / 60.0 if active else None)
        elif name == "VENT_RR":
            last_active = _last_minute(d, lambda v: v > 0.0)
        elif name in ("FIO2", "PEEP", "PIP"):
            last_active = None  # ajustes: no definen actividad
        if fin_activa is not None and last_any > fin_activa + 0.5:
            prolongan.append(name)

    tail_h = (float(obs_end) - fin_activa) if (obs_end is not None
                                              and fin_activa is not None) else None
    return {
        "event_id": event["event_id"], "box": event["box"],
        "fin_vent_actual_h": round(vent_end_actual, 3),
        "fin_vent_activa_h": (None if fin_activa is None else round(fin_activa, 3)),
        "fin_monitor_fisiologico_h": obs_end,
        "cola_nueva_h": (None if tail_h is None else round(tail_h, 3)),
        "pistas_que_prolongan": sorted(prolongan),
        "n_minutos_activos": len(active),
        "per_min": {k: {str(m): round(v, 3) for m, v in sorted(d.items())}
                    for k, d in per_min.items()},
        "_series": series,  # ya enmascaradas a la ventana
        "ventana_h": None if window_h is None else [round(window_h[0], 3),
                                                    round(window_h[1], 3)],
    }


def plot_event(res: dict, paths: list[str], t0_unix: float, out_png: Path) -> bool:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    series = res.pop("_series", None) or _mask_window(
        read_track_series(paths, list(MONITOR), t0_unix=t0_unix), None)
    per_min = res["per_min"]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    for canon, tracks in (("HR", ("Intellivue/ECG_HR", "Intellivue/PLETH_HR",
                                  "Intellivue/HR")),
                          ("SpO2", ("Intellivue/PLETH_SAT_O2",)),
                          ("MAP", ("Intellivue/ABP_MEAN",
                                   "Intellivue/NIBP_MEAN",
                                   "Intellivue/ART_MEAN"))):
        for trk in tracks:
            t, v = series.get(trk, (np.array([]), np.array([])))
            if v.size:
                ax1.plot(t / 3600.0, v, ".", ms=1, label=canon)
                break
    ax1.set_ylabel("monitor")
    ax1.legend(loc="upper right", fontsize=7, ncol=3)

    for name, color in (("TV_EXP", "tab:blue"), ("VENT_RR", "tab:green"),
                        ("AWP_WAV", "tab:red"), ("FIO2", "tab:orange")):
        d = {int(k): v for k, v in per_min.get(name, {}).items()}
        if d:
            ax2.plot([m / 60.0 for m in sorted(d)],
                     [d[m] for m in sorted(d)], ".", ms=2, color=color,
                     label=name)
    pip = {int(k): v for k, v in per_min.get("PIP", {}).items()}
    peep = {int(k): v for k, v in per_min.get("PEEP", {}).items()}
    common = sorted(set(pip) & set(peep))
    if common:
        ax2.plot([m / 60.0 for m in common],
                 [pip[m] - peep[m] for m in common], ".", ms=2,
                 color="tab:purple", label="PIP-PEEP")
    ax2.set_xlabel("horas desde t0")
    ax2.set_ylabel("ventilador")
    ax2.legend(loc="upper right", fontsize=7, ncol=3)

    for ax in (ax1, ax2):
        ax.axvline(res["fin_vent_actual_h"], color="k", ls="--", lw=1,
                   label="fin vent actual")
        if res["fin_vent_activa_h"] is not None:
            ax.axvline(res["fin_vent_activa_h"], color="tab:red", ls=":", lw=1.5)
        if res["fin_monitor_fisiologico_h"] is not None:
            ax.axvline(res["fin_monitor_fisiologico_h"], color="tab:blue",
                       ls="-.", lw=1)
    ax2.set_title(f"{res['event_id']} — fin actual {res['fin_vent_actual_h']} h · "
                  f"fin activa {res['fin_vent_activa_h']} h · cola nueva "
                  f"{res['cola_nueva_h']} h · prolongan: "
                  f"{','.join(res['pistas_que_prolongan'])}")
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=110)
    plt.close(fig)
    return True


def _affected_from_index(index: dict, *, max_tail_h: float = 1.0) -> list[str]:
    """Eventos candidatos a recuperar: censurados ``end_of_record`` con cola
    de monitor fisiológico < 1 h **en el índice nuevo**.

    OJO: los ``event_id`` son posicionales por box (``..._event_i``), así que
    emparejar ids entre el índice antiguo y el nuevo compara eventos distintos.
    La comparación debe hacerse dentro del MISMO índice.
    """
    out = []
    for e in index["events"]:
        lab = (e.get("labels") or {}).get("48h") or {}
        if lab.get("censor_cause") != "end_of_record":
            continue
        tail = e.get("monitor_tail_h")
        if tail is None or float(tail) < max_tail_h:
            out.append(e["event_id"])
    return out


def _event_paths(ev: dict, by_box_meta: dict) -> list[str]:
    """Rutas de TODOS los ``source_files`` del evento (sin filtrar por hora).

    En Clínic los ficheros de origen NO son horarios (hay grabaciones de 20-48 h)
    y la hora del nombre va 1-2 h por detrás del contenido, así que seleccionar
    ficheros por hora de nombre pierde los que cubren la ventana. Se leen todos
    y se enmascara por tiempo en ``analyse_event``.
    """
    out: list[str] = []
    for n in dict.fromkeys(ev.get("source_files") or []):
        sf = by_box_meta.get(ev.get("box"), {}).get(n)
        if sf is not None:
            out.append(str(sf.path))
    return out


def _window_for(ev: dict) -> tuple[float, float]:
    """Ventana de análisis anclada al fin de la VENTILACIÓN grabada.

    12 h antes y hasta ``min(max(fin+2 h, fin del monitor), fin+24 h)`` después:
    así se mide también la cola real de los controles (los afectados tienen cola
    ~0 y el tope no cambia nada).
    """
    vent_end_h = float(ev["attempts"][-1]["vent_end_h"])
    obs_end = ev.get("obs_end_h")
    return (vent_end_h - 12.0,
            min(max(vent_end_h + 2.0, float(obs_end or vent_end_h)),
                vent_end_h + 24.0))


def _controls(cohort: str, n: int) -> list[str]:
    cands = sorted((ROOT / "datasets" / cohort).glob(
        f"cases_v*/{cohort}_cases_index.json"))
    if not cands:
        return []
    idx = json.loads(cands[-1].read_text(encoding="utf-8"))
    ok = [e["event_id"] for e in idx["events"]
          if (e.get("labels") or {}).get("48h", {}).get("event_type")
          == "successful_extubation"]
    if not ok or n <= 0:
        return []
    step = max(1, len(ok) // n)
    return ok[::step][:n]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--cohort", required=True, choices=sorted(SPECS))
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--affected-limit", type=int, default=None)
    p.add_argument("--control", type=int, default=0)
    p.add_argument("--png", type=int, default=0)
    # Reanudación y red de seguridad ante los parones intermitentes de D:\.
    p.add_argument("--jsonl", default=None,
                   help="Estado incremental (reanudable). Por defecto "
                        "reports/fase1_6e/diagnostico_vent_activa_<cohort>.jsonl")
    p.add_argument("--min-timeout", type=float, default=240.0,
                   help="Timeout mínimo por evento (s).")
    p.add_argument("--s-per-gb", type=float, default=300.0,
                   help="Segundos de timeout por GB de ficheros del evento.")
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--only-event", default=None,
                   help="Analiza UN evento y escribe el resultado en --emit "
                        "(uso interno: se ejecuta en subproceso).")
    p.add_argument("--emit", default=None)
    p.add_argument("--emit-png", default=None)
    args = p.parse_args()

    config = load_config(args.config)
    cohort = args.cohort
    Report = REPORT
    boxes = scan_source_files(config_path(config, "paths", f"{cohort}_raw_dir"),
                              SPECS[cohort])
    by_box_meta = {b: {sf.path.name: sf for sf in files}
                   for b, files in boxes.items()}

    indices = sorted((ROOT / "datasets" / cohort).glob(
        f"cases_v*/{cohort}_cases_index.json"))
    idx = json.loads(indices[-1].read_text(encoding="utf-8"))
    by_id = {e["event_id"]: e for e in idx["events"]}

    # ── Modo de un solo evento (subproceso con timeout del padre) ────────────
    if args.only_event:
        ev = by_id[args.only_event]
        paths = _event_paths(ev, by_box_meta)
        if not paths:
            raise SystemExit("evento sin source_files")
        res = analyse_event(ev, paths, float(ev["t0_unix"]),
                            window_h=_window_for(ev))
        if args.emit_png:
            plot_event(dict(res), paths, float(ev["t0_unix"]),
                       Path(args.emit_png))
        res.pop("_series", None)
        Path(args.emit).write_text(json.dumps(res, ensure_ascii=False),
                                   encoding="utf-8")
        return

    affected = _affected_from_index(idx)
    if args.affected_limit:
        affected = affected[: args.affected_limit]
    controls = _controls(cohort, args.control)
    todo = affected + controls
    print(f"[{cohort}] afectados {len(affected)}; controles {len(controls)}",
          flush=True)

    jsonl = Path(args.jsonl) if args.jsonl else (
        REPORT / f"diagnostico_vent_activa_{cohort}.jsonl")
    done: dict[str, dict] = {}
    if jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done[row["event_id"]] = row
    if done:
        print(f"  reanudando: {len(done)} eventos ya analizados", flush=True)

    n_timeout = 0
    for i, eid in enumerate(todo, start=1):
        ev = by_id.get(eid)
        if ev is None or (eid in done and not done[eid].get("timeout")):
            continue
        paths = _event_paths(ev, by_box_meta)
        if not paths:
            print(f"  [sin ficheros] {eid}", flush=True)
            continue
        size_gb = sum(Path(p).stat().st_size for p in paths) / 1e9
        tmo = max(args.min_timeout, size_gb * args.s_per_gb)
        tmp = REPORT / f".tmp_{cohort}_{eid}.json"
        png = None
        if args.png and i <= len(affected):
            png = REPORT / "figs_diag" / f"{eid}.png"
        row: dict | None = None
        for attempt in range(1, args.retries + 1):
            if tmp.exists():
                tmp.unlink()
            cmd = [sys.executable, str(Path(__file__).resolve()),
                   "--cohort", cohort, "--config", args.config,
                   "--only-event", eid, "--emit", str(tmp)]
            if png is not None:
                cmd += ["--emit-png", str(png)]
            t0 = time.time()
            try:
                subprocess.run(cmd, timeout=tmo, capture_output=True,
                               cwd=str(ROOT), check=False)
            except subprocess.TimeoutExpired:
                print(f"  [{i}/{len(todo)}] {eid} TIMEOUT {tmo:.0f}s "
                      f"({size_gb:.2f} GB) intento {attempt}", flush=True)
                continue
            if tmp.exists():
                row = json.loads(tmp.read_text(encoding="utf-8"))
                tmp.unlink()
                print(f"  [{i}/{len(todo)}] {eid} ficheros={len(paths)} "
                      f"{size_gb:.2f} GB en {time.time()-t0:.1f}s "
                      f"n_act={row['n_minutos_activos']}", flush=True)
                break
            print(f"  [{i}/{len(todo)}] {eid} sin salida (intento {attempt})",
                  flush=True)
        if row is None:
            # NO se salta: se marca para reintentar en la siguiente pasada.
            row = {"event_id": eid, "box": ev["box"], "timeout": True,
                   "n_ficheros": len(paths), "gb": round(size_gb, 3)}
            n_timeout += 1
        with jsonl.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        done[eid] = row

    rows = [done[e] for e in todo
            if e in done and not done[e].get("timeout")]
    Report.mkdir(parents=True, exist_ok=True)
    (Report / f"diagnostico_vent_activa_{cohort}.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"analizados {len(rows)}/{len(todo)} · pendientes por timeout: "
          f"{sum(1 for e in todo if done.get(e, {}).get('timeout'))}"
          f" (nuevos en esta pasada: {n_timeout})", flush=True)

    lines = [f"# Diagnóstico de ventilación activa — {cohort} "
             "(Fase 1.6e, punto 1)", "",
             f"Eventos analizados: {len(rows)} "
             f"(afectados {len(affected)}, controles {len(controls)})", "",
             "| Evento | fin_vent_actual | fin_vent_activa | fin_monitor_fis | "
             "cola_nueva_h | pistas que prolongan |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['event_id']} | {r['fin_vent_actual_h']} | "
            f"{r['fin_vent_activa_h']} | {r['fin_monitor_fisiologico_h']} | "
            f"{r['cola_nueva_h']} | {', '.join(r['pistas_que_prolongan']) or '—'} |")
    (Report / f"diagnostico_vent_activa_{cohort}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print(f"tabla -> {Report / f'diagnostico_vent_activa_{cohort}.md'}")


if __name__ == "__main__":
    main()
