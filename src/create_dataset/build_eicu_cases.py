"""
Convierte los registros raw de eICU (vitalPeriodic, respiratoryCharting) en
archivos .vital por paciente, calculando las derivadas fisiológicas con la
misma lógica que MIMIC.
"""
import os
import sys
sys.path.append('.')

import gc
import numpy as np
import pandas as pd
import vitaldb
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse
import traceback

# Importamos la función compute_batch desde el script de MIMIC
from src.create_dataset.enrich_mimic_full_cases import (
    compute_batch,
    SAVGOL_WIN_1HZ,
    MIMIC_NUMERIC_TRACKS,
    MIMIC_VENT_TRACKS,
    sparse_to_dense,
    numeric_recs,
    wav_recs
)

IN_DIR = Path("datasets/eicu_collaborative")
OUT_DIR = Path("datasets/eicu_collaborative/eicu_full_cases")

# Reglas eICU compartidas con el adaptador (Fase 1.4).
from src.common.eicu_rules import (  # noqa: E402
    DOSE_UNKNOWN_SENTINEL,
    MAP_NONINVASIVE_TRACK,
    RR_NURSE_TRACK,
    RR_VENT_TRACK,
    add_vasopressor_series,
    eicu_t0_minutes,
    merge_vent_episodes as _merge_vent_episodes,
    sanitize_vent_episodes,
)

EICU_NUMERIC_MAP = {
    "heartrate": "eICU/HR",
    "sao2": "eICU/SpO2",
    "systemicsystolic": "eICU/ABP_S",
    "systemicdiastolic": "eICU/ABP_D",
    "systemicmean": "eICU/ABP_M",
    "noninvasivemean": MAP_NONINVASIVE_TRACK,   # D7: MAP no invasiva (vitalAperiodic)
    "respiration": "eICU/RESP",
    "temperature": "eICU/Temp"
}

EICU_VENT_MAP = {
    "peep": "eICU/PEEP",
    "fio2": "eICU/FiO2",
    "tv": "eICU/TV",
    "pip": "eICU/PIP",
    "rr": "eICU/RR_V"
}

def merge_vent_episodes(pt_vent: pd.DataFrame) -> pd.DataFrame:  # noqa: D401
    """Compatibilidad: delega en ``src.common.eicu_rules.merge_vent_episodes``."""
    return _merge_vent_episodes(pt_vent)

def load_data(in_dir):
    print("Cargando patient.csv.gz...")
    df_pat = pd.read_csv(in_dir / "patient.csv.gz", usecols=["patientunitstayid", "unitdischargeoffset"])
    
    print("Cargando respiratoryCare.csv.gz...")
    df_vent = pd.read_csv(in_dir / "respiratoryCare.csv.gz", usecols=["patientunitstayid", "ventstartoffset", "ventendoffset"])
    df_vent = df_vent.dropna(subset=['ventstartoffset', 'ventendoffset'])
    df_vent = df_vent[df_vent['ventendoffset'] > df_vent['ventstartoffset']]
    
    print("Cargando vitalPeriodic.csv.gz...")
    df_vit = pd.read_csv(in_dir / "vitalPeriodic.csv.gz", 
                         usecols=["patientunitstayid", "observationoffset", "heartrate", "sao2", "systemicsystolic", "systemicdiastolic", "systemicmean", "respiration", "temperature"])
    
    print("Cargando respiratoryCharting.csv.gz...")
    df_resp = pd.read_csv(in_dir / "respiratoryCharting.csv.gz",
                          usecols=["patientunitstayid", "respchartoffset", "respchartvaluelabel", "respchartvalue"])

    # D7: MAP no invasiva desde vitalAperiodic.noninvasivemean. Se añade como
    # filas extra de vitales (resto de columnas NaN) para no cambiar el flujo.
    print("Cargando vitalAperiodic.csv.gz (MAP no invasiva)...")
    df_ap = pd.read_csv(in_dir / "vitalAperiodic.csv.gz",
                        usecols=["patientunitstayid", "observationoffset", "noninvasivemean"])
    df_vit = pd.concat([df_vit, df_ap], ignore_index=True, sort=False)

    return df_pat, df_vent, df_vit, df_resp

