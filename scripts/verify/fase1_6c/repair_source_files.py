#!/usr/bin/env python3
"""
scripts/verify/fase1_6c/repair_source_files.py
==============================================
Fase 1.6c — **punto 4**: repara los eventos que quedaron **sin
``source_files``** en un índice ya construido.

Por qué existe: en VitalDB (y en Clínic) hay ficheros ``.vital`` cuyo **nombre
no coincide con la fecha del contenido**; el episodio nace de la **cabecera**
(``dtstart``/``dtend``), no del nombre. Sin respaldo, esos eventos se quedaban
sin ficheros de origen: no se podían auditar y su cobertura quedaba a 0, con lo
que D13 los excluía por un artefacto.

El builder ya incorpora el respaldo
(``build_signal_cases._files_for_episode(..., probes=...)``). Este script aplica
el MISMO criterio a un índice existente —reconstruyendo el episodio mínimo desde
los campos del evento— y recalcula ``coverage``, ``vars_ok_50/80`` de los eventos
reparados. Se usa para no repetir reconstrucciones de decenas de horas (Clínic:
101.5 GB) cuando el resto del índice es correcto.

Salida (``reports/fase1_6c/``):
  ``reparacion_source_files.json``

Uso:
    python scripts/verify/fase1_6c/repair_source_files.py --cohort vitaldb
    python scripts/verify/fase1_6c/repair_source_files.py --cohort clinic --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.episodes import Attempt, Episode  # noqa: E402
from src.common.paths import config_path  # noqa: E402
from src.common.vital_signals import (  # noqa: E402
    coverage_fractions,
    read_coverage_series,
)
from src.create_dataset.build_signal_cases import (  # noqa: E402
    SPECS,
    _files_for_episode,
    _segment_box_task,
    scan_source_files,
)
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
SECONDS_PER_HOUR = 3600.0


def latest_index(cohort: str) -> Path | None:
    cands = sorted((ROOT / "datasets" / cohort).glob(
        f"cases_v*/{cohort}_cases_index.json"))
    return cands[-1] if cands else None


def episode_from_event(event: dict) -> Episode:
    """Reconstruye un ``Episode`` mínimo desde un evento del índice."""
    t0_h = float(event["t0_unix"]) / SECONDS_PER_HOUR
    attempts = [Attempt(attempt_idx=a["attempt_idx"],
                        start_h=t0_h + float(a["vent_start_h"]),
                        end_h=t0_h + float(a["vent_end_h"]))
                for a in event["attempts"]]
    obs_end = t0_h + float(event.get("obs_end_h") or 0.0)
    region_end = obs_end + float(event.get("monitor_tail_h") or 0.0)
    return Episode(attempts=attempts, region_start_h=t0_h,
                   region_end_h=region_end)


def main() -> None:
    try:  # consola Windows en cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--cohort", required=True, choices=sorted(SPECS))
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--index", default=None)
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6c"))
    p.add_argument("--workers", type=int, default=4,
                   help="cajas en paralelo (cada caja se sondea entera)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    cohort = args.cohort
    config = load_config(args.config)
    spec = SPECS[cohort]
    path = Path(args.index) if args.index else latest_index(cohort)
    if path is None:
        raise SystemExit(f"no hay índice de {cohort}")
    index = json.loads(path.read_text(encoding="utf-8"))
    events = index["events"]

    empty = [e for e in events if not e.get("source_files")]
    if not empty:
        print(f"[{cohort}] ningún evento sin source_files")
        return
    by_box: dict[str, list[dict]] = defaultdict(list)
    for e in empty:
        by_box[e["box"]].append(e)
    print(f"[{cohort}] {len(empty)} eventos sin ficheros en "
          f"{len(by_box)} cajas: {sorted(by_box)}")

    raw_dir = config_path(config, "paths", f"{cohort}_raw_dir")
    boxes = scan_source_files(raw_dir, spec)
    report: dict = {"index": str(path.relative_to(ROOT)), "cohort": cohort,
                    "n_events": len(events), "n_without_source_files": len(empty),
                    "boxes": sorted(by_box), "repaired": [], "failed": []}

    # Sondeo de las cajas afectadas (en paralelo: cada caja entera).
    missing_boxes = [b for b in sorted(by_box) if b in boxes]
    print(f"  sondeando {len(missing_boxes)} cajas con {args.workers} procesos...",
          flush=True)
    segs: dict[str, object] = {}
    if missing_boxes:
        if args.workers > 1 and len(missing_boxes) > 1:
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=args.workers) as ex:
                for box, seg in ex.map(_segment_box_task,
                                       [(b, boxes[b]) for b in missing_boxes]):
                    segs[box] = seg
        else:
            for b in missing_boxes:
                box, seg = _segment_box_task((b, boxes[b]))
                segs[box] = seg

    for box, evs in sorted(by_box.items()):
        seg = segs.get(box)
        if seg is None:
            report["failed"] += [{"event_id": e["event_id"],
                                  "reason": "caja no encontrada"} for e in evs]
            continue
        for ev in evs:
            ep = episode_from_event(ev)
            found = _files_for_episode(seg.files, ep, probes=seg.probes)
            if not found:
                report["failed"].append({"event_id": ev["event_id"],
                                         "reason": "sin coincidencia por cabecera"})
                continue
            cov = None
            if "coverage" in ev:
                series = read_coverage_series([f.path for f in found],
                                              float(ev["t0_unix"]))
                vent_spans_h = [(a["vent_start_h"], a["vent_end_h"])
                                for a in ev["attempts"]]
                fracs = coverage_fractions(series, vent_spans_h)
                cov = {k: round(v, 4) for k, v in fracs.items()}
                ev["vars_ok_50"] = bool(fracs) and all(v > 0.5 for v in fracs.values())
                ev["vars_ok_80"] = bool(fracs) and all(v > 0.8 for v in fracs.values())
                ev["coverage"] = cov
            if not args.dry_run:
                ev["source_files"] = [f.path.name for f in found]
                ev["source_tokens"] = sorted({f.token for f in found})
            report["repaired"].append({
                "event_id": ev["event_id"], "box": box,
                "n_files": len(found), "first": found[0].path.name,
                "last": found[-1].path.name,
                "vars_ok_50": ev.get("vars_ok_50"),
                "coverage": cov,
            })
            print(f"  [ok] {ev['event_id']}: {len(found)} ficheros "
                  f"({found[0].path.name} ... {found[-1].path.name})")

    report["n_repaired"] = len(report["repaired"])
    report["n_failed"] = len(report["failed"])
    report["dry_run"] = bool(args.dry_run)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not args.dry_run and report["n_repaired"]:
        index_path = path if path.suffix == ".json" else path.with_suffix(".json")
        index_path.write_text(json.dumps(index, ensure_ascii=False),
                              encoding="utf-8")
        print(f"índice reescrito: {index_path.relative_to(ROOT)}")
    (out_dir / "reparacion_source_files.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # Cobertura de la cohorte tras la reparación (efecto sobre D13).
    core = ("HR", "SpO2")
    d13 = sum(1 for e in events
              if all(float((e.get("coverage") or {}).get(v, 0.0)) > 0.5
                     for v in core))
    print(f"\n[{cohort}] reparados {report['n_repaired']}, "
          f"fallidos {report['n_failed']}; D13 ahora {d13}/{len(events)}")
    if report["failed"]:
        print("fallidos:", json.dumps(report["failed"], ensure_ascii=False))


if __name__ == "__main__":
    main()
