"""
Enriquece los .vital de mimic_full_cases con:
1. Parametros ventilatorios desde chartevents (forward-fill en el periodo de VM)
2. Derivadas de señales numéricas (1 Hz) en BATCHES de 24h
3. Indices ventilatorios y tendencias
"""
import os
import re
import glob
import numpy as np
import pandas as pd
import vitaldb
import gc
from scipy.signal import savgol_filter
from pathlib import Path
from datetime import datetime, timedelta
import math

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.common.timeutils import to_epoch_utc  # noqa: E402

# ── Paths ─────────────────────────────────────────────────────────────────────
IN_DIR      = Path("datasets/mimic3wdb/mimic_full_cases")
OUT_DIR     = Path("datasets/mimic3wdb/mimic_full_cases_enriched")
CHART_PATH  = Path("datasets/mimic3wdb/mimic_chartevents_vent.parquet")

OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Configuracion Batching ────────────────────────────────────────────────────
CHUNK_HOURS = 6
OVERLAP_MINUTES = 15

# ── Mapeo itemid -> nombre canonico ─────────────────────────────────────────
ITEMID_MAP = {
    3420:   "FiO2",      # CareVue
    223835: "FiO2",      # Metavision
    505:    "PEEP",      # CareVue
    224700: "PEEP",      # Metavision
    681:    "TV",        # CareVue
    224685: "TV",        # Metavision
    507:    "PIP",       # CareVue
    224696: "PIP",       # Metavision
    618:    "RR_V",      # CareVue
    220210: "RR_V",      # Metavision
    682:    "MV",        # CareVue
    224687: "MV",        # Metavision
}

# ── Señales numéricas de MIMIC sobre las que calcular derivadas ───────────────
MIMIC_NUMERIC_TRACKS = [
    "MIMIC/HR", "MIMIC/RESP", "MIMIC/SpO2",
    "MIMIC/ABP_S", "MIMIC/ABP_D", "MIMIC/ABP_M",
    "MIMIC/NBP", "MIMIC/NBP Sys", "MIMIC/NBP Dias", "MIMIC/NBP_M",
    "MIMIC/CVP", "MIMIC/PULSE", "MIMIC/Temp Rect",
]

# Señales ventilatorias que añadimos (1 Hz, numeric recs)
MIMIC_VENT_TRACKS = [
    "MIMIC/PEEP", "MIMIC/PIP", "MIMIC/TV",
    "MIMIC/MV", "MIMIC/RR_V", "MIMIC/FiO2",
]

SAVGOL_WIN_1HZ = 11  # 11 muestras = 11 s, polyorder 3

# ── Helpers ───────────────────────────────────────────────────────────────────

def fill_nan(arr):
    s = pd.Series(arr.astype(np.float32))
    return s.interpolate(method="linear").ffill().bfill().values.astype(np.float32)

def numeric_recs(times, values):
    """Arrays -> vitaldb numeric recs, descarta NaN."""
    out = []
    for t, v in zip(times, values):
        if np.isfinite(v) and np.isfinite(t):
            out.append({"dt": float(t), "val": float(v)})
    return out

def wav_recs(start_dt, values, srate=1.0, chunk=60):
    """Array continuo -> vitaldb waveform recs."""
    arr = np.asarray(values, dtype=np.float32)
    recs = []
    for i in range(0, len(arr), chunk):
        recs.append({"dt": float(start_dt + i / srate), "val": arr[i : i + chunk]})
    return recs

def parse_vital_filename(fname):
    """mimic_{sid}_{YYYYMMDD}_{HHMMSS}_to_{YYYYMMDD}_{HHMMSS}.vital
    -> sid, start_dt, end_dt"""
    m = re.search(r"mimic_(\d+)_(\d{8})_(\d{6})_to_(\d{8})_(\d{6})\.vital", fname)
    if not m:
        return None, None, None
    sid = int(m.group(1))
    start_dt = datetime.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M%S")
    end_dt   = datetime.strptime(m.group(4) + m.group(5), "%Y%m%d%H%M%S")
    return sid, start_dt, end_dt

