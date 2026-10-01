"""Test rápido: construcción de datasets para Escalón 2."""
import sys
sys.path.insert(0, ".")

from src.stage2.dataset import (
    build_datasets, _load_survival, _list_patient_files,
    _build_windows, build_sequences_from_patient,
)
import pandas as pd

# Test supervivencia
surv = _load_survival()
print("Survival cohorts:", surv["cohort"].value_counts().to_dict())

# Test eICU
files = _list_patient_files("eicu")
print(f"eICU files: {len(files)}")

# Test: find a patient with good data
found = 0
for f in files:
    pid = f.split("\\")[-1].replace(".parquet", "")
    eicu_surv = surv[surv["cohort"] == "eicu"]
    if pid not in eicu_surv["patient_id"].values:
        continue
    df = pd.read_parquet(f)
    # Skip if missing any mandatory column
    if not all(c in df.columns for c in ["RR","HR","SpO2","PEEP","PIP","MAP"]):
        continue
    ext = eicu_surv[eicu_surv["patient_id"] == pid]["extubation_time_hours"].values[0]
    windows = _build_windows(df)
    seqs = build_sequences_from_patient(df, ext, pid)
    print(f"  {pid}: {len(df)} rows, {len(windows)} windows, {len(seqs)} seqs, ext={ext:.1f}h")
    if seqs:
        s = seqs[0]
        print(f"    Sample: feat_shape={s['window_feats'].shape}, dt={s['delta_ts']}, target={s['time_remaining']:.1f}h")
    found += 1
    if found >= 5:
        break

# Test MIMIC
mimic_files = _list_patient_files("mimic")
print(f"\nMIMIC files: {len(mimic_files)}")
if mimic_files:
    df = pd.read_parquet(mimic_files[0])
    print(f"  Columns: {list(df.columns)}")
    pid = df["patient_id"].iloc[0]
    mimic_surv = surv[surv["cohort"] == "mimic"]
    if pid in mimic_surv["patient_id"].values:
        ext = mimic_surv[mimic_surv["patient_id"] == pid]["extubation_time_hours"].values[0]
        print(f"  {pid}: ext_time={ext:.1f}h, rows={len(df)}, time_range=[{df['time_rel_hours'].min():.1f}, {df['time_rel_hours'].max():.1f}]")
    else:
        print(f"  {pid}: NOT in survival")
