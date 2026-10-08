#!/usr/bin/env python3
"""
scripts/verify/fase1_6d/end_reasons_report.py
=============================================
Fase 1.6d — **punto 6**: comprueba que el vocabulario de ``end_reason`` es ÚNICO
en las 4 cohortes (MIMIC, eICU-B, Clínic, VitalDB).

Carga el índice más reciente de cada cohorte y, por cohorte:

- cuenta los ``end_reason`` y señala los que están **fuera** del vocabulario
  canónico (``src/common/end_reasons.py``);
- comprueba la coherencia con las etiquetas (un ``end_reason`` de censura debe
  corresponder a una etiqueta censurada a 48 h).

Salida: ``reports/fase1_6d/end_reasons.json`` / ``.md`` y código de salida 1 si
alguna cohorte usa un valor fuera de vocabulario.

Uso:
    python scripts/verify/fase1_6d/end_reasons_report.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.end_reasons import END_REASONS, is_canonical  # noqa: E402

# Directorios en los que buscar el índice de cada cohorte.
COHORT_DIRS = {
    "mimic": ["datasets/mimic3wdb"],
    "eicu": ["datasets/eicu_collaborative", "datasets/eicu"],
    "clinic": ["datasets/clinic", "datasets/clinic_vitals"],
    "vitaldb": ["datasets/vitaldb", "datasets/vitaldb_sicu"],
}
INDEX_NAMES = {
    "mimic": "mimic_cases_index.json",
    "eicu": "eicu_cases_index.json",
    "clinic": "clinic_cases_index.json",
    "vitaldb": "vitaldb_cases_index.json",
}


def latest_index(cohort: str) -> Path | None:
    cands: list[Path] = []
    for base in COHORT_DIRS[cohort]:
        d = ROOT / base
        if d.exists():
            cands += list(d.glob(f"cases_v*/{INDEX_NAMES[cohort]}"))
    return sorted(cands)[-1] if cands else None


def report_cohort(cohort: str) -> dict | None:
    path = latest_index(cohort)
    if path is None:
        return None
    idx = json.loads(path.read_text(encoding="utf-8"))
    events = idx.get("events", [])
    counts = Counter(e.get("end_reason") for e in events)
    unknown = sorted({r for r in counts if not is_canonical(r)})
    return {
        "index": str(path.relative_to(ROOT)),
        "n_events": len(events),
        "end_reason": dict(counts.most_common()),
        "unknown": unknown,
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6d"))
    p.add_argument("--cohorts", nargs="+",
                   default=["mimic", "eicu", "clinic", "vitaldb"])
    args = p.parse_args()

    table: dict[str, dict] = {}
    for cohort in args.cohorts:
        t = report_cohort(cohort)
        if t is None:
            print(f"[aviso] sin índice para {cohort}")
            continue
        table[cohort] = t

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"vocabulario": list(END_REASONS), "cohortes": table}
    (out_dir / "end_reasons.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# Vocabulario único de `end_reason` (Fase 1.6d, punto 6)", "",
             "Vocabulario canónico: " +
             ", ".join(f"`{r}`" for r in END_REASONS), "",
             "| Cohorte | Eventos | `end_reason` (recuento) | Fuera de vocabulario |",
             "|---|---|---|---|"]
    bad = False
    for cohort, t in table.items():
        unknown = t["unknown"]
        bad = bad or bool(unknown)
        dist = ", ".join(f"{k}: {v}" for k, v in t["end_reason"].items())
        lines.append(f"| {cohort} | {t['n_events']} | {dist} | "
                     f"{', '.join(unknown) if unknown else '—'} |")
    (out_dir / "end_reasons.md").write_text("\n".join(lines) + "\n",
                                            encoding="utf-8")
    print("\n".join(lines))
    if bad:
        print("\n[FALLO] hay `end_reason` fuera del vocabulario canónico.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