def get_chart_recs_for_patient(chart_df, sid, t_start, t_end):
    """
    Obtiene recs numericos de chartevents para un paciente en un rango temporal.
    Usa change-point compression: solo guarda un rec cuando el valor cambia.
    """
    pat = chart_df[chart_df["SUBJECT_ID"] == sid].copy()
    if pat.empty:
        return {}

    pat["param"] = pat["ITEMID"].map(ITEMID_MAP)
    pat = pat.dropna(subset=["param", "VALUENUM"])
    if pat.empty:
        return {}

    result = {}
    for param, group in pat.groupby("param"):
        group = group.sort_values("CHARTTIME")
        
        # Remover duplicados para evitar conflictos al reindexar
        group = group.drop_duplicates(subset=["CHARTTIME"], keep="last")
        
        times = group["CHARTTIME"]
        vals = group["VALUENUM"]
        
        if times.empty:
            continue
            
        # Crear serie sparse original
        s = pd.Series(index=times, data=vals.values)
        
        # Añadir limites de la ventana
        s_bound = pd.Series(index=[t_start, t_end], data=[np.nan, np.nan])
        s_combined = pd.concat([s, s_bound]).sort_index()
        
        # Propagar valores
        s_combined = s_combined.ffill().bfill()
        
        # Recortar estrictamente al rango de interés
        s_combined = s_combined.loc[t_start:t_end]
        
        if s_combined.empty:
            continue

        # Change-point compression
        diff = s_combined.diff().abs()
        change_mask = diff > 1e-6
        change_mask.iloc[0] = True  # siempre incluir el primero
        change_mask.iloc[-1] = True  # siempre incluir el ultimo

        s_compressed = s_combined[change_mask]
        
        # Convertir timestamps usando vectorización rápida de Numpy
        ts_epoch = s_compressed.index.astype(np.int64) // 10**9
        vals_np = s_compressed.values.astype(np.float32)

        recs = numeric_recs(ts_epoch, vals_np)
        if recs:
            result[f"MIMIC/{param}"] = recs
            
    return result

# ── Extracción directa desde vf.trks (sin to_pandas) ────────────────────

def sparse_to_dense(dts, vals, start_ts, end_ts, interval=1.0):
    """
    Convierte arrays esparsos (timestamps + valores) a array denso
    a 1Hz usando SOLO numpy, sin crear ningún DataFrame intermedio.
    Forward-fill + back-fill con truco de índice acumulado.
    """
    n = int(round((end_ts - start_ts) / interval)) + 1
    result = np.full(n, np.nan, dtype=np.float32)

    # Proyectar timestamps esparsos al índice del array denso
    indices = np.round((dts - start_ts) / interval).astype(np.int64)
    valid = (indices >= 0) & (indices < n) & np.isfinite(vals)
    if not valid.any():
        return None

    result[indices[valid]] = vals[valid]

    # Forward-fill: truco de índice acumulado (O(n), sin bucles Python)
    nan_m = np.isnan(result)
    if nan_m.any() and not nan_m.all():
        idx = np.where(~nan_m, np.arange(n), 0)
        np.maximum.accumulate(idx, out=idx)
        result = result[idx]

    # Back-fill: rellena NaN iniciales con el primer valor válido
    nan_m = np.isnan(result)
    if nan_m.any() and not nan_m.all():
        first = int(np.argmax(~nan_m))
        result[:first] = result[first]

    return result


