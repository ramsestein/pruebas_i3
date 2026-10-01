import os
import glob
import numpy as np
import pandas as pd
import vitaldb
import json
from pathlib import Path

# Definimos cuánto tiempo sin señales consideramos un "hueco" (desconexión)
# Si desaparecen > 2 horas y luego vuelven, se cuenta como 1 evento
GAP_THRESHOLD_HOURS = 2.0
GAP_SEC = GAP_THRESHOLD_HOURS * 3600

def check_gaps_in_vital_file(file_path, vent_tracks):
    try:
        vf = vitaldb.VitalFile(str(file_path))
        tracks_in_file = [t for t in vent_tracks if t in vf.get_track_names()]
        if not tracks_in_file:
            return 0, []
            
        all_times = []
        for tname in tracks_in_file:
            trk = vf.trks.get(tname)
            if trk and hasattr(trk, 'recs'):
                for r in trk.recs:
                    v = r.get('val')
                    if v is None: 
                        continue
                        
                    # Filtrar NaNs y Ceros (el monitor puede seguir grabando "0" o NaN desconectado)
                    if isinstance(v, (int, float)):
                        if np.isnan(v) or v == 0:
                            continue
                        all_times.append(r['dt'])
                    elif isinstance(v, (list, np.ndarray)):
                        v_arr = np.array(v)
                        # Si es waveform, cuenta solo si no son todos NaN/0
                        if np.any((~np.isnan(v_arr)) & (np.abs(v_arr) > 1e-5)):
                            all_times.append(r['dt'])
        
        if not all_times:
            return 0, []
            
        # Ordenamos todos los timestamps de todas las señales ventilatorias
        all_times = np.sort(np.unique(all_times))
        
        # Calculamos la diferencia de tiempo entre registros consecutivos
        diffs = np.diff(all_times)
        
        # Un hueco es una diferencia mayor que GAP_SEC
        gaps = diffs[diffs > GAP_SEC]
        return int(len(gaps)), gaps.tolist()
    except Exception as e:
        return 0, []