def load_chunked_filtered(in_dir, valid_pids):
    print("Cargando nurseCharting.csv.gz (Respiratory Rate)...")
    chunks = pd.read_csv(in_dir / "nurseCharting.csv.gz", 
                         usecols=["patientunitstayid", "nursingchartoffset", "nursingchartcelltypevallabel", "nursingchartvalue"], 
                         chunksize=500000)
    nurse_list = []
    for c in chunks:
        mask = (c['nursingchartcelltypevallabel'] == 'Respiratory Rate') & (c['patientunitstayid'].isin(valid_pids))
        if mask.any():
            nurse_list.append(c[mask])
    df_nurse = pd.concat(nurse_list) if nurse_list else pd.DataFrame()

    print("Cargando lab.csv.gz (lactate)...")
    chunks = pd.read_csv(in_dir / "lab.csv.gz", 
                         usecols=["patientunitstayid", "labresultoffset", "labname", "labresult"], 
                         chunksize=500000)
    lab_list = []
    for c in chunks:
        mask = c['labname'].str.contains('lactate', case=False, na=False) & c['patientunitstayid'].isin(valid_pids)
        if mask.any():
            lab_list.append(c[mask])
    df_lab = pd.concat(lab_list) if lab_list else pd.DataFrame()

    print("Cargando infusionDrug.csv.gz (vasopressors)...")
    chunks = pd.read_csv(in_dir / "infusionDrug.csv.gz", 
                         usecols=["patientunitstayid", "infusionoffset", "drugname", "drugrate"], 
                         chunksize=500000)
    vaso_list = []
    for c in chunks:
        mask = c['drugname'].str.contains('norepi|epine|dopam', case=False, na=False) & c['patientunitstayid'].isin(valid_pids)
        if mask.any():
            vaso_list.append(c[mask])
    df_vaso_inf = pd.concat(vaso_list) if vaso_list else pd.DataFrame()

    print("Cargando medication.csv.gz (vasopressors)...")
    chunks = pd.read_csv(in_dir / "medication.csv.gz", 
                         usecols=["patientunitstayid", "drugstartoffset", "drugname", "dosage"], 
                         chunksize=500000)
    vaso_med_list = []
    for c in chunks:
        mask = c['drugname'].str.contains('norepi|epine|dopam', case=False, na=False) & c['patientunitstayid'].isin(valid_pids)
        if mask.any():
            vaso_med_list.append(c[mask])
    df_vaso_med = pd.concat(vaso_med_list) if vaso_med_list else pd.DataFrame()

    return df_nurse, df_lab, df_vaso_inf, df_vaso_med

