"""
io/writers.py
=============
Escritura de todos los artefactos de salida de la Etapa 0.

  - Tablas parquet (survival, landmarks, attempts, availability, outcome_vars)
  - Ventanas de waveform por landmark (NPZ)
  - Máscaras SQI por landmark (NPZ)
  - Reporte de QC (HTML/texto)
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


def write_parquet(df: pd.DataFrame, path: Path, description: str = "") -> None:
    """Escribe un DataFrame como Parquet con compresión Snappy."""
    if df.empty:
        logger.warning("[writers] %s está vacío; no se escribe parquet en %s",
                       description or "DataFrame", path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, str(path), compression="snappy")
    logger.info("[writers] %s → %s (%d filas, %.1f KB)",
                description or path.stem, path, len(df), path.stat().st_size / 1024)


def write_waveform_window(
    path: Path,
    ecg: np.ndarray,
    ppg: np.ndarray,
    abp: np.ndarray,
    t_start_unix: float,
    fs: float,
) -> None:
    """Escribe una ventana de waveform por landmark como NPZ."""
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        str(path),
        ecg=ecg.astype(np.float32),
        ppg=ppg.astype(np.float32),
        abp=abp.astype(np.float32),
        t_start_unix=np.array(t_start_unix, dtype=np.float64),
        fs=np.array(fs, dtype=np.float32),
    )


def write_sqi_mask(
    path: Path,
    ecg_mask: np.ndarray,
    ppg_mask: np.ndarray,
    abp_mask: np.ndarray,
    ecg_sqi: np.ndarray,
    ppg_sqi: np.ndarray,
    abp_sqi: np.ndarray,
    fs: float,
) -> None:
    """Escribe la máscara SQI por landmark como NPZ."""
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        str(path),
        ecg_mask=ecg_mask.astype(bool),
        ppg_mask=ppg_mask.astype(bool),
        abp_mask=abp_mask.astype(bool),
        ecg_sqi_values=ecg_sqi.astype(np.float32),
        ppg_sqi_values=ppg_sqi.astype(np.float32),
        abp_sqi_values=abp_sqi.astype(np.float32),
        fs=np.array(fs, dtype=np.float32),
    )


def write_all_tables(
    output_dir: Path,
    dataset_version: str,
    survival_tables: dict[str, pd.DataFrame],
    landmarks_df: pd.DataFrame,
    channel_availability_df: pd.DataFrame,
    outcome_variables_df: pd.DataFrame,
) -> None:
    """
    Escribe todas las tablas parquet en el directorio de salida versionado.

    Estructura de salida:
        output_dir/
          {dataset_version}/
            survival_48h.parquet
            survival_72h.parquet
            extubation_attempts.parquet
            landmarks_index.parquet
            channel_availability.parquet
            outcome_variables.parquet
    """
    version_dir = output_dir / dataset_version
    version_dir.mkdir(parents=True, exist_ok=True)

    # Survival tables (una por ventana de fallo + attempts)
    for key, df in survival_tables.items():
        fname = f"{key}.parquet"
        write_parquet(df, version_dir / fname, description=key)

    # Landmarks
    write_parquet(landmarks_df, version_dir / "landmarks_index.parquet",
                  description="landmarks_index")

    # Disponibilidad de canal
    write_parquet(channel_availability_df, version_dir / "channel_availability.parquet",
                  description="channel_availability")

    # Outcome variables (separadas de las features de entrada)
    write_parquet(outcome_variables_df, version_dir / "outcome_variables.parquet",
                  description="outcome_variables")

    logger.info("[writers] Todas las tablas escritas en %s", version_dir)
