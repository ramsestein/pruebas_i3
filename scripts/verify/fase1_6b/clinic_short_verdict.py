#!/usr/bin/env python3
"""
scripts/verify/fase1_6b/clinic_short_verdict.py
===============================================
Fase 1.6b — **punto 3**: veredicto final de los 16 eventos de < 1 h de Clínic.

Usa ``reports/fase1_6b/clinic_short_events_v2.json`` (pistas REALES por fichero,
localizadas en la caja del evento) y decide con los criterios de
``src/common/short_events.py``, que desde la Fase 1.6b aceptan también la
evidencia por **ajustes** (marcadores de D6) cuando no hay onda de presión:

- onda de presión cíclica (AWP) → ventilación;
- o ≥ ``MIN_MARKER_TRACKS`` marcadores distintos con registros
  (PEEP, PIP, FR total del ventilador, volumen tidal) → ventilación;
- en otro caso → artefacto / sin señal.

Salida: ``reports/fase1_6b/clinic_short_verdict.json``

Uso:
    python scripts/verify/fase1_6b/clinic_short_verdict.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.short_events import MARKER_TRACKS, classify_short_event  # noqa: E402

WAVE_TRACKS = ("Intellivue/AWP_WAV", "Intellivue/FLOW_WAV")


def _marker_counts(event: dict) -> dict[str, int]:
    """Registros de cada marcador D6 sumando los ficheros del evento."""
    counts: Counter = Counter()
    for r in event.get("reads", []):
        for track, n in (r.get("tracks") or {}).items():
            if track in MARKER_TRACKS:
                counts[track] += int(n)
    return dict(counts)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--events", default=str(ROOT / "reports" / "fase1_6b"
                                          / "clinic_short_events_v2.json"))
    p.add_argument("--old", default=str(ROOT / "reports" / "fase1_5"
                                        / "clinic_short_events.json"))
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6b"))
    args = p.parse_args()

    data = json.loads(Path(args.events).read_text(encoding="utf-8"))
    old = {}
    if Path(args.old).exists():
        old = {r["event_id"]: r["classification"]
               for r in json.loads(Path(args.old).read_text(encoding="utf-8"))["events"]}

    out_events = []
    for e in data["events"]:
        markers = _marker_counts(e)
        has_wave = any(t in WAVE_TRACKS for t in e["vent_tracks_present"])
        # Sin onda no se puede medir amplitud/ciclos: se decide con ajustes.
        verdict = classify_short_event(np.array([]), np.array([]),
                                       marker_tracks=markers)
        plausible = has_wave or verdict["classification"].endswith("plausible")
        out_events.append({
            "event_id": e["event_id"], "box": e["box"],
            "duration_min": e["duration_min"],
            "n_files_found": e["n_files_found"],
            "vent_tracks_present": e["vent_tracks_present"],
            "marker_counts": markers,
            "has_wave": has_wave,
            "n_marker_tracks": verdict["metrics"]["n_marker_tracks"],
            "classification_fase1_6b": (
                "ventilacion_invasiva_plausible" if plausible else "artefacto"),
            "classification_fase1_5": old.get(e["event_id"]),
            "evidence": ("onda" if has_wave else
                         ("ajustes" if verdict["metrics"]["n_marker_tracks"] >= 2
                          else "ninguna")),
        })

    n_plausible = sum(1 for e in out_events if e["classification_fase1_6b"]
                      .endswith("plausible"))
    summary = {
        "n_events": len(out_events),
        "fase1_5_plausible": sum(1 for v in old.values() if v.endswith("plausible")),
        "fase1_6b_plausible": n_plausible,
        "fase1_6b_artifact": len(out_events) - n_plausible,
        "by_evidence": dict(Counter(e["evidence"] for e in out_events)),
        "events": out_events,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "clinic_short_verdict.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "events"},
                     ensure_ascii=False, indent=2))
    for e in out_events:
        print(f"  {e['event_id']:26} {e['duration_min']:6.2f} min  "
              f"{e['classification_fase1_6b']:30} evidencia={e['evidence']:8} "
              f"marcadores={e['n_marker_tracks']} "
              f"(antes: {e['classification_fase1_5']})")


if __name__ == "__main__":
    main()
