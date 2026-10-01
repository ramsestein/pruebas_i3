"""
Extrae features desde las ventanas enriquecidas de MIMIC y genera
arrays numpy (X, y) listos para modelado.

Entrada: datasets/mimic3wdb/windows_10min_enriched/*.parquet
Salida:  datasets/mimic3wdb/mimic_enriched_features.npz

Estructura de salida:
  X: (n_windows, n_timesteps, n_features)  float32
  y: (n_windows,)                           int32
  feature_names: list[str]
  window_files: list[str]
  subject_ids: list[int]
"""
import os
import json
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.impute import SimpleImputer

# ── Paths ─────────────────────────────────────────────────────────────────────
WIN_DIR    = Path("datasets/mimic3wdb/windows_10min_enriched")
INDEX_PATH = Path("datasets/mimic3wdb/windows_enriched_index.json")
OUT_PATH   = Path("datasets/mimic3wdb/mimic_enriched_features.npz")

# Columnas a excluir siempre
DROP_COLS = {"Time"}


def main():
    if not INDEX_PATH.exists():
        print(f"ERROR: No existe {INDEX_PATH}")
        return

    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        index = json.load(f)

    windows_meta = index["windows"]
    n_total = len(windows_meta)
    print(f"Ventanas en indice: {n_total:,}")

    if n_total == 0:
        print("Sin ventanas para procesar")
        return

    # --- Primera pasada: detectar union de todas las columnas ---
    print("Detectando features (union de todas las ventanas)...")
    all_cols = set()
    n_timesteps = None
    for meta in windows_meta[:50]:  # sample rapido
        fpath = WIN_DIR / meta["window_file"]
        if fpath.exists():
            df = pd.read_parquet(fpath)
            all_cols.update(c for c in df.columns if c not in DROP_COLS)
            if n_timesteps is None:
                n_timesteps = len(df)

    feature_names = sorted(all_cols)
    n_features = len(feature_names)
    print(f"  Features: {n_features}, Timesteps: {n_timesteps}")

    # Pre-alloc
    X = np.empty((n_total, n_timesteps, n_features), dtype=np.float32)
    X.fill(np.nan)
    y = np.empty(n_total, dtype=np.int32)
    window_files = []
    subject_ids = []

    print(f"\nCargando {n_total:,} ventanas...")
    for i, meta in enumerate(windows_meta, 1):
        fpath = WIN_DIR / meta["window_file"]
        if not fpath.exists():
            print(f"  [{i}/{n_total}] FALTANTE: {meta['window_file']}")
            continue

        df = pd.read_parquet(fpath)
        # Para cada feature esperada, tomarla si existe, sino NaN
        for j, feat in enumerate(feature_names):
            if feat in df.columns:
                col_vals = df[feat].values.astype(np.float32)
                # Rellenar o truncar a n_timesteps
                n_avail = min(len(col_vals), n_timesteps)
                X[i - 1, :n_avail, j] = col_vals[:n_avail]

        y[i - 1] = int(meta["label"])
        window_files.append(meta["window_file"])
        subject_ids.append(int(meta["subject_id"]))

        if i % 1000 == 0 or i == n_total:
            print(f"  [{i}/{n_total}] cargadas")

    # --- Imputacion global (por feature) ---
    print("\nImputando NaNs (mediana por feature)...")
    n_nan_before = np.sum(~np.isfinite(X))
    # Aplanar a (n_total * n_timesteps, n_features) para imputar
    orig_shape = X.shape
    X_flat = X.reshape(-1, n_features)

    # Reemplazar inf por nan
    X_flat[~np.isfinite(X_flat)] = np.nan

    # Detectar features completamente NaN y eliminarlas
    nan_mask = np.isnan(X_flat).all(axis=0)
    n_all_nan = nan_mask.sum()
    if n_all_nan > 0:
        removed_names = [feature_names[i] for i in np.where(nan_mask)[0]]
        print(f"  Eliminando {n_all_nan} features completamente NaN: {removed_names}")
        X_flat = X_flat[:, ~nan_mask]
        feature_names = [f for i, f in enumerate(feature_names) if not nan_mask[i]]
        n_features = len(feature_names)

    imputer = SimpleImputer(strategy="median")
    X_flat = imputer.fit_transform(X_flat)
    X = X_flat.reshape(orig_shape[0], orig_shape[1], n_features)
    n_nan_after = np.sum(~np.isfinite(X))
    print(f"  NaNs: {n_nan_before:,} -> {n_nan_after:,}")

    # --- Estadisticas ---
    print(f"\nEstadisticas:")
    print(f"  Forma X: {X.shape}")
    print(f"  Labels:  {{0: {np.sum(y==0)}, 1: {np.sum(y==1)}}}")
    print(f"  Features: {feature_names[:10]}... ({len(feature_names)} total)")

    # --- Guardar ---
    print(f"\nGuardando en {OUT_PATH}...")
    np.savez_compressed(
        OUT_PATH,
        X=X,
        y=y,
        feature_names=np.array(feature_names, dtype=object),
        window_files=np.array(window_files, dtype=object),
        subject_ids=np.array(subject_ids, dtype=np.int32),
    )
    print(f"  OK ({OUT_PATH.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