def extract_tracks_as_arrays(vf, tracks, start_ts, end_ts, interval=1.0):
    """
    Lee directamente vf.trks para cada track necesario SIN llamar a
    to_pandas() (que crea DataFrames intermedios enormes).
    IMPORTANTE: borra vf.trks[tname].recs conforme avanza para liberar
    la memoria del VitalFile progresivamente.
    """
    n_pts = int(round((end_ts - start_ts) / interval)) + 1
    dt_index = np.arange(start_ts, start_ts + n_pts * interval, interval,
                          dtype=np.float64)
    arrays = {}

    for tname in tracks:
        trk = vf.trks.get(tname) if hasattr(vf, 'trks') else None
        if trk is None or not hasattr(trk, 'recs') or not trk.recs:
            continue

        recs = trk.recs
        n_rec = len(recs)

        # Pre-alocar arrays numpy (evita listas Python y su overhead)
        dts  = np.empty(n_rec, dtype=np.float64)
        vals = np.empty(n_rec, dtype=np.float32)
        j = 0

        for r in recs:
            v = r.get('val', None)
            # Para tracks numéricos val es float; ignorar chunks de waveform
            if v is not None and isinstance(v, (int, float)) and not isinstance(v, bool):
                dts[j]  = r['dt']
                vals[j] = float(v)
                j += 1

        # NOTA: No liberar trk.recs aquí porque necesitamos que se guarden en el 
        # archivo final al hacer vf.save_vital()
        # trk.recs = []
        # gc.collect()

        if j == 0:
            continue

        dts  = dts[:j]
        vals = vals[:j]

        arr = sparse_to_dense(dts, vals, start_ts, end_ts, interval)
        del dts, vals

        if arr is not None:
            arrays[tname] = arr

    return dt_index, arrays

# ── Computos a Nivel Batch ────────────────────────────────────────────────────

def compute_batch(arr_dict, tnames_present):
    """
    Recibe dict {tname: np.array_float32} (un batch ya recortado)
    y devuelve {track_name: np.array}.
    """
    derived = {}

    # 1. Numéricas: derivadas, filtro, residual, auc_cum
    load_num = [t for t in MIMIC_NUMERIC_TRACKS + MIMIC_VENT_TRACKS if t in tnames_present]
    for tname in load_num:
        short = tname.split("/")[-1]
        vals = arr_dict[tname]  # ya float32, no copia
        if np.sum(np.isfinite(vals)) < 15:
            continue

        filled = fill_nan(vals)
        d1 = np.gradient(filled, 1.0).astype(np.float32)
        d2 = np.gradient(d1, 1.0).astype(np.float32)

        try:
            filt = savgol_filter(filled, window_length=SAVGOL_WIN_1HZ, polyorder=3).astype(np.float32)
            residual = (filled - filt).astype(np.float32)
        except Exception:
            filt = filled.copy()
            residual = np.zeros_like(filled)

        auc_cum = (np.cumsum(np.abs(filled)) / np.arange(1, len(filled) + 1)).astype(np.float32)

        derived[f"Derived/{short}_d1"] = d1
        derived[f"Derived/{short}_d2"] = d2
        derived[f"Derived/{short}_filtered"] = filt
        derived[f"Derived/{short}_residual"] = residual
        derived[f"Derived/{short}_auc_cum"] = auc_cum

    # 2. Índices ventilatorios (trabajamos directo con arrays, sin DataFrame)
    def col(name):
        key = f"MIMIC/{name}"
        return arr_dict[key] if key in tnames_present else None

    tv   = col("TV")
    pip  = col("PIP")
    peep = col("PEEP")
    rr   = col("RR_V")
    mv   = col("MV")

    if pip is not None and peep is not None:
        derived["Derived/driving_pressure"] = (pip - peep).astype(np.float32)

    if tv is not None and pip is not None and peep is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            derived["Derived/compliance_dyn"] = np.where(
                (pip - peep) > 0, tv / (pip - peep), np.nan).astype(np.float32)

    if rr is not None and tv is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            derived["Derived/rsbi"] = np.where(
                tv > 0, rr / (tv / 1000.0), np.nan).astype(np.float32)

    if rr is not None and tv is not None and pip is not None and peep is not None:
        tv_l = tv / 1000.0
        dp   = pip - peep
        with np.errstate(divide="ignore", invalid="ignore"):
            mp = (0.098 * rr * tv_l * (pip - dp / 2.0)).astype(np.float32)
        derived["Derived/mechanical_power"] = np.where(np.isfinite(mp), mp, np.nan).astype(np.float32)

    if pip is not None and peep is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            derived["Derived/peep_pip_ratio"] = np.where(
                pip > 0, peep / pip, np.nan).astype(np.float32)

    for arr_s, name in [(pip, "Derived/delta_pip"), (peep, "Derived/delta_peep"), (tv, "Derived/delta_tv")]:
        if arr_s is not None:
            derived[name] = np.diff(arr_s, prepend=arr_s[0]).astype(np.float32)

    # Tendencias rolling usando numpy (sin DataFrame, evita copias internas de pandas)
    roll_map = {
        "MIMIC/TV":   "Derived/trend_tv",
        "MIMIC/MV":   "Derived/trend_mv",
        "MIMIC/PIP":  "Derived/trend_pip",
        "MIMIC/PEEP": "Derived/trend_peep",
        "MIMIC/RR_V": "Derived/trend_rr",
        "MIMIC/FiO2": "Derived/trend_fio2",
    }
    win = 60
    for src, dst in roll_map.items():
        if src in tnames_present:
            a = arr_dict[src]
            # Mediana rolling eficiente con pandas Series (solo esta columna)
            trend = pd.Series(a).rolling(win, min_periods=1, center=True).median().values.astype(np.float32)
            derived[dst] = trend

    return derived

