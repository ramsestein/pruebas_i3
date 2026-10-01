"""
DatasetBuilder para Escalón 2.

Lee los archivos parquet de numerics/ y survival_48h.parquet,
construye secuencias de 4 ventanas con Δt por paso, y devuelve
tensores listos para el modelo LSTM distribucional.
"""

import os
import glob
import numpy as np
import pandas as pd
from multiprocessing import Pool, cpu_count
from collections import defaultdict
from typing import Tuple, List, Dict, Optional

import torch
from torch.utils.data import Dataset

from .config import (
    NUMERICS_DIR, SURVIVAL_PATH, MIMIC_SURVIVAL_PATH,
    TRAIN_COHORT, TEST_COHORT, BASE_FEATURES, DERIVED_FEATURES, ALL_FEATURES,
    MIN_FEATURES_PER_WINDOW, WINDOW_SIZE_MINUTES, MIN_GAP_MINUTES, SEQ_LEN,
    LANDMARK_STRIDE_MINUTES, LOCF_MAX_AGE_HOURS, RANDOM_SEED,
    OUTPUT_DIR,
)

# ── Normalización por mediana (escala relativa) ───────────────────────
# Calculadas sobre eICU train (26k pacientes). Conserva interpretación
# clínica: 1.0 = valor típico, <1 por debajo, >1 por encima.
# Índices derivados: medianas aproximadas basadas en fisiología.
FEATURE_MEDIAN = np.array([
    # Base features
    18.0,   # RR (rpm)
    87.0,   # HR (bpm)
    98.0,   # SpO2 (%)
     5.0,   # PEEP (cmH2O)
    80.0,   # MAP (mmHg)
    40.0,   # FiO2 (%)
   466.0,   # TV (ml)
    21.0,   # PIP (cmH2O)
    # Derived features
     0.04,  # RSBI = RR/TV (breaths/min / ml)
     2.45,  # SF_ratio = SpO2/FiO2
    15.0,   # compliance = TV/(PIP-PEEP) (ml/cmH2O)
    16.0,   # driving_pressure = PIP-PEEP (cmH2O)
     2.0,   # MAP/FiO2
], dtype=np.float32)

# Evitar división por cero
FEATURE_MEDIAN = np.maximum(FEATURE_MEDIAN, 1e-6)

# ── Caché ──────────────────────────────────────────────────────────────
CACHE_DIR = os.path.join(OUTPUT_DIR, "cache")
CACHE_TRAIN = os.path.join(CACHE_DIR, "train_sequences.parquet")
CACHE_VAL   = os.path.join(CACHE_DIR, "val_sequences.parquet")
CACHE_TEST  = os.path.join(CACHE_DIR, "test_sequences.parquet")
# Clave de caché: si cambia la config, se reconstruye
import hashlib, json
CACHE_KEY = hashlib.md5(json.dumps({
    "min_feats": MIN_FEATURES_PER_WINDOW,
    "val_split": 0.15,
    "seed": RANDOM_SEED,
    "feats": ALL_FEATURES,
}, sort_keys=True).encode()).hexdigest()[:8]
CACHE_META = os.path.join(CACHE_DIR, f"cache_meta_{CACHE_KEY}.txt")

# Conversion de minutos a horas para operar con time_rel_hours
WINDOW_H = WINDOW_SIZE_MINUTES / 60.0
MIN_GAP_H = MIN_GAP_MINUTES / 60.0
STRIDE_H = LANDMARK_STRIDE_MINUTES / 60.0


def _load_survival() -> pd.DataFrame:
    """Carga y consolida los datos de supervivencia (eICU + MIMIC)."""
    surv = pd.read_parquet(SURVIVAL_PATH, engine="fastparquet")
    # Añadir MIMIC desde la otra versión
    if os.path.exists(MIMIC_SURVIVAL_PATH):
        surv_mimic = pd.read_parquet(MIMIC_SURVIVAL_PATH, engine="fastparquet")
        surv_mimic = surv_mimic[surv_mimic["cohort"] == "mimic"]
        surv = pd.concat([surv, surv_mimic], ignore_index=True)
    return surv


