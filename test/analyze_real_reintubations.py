import os
import glob
import pandas as pd
import re

def main():
    print("=== Análisis de Reintubaciones reales en MIMIC-III ===")
    
    # 1. Obtener los 82 pacientes que tenemos en mimic_full_cases
    mimic_dir = "datasets/mimic3wdb/mimic_full_cases"
    vital_files = glob.glob(os.path.join(mimic_dir, "*.vital"))
    
    if not vital_files:
        print(f"No se encontraron archivos en {mimic_dir}")
        return
        
    subject_ids = set()
    for f in vital_files:
        # Extraer los números del nombre del archivo (ej. 3012312_0001.vital o p12345.vital)
        # Asumimos que el primer grupo de números largos puede identificar al subject o buscamos en el chartevents.
        pass
        
    # Mejor sacar los subject_ids del parquet que ya tenemos y sabemos que cruza bien
    chart_path = "datasets/mimic3wdb/mimic_chartevents_vent.parquet"
    if os.path.exists(chart_path):
        df_chart = pd.read_parquet(chart_path, columns=['SUBJECT_ID'])
        subject_ids = set(df_chart['SUBJECT_ID'].unique())
        print(f"Identificados {len(subject_ids)} pacientes únicos en MIMIC locales.")
    else:
        print("No se encontró el parquet de chartevents.")
        return

    # 2. Buscar PROCEDUREEVENTS_MV.csv.gz
    proc_path = "PROCEDUREEVENTS_MV.csv.gz"
    
    if not os.path.exists(proc_path):
        proc_path = input("No se encontró automáticamente en la raíz. Introduce la ruta completa: ").strip()
        proc_path = proc_path.strip("'").strip('"')
        
        if not os.path.exists(proc_path):
            print(f"Tampoco se encontró el archivo: {proc_path}")
            return
        
    print(f"\nCargando PROCEDUREEVENTS_MV...")
    try:
        df = pd.read_csv(proc_path, compression='gzip', 
                         usecols=['SUBJECT_ID', 'ITEMID', 'STARTTIME', 'ENDTIME'])
    except Exception as e:
        print(f"Error al leer el CSV: {e}")
        return
        
    # Filtrar solo 'Invasive Mechanical Ventilation' (225792)
    df_vent = df[df['ITEMID'] == 225792].copy()
    
    # Filtrar solo nuestros 82 pacientes
    df_vent = df_vent[df_vent['SUBJECT_ID'].isin(subject_ids)].copy()
    
    if df_vent.empty:
        print("No se encontró ningún evento de ventilación para estos pacientes en PROCEDUREEVENTS_MV.")
        return
        
    df_vent['STARTTIME'] = pd.to_datetime(df_vent['STARTTIME'])
    df_vent['ENDTIME'] = pd.to_datetime(df_vent['ENDTIME'])
    df_vent = df_vent.sort_values(['SUBJECT_ID', 'STARTTIME'])
    
    # Calcular huecos reales (tiempo entre el FIN de una intubación y el INICIO de la siguiente)
    df_vent['prev_endtime'] = df_vent.groupby('SUBJECT_ID')['ENDTIME'].shift(1)
    df_vent['gap_hours'] = (df_vent['STARTTIME'] - df_vent['prev_endtime']).dt.total_seconds() / 3600.0
    
    # Contar reintubaciones
    counts = df_vent.groupby('SUBJECT_ID').size()
    patients_with_reintubation = counts[counts > 1]
    
    print("\n=== RESULTADOS ===")
    print(f"Pacientes totales analizados: {len(counts)}")
    print(f"Pacientes con 1 sola intubación (éxito a la primera): {len(counts) - len(patients_with_reintubation)}")
    print(f"Pacientes con REINTUBACIÓN (múltiples eventos): {len(patients_with_reintubation)} ({(len(patients_with_reintubation)/len(counts))*100:.1f}%)")
    print(f"Total de eventos de ventilación registrados: {len(df_vent)}")
    
    # Mostrar el tiempo medio del hueco para las reintubaciones reales
    reintubations = df_vent[df_vent['gap_hours'].notnull()]
    if not reintubations.empty:
        mean_gap = reintubations['gap_hours'].mean()
        median_gap = reintubations['gap_hours'].median()
        print(f"\nEstadísticas del tiempo entre extubación y reintubación:")
        print(f"  Media: {mean_gap:.2f} horas")
        print(f"  Mediana: {median_gap:.2f} horas")
        print(f"  Mínimo: {reintubations['gap_hours'].min():.2f} horas")
        print(f"  Máximo: {reintubations['gap_hours'].max():.2f} horas")
        
        # Cuántas ocurren antes de 48h y 72h
        r_48h = len(reintubations[reintubations['gap_hours'] <= 48])
        r_72h = len(reintubations[reintubations['gap_hours'] <= 72])
        print(f"\n  Reintubaciones en < 48h (fallo primario): {r_48h}")
        print(f"  Reintubaciones en < 72h: {r_72h}")

if __name__ == "__main__":
    main()
