#!/usr/bin/env python3
"""
scripts/verify/fase1_6a/vent_label_inventory.py
===============================================
Fase 1.6a — Inventario de etiquetas relacionadas con la vía aérea o la
ventilación en las tablas de eICU, con nº de estancias y nº de hospitales, y su
clasificación (invasiva / VNI-alto flujo / oxigenoterapia / ambigua).

Salida: reports/fase1_6a/eicu_vent_labels.csv
Uso:    python scripts/verify/fase1_6a/vent_label_inventory.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.eicu_vent import (  # noqa: E402
    classify_airway,
    classify_careplan,
    classify_respchart_label,
    classify_treatment,
)
from src.common.paths import config_path  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"

RESP_RX = re.compile(r"vent|peep|fio2|fio_2|tidal|\bpip\b|peak|airway|\bett\b|intubat|trach|"
                     r"o2|oxygen|cpap|bipap|\bniv\b|minute|\bmv\b|\btv\b|\bvt\b|"
                     r"plateau|compliance|resistance|resp|mode|device", re.I)
NURSE_RX = re.compile(r"vent|airway|\bett\b|intubat|trach|oxygen|\bo2\b|cpap|bipap|"
                      r"respiratory rate|breath", re.I)


def _chunked_labels(eicu_dir: Path, fname: str, label_col: str, pid_col: str,
                    rx: re.Pattern, stay2hosp: dict, chunksize: int = 3_000_000) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for ch in pd.read_csv(eicu_dir / fname, usecols=[pid_col, label_col],
                          chunksize=chunksize, low_memory=False):
        ch = ch[ch[label_col].notna()]
        if ch.empty:
            continue
        ch = ch[ch[label_col].astype(str).str.contains(rx, na=False)]
        if ch.empty:
            continue
        sub = ch[[pid_col, label_col]].drop_duplicates().rename(
            columns={label_col: "value", pid_col: "patientunitstayid"})
        parts.append(sub)
    if not parts:
        return pd.DataFrame(columns=["value", "n_stays", "n_hospitals"])
    df = pd.concat(parts, ignore_index=True).drop_duplicates()
    df["hospitalid"] = df["patientunitstayid"].map(stay2hosp)
    g = df.groupby("value", sort=False)
    return pd.DataFrame({
        "n_stays": g["patientunitstayid"].nunique(),
        "n_hospitals": g["hospitalid"].nunique(),
    }).reset_index()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    args = p.parse_args()
    config = load_config(args.config)
    eicu_dir = config_path(config, "paths", "eicu_dir")
    out_dir = ROOT / "reports" / "fase1_6a"
    out_dir.mkdir(parents=True, exist_ok=True)

    pat = pd.read_csv(eicu_dir / "patient.csv.gz",
                      usecols=["patientunitstayid", "hospitalid"])
    stay2hosp = dict(zip(pat.patientunitstayid, pat.hospitalid))

    records: list[dict] = []

    def _add(source: str, value: object, sub: pd.DataFrame, category: str):
        records.append({
            "source": source, "value": value,
            "n_stays": int(sub["patientunitstayid"].nunique()),
            "n_hospitals": len({stay2hosp.get(p) for p in sub["patientunitstayid"]}),
            "category": category,
        })

    agg = _chunked_labels(eicu_dir, "respiratoryCharting.csv.gz",
                          "respchartvaluelabel", "patientunitstayid", RESP_RX, stay2hosp)
    for _, r in agg.iterrows():
        records.append({"source": "respiratoryCharting.respchartvaluelabel",
                        "value": r["value"], "n_stays": int(r["n_stays"]),
                        "n_hospitals": int(r["n_hospitals"]),
                        "category": classify_respchart_label(r["value"])})
    print(f"[labels] respiratoryCharting: {len(agg)} etiquetas", flush=True)

    agg = _chunked_labels(eicu_dir, "nurseCharting.csv.gz",
                          "nursingchartcelltypevallabel", "patientunitstayid",
                          NURSE_RX, stay2hosp)
    for _, r in agg.iterrows():
        records.append({"source": "nurseCharting.nursingchartcelltypevallabel",
                        "value": r["value"], "n_stays": int(r["n_stays"]),
                        "n_hospitals": int(r["n_hospitals"]),
                        "category": classify_respchart_label(r["value"])})
    print(f"[labels] nurseCharting: {len(agg)} etiquetas", flush=True)

    rc = pd.read_csv(eicu_dir / "respiratoryCare.csv.gz",
                     usecols=["patientunitstayid", "airwaytype"], low_memory=False)
    for val, g in rc.dropna(subset=["airwaytype"]).groupby("airwaytype"):
        _add("respiratoryCare.airwayType", val, g, classify_airway(val))

    cg = pd.read_csv(eicu_dir / "carePlanGeneral.csv.gz",
                     usecols=["patientunitstayid", "cplgroup", "cplitemvalue"], low_memory=False)
    for (grp, v), sub in cg[cg.cplgroup.isin(["Ventilation", "Airway"])].groupby(
            ["cplgroup", "cplitemvalue"]):
        _add(f"carePlanGeneral.{grp}", v, sub, classify_careplan(grp, v))

    tx = pd.read_csv(eicu_dir / "treatment.csv.gz",
                     usecols=["patientunitstayid", "treatmentstring"], low_memory=False)
    m = tx.treatmentstring.str.contains(
        "ventilat|intubat|airway|trach|extubat|mechanical|cpap|oxygen", case=False, na=False)
    for v, sub in tx[m].groupby("treatmentstring"):
        _add("treatment.treatmentstring", v, sub, classify_treatment(v))

    ap = pd.read_csv(eicu_dir / "apachePredVar.csv.gz",
                     usecols=["patientunitstayid", "oobventday1", "oobintubday1"], low_memory=False)
    aa = pd.read_csv(eicu_dir / "apacheApsVar.csv.gz",
                     usecols=["patientunitstayid", "vent", "intubated"], low_memory=False)
    for tbl, df, col in (("apachePredVar", ap, "oobventday1"),
                         ("apachePredVar", ap, "oobintubday1"),
                         ("apacheApsVar", aa, "vent"),
                         ("apacheApsVar", aa, "intubated")):
        _add(f"{tbl}.{col}", 1, df[df[col] == 1], "invasiva")

    out = pd.DataFrame(records).sort_values(
        ["category", "source", "n_stays"], ascending=[True, True, False])
    out.to_csv(out_dir / "eicu_vent_labels.csv", index=False)
    print(f"[labels] {len(out)} filas -> {out_dir / 'eicu_vent_labels.csv'}")
    print(out.groupby("category").size().to_string())


if __name__ == "__main__":
    main()
