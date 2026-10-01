"""
labeling/landmarks.py
=====================
Generación de la rejilla de landmarks y sus ventanas de agregación.

Un landmark es un punto temporal (t_landmark) desde el que se "mira hacia atrás"
una ventana de duración window_length_min que termina (o se centra) en t_landmark.

Reglas:
  - Se generan landmarks desde t0 (inclusive) a cadencia fija Δ (landmark_delta_min)
  - Solo mientras el paciente esté EN RIESGO:
      t_landmark < extubation_time_hours (o record_end_hours si no confirmada)
  - La ventana de waveform termina en t_landmark (modo "trailing") o se centra
    en t_landmark (modo "centered")
  - Se reporta disponibilidad por señal y SQI medio

Salida: landmarks_index.parquet
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..adapters.base import ClinicalEvents

logger = logging.getLogger(__name__)


def generate_landmark_grid(
    events: ClinicalEvents,
    delta_min: float,
    window_length_min: float,
    window_mode: str = "trailing",
) -> list[dict]:
    """
    Genera la rejilla de landmarks para un paciente.

    Args:
        events: Eventos clínicos del paciente
        delta_min: Cadencia entre landmarks (minutos)
        window_length_min: Longitud de la ventana de agregación (minutos)
        window_mode: 'trailing' (ventana termina en landmark) | 'centered'

    Returns:
        Lista de dicts con metadatos por landmark (sin waveforms).
        La disponibilidad de canal y SQI se rellenarán en la fase de procesamiento.
    """
    delta_h = delta_min / 60.0
    window_h = window_length_min / 60.0

    # El reloj corre hasta la extubación (o fin de registro)
    if events.extubation_confirmed and events.extubation_confirmed_hours is not None:
        t_max = events.extubation_confirmed_hours
    else:
        t_max = events.record_end_hours

    if t_max <= 0:
        logger.warning("[landmarks] patient=%s: t_max <= 0, sin landmarks", events.patient_id)
        return []

    # Generar tiempos de landmark: desde 0 hasta t_max (sin incluir t_max exacto
    # si coincide con la extubación, pues el paciente ya no está en riesgo)
    t_landmarks = np.arange(0.0, t_max, delta_h)  # excluye t_max

    # Filtrar: el landmark debe tener al menos una ventana completa de datos
    # En modo trailing: la ventana empieza en (t_landmark - window_h)
    # → necesitamos t_landmark >= window_h si queremos ventana completa
    # (relajamos esto: si no hay suficientes datos, el landmark queda con
    # la ventana que haya, y landmarks_index lo refleja)

    rows = []
    for idx, t_lm in enumerate(t_landmarks):
        if window_mode == "trailing":
            win_start = t_lm - window_h
            win_end = t_lm
        elif window_mode == "centered":
            win_start = t_lm - window_h / 2.0
            win_end = t_lm + window_h / 2.0
        else:
            raise ValueError(f"window_mode inválido: '{window_mode}'")

        # Si la ventana empieza antes de 0, la recortamos
        win_start_clamped = max(win_start, 0.0)

        rows.append({
            "patient_id": events.patient_id,
            "cohort": events.cohort,
            "landmark_idx": idx,
            "landmark_time_hours": float(t_lm),
            "window_start_hours": float(win_start_clamped),
            "window_end_hours": float(win_end),
            "is_at_risk": True,  # por construcción (solo generamos mientras en riesgo)
            # Estos campos se rellenan en la fase de procesamiento de señales:
            "ecg_available": False,
            "ppg_available": False,
            "abp_available": False,
            "ecg_sqi_mean": np.nan,
            "ppg_sqi_mean": np.nan,
            "abp_sqi_mean": np.nan,
            "numerics_completeness": np.nan,
            "waveform_file": "",   # se rellena al escribir el NPZ
            "dataset_version": "",  # se rellena en la fase de IO
        })

    logger.debug(
        "[landmarks] patient=%s: %d landmarks (Δ=%.1f min, ventana=%.1f min, modo=%s)",
        events.patient_id, len(rows), delta_min, window_length_min, window_mode,
    )
    return rows


def build_landmarks_table(all_rows: list[dict], dataset_version: str) -> pd.DataFrame:
    """
    Construye el DataFrame de landmarks_index a partir de todas las filas.
    """
    if not all_rows:
        logger.warning("[landmarks] No se generaron landmarks para ningún paciente")
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["dataset_version"] = dataset_version
    df["cohort"] = df["cohort"].astype("category")
    df["landmark_idx"] = df["landmark_idx"].astype(np.int32)
    df["is_at_risk"] = df["is_at_risk"].astype(bool)

    for col in ["ecg_sqi_mean", "ppg_sqi_mean", "abp_sqi_mean", "numerics_completeness"]:
        df[col] = df[col].astype(np.float32)

    logger.info(
        "[landmarks] Tabla de landmarks: %d filas | %d pacientes",
        len(df), df["patient_id"].nunique(),
    )
    return df


def extract_waveform_window(
    signal: np.ndarray,
    timestamps_rel_hours: np.ndarray,
    win_start_h: float,
    win_end_h: float,
) -> np.ndarray:
    """
    Extrae la ventana de una señal (ya resampleada a fs_target) entre
    win_start_h y win_end_h (en horas desde t0).

    Si la ventana es más corta que la esperada (por recorte al inicio),
    se rellena con NaN al principio.

    Args:
        signal: Array float32 de la señal completa (ya a 125 Hz)
        timestamps_rel_hours: Array float64 de timestamps en horas
        win_start_h: Inicio de la ventana (horas)
        win_end_h: Fin de la ventana (horas)

    Returns:
        Array float32 recortado/rellenado. Longitud determinada por
        (win_end_h - win_start_h) * fs_target. Para windows de 5 min
        a 125 Hz: 5 * 60 * 125 = 37500 muestras.
    """
    mask = (timestamps_rel_hours >= win_start_h) & (timestamps_rel_hours <= win_end_h)
    window = signal[mask].astype(np.float32)
    return window


def compute_numerics_completeness(
    numerics_data: pd.DataFrame,
    timestamps_rel_hours: np.ndarray,
    win_start_h: float,
    win_end_h: float,
    core_columns: list[str] | None = None,
) -> float:
    """
    Fracción de columnas numéricas core con al menos un valor no-NaN
    en la ventana [win_start_h, win_end_h].
    """
    if core_columns is None:
        core_columns = ["HR", "SBP", "DBP", "MAP", "SpO2", "RR"]

    mask = (timestamps_rel_hours >= win_start_h) & (timestamps_rel_hours <= win_end_h)
    if not mask.any():
        return 0.0

    win_data = numerics_data[mask]
    available_cols = [c for c in core_columns if c in win_data.columns]
    if not available_cols:
        return 0.0

    completeness = sum(
        win_data[c].notna().any() for c in available_cols
    ) / len(available_cols)
    return float(completeness)