def _process_patient(pid, meta, pdata, out_dir):
    vent_df = pdata['vent']
    vit_df = pdata['vit']
    resp_df = pdata['resp']
    nurse_df = pdata['nurse']
    lab_df = pdata['lab']
    vaso_inf_df = pdata['vaso_inf']
    vaso_med_df = pdata['vaso_med']
    
    # Encontrar T0 (inicio del primer episodio de ventilación) con la regla
    # compartida con el adaptador (Fase 1.4).
    if vent_df is None or vent_df.empty:
        return 0

    t_max_minutes = meta['unitdischargeoffset']

    # Anomalías de duración/hueco (offsets fuera de la estancia): se REGISTRAN.
    san = sanitize_vent_episodes(vent_df, t_max_minutes)
    for a in san.anomalies:
        print(f"  [eicu][anomalia] pid={a.patientunitstayid} {a.kind}: {a.detail}")

    t0_minutes = eicu_t0_minutes(san.episodes)
    if t0_minutes is None:
        return 0

    # Rango en segundos desde T0
    t0_sec = 0.0
    tend_sec = (t_max_minutes - t0_minutes) * 60.0
    
    if tend_sec <= 0:
        return 0
        
    # El patientunitstayid (pid) será el patient_id en los vitalfiles
    fname = out_dir / f"eicu_{pid}.vital"
    
    if not meta.get('overwrite', False) and fname.exists():
        return 0
        
    vf = vitaldb.VitalFile()
    
    # 1. Añadir Vitals
    if vit_df is not None and not vit_df.empty:
        vit_df = vit_df.sort_values('observationoffset')
        vit_times = (vit_df['observationoffset'] - t0_minutes) * 60.0  # a segundos
        
        for raw_col, eicu_track in EICU_NUMERIC_MAP.items():
            if raw_col in vit_df.columns:
                mask = vit_df[raw_col].notna()
                if mask.any():
                    t = vit_times[mask].values
                    v = vit_df.loc[mask, raw_col].values
                    recs = numeric_recs(t, v)
                    if recs:
                        vf.add_track(eicu_track, recs, srate=0)
    
    # 2. Añadir Respiratorios
    if resp_df is not None and not resp_df.empty:
        resp_df = resp_df.sort_values('respchartoffset')
        resp_df['val'] = pd.to_numeric(resp_df['respchartvalue'], errors='coerce')
        resp_df = resp_df.dropna(subset=['val'])
        
        # Mapeo simple
        label_map = {
            'PEEP': 'eICU/PEEP',
            'FiO2': 'eICU/FiO2',
            'Tidal Volume (set)': 'eICU/TV',
            'Peak Insp. Pressure': 'eICU/PIP',
            'Respiratory Rate': 'eICU/RR_V'
        }
        
        for raw_lbl, eicu_track in label_map.items():
            sub = resp_df[resp_df['respchartvaluelabel'] == raw_lbl]
            if not sub.empty:
                t = (sub['respchartoffset'] - t0_minutes) * 60.0
                v = sub['val'].values
                recs = numeric_recs(t, v)
                if recs:
                    vf.add_track(eicu_track, recs, srate=0)
                    
    # 3. Añadir nurseCharting (FR de enfermería) — NUNCA como RR del modelo (D7):
    # la RR debe ser la del ventilador (respiratoryCharting, ya añadida arriba).
    if nurse_df is not None and not nurse_df.empty:
        nurse_df = nurse_df.sort_values('nursingchartoffset')
        nurse_df['val'] = pd.to_numeric(nurse_df['nursingchartvalue'], errors='coerce')
        nurse_df = nurse_df.dropna(subset=['val'])
        if not nurse_df.empty:
            t = (nurse_df['nursingchartoffset'] - t0_minutes) * 60.0
            v = nurse_df['val'].values
            recs = numeric_recs(t, v)
            if recs:
                vf.add_track(RR_NURSE_TRACK, recs, srate=0)
                
    # 4. Añadir Lactate
    if lab_df is not None and not lab_df.empty:
        lab_df = lab_df.sort_values('labresultoffset')
        lab_df['val'] = pd.to_numeric(lab_df['labresult'], errors='coerce')
        lab_df = lab_df.dropna(subset=['val'])
        if not lab_df.empty:
            t = (lab_df['labresultoffset'] - t0_minutes) * 60.0
            v = lab_df['val'].values
            recs = numeric_recs(t, v)
            if recs:
                vf.add_track('eICU/Lactate', recs, srate=0)
                
    # 5. Añadir Vasopresores (infusión continua con PRIORIDAD sobre bolos, Fase 1.4)
    vaso_store: dict[str, tuple[list[float], list[float]]] = {}
    add_vasopressor_series(
        vaso_store, vaso_inf_df,
        offset_col='infusionoffset', value_col='drugrate',
        t0_minutes=t0_minutes, overwrite=True,
    )
    # Los bolos de medication NO sobrescriben las infusiones continuas.
    add_vasopressor_series(
        vaso_store, vaso_med_df,
        offset_col='drugstartoffset', value_col='dosage',
        t0_minutes=t0_minutes, overwrite=False,
    )
    for trk_name, (times, vals) in vaso_store.items():
        recs = numeric_recs(np.asarray(times, dtype=np.float64),
                            np.asarray(vals, dtype=np.float32))
        if recs:
            vf.add_track(trk_name, recs, srate=0)
    
    # Calcular MAP si falta pero SBP y DBP están (ya lo hace vitalPeriodic pero porsi)
    # Extraer los tracks al estilo enrich_mimic_full_cases para calcular derivadas
    tracks = set(vf.get_track_names())
    
    # Hack para reusar compute_batch sin modificarlo:
    # Renombramos temporalmente eICU/ a MIMIC/ para que compute_batch los reconozca.
    track_arrays = {}
    for tname in tracks:
        # Recuperar records
        trk = vf.trks.get(tname)
        if not trk or not trk.recs:
            continue
        dts = np.array([r['dt'] for r in trk.recs], dtype=np.float64)
        vals = np.array([r['val'] for r in trk.recs], dtype=np.float32)
        
        # Pasamos a denso 1Hz
        dense = sparse_to_dense(dts, vals, t0_sec, tend_sec, 1.0)
        if dense is not None:
            # Mapear eICU/ a MIMIC/ para compute_batch
            mimic_name = tname.replace("eICU/", "MIMIC/")
            track_arrays[mimic_name] = dense
            
    # Derivar MV si no está, a partir de TV y RR_V
    if "MIMIC/TV" in track_arrays and "MIMIC/RR_V" in track_arrays and "MIMIC/MV" not in track_arrays:
        track_arrays["MIMIC/MV"] = (track_arrays["MIMIC/TV"] * track_arrays["MIMIC/RR_V"]) / 1000.0
        
    # Calcular en batches
    chunk_sec = 6 * 3600
    overlap_sec = 15 * 60
    
    derived_accum = {}
    curr_ts = t0_sec
    
    # Precalculamos el dt_index completo
    n_pts = int(round((tend_sec - t0_sec) / 1.0)) + 1
    dt_index = np.arange(t0_sec, t0_sec + n_pts * 1.0, 1.0, dtype=np.float64)
    tnames_present = set(track_arrays.keys())
    
    while curr_ts < tend_sec:
        b_start = max(t0_sec, curr_ts - overlap_sec)
        b_end   = min(tend_sec, curr_ts + chunk_sec + overlap_sec)
        valid_start = curr_ts
        valid_end   = min(tend_sec, curr_ts + chunk_sec)
        
        bmask = (dt_index >= b_start) & (dt_index <= b_end)
        b_dt = dt_index[bmask]
        
        if len(b_dt) == 0:
            curr_ts += chunk_sec
            continue
            
        batch_arr = {k: v[bmask] for k, v in track_arrays.items()}
        derived_arrays = compute_batch(batch_arr, tnames_present)
        
        vmask = (b_dt >= valid_start) & (b_dt < valid_end + 1e-5)
        valid_times = b_dt[vmask]
        
        if len(valid_times) > 0:
            for tname, arr in derived_arrays.items():
                if tname not in derived_accum:
                    derived_accum[tname] = []
                derived_accum[tname].append(arr[vmask])
                
        curr_ts += chunk_sec

    # Añadir los tracks derivados
    for tname, chunks in derived_accum.items():
        if not chunks:
            continue
        full_arr = np.concatenate(chunks).astype(np.float32)
        recs = wav_recs(t0_sec, full_arr, srate=1.0)
        # Restore original name if needed (MIMIC/ -> eICU/, or keep Derived/)
        final_name = tname.replace("MIMIC/", "eICU/")
        vf.add_track(final_name, recs, srate=1.0)

    vf.save_vital(str(fname))
    return 1