# ── Enriquecer un archivo por batches ─────────────────────────────────────────

def enrich_one(vital_path, out_path, chart_groups):
    """
    chart_groups: dict { SUBJECT_ID -> DataFrame } pre-agrupado en main().
    """
    fname = os.path.basename(vital_path)
    print(f"  {fname} ...")

    sid, t_start, t_end = parse_vital_filename(fname)
    if sid is None:
        print(f"    SKIP: no se pudo parsear nombre")
        return 0

    vf = vitaldb.VitalFile(str(vital_path))
    tracks = set(vf.get_track_names())
    added = 0

    # 1) Chartevents como tracks numéricos
    print(f"    Chartevents (sid={sid}) ...")
    pat_chart = chart_groups.get(sid, pd.DataFrame())
    chart_recs = get_chart_recs_for_patient(pat_chart, sid, t_start, t_end)
    del pat_chart
    if chart_recs:
        for tname, recs in chart_recs.items():
            if recs:
                vf.add_track(tname, recs, srate=0)
                added += 1
                print(f"      + {tname} ({len(recs)} recs)")
    else:
        print(f"      (sin chartevents)")
    del chart_recs

    # Actualizar tracks disponibles tras añadir chartevents
    tracks = set(vf.get_track_names())
    needed_tracks = [t for t in MIMIC_NUMERIC_TRACKS + MIMIC_VENT_TRACKS if t in tracks]

    # 2) Extraer tracks necesarios UNA SOLA VEZ como arrays numpy
    # Coste máximo: ~19 tracks × 2 semanas × 4 bytes = ~93 MB por paciente
    print(f"    Extrayendo {len(needed_tracks)} tracks base a 1Hz...")
    start_ts = to_epoch_utc(t_start)
    end_ts   = to_epoch_utc(t_end)
    dt_index, track_arrays = extract_tracks_as_arrays(vf, needed_tracks, start_ts, end_ts)
    tnames_present = set(track_arrays.keys())
    n_pts = len(dt_index)
    print(f"    -> {len(tnames_present)} tracks extraidos, {n_pts} puntos ({n_pts/3600:.1f}h)")

    # 3) Procesamiento batch a batch: slice de arrays (vistas sin copia)
    print(f"    Derivadas en batches de {CHUNK_HOURS}h + {OVERLAP_MINUTES}m overlap ...")
    chunk_sec   = CHUNK_HOURS * 3600
    overlap_sec = OVERLAP_MINUTES * 60

    # Acumular resultados derivados como arrays numpy (mucho más ligero que listas de dicts)
    derived_accum = {}  # {tname: [np.array, np.array, ...]}

    curr_ts = start_ts
    while curr_ts < end_ts:
        b_start = max(start_ts, curr_ts - overlap_sec)
        b_end   = min(end_ts,   curr_ts + chunk_sec + overlap_sec)
        valid_start = curr_ts
        valid_end   = min(end_ts, curr_ts + chunk_sec)

        # Slice del índice (vista numpy, sin copia)
        bmask  = (dt_index >= b_start) & (dt_index <= b_end)
        vmask_rel = None  # calculado dentro si hay datos

        b_dt = dt_index[bmask]
        if len(b_dt) == 0:
            curr_ts += chunk_sec
            continue

        # Construir dict de arrays para este batch (slices = vistas sin copia)
        batch_arr = {tname: arr[bmask] for tname, arr in track_arrays.items()}

        derived_arrays = compute_batch(batch_arr, tnames_present)

        # Máscara de zona válida (sin overlap)
        vmask = (b_dt >= valid_start) & (b_dt < valid_end + 1e-5)
        valid_times = b_dt[vmask]

        if len(valid_times) > 0:
            for tname, arr in derived_arrays.items():
                valid_arr = arr[vmask]
                if tname not in derived_accum:
                    derived_accum[tname] = []
                derived_accum[tname].append(valid_arr)

        del batch_arr, derived_arrays
        curr_ts += chunk_sec

    # Liberar arrays base (ya no se necesitan)
    del track_arrays
    gc.collect()

    # 4) Concatenar y convertir a wav_recs solo en el momento de inyectar
    for tname, chunks in derived_accum.items():
        if not chunks:
            continue
        full_arr = np.concatenate(chunks).astype(np.float32)
        recs = wav_recs(start_ts, full_arr, srate=1.0)
        vf.add_track(tname, recs, srate=1.0)
        added += 1
        del full_arr, recs
    del derived_accum

    print(f"    Guardando ({added} tracks nuevos) ...")
    vf.save_vital(str(out_path))

    del vf
    gc.collect()

    print(f"    ✓ Listo")
    return added


