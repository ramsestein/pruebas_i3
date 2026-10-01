import numpy as np
import pandas as pd
import lightgbm as lgb
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.model_selection import GroupShuffleSplit
from sklearn.linear_model import TheilSenRegressor
import warnings
import os
import glob
from tqdm import tqdm

warnings.filterwarnings('ignore')

# ==============================================================================
# 1. CONFIGURACIÓN
# ==============================================================================
CONFIG = {
    # Puede ser un archivo .parquet único consolidado, o el directorio 'numerics/' 
    # que contiene archivos separados como 'eicu_123.parquet', 'SICU1_456.parquet', etc.
    "data_path": "datasets/harmonized/v0.1.0_fbb5b280/numerics", 
    
    # Mapeo de cohortes a sus prefijos en caso de ser directorio de archivos separados
    "cohort_prefixes": {
        "eicu": "eicu_",
        "clinic": "box",      # Ajusta este prefijo si es diferente
        "mimic": "mimic_",    # Ajusta este prefijo si es diferente
        "vitaldb": "SICU"     # VitalDB suele tener prefijo SICU
    },
    
    "id_col": "patient_id", # Si no existe en el parquet, se creará usando el nombre del archivo
    "time_col": "auto",     # Detectará automáticamente 'time', 'time_offset', 'timestamp', etc.
    "features_raw": ['RR', 'HR', 'SpO2', 'PEEP', 'PIP', 'MAP', 'FiO2', 'MV', 'Pmean', 'TV', 'PaO2', 'PaCO2'],
    
    "window_hours": 2,      # Ventana para agregar features (horas)
    "samples_per_patient_train": 5, # Puntos de corte a extraer por paciente en train
    "test_grid_hours": 1.0,   # Resolución temporal para inferencia en test (cada 1h)
    "random_seed": 42
}

# ==============================================================================
# 2. CARGA Y PREPROCESAMIENTO
# ==============================================================================
def load_cohort_data(config, cohort_name):
    """
    Carga todos los datos de una cohorte específica.
    Soporta leer de un directorio con múltiples parquets o de un único parquet consolidado.
    """
    print(f"-> Cargando datos para la cohorte: {cohort_name.upper()}...")
    data_path = config["data_path"]
    df_list = []
    
    if os.path.isdir(data_path) or not os.path.exists(data_path):
        # Es un directorio con múltiples archivos
        prefix = config["cohort_prefixes"][cohort_name]
        files = glob.glob(os.path.join(data_path, f"{prefix}*.parquet"))
        if not files:
            # Fallback a buscar en cualquier subdirectorio de versión en 'datasets/harmonized'
            files = glob.glob(os.path.join("datasets/harmonized/*/numerics", f"{prefix}*.parquet"))
            
        if not files:
            print(f"   [!] No se encontraron archivos para {cohort_name} con prefijo '{prefix}'")
            return pd.DataFrame()
            
        print(f"   Encontrados {len(files)} episodios. Leyendo...")
        for f in tqdm(files, desc=f"Leyendo {cohort_name}", leave=False):
            try:
                temp_df = pd.read_parquet(f)
                # Si no tiene columna de ID, usar el nombre del archivo
                if config["id_col"] not in temp_df.columns:
                    pid = os.path.basename(f).replace(".parquet", "")
                    temp_df[config["id_col"]] = pid
                df_list.append(temp_df)
            except Exception as e:
                continue
        if df_list:
            df = pd.concat(df_list, ignore_index=True)
        else:
            return pd.DataFrame()
    elif os.path.isfile(data_path):
        # Asume que es un solo parquet consolidado
        df = pd.read_parquet(data_path)
    else:
        raise ValueError(f"La ruta {data_path} no existe.")

    if len(df) == 0: return df
    
    # Auto-detectar columna de tiempo
    if config["time_col"] == "auto" or config["time_col"] not in df.columns:
        possible_time_cols = ["time", "time_offset", "timestamp", "t", "time_offset_hours", "hours", "time_rel_hours"]
        detected = None
        for col in possible_time_cols:
            if col in df.columns:
                detected = col
                break
        if detected:
            print(f"   [!] Columna de tiempo detectada como: '{detected}'")
            config["time_col"] = detected
        else:
            print(f"   [!] Error: No se pudo detectar la columna de tiempo en: {list(df.columns)}")
            raise KeyError(f"No se encontró columna de tiempo.")

    # Normalizar columna de tiempo para que empiece en 0 para cada paciente
    if len(df) > 0:
        t_col = config["time_col"]
        id_col = config["id_col"]
        min_times = df.groupby(id_col)[t_col].transform('min')
        df[t_col] = df[t_col] - min_times

    # Calcular duración total por episodio
    if len(df) > 0:
        durations = df.groupby(config["id_col"])[config["time_col"]].max().rename('total_duration')
        if 'total_duration' in df.columns:
            df = df.drop(columns=['total_duration'])
        df = df.merge(durations, on=config["id_col"], how='left')
        
    print(f"   Total filas: {len(df)}, Pacientes únicos: {df[config['id_col']].nunique() if len(df)>0 else 0}")
    
    if len(df) > 0:
        med_dur = df.groupby(config["id_col"])["total_duration"].first().median()
        med_pts = df.groupby(config["id_col"]).size().median()
        print(f"   [AUTOPSIA {cohort_name.upper()}] Duración Mediana = {med_dur:.2f}h | Puntos/Paciente (mediana) = {med_pts:.1f}")
        
    return df