def _list_patient_files(cohort: str) -> List[str]:
    """Lista los archivos parquet de una cohorte."""
    prefix = "eicu_" if cohort == "eicu" else "mimic_"
    pattern = os.path.join(NUMERICS_DIR, f"{prefix}*.parquet")
    return sorted(glob.glob(pattern))


def _is_window_valid(feats: np.ndarray) -> bool:
    """
    Una ventana es válida si tiene >= MIN_FEATURES_PER_WINDOW features no-NaN.
    `feats` es un array de shape (N_FEATURES,).
    """
    return int(np.sum(~np.isnan(feats))) >= MIN_FEATURES_PER_WINDOW


def _apply_locf(df_patient: pd.DataFrame) -> pd.DataFrame:
    """
    Aplica Last Observation Carried Forward a variables esparsas
    (PEEP, PIP, FiO2, TV) respetando antigüedad máxima.
    Versión vectorizada con pandas ffill + máscara temporal.
    """
    df = df_patient.copy()
    sparse_cols = [c for c in ALL_FEATURES if c in df.columns]
    t = df["time_rel_hours"].values

    for col in sparse_cols:
        vals = df[col].values.astype(np.float64)
        not_null = ~np.isnan(vals)

        if not not_null.any():
            continue

        # Forward-fill vectorizado con pandas
        filled = pd.Series(vals).ffill().values
        # Tiempos del último valor real (vectorizado)
        last_t = pd.Series(np.where(not_null, t, np.nan)).ffill().values
        age = t - last_t

        # Solo mantener fill donde antigüedad <= max_age
        valid = ~np.isnan(age) & (age <= LOCF_MAX_AGE_HOURS) & np.isnan(vals)
        result = np.where(valid, filled, vals)
        df[col] = result.astype(np.float32)

    return df


def _compute_derived(base_feats: np.ndarray) -> np.ndarray:
    """
    Calcula índices fisiológicos derivados a partir de las features base.

    Índices:
    - RSBI = RR / TV  (mayor = peor, respiración rápida superficial)
    - SF_ratio = SpO2 / FiO2  (mayor = mejor oxigenación)
    - compliance = TV / (PIP - PEEP)  (mayor = pulmón más distensible)
    - driving_pressure = PIP - PEEP  (menor = menos esfuerzo)
    - MAP_FiO2 = MAP / FiO2

    Returns:
        np.ndarray de shape (len(DERIVED_FEATURES),) con NaN donde
        no se puede calcular.
    """
    # Índices en BASE_FEATURES
    idx = {name: i for i, name in enumerate(BASE_FEATURES)}

    rr = base_feats[idx["RR"]]
    hr = base_feats[idx["HR"]]
    spo2 = base_feats[idx["SpO2"]]
    peep = base_feats[idx["PEEP"]]
    map_ = base_feats[idx["MAP"]]
    fio2 = base_feats[idx["FiO2"]]
    tv = base_feats[idx["TV"]]
    pip = base_feats[idx["PIP"]]

    derived = np.full(len(DERIVED_FEATURES), np.nan, dtype=np.float32)

    # RSBI = RR / TV (TV en litros para unidades clásicas; usamos ml → /1000)
    if np.isfinite(rr) and np.isfinite(tv) and tv > 0:
        derived[0] = rr / (tv / 1000.0)

    # SF_ratio = SpO2 / FiO2
    if np.isfinite(spo2) and np.isfinite(fio2) and fio2 > 0:
        derived[1] = spo2 / fio2

    # compliance = TV / (PIP - PEEP)
    if np.isfinite(tv) and np.isfinite(pip) and np.isfinite(peep) and (pip - peep) > 0:
        derived[2] = tv / (pip - peep)

    # driving_pressure = PIP - PEEP
    if np.isfinite(pip) and np.isfinite(peep):
        derived[3] = pip - peep

    # MAP / FiO2
    if np.isfinite(map_) and np.isfinite(fio2) and fio2 > 0:
        derived[4] = map_ / fio2

    return derived


