"""
harmonize/availability.py
=========================
Genera la matriz de disponibilidad de canal por cohorte y por paciente.

Salida: channel_availability.parquet
  Una fila por paciente, columna booleana por canal.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..adapters.base import NumericsRecord, WaveformRecord

logger = logging.getLogger(__name__)

# Canales a reportar en la matriz
WAVEFORM_CHANNELS = ["ECG", "PPG", "ABP"]
NUMERIC_CHANNELS = [
    "HR", "SBP", "DBP", "MAP", "SpO2", "RR",
    "FiO2", "PEEP", "TV", "MV", "PIP",
    "vasopressor_norepinephrine", "vasopressor_epinephrine",
    "vasopressor_dopamine", "lactate",
    "SOFA_respiratory", "SOFA_cardiovascular", "SOFA_total",
]


def compute_availability_row(
    patient_id: str,
    cohort: str,
    waveforms: dict[str, WaveformRecord],
    numerics: NumericsRecord,
) -> dict:
    """
    Computa la fila de disponibilidad para un paciente.

    Un canal waveform es "disponible" si WaveformRecord.available=True y n_samples > 0.
    Un canal numérico es "disponible" si la columna existe en el DataFrame
    y tiene al menos un valor no-NaN.
    """
    row: dict = {"patient_id": patient_id, "cohort": cohort}

    # Waveforms
    for ch in WAVEFORM_CHANNELS:
        col_name = f"{ch.lower()}_waveform"
        rec = waveforms.get(ch)
        if rec is None or not rec.available or rec.n_samples == 0:
            row[col_name] = False
        else:
            row[col_name] = True

    # Numéricos
    for ch in NUMERIC_CHANNELS:
        if ch in numerics.data.columns:
            # "Disponible" si hay al menos un valor no-NaN
            row[ch] = bool(numerics.data[ch].notna().any())
        else:
            row[ch] = False

    return row


def build_availability_table(
    all_rows: list[dict],
) -> pd.DataFrame:
    """
    Construye el DataFrame de disponibilidad a partir de la lista de filas.
    """
    df = pd.DataFrame(all_rows)

    # Tipos
    bool_cols = [c for c in df.columns if c not in ("patient_id", "cohort")]
    for col in bool_cols:
        df[col] = df[col].astype(bool)

    df["cohort"] = df["cohort"].astype("category")

    logger.info(
        "[availability] Matriz de disponibilidad: %d pacientes × %d canales",
        len(df), len(bool_cols),
    )
    for col in bool_cols:
        pct = df[col].mean() * 100
        logger.info("  %-40s: %.1f%% disponible", col, pct)

    return df