# ==============================================================================
# 3. GENERACIÓN DE EJEMPLOS (MÚLTIPLES CORTES)
# ==============================================================================
def build_features_at_cutoff(df_patient, t_cutoff, config):
    """Extrae features crudos resumidos en la ventana [t_cutoff - W, t_cutoff]"""
    mask = (df_patient[config["time_col"]] <= t_cutoff) & \
           (df_patient[config["time_col"]] >= t_cutoff - config["window_hours"])
    df_window = df_patient[mask]
    
    features = {}
    if len(df_window) == 0:
        df_window = df_patient[df_patient[config["time_col"]] <= t_cutoff].tail(1)
        
    for f in config["features_raw"]:
        if f in df_window.columns:
            features[f"{f}_mean"] = df_window[f].mean()
            features[f"{f}_last"] = df_window[f].iloc[-1] if len(df_window)>0 else np.nan
            
    return features

def create_training_set(df_train, config):
    print("-> Construyendo dataset de entrenamiento (múltiples cortes por paciente)...")
    X, y = [], []
    
    groups = list(df_train.groupby(config["id_col"]))
    for pid, group in tqdm(groups, desc="Extrayendo cortes Train"):
        total_duration = group['total_duration'].iloc[0]
        if pd.isna(total_duration) or total_duration <= 0:
            continue
            
        if total_duration > 1:
            t_cutoffs = np.random.uniform(1, total_duration, config["samples_per_patient_train"])
            t_cutoffs = np.append(t_cutoffs, [1.0, total_duration])
        else:
            t_cutoffs = [total_duration]
            
        for t in np.unique(t_cutoffs):
            feat_dict = build_features_at_cutoff(group, t, config)
            X.append(feat_dict)
            y.append(total_duration) 
            
    return pd.DataFrame(X), np.array(y)

# ==============================================================================
# 4. ENTRENAMIENTO DEL MODELO ONE-SHOT
# ==============================================================================
def train_probe_model(X_train, y_train, config):
    print(f"-> Entrenando modelos LightGBM con {len(X_train)} ejemplos...")
    
    model_mean = lgb.LGBMRegressor(
        random_state=config["random_seed"], 
        n_estimators=100, 
        max_depth=5, 
        learning_rate=0.05,
        verbose=-1
    )
    model_mean.fit(X_train, y_train)
    
    quantiles = [0.1, 0.9]
    models_q = {}
    for q in quantiles:
        model_q = lgb.LGBMRegressor(
            objective='quantile', 
            alpha=q, 
            random_state=config["random_seed"],
            n_estimators=100, 
            max_depth=5, 
            learning_rate=0.05,
            verbose=-1
        )
        model_q.fit(X_train, y_train)
        models_q[q] = model_q
        
    return {"mean": model_mean, "quantiles": models_q}