def print_progress(current, total):
    if total > 0 and current % max(1, total // 10) == 0:
        print(f"    Procesados {current}/{total} ({(current/total)*100:.0f}%)")

def main():
    print(f"=== Analizando desapariciones de señales de ventilación (> {GAP_THRESHOLD_HOURS} horas) ===\n")
    results = {}

    # 1. Clínic
    print("=== Hospital Clínic ===")
    clinic_dir = "datasets/clinic_vitals/clinic_full_cases"
    clinic_files = glob.glob(os.path.join(clinic_dir, "*.vital"))
    # Usamos CO2 (capnografía), TV_EXP, y VENT_RR
    clinic_tracks = ['Intellivue/TV_EXP', 'Intellivue/VENT_RR', 'Intellivue/CO2', 'Intellivue/AWAY_CO2_ET']
    
    if clinic_files:
        print(f"Analizando {len(clinic_files)} archivos de Clinic...")
        clinic_gaps = []
        clinic_gap_durations = []
        for i, f in enumerate(clinic_files, 1):
            n, dur = check_gaps_in_vital_file(f, clinic_tracks)
            clinic_gaps.append(n)
            clinic_gap_durations.extend(dur)
            print_progress(i, len(clinic_files))
        
        avg = np.mean(clinic_gaps)
        total = np.sum(clinic_gaps)
        has_gaps = sum(1 for x in clinic_gaps if x > 0)
        mean_dur_h = np.mean(clinic_gap_durations) / 3600 if clinic_gap_durations else 0
        print(f"  Media de huecos por paciente: {avg:.2f}")
        print(f"  Pacientes con al menos 1 hueco: {has_gaps} de {len(clinic_files)} ({(has_gaps/len(clinic_files))*100:.1f}%)")
        print(f"  Total de veces que la ventilación desapareció y volvió: {total}")
        print(f"  Tiempo medio del hueco: {mean_dur_h:.2f} horas")
    else:
        print("No se encontraron archivos de Clinic.")

    # 2. VitalDB
    print("\n=== VitalDB ===")
    vitaldb_dir = "datasets/vitaldb_sicu/vitaldb_full_cases"
    vitaldb_idx = "datasets/vitaldb_sicu/vitaldb_full_cases_index.json"
    vitaldb_tracks = ['Intellivue/TV_EXP', 'Intellivue/VENT_RR', 'Intellivue/CO2']
    
    if os.path.exists(vitaldb_idx):
        with open(vitaldb_idx) as f:
            idx = json.load(f)
        events = idx.get("events", [])
        vitaldb_files = [os.path.join(vitaldb_dir, ev["file"]) for ev in events]
        print(f"Analizando {len(vitaldb_files)} archivos de VitalDB...")
        vitaldb_gaps = []
        vitaldb_gap_durations = []
        valid_files = 0
        for i, f in enumerate(vitaldb_files, 1):
            if os.path.exists(f):
                n, dur = check_gaps_in_vital_file(f, vitaldb_tracks)
                vitaldb_gaps.append(n)
                vitaldb_gap_durations.extend(dur)
                valid_files += 1
            print_progress(i, len(vitaldb_files))
                
        if vitaldb_gaps:
            avg = np.mean(vitaldb_gaps)
            total = np.sum(vitaldb_gaps)
            has_gaps = sum(1 for x in vitaldb_gaps if x > 0)
            mean_dur_h = np.mean(vitaldb_gap_durations) / 3600 if vitaldb_gap_durations else 0
            print(f"  Media de huecos por paciente: {avg:.2f}")
            print(f"  Pacientes con al menos 1 hueco: {has_gaps} de {valid_files} ({(has_gaps/valid_files)*100:.1f}%)")
            print(f"  Total de veces que la ventilación desapareció y volvió: {total}")
            print(f"  Tiempo medio del hueco: {mean_dur_h:.2f} horas")
    else:
        print("No se encontró index de VitalDB.")

    # 3. MIMIC-III
    print("\n=== MIMIC-III ===")
    mimic_chart = "datasets/mimic3wdb/mimic_chartevents_vent.parquet"
    if os.path.exists(mimic_chart):
        print("Analizando chartevents de MIMIC con criterios más robustos...")
        df = pd.read_parquet(mimic_chart)
        vent_itemids = [3420, 223835, 505, 224700, 681, 224685, 507, 224696, 618, 220210, 682, 224687]
        df_vent = df[df['ITEMID'].isin(vent_itemids)].copy()
        
        df_vent['CHARTTIME'] = pd.to_datetime(df_vent['CHARTTIME'])
        df_vent = df_vent.sort_values(['SUBJECT_ID', 'CHARTTIME'])
        
        # Para anotaciones manuales (chartevents), un hueco de 2h es normal si no se cambia el ventilador.
        # Vamos a usar un hueco mucho más agresivo: > 6 horas sin una sola anotación del ventilador.
        MIMIC_GAP_SEC = 6.0 * 3600
        
        df_vent['diff_sec'] = df_vent.groupby('SUBJECT_ID')['CHARTTIME'].diff().dt.total_seconds()
        df_gaps = df_vent[df_vent['diff_sec'] > MIMIC_GAP_SEC]
        
        total_patients = df_vent['SUBJECT_ID'].nunique()
        gaps_per_patient = df_gaps.groupby('SUBJECT_ID').size()
        has_gaps = len(gaps_per_patient)
        total_gaps = gaps_per_patient.sum()
        avg = total_gaps / total_patients if total_patients > 0 else 0
        mean_dur_h = df_gaps['diff_sec'].mean() / 3600 if not df_gaps.empty else 0
        
        print(f"  Media de huecos (> 6.0h sin registros) por paciente: {avg:.2f}")
        print(f"  Pacientes con al menos 1 hueco: {has_gaps} de {total_patients} ({(has_gaps/total_patients)*100:.1f}%)")
        print(f"  Total de veces que la ventilación desapareció y volvió: {total_gaps}")
        print(f"  Tiempo medio del hueco: {mean_dur_h:.2f} horas")
        print("  *(Nota: en MIMIC lo ideal es extraer esto directamente de PROCEDUREEVENTS_MV, que registra explícitamente el inicio y fin de la intubación)*")
    else:
        print("No se encontró mimic_chartevents_vent.parquet")

if __name__ == "__main__":
    main()
