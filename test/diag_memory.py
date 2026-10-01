"""
Diagnóstico de memoria: mide RAM en cada paso del procesamiento de UN archivo.
Ejecutar con: venv\Scripts\python.exe diag_memory.py
"""
import os, gc, psutil, numpy as np, pandas as pd
import vitaldb
from pathlib import Path

PROCESS = psutil.Process(os.getpid())

def mb():
    return PROCESS.memory_info().rss / 1024**2

def step(label):
    gc.collect()
    print(f"  [{mb():7.1f} MB] {label}")

IN_DIR    = Path("datasets/mimic3wdb/mimic_full_cases")
CHART_PATH = Path("datasets/mimic3wdb/mimic_chartevents_vent.parquet")

print(f"[{mb():.1f} MB] Inicio")

# 1. Chart_df
step("Antes de cargar chart_df")
chart_df = pd.read_parquet(CHART_PATH)
chart_df["CHARTTIME"] = pd.to_datetime(chart_df["CHARTTIME"])
step(f"Tras cargar chart_df ({len(chart_df):,} filas)")
chart_groups = {sid: grp.reset_index(drop=True) for sid, grp in chart_df.groupby("SUBJECT_ID")}
del chart_df; gc.collect()
step("Tras agrupar y borrar chart_df")

# 2. Archivo más grande
files = sorted(IN_DIR.glob("*.vital"), key=lambda f: f.stat().st_size, reverse=True)
vital_path = files[0]
print(f"\nAnalizando: {vital_path.name} ({vital_path.stat().st_size/1024**2:.1f} MB en disco)")

# 3. VitalFile completo
step("Antes de VitalFile()")
vf = vitaldb.VitalFile(str(vital_path))
step("Tras VitalFile() -- RAM actual es el 'coste base'")

NEEDED = [
    "MIMIC/HR", "MIMIC/RESP", "MIMIC/SpO2",
    "MIMIC/ABP_S", "MIMIC/ABP_D", "MIMIC/ABP_M",
    "MIMIC/NBP", "MIMIC/NBP Sys", "MIMIC/NBP Dias", "MIMIC/NBP_M",
    "MIMIC/CVP", "MIMIC/PULSE", "MIMIC/Temp Rect",
    "MIMIC/PEEP", "MIMIC/PIP", "MIMIC/TV",
    "MIMIC/MV", "MIMIC/RR_V", "MIMIC/FiO2",
]
tracks = set(vf.get_track_names())
needed = [t for t in NEEDED if t in tracks]
print(f"  Tracks presentes: {len(tracks)} total, {len(needed)} necesarios")

# Verificar si vf.trks existe
if hasattr(vf, 'trks'):
    print(f"  vf.trks disponible. Keys: {list(vf.trks.keys())[:5]}...")
else:
    print("  ATENCION: vf.trks NO existe. Estructura interna diferente.")
    print(f"  Atributos de vf: {[a for a in dir(vf) if not a.startswith('_')]}")

# 4. Extraer directamente de vf.trks (nuevo metodo)
import re
from datetime import datetime
m = re.search(r"mimic_(\d+)_(\d{8})_(\d{6})_to_(\d{8})_(\d{6})\.vital", vital_path.name)
sid = int(m.group(1))
t_start = datetime.strptime(m.group(2)+m.group(3), "%Y%m%d%H%M%S")
t_end   = datetime.strptime(m.group(4)+m.group(5), "%Y%m%d%H%M%S")
start_ts, end_ts = t_start.timestamp(), t_end.timestamp()
duration_h = (end_ts - start_ts) / 3600
print(f"  Duracion: {duration_h:.1f}h = {duration_h/24:.1f} dias")

step("Antes de extraccion directa vf.trks (nuevo metodo)")
arrays = {}

def sparse_to_dense(dts, vals, start_ts, end_ts, interval=1.0):
    n = int(round((end_ts - start_ts) / interval)) + 1
    result = np.full(n, np.nan, dtype=np.float32)
    indices = np.round((dts - start_ts) / interval).astype(np.int64)
    valid = (indices >= 0) & (indices < n) & np.isfinite(vals)
    if not valid.any():
        return None
    result[indices[valid]] = vals[valid]
    nan_m = np.isnan(result)
    if nan_m.any() and not nan_m.all():
        idx = np.where(~nan_m, np.arange(n), 0)
        np.maximum.accumulate(idx, out=idx)
        result = result[idx]
    nan_m = np.isnan(result)
    if nan_m.any() and not nan_m.all():
        first = int(np.argmax(~nan_m))
        result[:first] = result[first]
    return result

for tname in needed:
    trk = vf.trks.get(tname) if hasattr(vf, 'trks') else None
    if trk is None or not hasattr(trk, 'recs') or not trk.recs:
        print(f"  SKIP {tname} (no disponible en trks)")
        continue

    recs = trk.recs
    n_rec = len(recs)
    dts  = np.empty(n_rec, dtype=np.float64)
    vals = np.empty(n_rec, dtype=np.float32)
    j = 0
    for r in recs:
        v = r.get('val', None)
        if v is not None and isinstance(v, (int, float)) and not isinstance(v, bool):
            dts[j]  = r['dt']
            vals[j] = float(v)
            j += 1

    # CLAVE: liberar records del VitalFile inmediatamente
    trk.recs = []
    gc.collect()

    if j > 0:
        arr = sparse_to_dense(dts[:j], vals[:j], start_ts, end_ts)
        if arr is not None:
            arrays[tname] = arr
    del dts, vals

    step(f"after {tname} ({n_rec} recs freed)")

step(f"Tras extraer {len(arrays)} tracks -- VitalFile casi vaciado")
del vf; gc.collect()
step("Tras del vf -- DEBERIA ser bajo ahora")

if arrays:
    total_mb = sum(a.nbytes for a in arrays.values()) / 1024**2
    n_pts = max(len(v) for v in arrays.values())
    print(f"\n  Arrays totales: {total_mb:.1f} MB para {n_pts:,} puntos ({n_pts/3600:.1f}h)")

print("\n--- FIN DIAGNOSTICO ---")