def _build_windows(
    df_patient: pd.DataFrame,
) -> List[Dict]:
    """
    Construye todas las ventanas válidas para un paciente.

    Algoritmo: sliding window con stride fijo sobre el timeline.
    Cada ventana cubre [t, t + WINDOW_H].

    Returns:
        Lista de dicts con keys:
        - t_center: tiempo central de la ventana (horas)
        - feats: np.array de shape (N_FEATURES,) con la mediana de cada feature
        - missing: np.array de shape (N_FEATURES,) con indicador 0/1
    """
    if len(df_patient) == 0:
        return []

    # Aplicar LOCF antes de construir ventanas
    df_patient = _apply_locf(df_patient)

    t_min = df_patient["time_rel_hours"].min()
    t_max = df_patient["time_rel_hours"].max()

    windows = []
    t = t_min
    while t + WINDOW_H <= t_max + 1e-9:
        # Filas dentro de la ventana [t, t + WINDOW_H]
        mask = (df_patient["time_rel_hours"] >= t) & (
            df_patient["time_rel_hours"] < t + WINDOW_H
        )
        df_win = df_patient[mask]

        if len(df_win) == 0:
            t += STRIDE_H
            continue

        # Extraer valores base y missingness
        base_feats = np.full(len(BASE_FEATURES), np.nan, dtype=np.float32)
        for i, col in enumerate(BASE_FEATURES):
            if col in df_win.columns:
                col_vals = df_win[col].dropna().values
                if len(col_vals) > 0:
                    base_feats[i] = np.median(col_vals)  # mediana robusta a outliers

        # Construir features derivadas a partir de las bases
        derived = _compute_derived(base_feats)

        # Concatenar base + derived
        feats = np.concatenate([base_feats, derived]).astype(np.float32)

        # Validar: al menos MIN_FEATURES_PER_WINDOW features base no-NaN
        if not _is_window_valid(base_feats):
            t += STRIDE_H
            continue

        missing = np.isnan(feats).astype(np.float32)
        # Normalización por mediana: conserva escala relativa y sentido clínico
        feats = np.where(
            np.isnan(feats),
            0.0,  # missing → 0 (neutral: 0/mediana)
            feats / FEATURE_MEDIAN,
        ).astype(np.float32)

        windows.append({
            "t_center": t + WINDOW_H / 2,
            "t_start": t,
            "t_end": t + WINDOW_H,
            "feats": feats,
            "missing": missing,
        })

        t += STRIDE_H

    return windows


def build_sequences_from_patient(
    df_patient: pd.DataFrame,
    extubation_time: float,
    patient_id: str,
) -> List[Dict]:
    """
    Construye todas las secuencias (ejemplos de entrenamiento) para un paciente.

    Cada secuencia consiste en las 4 ventanas válidas más recientes hasta
    un landmark. El landmark se muestrea uniformemente sobre las ventanas
    disponibles.

    Returns:
        Lista de dicts con:
        - patient_id
        - landmark_t: tiempo del landmark
        - window_feats: (SEQ_LEN, N_FEATURES) features de cada ventana
        - window_missing: (SEQ_LEN, N_FEATURES) indicadores de missing
        - delta_ts: (SEQ_LEN - 1,) diferencias temporales entre ventanas
        - time_remaining: target (extubation_time - landmark_t)
    """
    windows = _build_windows(df_patient)
    if len(windows) < SEQ_LEN:
        return []

    sequences = []

    # Muestrear landmarks uniformemente sobre ventanas con índice >= SEQ_LEN - 1
    # Para cada ventana i (i >= 3), el landmark es t_end de la ventana i
    for i in range(SEQ_LEN - 1, len(windows)):
        # Las 4 ventanas más recientes hasta window i (inclusive)
        recent = windows[i - SEQ_LEN + 1 : i + 1]

        landmark_t = recent[-1]["t_end"]
        time_remaining = extubation_time - landmark_t

        # Saltar landmarks después de la extubación o con tiempo restante <= 0
        if time_remaining <= 0:
            continue

        # Saltar landmarks con tiempo negativo (datos corruptos)
        if landmark_t < 0:
            continue

        # Stack features y missingness
        w_feats = np.stack([w["feats"] for w in recent])      # (4, N_FEATURES)
        w_missing = np.stack([w["missing"] for w in recent])  # (4, N_FEATURES)

        # Δt entre ventanas consecutivas
        t_centers = np.array([w["t_center"] for w in recent])
        delta_ts = np.diff(t_centers).astype(np.float32)      # (3,)

        sequences.append({
            "patient_id": patient_id,
            "landmark_t": landmark_t,
            "window_feats": w_feats,
            "window_missing": w_missing,
            "delta_ts": delta_ts,
            "log_time_remaining": np.float32(np.log(max(time_remaining, 1e-6))),
        })

    return sequences


