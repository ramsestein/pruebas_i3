#!/usr/bin/env python3
"""
scripts/verify/fase1_6d/wave_derived_validation.py
==================================================
Fase 1.6d — **punto 4**: validación obligatoria de las variables derivadas de las
ondas del ventilador (``src/common/wave_derived.py``).

Para cada evento de una cohorte de señal (Clínic / VitalDB) se derivan PIP,
PEEP y RR de ``Intellivue/AWP_WAV`` y TV de ``Intellivue/FLOW_WAV`` (agregación
por minuto con la mediana). Donde **coexisten** el valor derivado y el numérico
del ventilador (``PEEP_CMH2O``, ``PIP_CMH2O``, ``VENT_RR``, ``TV_EXP``) se
calcula, por variable:

- sesgo y límites de acuerdo de Bland–Altman (derivado − numérico);
- % de minutos dentro de ±2 cmH2O (PEEP/PIP), ±2 rpm (RR) y ±10 % (TV);
- veredicto ``usable``: ``|sesgo| <= 1 cmH2O / 1 rpm / 5 %`` y ``>= 80 %`` de
  minutos dentro del margen.

Salidas (``reports/fase1_6d/``):
  ``derivadas_<cohort>.json`` y ``derivadas_<cohort>.md``
  (y, si se pide, una figura de Bland–Altman por variable).

Uso:
    python scripts/verify/fase1_6d/wave_derived_validation.py --cohort vitaldb --limit 20
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.common.vital_signals import read_track_series  # noqa: E402
from src.common.wave_derived import (  # noqa: E402
    DEFAULT_FS_HZ,
    derive_from_waves,
    variable_is_usable,
)
from src.create_dataset.build_signal_cases import SPECS, scan_source_files  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
_AWP = "Intellivue/AWP_WAV"
_FLOW = "Intellivue/FLOW_WAV"
# Numérico del ventilador -> variable derivada correspondiente.
NUMERIC_TRACKS = {
    "PEEP": "Intellivue/PEEP_CMH2O",
    "PIP": "Intellivue/PIP_CMH2O",
    "RR": "Intellivue/VENT_RR",
    "TV": "Intellivue/TV_EXP",
}
TRACKS = [_AWP, _FLOW] + list(NUMERIC_TRACKS.values())


def latest_index(cohort: str) -> Path | None:
    cands = sorted((ROOT / "datasets" / cohort).glob(
        f"cases_v*/{cohort}_cases_index.json"))
    return cands[-1] if cands else None


def median_by_minute(t_sec: np.ndarray, vals: np.ndarray) -> dict[int, float]:
    if t_sec.size == 0:
        return {}
    keys = np.floor(t_sec / 60.0).astype(np.int64)
    out: dict[int, list[float]] = defaultdict(list)
    for k, v in zip(keys, vals):
        if np.isfinite(v):
            out[int(k)].append(float(v))
    return {k: float(np.median(v)) for k, v in out.items()}


def main() -> None:
    try:  # consola Windows cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--cohort", required=True, choices=sorted(SPECS))
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--index", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6d"))
    args = p.parse_args()

    config = load_config(args.config)
    path = Path(args.index) if args.index else latest_index(args.cohort)
    if path is None:
        raise SystemExit(f"no hay índice de {args.cohort}")
    index = json.loads(path.read_text(encoding="utf-8"))
    events = index["events"]
    if args.limit:
        events = events[: args.limit]

    boxes = scan_source_files(config_path(config, "paths", f"{args.cohort}_raw_dir"),
                              SPECS[args.cohort])
    by_box = {b: {sf.path.name: sf.path for sf in files}
              for b, files in boxes.items()}

    pairs_by_var: dict[str, list[tuple[float, float]]] = defaultdict(list)
    per_event: list[dict] = []
    n_no_waves = 0
    for n, e in enumerate(events, start=1):
        names = e.get("source_files") or []
        paths = [str(by_box.get(e["box"], {}).get(x)) if x else None for x in names]
        if not names or any(x is None for x in paths):
            continue
        series = read_track_series(paths, TRACKS, t0_unix=float(e["t0_unix"]))
        awp_t, awp_v = series.get(_AWP, (np.array([]), np.array([])))
        if awp_v.size == 0:
            n_no_waves += 1
            continue
        flow_t, flow_v = series.get(_FLOW, (np.array([]), np.array([])))
        # t en segundos desde t0 (read_track_series resta t0_unix).
        derived = derive_from_waves(
            awp_t, awp=awp_v,
            flow=(flow_v if flow_v.size else None), fs=DEFAULT_FS_HZ)
        derived_min = {"PIP": derived.pip_cmh2o, "PEEP": derived.peep_cmh2o,
                       "RR": derived.rr_rpm, "TV": derived.tv_ml}
        ev_pairs = {}
        for var, track in NUMERIC_TRACKS.items():
            nt, nv = series.get(track, (np.array([]), np.array([])))
            num_min = median_by_minute(nt, nv)
            dv = derived_min[var]
            common = [(dv[m], num_min[m]) for m in sorted(dv) if m in num_min]
            if common:
                pairs_by_var[var].extend(common)
                ev_pairs[var] = len(common)
        if ev_pairs:
            per_event.append({"event_id": e["event_id"], "pairs": ev_pairs})
        if n % 10 == 0:
            print(f"  {n}/{len(events)}", flush=True)

    report = {
        "cohort": args.cohort,
        "index": str(path.relative_to(ROOT)),
        "n_events": len(events),
        "n_events_with_awp": len(events) - n_no_waves,
        "n_events_without_awp": n_no_waves,
        "n_events_with_pairs": len(per_event),
        "variables": {},
    }
    for var in ("PIP", "PEEP", "RR", "TV"):
        rep = variable_is_usable(var, pairs_by_var.get(var, []))
        report["variables"][var] = rep

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"derivadas_{args.cohort}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=float),
        encoding="utf-8")

    lines = [f"# Variables derivadas de las ondas — {args.cohort} "
             "(Fase 1.6d, punto 4)", "",
             f"- Índice: `{report['index']}`",
             f"- Eventos: {report['n_events']} "
             f"(con `AWP_WAV`: {report['n_events_with_awp']}, "
             f"sin ondas: {report['n_events_without_awp']})",
             f"- Eventos con algún par derivado/numérico: "
             f"{report['n_events_with_pairs']}", "",
             "| Variable | n pares | Sesgo | LoA inf | LoA sup | Margen | "
             "Dentro del margen | ¿Usable? |",
             "|---|---|---|---|---|---|---|---|"]
    for var, rep in report["variables"].items():
        margin = rep["margin"]
        margin_s = f"±{margin:.0%}" if rep["relative"] else f"±{margin:g}"
        bias = "—" if rep["bias"] is None else f"{rep['bias']:+.3f}"
        loa_low = "—" if rep["loa_low"] is None else f"{rep['loa_low']:+.3f}"
        loa_high = "—" if rep["loa_high"] is None else f"{rep['loa_high']:+.3f}"
        lines.append(
            f"| {var} | {rep['n']} | {bias} | {loa_low} | {loa_high} | "
            f"{margin_s} | {rep['fraction_within']:.1%} | "
            f"{'SÍ' if rep['usable'] else 'NO'} |")
    (out_dir / f"derivadas_{args.cohort}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
