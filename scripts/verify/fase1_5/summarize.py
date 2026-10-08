#!/usr/bin/env python3
"""
scripts/verify/fase1_5/summarize.py
===================================
Recopila las cifras de la Fase 1.5 desde los índices de las cuatro cohortes y
las imprime en JSON (para redactar ``reports/fase1_5/resumen.md``).

Uso:
    python scripts/verify/fase1_5/summarize.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"


def _latest(pattern: str) -> Path | None:
    hits = sorted(ROOT.glob(pattern))
    return hits[-1] if hits else None


def _index_stats(idx_path: Path | None) -> dict:
    if idx_path is None or not idx_path.exists():
        return {"available": False}
    with open(idx_path, encoding="utf-8") as fh:
        idx = json.load(fh)
    events = idx.get("events", [])
    levels: Counter = Counter(e.get("level") for e in events if e.get("level"))
    ok50 = sum(1 for e in events if e.get("vars_ok_50"))
    ok80 = sum(1 for e in events if e.get("vars_ok_80"))
    win = Counter()
    causes = Counter()
    failures = 0
    for e in events:
        lab = (e.get("labels") or {}).get("48h") or {}
        win[lab.get("event_type")] += 1
        if lab.get("censor_cause"):
            causes[lab["censor_cause"]] += 1
        if lab.get("n_failed_attempts", 0) > 0:
            failures += 1
    return {
        "available": True,
        "path": str(idx_path),
        "total_events": len(events),
        "total_excluded": idx.get("total_excluded_events"),
        "levels": dict(levels),
        "vars_ok_50": ok50,
        "vars_ok_80": ok80,
        "window_48h": dict(win),
        "causes_48h": dict(causes),
        "events_with_failure": failures,
        "n_missing_files": idx.get("n_missing_files"),
    }


def main() -> None:
    config = load_config(CONFIG)
    out: dict[str, dict] = {}

    out["clinic"] = _index_stats(_latest("datasets/clinic/cases_*/*cases_index.json"))
    out["vitaldb"] = _index_stats(_latest("datasets/vitaldb/cases_*/*cases_index.json"))
    out["mimic"] = _index_stats(_latest("datasets/mimic3wdb/cases_*/*cases_index.json"))

    eicu_sum = _latest("datasets/eicu_collaborative/cases_*/eicu_levels_summary.json")
    if eicu_sum and eicu_sum.exists():
        with open(eicu_sum, encoding="utf-8") as fh:
            out["eicu"] = {"available": True, "path": str(eicu_sum), **json.load(fh)}
    else:
        out["eicu"] = {"available": False}

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
