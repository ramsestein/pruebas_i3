#!/usr/bin/env python3
"""
scripts/verify/fase1_6a/verify_claims.py
========================================
Fase 1.6a — Verificación LOCAL de las afirmaciones de la auditoría documental
(sección 1 de ``reports/fase1_6a/eicu_auditoria.md``).

Comprueba:
  - % de ``ventendoffset`` = 0 o nulo (las fuentes dicen ~99,98 %);
  - si ``ventstartoffset`` marca intubación o solo oxigenoterapia (cruce con
    ``airwayType`` Oral/Nasal ETT, con APACHE ``oobVentDay1``/``oobIntubDay1`` y
    con el primer ajuste invasivo de ``respiratoryCharting``);
  - diferencia entre ``respChartOffset`` (observación) y
    ``respChartEntryOffset`` (entrada).

Salida: reports/fase1_6a/eicu_claims.json
Uso:    python scripts/verify/fase1_6a/verify_claims.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.eicu_vent import CAT_INVASIVE, classify_respchart_label  # noqa: E402
from src.common.paths import config_path  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    args = p.parse_args()
    config = load_config(args.config)
    eicu_dir = config_path(config, "paths", "eicu_dir")
    out_dir = ROOT / "reports" / "fase1_6a"
    out_dir.mkdir(parents=True, exist_ok=True)

    claims: dict = {}

    rc = pd.read_csv(eicu_dir / "respiratoryCare.csv.gz",
                     usecols=["patientunitstayid", "ventstartoffset", "ventendoffset",
                              "airwaytype"], low_memory=False)
    ve = pd.to_numeric(rc.ventendoffset, errors="coerce")
    vs = pd.to_numeric(rc.ventstartoffset, errors="coerce")
    claims["ventendoffset"] = {
        "n_rows": int(len(rc)),
        "n_zero_or_null": int((ve.isna() | (ve == 0)).sum()),
        "pct_zero_or_null": round(100.0 * (ve.isna() | (ve == 0)).mean(), 4),
        "n_stays_with_ventend_gt0": int(rc.loc[ve > 0, "patientunitstayid"].nunique()),
        "source": "eICU-CRD respiratoryCare; issue #82 (865224/865381)",
    }
    claims["ventstartoffset"] = {
        "n_rows_gt0": int((vs > 0).sum()),
        "n_stays_gt0": int(rc.loc[vs > 0, "patientunitstayid"].nunique()),
        "n_rows_lt0": int((vs < 0).sum()),
    }

    # Airway ETT entre las estancias con ventstartoffset>0
    ett_stays = set(rc.loc[rc.airwaytype.isin(["Oral ETT", "Nasal ETT"]), "patientunitstayid"])
    vs_stays = set(rc.loc[vs > 0, "patientunitstayid"])
    claims["ventstartoffset"]["pct_with_airway_ett"] = round(
        100.0 * len(vs_stays & ett_stays) / max(len(vs_stays), 1), 2)

    ap = pd.read_csv(eicu_dir / "apachePredVar.csv.gz",
                     usecols=["patientunitstayid", "oobventday1", "oobintubday1"], low_memory=False)
    apache_vent = set(ap.loc[ap.oobventday1 == 1, "patientunitstayid"])
    apache_intub = set(ap.loc[ap.oobintubday1 == 1, "patientunitstayid"])
    claims["ventstartoffset"]["pct_with_apache_vent"] = round(
        100.0 * len(vs_stays & apache_vent) / max(len(vs_stays), 1), 2)
    claims["ventstartoffset"]["pct_with_apache_intub"] = round(
        100.0 * len(vs_stays & apache_intub) / max(len(vs_stays), 1), 2)
    claims["apache_disagreement"] = {
        "n_vent_not_intub": int(len(apache_vent - apache_intub)),
        "n_intub_not_vent": int(len(apache_intub - apache_vent)),
        "note": "oobVentDay1 sin oobIntubDay1 = ventilado no invasivo (docs eICU)",
    }

    # respChartOffset vs respChartEntryOffset
    diffs = []
    first_invasive: dict[int, float] = {}
    for ch in pd.read_csv(eicu_dir / "respiratoryCharting.csv.gz",
                          usecols=["patientunitstayid", "respchartoffset",
                                   "respchartentryoffset", "respchartvaluelabel"],
                          chunksize=3_000_000, low_memory=False):
        off = pd.to_numeric(ch["respchartoffset"], errors="coerce")
        ent = pd.to_numeric(ch["respchartentryoffset"], errors="coerce")
        d = (ent - off).dropna()
        if len(d):
            diffs.append(d.to_numpy())
        inv = ch["respchartvaluelabel"].map(
            lambda x: classify_respchart_label(x) == CAT_INVASIVE)
        sub = ch.loc[inv, ["patientunitstayid", "respchartoffset"]].dropna()
        for pid, o in zip(sub["patientunitstayid"], sub["respchartoffset"]):
            pid = int(pid)
            o = float(o)
            if pid not in first_invasive or o < first_invasive[pid]:
                first_invasive[pid] = o
    if diffs:
        all_d = np.concatenate(diffs)
        claims["respchart_entry_minus_offset"] = {
            "n": int(all_d.size),
            "pct_equal": round(100.0 * (all_d == 0).mean(), 2),
            "median_min": float(np.median(all_d)),
            "p90_min": float(np.percentile(all_d, 90)),
        }

    # ventstartoffset vs primer ajuste invasivo de respiratoryCharting
    mapping = dict(zip(rc.patientunitstayid, vs))
    within2h = 0
    n_cmp = 0
    for pid, adj in first_invasive.items():
        v = mapping.get(pid)
        if v is None or not np.isfinite(v) or v <= 0:
            continue
        n_cmp += 1
        if abs(adj - float(v)) <= 120:
            within2h += 1
    claims["ventstart_vs_first_invasive"] = {
        "n_compared": n_cmp,
        "pct_within_2h": round(100.0 * within2h / max(n_cmp, 1), 2),
        "n_stays_with_invasive": len(first_invasive),
    }

    (out_dir / "eicu_claims.json").write_text(
        json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(claims, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