class ExtubationDataset(Dataset):
    """
    Dataset PyTorch para el Escalón 2.

    Cada item es un dict con:
    - x: (SEQ_LEN, 2*N_FEATURES) — features + missing concatenados
    - delta_t: (SEQ_LEN - 1,) — intervalos entre ventanas
    - y: (1,) — tiempo restante real
    - patient_id: str
    """

    def __init__(self, sequences: List[Dict]):
        self.sequences = sequences

    def __len__(self):
        return len(self.sequences)

    def __getitem__(self, idx):
        seq = self.sequences[idx]
        wf = seq["window_feats"]          # (4, 8)
        wm = seq["window_missing"]        # (4, 8)
        x = np.concatenate([wf, wm], axis=-1).astype(np.float32)  # (4, 16)

        return {
            "x": torch.from_numpy(x),
            "delta_t": torch.from_numpy(seq["delta_ts"]),
            "y": torch.tensor(seq["log_time_remaining"], dtype=torch.float32),
            "landmark_t": torch.tensor(seq["landmark_t"], dtype=torch.float32),
            "patient_id": seq["patient_id"],
        }


def _process_one_patient(args):
    """
    Worker para multiprocessing: procesa un archivo y devuelve sus secuencias.
    args = (filepath, patient_id, extubation_time)
    """
    f, pid, ext_time = args
    try:
        df = pd.read_parquet(f, engine="fastparquet")
        # Verificar que al menos MIN_FEATURES_PER_WINDOW columnas existen
        present = sum(1 for c in ALL_FEATURES if c in df.columns and df[c].notna().sum() > 0)
        if present < MIN_FEATURES_PER_WINDOW:
            return []
        seqs = build_sequences_from_patient(df, ext_time, pid)
        return seqs
    except Exception:
        return []


def _seqs_to_cache(sequences, prefix):
    """Guarda secuencias a disco en formato numpy (.npz) + metadata parquet."""
    if not sequences:
        return
    n = len(sequences)
    # Arrays compactos
    feats   = np.zeros((n, SEQ_LEN, len(ALL_FEATURES)), dtype=np.float32)
    missing = np.zeros((n, SEQ_LEN, len(ALL_FEATURES)), dtype=np.float32)
    dts     = np.zeros((n, SEQ_LEN - 1), dtype=np.float32)
    targets = np.zeros(n, dtype=np.float32)
    pids    = [""] * n
    landmarks_t = np.zeros(n, dtype=np.float32)

    for i, s in enumerate(sequences):
        feats[i] = s["window_feats"]
        missing[i] = s["window_missing"]
        dts[i] = s["delta_ts"]
        targets[i] = s["log_time_remaining"]
        pids[i] = s["patient_id"]
        landmarks_t[i] = s["landmark_t"]

    np.savez_compressed(f"{prefix}_arrays.npz", feats=feats, missing=missing, dts=dts, targets=targets)
    meta = pd.DataFrame({"patient_id": pids, "landmark_t": landmarks_t})
    meta.to_parquet(f"{prefix}_meta.parquet", engine="fastparquet")


def _seqs_from_cache(prefix):
    """Carga secuencias desde caché numpy."""
    if not os.path.exists(f"{prefix}_arrays.npz"):
        return []
    data = np.load(f"{prefix}_arrays.npz")
    meta = pd.read_parquet(f"{prefix}_meta.parquet", engine="fastparquet")
    feats = data["feats"]
    missing = data["missing"]
    dts = data["dts"]
    targets = data["targets"]
    sequences = []
    for i in range(len(meta)):
        sequences.append({
            "patient_id": str(meta.iloc[i]["patient_id"]),
            "landmark_t": float(meta.iloc[i]["landmark_t"]),
            "window_feats": feats[i],
            "window_missing": missing[i],
            "delta_ts": dts[i],
            "log_time_remaining": np.float32(targets[i]),
        })
    data.close()
    return sequences


