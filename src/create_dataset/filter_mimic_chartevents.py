"""
Filtra CHARTEVENTS.csv.gz de MIMIC-III para quedarse solo con los parametros
ventilatorios de los pacientes que tenemos en mimic_full_cases.

Salida: datasets/mimic3wdb/mimic_chartevents_vent.parquet
"""
import gzip
import pandas as pd
from pathlib import Path
import re
import glob

CHART_PATH = Path("mimiciii/CHARTEVENTS.csv.gz")
OUT_PATH   = Path("datasets/mimic3wdb/mimic_chartevents_vent.parquet")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

# itemids de parametros ventilatorios
VENT_ITEMIDS = {
    3420, 223835,   # FiO2
    505,  224700,   # PEEP
    681,  224685,   # TV
    507,  224696,   # PIP
    618,  220210,   # RR_V
    682,  224687,   # MV
}

# Extraer subject_ids de los archivos .vital existentes
vital_files = glob.glob("datasets/mimic3wdb/mimic_full_cases/*.vital")
subject_ids = set()
for f in vital_files:
    m = re.search(r"mimic_(\d+)_\d{8}_\d{6}_to_", f)
    if m:
        subject_ids.add(int(m.group(1)))

print(f"Pacientes en mimic_full_cases: {len(subject_ids)}")
print(f"Itemids ventilatorios: {len(VENT_ITEMIDS)}")

# Leer CHARTEVENTS en chunks y filtrar
chunks = []
chunk_size = 500_000

print(f"Leyendo {CHART_PATH} (esto puede tardar varios minutos)...")

total_rows = 0
for i, chunk in enumerate(pd.read_csv(
    CHART_PATH,
    compression="gzip",
    chunksize=chunk_size,
    low_memory=False,
    parse_dates=["CHARTTIME"],
    dtype={"SUBJECT_ID": int, "ITEMID": int, "VALUENUM": float},
    usecols=["SUBJECT_ID", "ITEMID", "CHARTTIME", "VALUENUM"],
), start=1):
    filt = chunk[chunk["SUBJECT_ID"].isin(subject_ids) & chunk["ITEMID"].isin(VENT_ITEMIDS)]
    if not filt.empty:
        chunks.append(filt)
        print(f"  chunk {i}: +{len(filt)} filas")
    total_rows += len(chunk)
    if i % 10 == 0:
        print(f"  ...procesados {total_rows:,} filas totales")

if chunks:
    df = pd.concat(chunks, ignore_index=True)
    df = df.sort_values(["SUBJECT_ID", "ITEMID", "CHARTTIME"]).reset_index(drop=True)
    df.to_parquet(OUT_PATH, index=False)
    print(f"\nGuardado: {OUT_PATH}")
    print(f"  Filas: {len(df):,}")
    print(f"  Subject IDs: {df['SUBJECT_ID'].nunique()}")
    print(f"  Item IDs: {df['ITEMID'].nunique()}")
    print(f"\nDesglose por itemid:")
    print(df.groupby("ITEMID").size())
else:
    print("No se encontraron datos matching.")