# ==============================================================================
# 5. INFERENCIA EN SERIE Y CÁLCULO DE R(t)
# ==============================================================================
def run_serial_inference(df_test, models, config, cohort_name):
    print(f"-> Ejecutando inferencia en serie sobre pacientes de Test ({cohort_name.upper()})...")
    trajectories = []
    
    # Determinar resolución temporal según la duración máxima de la cohorte
    max_duration = df_test.groupby(config["id_col"])[config["time_col"]].max().max()
    if max_duration < 3.0:
        grid_step = 0.1  # Usar paso de 6 min si la cohorte es corta (ej. vitaldb)
    else:
        grid_step = config["test_grid_hours"]
    print(f"   [!] Usando resolución de inferencia (t_grid step): {grid_step} horas")
    
    groups = list(df_test.groupby(config["id_col"]))
    if len(groups) > 500:
        import random
        # Usar semilla para asegurar reproducibilidad
        rng = random.Random(config["random_seed"])
        groups = rng.sample(groups, 500)
    for pid, group in tqdm(groups, desc=f"Inferencia {cohort_name}"):
        total_duration = group['total_duration'].iloc[0]
        if pd.isna(total_duration) or total_duration <= 0:
            continue
            
        t_grid = np.arange(grid_step, total_duration + grid_step, grid_step)
        if len(t_grid) == 0: t_grid = [total_duration]
        
        for t in t_grid:
            feat_dict = build_features_at_cutoff(group, t, config)
            x_input = pd.DataFrame([feat_dict])
            
            # Forzar el orden de las columnas según entrenamiento (X_train) rellenando con NaN las faltantes
            x_input = x_input.reindex(columns=models["mean"].feature_name_)
            
            pred_total = models["mean"].predict(x_input)[0]
            pred_q10 = models["quantiles"][0.1].predict(x_input)[0]
            pred_q90 = models["quantiles"][0.9].predict(x_input)[0]
            
            trajectories.append({
                config["id_col"]: pid,
                "t": t,
                "total_duration_real": total_duration,
                "R_t": pred_total - t,
                "R_t_lower": pred_q10 - t,
                "R_t_upper": pred_q90 - t
            })
            
    return pd.DataFrame(trajectories)

