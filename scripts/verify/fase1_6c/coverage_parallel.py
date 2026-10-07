#!/usr/bin/env python3
"""
scripts/verify/fase1_6c/coverage_parallel.py
============================================
Fase 1.6c — **punto 4**: cobertura por evento en paralelo, fuera del builder.

Por qué: en el builder la cobertura se mide **en serie**, releyendo los ficheros
de cada evento. En Clínic (5 471 ficheros, 101.5 GB, algunos con latencias de
decenas de segundos) esa fase se convierte en el cuello de botella de toda la
reconstrucción. Medirla aparte, con un proceso por evento, deja el mismo
resultado y aprovecha los núcleos.

Uso típico (dos pasos, mucho más rápido que `--coverage` en serie):

    python -m src.create_dataset.build_signal_cases --cohort clinic --no-merge --workers 10
    python scripts/verify/fase1_6c/coverage_parallel.py --cohort clinic --workers 10

Escribe ``coverage``, ``vars_ok_50`` y ``vars_ok_80`` en cada evento del índice
(y el resumen de la cohorte), con un informe en ``reports/fase1_6c/``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.common.vital_signals import (  # noqa: E402
    coverage_fractions,
    read_coverage_series,
)
from src.create_dataset.build_signal_cases import SPECS, scan_source_files  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
D13_CORE = ("HR", "SpO2")


def latest_index(cohort: str) -> Path | None:
    cands = sorted((ROOT / "datasets" / cohort).glob(
        f"cases_v*/{cohort}_cases_index.json"))
    return cands[-1] if cands else None


def _coverage_task(task: tuple[str, list[str], float, list[tuple[float, float]]]):
    """Cobertura de un evento (función de nivel superior: se ejecuta en el pool)."""
    event_id, paths, t0_unix, vent_spans_h = task
    try:
        series = read_coverage_series(paths, t0_unix)
        fracs = coverage_fractions(series, vent_spans_h)
    except Exception as exc:  # noqa: BLE001
        return event_id, None, str(exc)
    return event_id, {k: round(v, 4) for k, v in fracs.items()}, None


def main() -> None:
    try:  # consola Windows en cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--cohort", required=True, choices=sorted(SPECS))
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--index", default=None)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6c"))
    args = p.parse_args()

    cohort = args.cohort
    config = load_config(args.config)
    path = Path(args.index) if args.index else latest_index(cohort)
    if path is None:
        raise SystemExit(f"no hay índice de {cohort}")
    index = json.loads(path.read_text(encoding="utf-8"))
    events = index["events"]
    if args.limit:
        events = events[: args.limit]

    boxes = scan_source_files(config_path(config, "paths", f"{cohort}_raw_dir"),
                              SPECS[cohort])
    by_box = {b: {sf.path.name: sf.path for sf in files}
              for b, files in boxes.items()}

    tasks = []
    missing: list[str] = []
    for e in events:
        names = e.get("source_files") or []
        paths = [str(by_box.get(e["box"], {}).get(n)) if n else None
                 for n in names]
        if not names or any(x is None for x in paths):
            missing.append(e["event_id"])
            continue
        tasks.append((e["event_id"], paths, float(e["t0_unix"]),
                      [(a["vent_start_h"], a["vent_end_h"])
                       for a in e["attempts"]]))

    print(f"[{cohort}] {len(tasks)} eventos a medir, {len(missing)} sin ficheros"
          f"{' (p.ej. ' + ', '.join(missing[:3]) + ')' if missing else ''}",
          flush=True)

    results: dict[str, dict] = {}
    errors: list[dict] = []
    if args.workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for i, (event_id, fracs, err) in enumerate(
                    ex.map(_coverage_task, tasks, chunksize=1), start=1):
                if err:
                    errors.append({"event_id": event_id, "error": err})
                else:
                    results[event_id] = fracs
                if i % 20 == 0:
                    print(f"  {i}/{len(tasks)}", flush=True)
    else:
        for t in tasks:
            event_id, fracs, err = _coverage_task(t)
            if err:
                errors.append({"event_id": event_id, "error": err})
            else:
                results[event_id] = fracs

    for e in events:
        fracs = results.get(e["event_id"])
        if fracs is None:
            continue
        e["coverage"] = fracs
        e["vars_ok_50"] = bool(fracs) and all(v > 0.5 for v in fracs.values())
        e["vars_ok_80"] = bool(fracs) and all(v > 0.8 for v in fracs.values())

    measured = [e for e in events if e["event_id"] in results]
    n50 = sum(1 for e in measured if e.get("vars_ok_50"))
    n80 = sum(1 for e in measured if e.get("vars_ok_80"))
    d13 = sum(1 for e in measured
              if all(float(e["coverage"].get(v, 0.0)) > 0.5 for v in D13_CORE))
    index["levels_coverage_measured"] = len(measured)
    index["vars_ok_50"] = n50
    index["vars_ok_80"] = n80
    index["d13_incluidos"] = d13
    per_var: dict[str, list[float]] = defaultdict(list)
    for e in measured:
        for k, v in e["coverage"].items():
            per_var[k].append(float(v))
    medians = {k: round(sorted(v)[len(v) // 2], 3) for k, v in sorted(per_var.items())}

    path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    out = {
        "index": str(path.relative_to(ROOT)), "cohort": cohort,
        "n_events": len(index["events"]), "n_measured": len(measured),
        "n_without_source_files": len(missing),
        "events_without_source_files": missing[:50],
        "vars_ok_50": n50, "vars_ok_80": n80,
        "vars_ok_50_pct": round(100.0 * n50 / len(measured), 1) if measured else 0.0,
        "d13_incluidos": d13,
        "d13_pct": round(100.0 * d13 / len(measured), 1) if measured else 0.0,
        "cobertura_mediana_por_variable": medians,
        "n_errors": len(errors), "errors": errors[:20],
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"cobertura_{cohort}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "errors"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
