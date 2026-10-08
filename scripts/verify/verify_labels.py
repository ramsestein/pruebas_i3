"""
scripts/verify/verify_labels.py
================================
Fase 0 (e): etiquetas en las tablas de supervivencia actuales.

Genera:
  - distribución de `event_type` por cohorte (todas las versiones armonizadas),
  - filas `censored_no_extubation` con `extubation_time_hours` finito,
  - reintubaciones de eICU con gap entre 48 y 72 h (tabla extubation_attempts),
  - tiempos de extubación negativos,
  - verificación de HADM/ICUSTAY para MIMIC (origen del episodio).

Salida JSON: reports/fase0/labels.json
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from _common import write_json

HARMONIZED = PROJECT_ROOT / "datasets" / "harmonized"


def survival_tables(version_dir: Path) -> dict:
    out = {}
    for p in sorted(version_dir.glob("survival_*.parquet")):
        if "attempts" in p.name:
            continue
        out[p.stem] = pd.read_parquet(p)
    return out


def attempts_table(version_dir: Path):
    p = version_dir / "extubation_attempts.parquet"
    return pd.read_parquet(p) if p.exists() else None


def main():
    report = {"versions": {}, "summary": {}}
    all_versions = sorted(d for d in HARMONIZED.iterdir() if d.is_dir())

    # agregado: distribuciones por cohorte (usando las versiones más pobladas)
    for vdir in all_versions:
        sv = survival_tables(vdir)
        if not sv:
            continue
        key = vdir.name
        report["versions"][key] = {}
        for stem, df in sv.items():
            crosstab = {
                str(cohort): {str(et): int(cnt) for et, cnt in sub.items()}
                for cohort, sub in pd.crosstab(df["cohort"], df["event_type"], dropna=False).to_dict().items()
            } if not df.empty else {}
            failed_dist = {
                int(k): int(v)
                for k, v in df["n_failed_attempts"].value_counts().sort_index().to_dict().items()
            } if "n_failed_attempts" in df.columns else {}
            report["versions"][key][stem] = {
                "n_rows": int(len(df)),
                "event_type_by_cohort": crosstab,
                "censored_with_finite_extub": int(
                    ((df["event_type"] == "censored_no_extubation") & df["extubation_time_hours"].notna()).sum()
                ),
                "negative_extubation_time": int((df["extubation_time_hours"] < 0).sum()),
                "negative_t0_unix": int((df["t0_unix"] < 0).sum()) if "t0_unix" in df.columns else None,
                "n_failed_attempts_distribution": failed_dist,
            }
        # reintubaciones eICU (solo si la versión tiene eicu)
        att = attempts_table(vdir)
        if att is not None and not att.empty and "cohort" in att.columns and (att["cohort"] == "eicu").any():
            eicu_att = att[att["cohort"] == "eicu"]
            ttr = eicu_att["time_to_reintubation_hours"].dropna()
            report["versions"][key]["eicu_reintubation_gaps"] = {
                "n_attempts": int(len(eicu_att)),
                "n_failures": int((eicu_att["outcome"] == "failure").sum()),
                "gap_48_72h": int(((ttr > 48) & (ttr <= 72)).sum()),
                "gap_min_h": round(float(ttr.min()), 3) if len(ttr) else None,
                "gap_max_h": round(float(ttr.max()), 3) if len(ttr) else None,
                "gap_median_h": round(float(ttr.median()), 3) if len(ttr) else None,
            }

    # Distribución consolidada por cohorte (sin duplicar pacientes entre versiones):
    # por cohorte se toma la versión con más filas de esa cohorte.
    from collections import defaultdict
    best_per_cohort = {}
    for vdir in all_versions:
        sv = survival_tables(vdir)
        for stem, df in sv.items():
            if df.empty or "cohort" not in df.columns or "event_type" not in df.columns:
                continue
            for cohort, sub in df.groupby("cohort", dropna=False):
                n = int(len(sub))
                if cohort not in best_per_cohort or n > best_per_cohort[cohort][0]:
                    counts = {str(et): int(c) for et, c in sub["event_type"].value_counts(dropna=False).items()}
                    best_per_cohort[cohort] = (n, counts, f"{vdir.name}/{stem}")
    report["summary"]["event_type_by_cohort_consolidated"] = {
        str(cohort): {"n_rows": n, "event_type_counts": counts, "source": src}
        for cohort, (n, counts, src) in best_per_cohort.items()
    }

    # MIMIC HADM
    report["summary"]["mimic_hadm_check"] = {
        "clinical_dir_exists": (PROJECT_ROOT / "datasets/mimic3wdb/clinical").exists(),
        "note": "No hay tablas ICUSTAYS/ADMISSIONS ni HADM_ID en los datos; "
                "no se puede comprobar si un episodio procede de otro HADM.",
    }

    write_json("labels.json", report)
    print(report["summary"])


if __name__ == "__main__":
    main()