# ==============================================================================
# 6. ANÁLISIS DE ESTRUCTURA VS RUIDO Y GRÁFICOS
# ==============================================================================
def calculate_metrics_and_plot(df_traj, cohort_name):
    print(f"-> Calculando métricas para {cohort_name.upper()}...")
    metrics = []
    output_dir = f"escalon1_results/{cohort_name}"
    os.makedirs(output_dir, exist_ok=True)
    
    if len(df_traj) == 0:
        print(f"   [!] No hay trayectorias para {cohort_name}.")
        return
        
    for pid, group in df_traj.groupby("patient_id"):
        if len(group) < 3: continue
        
        t = group["t"].values.reshape(-1, 1)
        r = group["R_t"].values
        
        reg = TheilSenRegressor(random_state=42).fit(t, r)
        slope = reg.coef_[0]
        
        diffs = np.diff(r)
        mono_frac = np.mean(diffs < 0) if len(diffs) > 0 else np.nan
        
        smoothness = np.var(diffs) if len(diffs) > 0 else np.nan
        
        P = r + t.flatten()
        P_shuffled = np.random.permutation(P)
        r_null = P_shuffled - t.flatten()
        
        diffs_null = np.diff(r_null)
        mono_frac_null = np.mean(diffs_null < 0) if len(diffs_null) > 0 else np.nan
        smoothness_null = np.var(diffs_null) if len(diffs_null) > 0 else np.nan
        
        metrics.append({
            "patient_id": pid,
            "slope": slope,
            "mono_frac": mono_frac,
            "mono_frac_null": mono_frac_null,
            "smoothness_var": smoothness,
            "smoothness_null": smoothness_null,
            "total_duration": group["total_duration_real"].iloc[0]
        })
        
    if not metrics:
        print(f"   [!] No hay suficientes puntos por paciente en {cohort_name}.")
        return
        
    df_metrics = pd.DataFrame(metrics)
    
    med_slope = df_metrics['slope'].median()
    conv = (df_metrics['slope'] < -0.5).mean() * 100
    flat = ((df_metrics['slope'] >= -0.5) & (df_metrics['slope'] <= 0.2)).mean() * 100
    div = (df_metrics['slope'] > 0.2).mean() * 100
    med_mono = df_metrics['mono_frac'].mean()
    med_mono_null = df_metrics['mono_frac_null'].mean()
    var_ratio = df_metrics['smoothness_var'].mean() / df_metrics['smoothness_null'].mean()
    
    # Estructura validada si mejora la monotonía base y reduce la varianza
    estructurado = (med_slope < -0.5) and (med_mono > med_mono_null) and (var_ratio < 1.0)
    reporte = f"CONCLUSIÓN {cohort_name.upper()}: R(t) {'SÍ' if estructurado else 'NO'} muestra estructura."
    
    with open(f"{output_dir}/reporte_{cohort_name}.txt", "w", encoding="utf-8") as f:
        f.write(reporte + "\n\n")
        f.write(f"Pendiente Mediana: {med_slope:.3f} (Ideal: -1.0)\n")
        f.write(f"Convergentes (< -0.5): {conv:.1f}%\n")
        f.write(f"Planos (-0.5 a 0.2): {flat:.1f}%\n")
        f.write(f"Divergentes (> 0.2): {div:.1f}%\n")
        f.write(f"Tasa de Monotonía (Real): {med_mono:.3f} (Null: {med_mono_null:.3f})\n")
        f.write(f"Suavidad (Real/Nulo): {var_ratio:.3f} (Estructurado: < 1.0)\n")
        
    print(f"\n[{cohort_name.upper()}] Pendiente: {med_slope:.3f} | Monotonía: {med_mono:.3f} (Null: {med_mono_null:.3f}) | Suavidad (Real/Null): {var_ratio:.3f}")
    
    # --- GRÁFICOS ---
    plt.style.use('ggplot')
    
    # A. Muestrario de trayectorias individuales
    plt.figure(figsize=(18, 5))
    df_metrics_sorted = df_metrics.sort_values("total_duration")
    
    short_pids = df_metrics_sorted.head(max(len(df_metrics)//3, 3)).sample(min(3, len(df_metrics)), replace=True)["patient_id"].values
    long_pids = df_metrics_sorted.tail(max(len(df_metrics)//3, 3)).sample(min(3, len(df_metrics)), replace=True)["patient_id"].values
    
    for i, pid in enumerate(np.unique(np.concatenate([short_pids, long_pids]))[:6]):
        plt.subplot(1, 6, i+1)
        pat_data = df_traj[df_traj["patient_id"] == pid]
        
        plt.plot(pat_data["t"], pat_data["R_t"], marker='o', markersize=3, label="R(t) Pred")
        plt.fill_between(pat_data["t"], pat_data["R_t_lower"], pat_data["R_t_upper"], alpha=0.2, color='blue')
        
        ideal_rt = pat_data["total_duration_real"].iloc[0] - pat_data["t"]
        plt.plot(pat_data["t"], ideal_rt, 'r--', alpha=0.6, label="Ideal")
        
        plt.title(f"ID: {pid}\nDur: {pat_data['total_duration_real'].iloc[0]:.1f}h")
        if i == 0: plt.legend()
        plt.xlabel("Tiempo transcurrido (h)")
        if i == 0: plt.ylabel("Tiempo restante R(t) (h)")
        
    plt.tight_layout()
    plt.savefig(f"{output_dir}/trayectorias_{cohort_name}.png", dpi=150)
    plt.close()
    
    # B. Distribución de pendientes
    plt.figure(figsize=(8, 5))
    sns.histplot(df_metrics['slope'], bins=30, kde=True)
    plt.axvline(-1.0, color='r', linestyle='--', label='Ideal (-1)')
    plt.axvline(0, color='k', linestyle=':', label='Ruido (0)')
    plt.title(f"Distribución Pendiente R(t) - {cohort_name.upper()}")
    plt.xlabel("Pendiente")
    plt.legend()
    plt.savefig(f"{output_dir}/distribucion_pendientes_{cohort_name}.png", dpi=150)
    plt.close()

# ==============================================================================
# EJECUCIÓN PRINCIPAL
# ==============================================================================
if __name__ == "__main__":
    print("=== INICIANDO EXPERIMENTO ESCALÓN 1 ===")
    
    # 1. ENTRENAR Y TESTEAR EN EICU
    df_eicu = load_cohort_data(CONFIG, "eicu")
    if len(df_eicu) > 0:
        gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=CONFIG["random_seed"])
        train_idx, test_idx = next(gss.split(df_eicu, groups=df_eicu[CONFIG["id_col"]]))
        
        df_train_eicu = df_eicu.iloc[train_idx].copy()
        df_test_eicu = df_eicu.iloc[test_idx].copy()
        
        X_train, y_train = create_training_set(df_train_eicu, CONFIG)
        models = train_probe_model(X_train, y_train, CONFIG)
        
        # Test en eICU
        df_traj_eicu = run_serial_inference(df_test_eicu, models, CONFIG, "eicu")
        calculate_metrics_and_plot(df_traj_eicu, "eicu")
    else:
        print("[ERROR] No se pudo cargar eICU para entrenar.")
        exit(1)
        
    # 2. EVALUAR EN OTRAS COHORTES (ZERO-SHOT)
    other_cohorts = ["clinic", "mimic", "vitaldb"]
    for cohort in other_cohorts:
        print("\n" + "="*50)
        df_cohort = load_cohort_data(CONFIG, cohort)
        if len(df_cohort) > 0:
            # Evaluar en el 100% de la cohorte externa
            df_traj_cohort = run_serial_inference(df_cohort, models, CONFIG, cohort)
            calculate_metrics_and_plot(df_traj_cohort, cohort)
        else:
            print(f"-> Saltando {cohort} (sin datos).")
            
    print("\n=== FIN DEL EXPERIMENTO ===")