# ── Main ──────────────────────────────────────────────────────────────────────
import argparse

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input_dir", default=str(IN_DIR))
    p.add_argument("--output_dir", default=str(OUT_DIR))
    p.add_argument("--file", help="Procesar un solo archivo")
    p.add_argument("--overwrite", action="store_true")
    args = p.parse_args()

    if not CHART_PATH.exists():
        print(f"ERROR: No existe {CHART_PATH}")
        return

    print(f"Cargando chartevents: {CHART_PATH} ...")
    chart_df = pd.read_parquet(CHART_PATH)
    chart_df["CHARTTIME"] = pd.to_datetime(chart_df["CHARTTIME"])
    print(f"  {len(chart_df):,} filas, {chart_df['SUBJECT_ID'].nunique()} pacientes")

    # Pre-agrupar por SUBJECT_ID: acceso O(1) sin copiar el DF completo por paciente
    print("  Pre-agrupando por SUBJECT_ID...")
    chart_groups = {sid: grp.reset_index(drop=True)
                    for sid, grp in chart_df.groupby("SUBJECT_ID")}
    del chart_df
    gc.collect()
    print(f"  {len(chart_groups)} grupos listos.")

    if args.file:
        files = [args.file]
    else:
        files = sorted(glob.glob(os.path.join(args.input_dir, "*.vital")))
        
    print(f"\nProcesando {len(files)} archivos .vital ...")

    errors = []
    for i, fpath in enumerate(files, 1):
        fname = os.path.basename(fpath)
        out_path = Path(args.output_dir) / fname
        
        if out_path.exists() and not args.overwrite:
            print(f"[{i}/{len(files)}] Saltar (ya existe): {fname}")
            continue

        print(f"[{i}/{len(files)}] {fname}")
        try:
            enrich_one(fpath, out_path, chart_groups)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            import traceback
            traceback.print_exc()
            errors.append((fname, str(exc)))
            
        # Aseguramos limpieza entre archivos
        gc.collect()

    print(f"\nFinalizado. Errores: {len(errors)}")
    for fname, err in errors:
        print(f"  {fname}: {err}")


if __name__ == "__main__":
    main()