def build_datasets(
    val_split: float = 0.15,
    random_seed: int = RANDOM_SEED,
    n_workers: int = None,
) -> Tuple[ExtubationDataset, ExtubationDataset, ExtubationDataset]:
    """
    Construye train/val/test datasets con split por paciente.
    Usa multiprocessing para acelerar el build.

    Returns:
        train_ds, val_ds, test_ds
    """
    if n_workers is None:
        n_workers = 0  # 0 = secuencial (evita problemas Windows multiprocessing)

    import time

    # ── Caché: si ya existe, cargar directo ──────────────────────────
    if os.path.exists(CACHE_META):
        print("=" * 60)
        print("CACHE ENCONTRADO — cargando datasets desde disco...")
        print(f"  Cache key: {CACHE_KEY}")
        t0 = time.time()
        train_seqs = _seqs_from_cache(CACHE_TRAIN.replace(".parquet", ""))
        val_seqs   = _seqs_from_cache(CACHE_VAL.replace(".parquet", ""))
        test_seqs  = _seqs_from_cache(CACHE_TEST.replace(".parquet", ""))
        print(f"  Train: {len(train_seqs)} seqs, Val: {len(val_seqs)} seqs, Test: {len(test_seqs)} seqs")
        print(f"  Cargado en {time.time()-t0:.1f}s")
        print("=" * 60)
        return (
            ExtubationDataset(train_seqs),
            ExtubationDataset(val_seqs),
            ExtubationDataset(test_seqs) if test_seqs else None,
        )

    print("=" * 60)
    print("Construyendo datasets para Escalón 2...")
    print(f"  Workers: {n_workers}")
    print("=" * 60)

    # Cargar supervivencia
    print("  [1/5] Cargando supervivencia...", flush=True)
    surv = _load_survival()
    surv_eicu = surv[surv["cohort"] == "eicu"].set_index("patient_id")
    surv_mimic = surv[surv["cohort"] == "mimic"].set_index("patient_id")
    print(f"  [1/5] OK: eICU={len(surv_eicu)}, MIMIC={len(surv_mimic)}", flush=True)

    # ── eICU: train + val ──
    eicu_files = _list_patient_files("eicu")
    total = len(eicu_files)
    print(f"  [2/5] eICU numerics files: {total}", flush=True)

    # Pre-filtro rápido de schemas: al menos MIN_FEATURES_PER_WINDOW columnas
    # Usamos fastparquet en vez de pyarrow (evita crash con PyTorch nightly + RTX 5080)
    from fastparquet import ParquetFile
    print("  Pre-filtrando schemas (min {} features)...".format(MIN_FEATURES_PER_WINDOW), flush=True)
    valid_files = []
    skipped_low_feat = 0
    skipped_nan_ext = 0
    for f in eicu_files:
        pid = os.path.basename(f).replace(".parquet", "")
        if pid not in surv_eicu.index:
            continue
        try:
            pf = ParquetFile(f)
            schema_names = pf.columns
            n_present = sum(1 for c in ALL_FEATURES if c in schema_names)
            if n_present >= MIN_FEATURES_PER_WINDOW:
                ext_time = surv_eicu.loc[pid, "extubation_time_hours"]
                if isinstance(ext_time, pd.Series):
                    ext_time = ext_time.iloc[0]
                if not np.isfinite(ext_time):
                    skipped_nan_ext += 1
                    continue
                valid_files.append((f, pid, ext_time))
            else:
                skipped_low_feat += 1
        except Exception:
            skipped_low_feat += 1

    print(f"  {len(valid_files)}/{total} archivos pasan el pre-filtro "
          f"({skipped_low_feat} con <{MIN_FEATURES_PER_WINDOW} features, "
          f"{skipped_nan_ext} con ext_time NaN)", flush=True)

    # Procesar (paralelo si n_workers > 0, si no secuencial)
    t0 = time.time()
    eicu_sequences = []

    if n_workers > 0:
        with Pool(processes=n_workers) as pool:
            for i, seqs in enumerate(pool.imap_unordered(_process_one_patient, valid_files, chunksize=50)):
                eicu_sequences.extend(seqs)
                if (i + 1) % 500 == 0:
                    elapsed = time.time() - t0
                    rate = (i + 1) / elapsed
                    eta = (len(valid_files) - (i + 1)) / rate / 60
                    print(f"  {i+1}/{len(valid_files)} pacientes procesados "
                          f"({len(eicu_sequences)} seqs, {rate:.1f} pac/s, ETA {eta:.0f} min)", flush=True)
    else:
        for i, args in enumerate(valid_files):
            seqs = _process_one_patient(args)
            eicu_sequences.extend(seqs)
            if (i + 1) % 500 == 0:
                elapsed = time.time() - t0
                rate = (i + 1) / elapsed
                eta = (len(valid_files) - (i + 1)) / rate / 60
                print(f"  {i+1}/{len(valid_files)} pacientes procesados "
                      f"({len(eicu_sequences)} seqs, {rate:.1f} pac/s, ETA {eta:.0f} min)", flush=True)

    elapsed = time.time() - t0
    print(f"  eICU build completado en {elapsed/60:.1f} min "
          f"({len(valid_files)/elapsed:.1f} pac/s)", flush=True)

    print(f"  eICU: {len(eicu_sequences)} secuencias generadas de "
          f"{len(set(s['patient_id'] for s in eicu_sequences))} pacientes")

    # Split por paciente (train/val)
    all_pids = list(set(s["patient_id"] for s in eicu_sequences))
    rng = np.random.RandomState(random_seed)
    rng.shuffle(all_pids)
    n_val = max(1, int(len(all_pids) * val_split))
    val_pids = set(all_pids[:n_val])
    train_pids = set(all_pids[n_val:])

    train_seqs = [s for s in eicu_sequences if s["patient_id"] in train_pids]
    val_seqs = [s for s in eicu_sequences if s["patient_id"] in val_pids]

    print(f"  Train pacientes: {len(train_pids)}, secuencias: {len(train_seqs)}")
    print(f"  Val   pacientes: {len(val_pids)}, secuencias: {len(val_seqs)}")

    # ── MIMIC: test ──
    mimic_files = _list_patient_files("mimic")
    mimic_sequences = []
    skipped_mimic = 0
    skipped_mimic_nan = 0

    for f in mimic_files:
        pid = os.path.basename(f).replace(".parquet", "")
        if pid not in surv_mimic.index:
            skipped_mimic += 1
            continue

        try:
            df = pd.read_parquet(f, engine="fastparquet")
        except Exception:
            continue

        ext_time = surv_mimic.loc[pid, "extubation_time_hours"]
        if isinstance(ext_time, pd.Series):
            ext_time = ext_time.iloc[0]

        if not np.isfinite(ext_time):
            skipped_mimic_nan += 1
            continue

        seqs = build_sequences_from_patient(df, ext_time, pid)
        mimic_sequences.extend(seqs)

    print(f"\n  MIMIC: {len(mimic_files)} archivos, "
          f"{skipped_mimic} sin supervivencia, {skipped_mimic_nan} con ext_time NaN")
    print(f"    → {len(mimic_sequences)} secuencias de "
          f"{len(set(s['patient_id'] for s in mimic_sequences))} pacientes")

    train_ds = ExtubationDataset(train_seqs)
    val_ds = ExtubationDataset(val_seqs)
    test_ds = ExtubationDataset(mimic_sequences) if mimic_sequences else None

    # ── Guardar caché ─────────────────────────────────────────────────
    os.makedirs(CACHE_DIR, exist_ok=True)
    print(f"\n  Guardando caché en {CACHE_DIR}...", flush=True)
    _t0 = time.time()
    _seqs_to_cache(train_seqs, CACHE_TRAIN.replace(".parquet", ""))
    _seqs_to_cache(val_seqs, CACHE_VAL.replace(".parquet", ""))
    if mimic_sequences:
        _seqs_to_cache(mimic_sequences, CACHE_TEST.replace(".parquet", ""))
    with open(CACHE_META, "w") as f:
        f.write(f"cache_key={CACHE_KEY}\ntrain={len(train_seqs)}\nval={len(val_seqs)}\ntest={len(mimic_sequences)}\n")
    print(f"  Caché guardado en {time.time()-_t0:.0f}s (key={CACHE_KEY}).", flush=True)

    return train_ds, val_ds, test_ds
