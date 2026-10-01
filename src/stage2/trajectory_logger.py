"""
Logger de trayectorias por paciente y landmark.

Guarda para cada paciente de test la trayectoria completa:
{patient_id, landmark_t, mu, sigma, median_R_t, mean_R_t,
 intervalos 80%/90%, time_remaining_true, total_duration}
"""

import os
import numpy as np
import pandas as pd
import torch
from typing import List, Dict

from src.stage2.config import TRAJECTORY_DIR


def _params_to_predictions(mu, log_sigma):
    """
    Convierte parámetros de log-normal a predicciones interpretables.

    Args:
        mu: (n,) numpy
        log_sigma: (n,) numpy

    Returns:
        dict con median, mean, interval_80_lower/upper, interval_90_lower/upper
    """
    sigma = np.exp(log_sigma)
    median = np.exp(mu)
    mean = np.exp(mu + sigma**2 / 2)

    # Cuantiles de la normal estándar
    z_80 = 1.28155
    z_90 = 1.64485

    return {
        "median_R_t": median.astype(np.float32),
        "mean_R_t": mean.astype(np.float32),
        "interval_80_lower": np.exp(mu - z_80 * sigma).astype(np.float32),
        "interval_80_upper": np.exp(mu + z_80 * sigma).astype(np.float32),
        "interval_90_lower": np.exp(mu - z_90 * sigma).astype(np.float32),
        "interval_90_upper": np.exp(mu + z_90 * sigma).astype(np.float32),
    }


def log_trajectory(
    patient_id: str,
    landmark_t: float,
    mu: float,
    log_sigma: float,
    log_time_remaining_true: float,
    total_duration: float,
) -> Dict:
    """Construye un registro de trayectoria para un landmark.
    mu y log_time_remaining_true están en log-space (log-horas)."""
    sigma = np.exp(log_sigma)
    median_h = np.exp(mu)
    mean_h = np.exp(mu + sigma**2 / 2)
    time_remaining_true_h = np.exp(log_time_remaining_true)

    z_80 = 1.28155
    z_90 = 1.64485

    return {
        "patient_id": patient_id,
        "landmark_t": np.float32(landmark_t),
        "mu": np.float32(mu),
        "sigma": np.float32(sigma),
        "median_R_t": np.float32(median_h),
        "mean_R_t": np.float32(mean_h),
        "interval_80_lower": np.float32(np.exp(mu - z_80 * sigma)),
        "interval_80_upper": np.float32(np.exp(mu + z_80 * sigma)),
        "interval_90_lower": np.float32(np.exp(mu - z_90 * sigma)),
        "interval_90_upper": np.float32(np.exp(mu + z_90 * sigma)),
        "time_remaining_true": np.float32(time_remaining_true_h),
        "total_duration": np.float32(total_duration),
    }


def save_trajectories(records: List[Dict], cohort_name: str):
    """Guarda las trayectorias a disco."""
    os.makedirs(TRAJECTORY_DIR, exist_ok=True)
    df = pd.DataFrame(records)
    path = os.path.join(TRAJECTORY_DIR, f"trajectories_{cohort_name}.parquet")
    df.to_parquet(path, index=False)
    print(f"  Trayectorias guardadas: {path} ({len(df)} landmarks de "
          f"{df['patient_id'].nunique()} pacientes)")
    return df