def worker_func(pid, meta, pdata, out_dir):
    try:
        return _process_patient(pid, meta, pdata, out_dir)
    except Exception as e:
        print(f"Error procesando {pid}: {e}")
        # traceback.print_exc()
        return 0

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing vital files")
    args = parser.parse_args()
    
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    
    df_pat, df_vent, df_vit, df_resp = load_data(IN_DIR)
    
    valid_pids = df_vent['patientunitstayid'].unique()
    print(f"Pacientes con ventilación mecánica: {len(valid_pids)}")
    
    df_nurse, df_lab, df_vaso_inf, df_vaso_med = load_chunked_filtered(IN_DIR, valid_pids)
    
    # Filtrar para no saturar la memoria
    df_pat = df_pat[df_pat['patientunitstayid'].isin(valid_pids)]
    df_vit = df_vit[df_vit['patientunitstayid'].isin(valid_pids)]
    df_resp = df_resp[df_resp['patientunitstayid'].isin(valid_pids)]
    
    print("Pre-agrupando DataFrames (solo pacientes válidos)...")
    vent_groups = {pid: grp for pid, grp in df_vent.groupby('patientunitstayid')}
    vit_groups = {pid: grp for pid, grp in df_vit.groupby('patientunitstayid')}
    resp_groups = {pid: grp for pid, grp in df_resp.groupby('patientunitstayid')}
    nurse_groups = {pid: grp for pid, grp in df_nurse.groupby('patientunitstayid')} if not df_nurse.empty else {}
    lab_groups = {pid: grp for pid, grp in df_lab.groupby('patientunitstayid')} if not df_lab.empty else {}
    vaso_inf_groups = {pid: grp for pid, grp in df_vaso_inf.groupby('patientunitstayid')} if not df_vaso_inf.empty else {}
    vaso_med_groups = {pid: grp for pid, grp in df_vaso_med.groupby('patientunitstayid')} if not df_vaso_med.empty else {}
    
    print(f"Procesando {len(df_pat)} pacientes con {args.workers} workers...")
    
    success = 0
    import concurrent.futures
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = set()
        patient_iter = iter(df_pat.iterrows())
        
        def submit_next():
            try:
                _, row = next(patient_iter)
                row_dict = row.to_dict()
                row_dict['overwrite'] = args.overwrite
                pid = row['patientunitstayid']
                pdata = {
                    'vent': vent_groups.get(pid, pd.DataFrame()),
                    'vit': vit_groups.get(pid, pd.DataFrame()),
                    'resp': resp_groups.get(pid, pd.DataFrame()),
                    'nurse': nurse_groups.get(pid, pd.DataFrame()),
                    'lab': lab_groups.get(pid, pd.DataFrame()),
                    'vaso_inf': vaso_inf_groups.get(pid, pd.DataFrame()),
                    'vaso_med': vaso_med_groups.get(pid, pd.DataFrame())
                }
                return executor.submit(
                    worker_func, pid, row_dict, pdata, OUT_DIR
                )
            except StopIteration:
                return None
                
        for _ in range(args.workers * 2):
            f = submit_next()
            if f:
                futures.add(f)
                
        # Process and track progress
        total = len(df_pat)
        done_count = 0
        while futures:
            done, futures = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
            for f in done:
                done_count += 1
                success += f.result()
                nxt = submit_next()
                if nxt:
                    futures.add(nxt)
                
                if done_count % 1000 == 0:
                    print(f"Progreso: {done_count}/{total} pacientes generados ({success} válidos)...")

    print(f"Construcción completada. {success} vitalfiles guardados con éxito en {OUT_DIR}.")

if __name__ == "__main__":
    main()
