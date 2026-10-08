#!/usr/bin/env python3
"""
scripts/verify/fase1_6b/clinic_files_and_short.py
=================================================
Fase 1.6b — **punto 3**: por qué el rebuild de Clínic no termina y por qué los
eventos de < 1 h se clasificaron como artefactos.

Dos comprobaciones independientes:

**A. Coste de lectura.** Se cronometra la apertura de una muestra de ``.vital``
de Clínic y se relaciona con su tamaño. Si unos pocos ficheros tardan decenas de
segundos, el rebuild completo (15 291 ficheros) es inviable por I/O, no por
código.

**B. Pistas reales de los eventos cortos.** Para cada evento de < 1 h del índice
se lee su fichero **localizado en su propia caja** (no por nombre global) y se
listan TODAS las pistas de ventilador y de monitor presentes, sin restringirse a
``AWP``/``TV`` (que es lo único que mira ``classify_short_event``).

Salida: ``reports/fase1_6b/clinic_short_events_v2.json``

Uso:
    python scripts/verify/fase1_6b/clinic_files_and_short.py [--sample 40]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.clinic_files import index_by_name, locate_by_datetime, locate_event_files  # noqa: E402
from src.common.paths import config_path  # noqa: E402
from src.common.short_events import classify_short_event  # noqa: E402
from src.common.vital_signals import VENT_TRACKS, MONITOR_NUM_TRACKS  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"


def read_all_tracks(path: Path, tracks: list[str]) -> dict:
    """Pistas presentes (y nº de registros) y tiempo de lectura del fichero."""
    import vitaldb

    t0 = time.perf_counter()
    try:
        vf = vitaldb.VitalFile(str(path), track_names=tracks)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}",
                "read_s": round(time.perf_counter() - t0, 2)}
    dt = time.perf_counter() - t0
    trks = getattr(vf, "trks", None) or {}
    return {
        "read_s": round(dt, 2),
        "size_mb": round(path.stat().st_size / 1e6, 2),
        "tracks": {k: len(v.recs) for k, v in trks.items() if v and v.recs},
    }


def _size_profile(files: list[Path], top: int = 12) -> dict:
    """Distribución de TAMAÑOS (metadatos, sin abrir): coste de I/O."""
    rows = [{"file": f.name, "dir": f.relative_to(f.parents[len(f.parents) - 5]).parts[0]
             if len(f.parents) > 5 else "",
             "size_mb": round(f.stat().st_size / 1e6, 2)} for f in files]
    sizes = sorted(r["size_mb"] for r in rows)
    med = statistics.median(sizes) if sizes else 0.0
    p90 = sizes[int(0.9 * (len(sizes) - 1))] if sizes else 0.0
    return {
        "n_files": len(rows),
        "total_gb": round(sum(sizes) / 1000.0, 2),
        "median_size_mb": round(med, 2),
        "p90_size_mb": round(p90, 2),
        "max_size_mb": round(max(sizes), 2) if sizes else None,
        "n_ge_50mb": sum(1 for s in sizes if s >= 50.0),
        "n_ge_200mb": sum(1 for s in sizes if s >= 200.0),
        "largest": sorted(rows, key=lambda r: -r["size_mb"])[:top],
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--index", default=None)
    p.add_argument("--sample", type=int, default=40)
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6b"))
    args = p.parse_args()

    config = load_config(args.config)
    raw = config_path(config, "paths", "clinic_raw_dir")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = list(raw.rglob("*.vital"))
    in_boxes = [f for f in files if "dataset_clinic" not in f.parts]
    by_name = index_by_name(in_boxes)

    index_path = Path(args.index) if args.index else (
        ROOT / "datasets" / "clinic" / "cases_v0.1.0_a225d21b" / "clinic_cases_index.json")
    idx = json.loads(index_path.read_text(encoding="utf-8"))
    short = [e for e in idx["events"] if e["duration_seconds"] < 3600]

    all_tracks = list(VENT_TRACKS) + list(MONITOR_NUM_TRACKS)
    events_out = []
    for i, ev in enumerate(short, start=1):
        found, missing = locate_event_files(ev["source_files"], ev.get("box"), by_name)
        how = "nombre_en_caja"
        if missing:
            by_dt = locate_by_datetime(in_boxes, ev.get("t0_unix"), box=ev.get("box"))
            found = found + [x for x in by_dt if x not in found]
            how = "fecha_hora" if by_dt else "no_encontrado"
        reads = [{"file": f.name, **read_all_tracks(f, all_tracks)} for f in found]
        vent_present = sorted({t for r in reads for t in (r.get("tracks") or {})
                               if t in VENT_TRACKS})
        monitor_present = sorted({t for r in reads for t in (r.get("tracks") or {})
                                  if t in MONITOR_NUM_TRACKS})
        events_out.append({
            "event_id": ev["event_id"], "box": ev.get("box"),
            "duration_min": round(ev["duration_seconds"] / 60.0, 2),
            "location": how, "n_files_found": len(found),
            "n_missing": len(missing),
            "vent_tracks_present": vent_present,
            "monitor_tracks_present": monitor_present,
            "reads": reads,
        })
        print(f"[{i}/{len(short)}] {ev['event_id']} vent={vent_present} "
              f"mon={monitor_present}", flush=True)

    summary = {
        "index": str(index_path.relative_to(ROOT)),
        "n_raw_files": len(files),
        "n_raw_in_boxes": len(in_boxes),
        "read_cost": _size_profile(in_boxes),
        "n_short_events": len(events_out),
        "n_with_vent_tracks": sum(1 for e in events_out if e["vent_tracks_present"]),
        "n_with_awp_or_tv": sum(
            1 for e in events_out
            if any(t in ("Intellivue/AWP_WAV", "Intellivue/TV_EXP", "Intellivue/TV")
                   for t in e["vent_tracks_present"])),
        "vent_tracks_seen": sorted({t for e in events_out for t in e["vent_tracks_present"]}),
        "events": events_out,
    }
    (out_dir / "clinic_short_events_v2.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "events"},
                     ensure_ascii=False, indent=2))
    for e in events_out:
        print(f"  {e['event_id']:28} {e['duration_min']:6.2f} min  {e['location']:16} "
              f"ficheros={e['n_files_found']} vent={e['vent_tracks_present']} "
              f"mon={e['monitor_tracks_present']}")


if __name__ == "__main__":
    main()
