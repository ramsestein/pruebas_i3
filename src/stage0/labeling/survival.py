"""
labeling/survival.py
====================
Lógica del estimando: clasificación de intentos de extubación y construcción
de las tablas de supervivencia.

ESTIMANDO
---------
Evento primario: primera extubación EXITOSA = el primer intento de extubación
que NO va seguido de reintubación dentro de la ventana de fallo configurada.

Clasificación de intentos:
  - Un intento es EXITOSO si, tras él, no hay reintubación en < failure_window_h
  - Un intento es FALLIDO si hay reintubación dentro de failure_window_h
  - El reloj corre desde t0 (inicio de VM) sin interrupción, incluso durante
    los intentos fallidos

Tiempo hasta el evento:
  - = tiempo desde t0 hasta la extubación exitosa (fin del archivo por construcción)

Outputs:
  1. survival_Nh.parquet  (una fila por paciente, una tabla por ventana de fallo)
  2. extubation_attempts.parquet  (una fila por intento, todas las ventanas)
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from ..adapters.base import ClinicalEvents, ExtubationAttempt

logger = logging.getLogger(__name__)


# ── Clasificación de intentos ─────────────────────────────────────────────────

def classify_attempts(
    events: ClinicalEvents,
    failure_window_h: float,
) -> list[ExtubationAttempt]:
    """
    Re-clasifica los intentos de extubación aplicando la ventana de fallo.

    Regla:
      - Si después de un intento hay reintubación dentro de failure_window_h → FAILURE
      - Si no hay reintubación en failure_window_h (o es el último intento confirmado
        como exitoso) → SUCCESS

    Args:
        events: ClinicalEvents del paciente (puede tener attempts pre-clasificados)
        failure_window_h: Ventana de fallo en horas

    Returns:
        Lista de ExtubationAttempt con outcome correcto para esta ventana.
    """
    attempts = events.extubation_attempts
    if not attempts:
        return []

    classified: list[ExtubationAttempt] = []

    for i, attempt in enumerate(attempts):
        # Si hay reintubación registrada y ocurre dentro de la ventana → fallo
        if attempt.reintubation_time_rel_hours is not None:
            time_to_reintub = (
                attempt.reintubation_time_rel_hours - attempt.time_rel_hours
            )
            if time_to_reintub <= failure_window_h:
                # Fallo confirmado con esta ventana
                classified.append(ExtubationAttempt(
                    attempt_index=attempt.attempt_index,
                    time_rel_hours=attempt.time_rel_hours,
                    outcome="failure",
                    reintubation_time_rel_hours=attempt.reintubation_time_rel_hours,
                ))
                continue
            else:
                # La reintubación ocurrió después de la ventana → este intento
                # se considera exitoso (el paciente estuvo extubado > failure_window_h)
                classified.append(ExtubationAttempt(
                    attempt_index=attempt.attempt_index,
                    time_rel_hours=attempt.time_rel_hours,
                    outcome="success",
                    reintubation_time_rel_hours=None,
                ))
                continue

        # Sin reintubación registrada
        if events.extubation_confirmed:
            # Es el intento exitoso final (confirmado por construcción del dataset)
            classified.append(ExtubationAttempt(
                attempt_index=attempt.attempt_index,
                time_rel_hours=attempt.time_rel_hours,
                outcome="success",
                reintubation_time_rel_hours=None,
            ))
        else:
            # censored_no_extubation: no sabemos si fue exitoso
            classified.append(ExtubationAttempt(
                attempt_index=attempt.attempt_index,
                time_rel_hours=attempt.time_rel_hours,
                outcome="unknown",
                reintubation_time_rel_hours=None,
            ))

    return classified


def find_first_success(
    classified_attempts: list[ExtubationAttempt],
) -> Optional[ExtubationAttempt]:
    """Devuelve el primer intento con outcome == 'success', o None."""
    for a in classified_attempts:
        if a.outcome == "success":
            return a
    return None


# ── Construcción de filas de supervivencia ────────────────────────────────────

def build_survival_row(
    events: ClinicalEvents,
    failure_window_h: float,
    dataset_version: str,
) -> dict:
    """
    Construye una fila de la tabla de supervivencia para un paciente
    y una ventana de fallo dada.

    Args:
        events: Eventos clínicos del paciente
        failure_window_h: Ventana de fallo (48 o 72 horas)
        dataset_version: Hash de la config para trazabilidad

    Returns:
        Diccionario con los campos de la tabla survival.
    """
    pid = events.patient_id

    # Caso censored (edge case MIMIC: muerte en vent_end)
    if events.censored_no_extubation:
        return {
            "patient_id": pid,
            "cohort": events.cohort,
            "t0_unix": events.t0_unix,
            "extubation_time_hours": np.nan,
            "event_type": "censored_no_extubation",
            "failure_window_hours": failure_window_h,
            "n_failed_attempts": 0,
            "first_attempt_time_hours": np.nan,
            "selection_bias_note": "clinical_table",
            "dataset_version": dataset_version,
        }

    # Re-clasificar intentos con esta ventana de fallo
    classified = classify_attempts(events, failure_window_h)
    first_success = find_first_success(classified)

    if first_success is None:
        # No se encontró extubación exitosa con esta ventana
        # (no debería ocurrir en Clínic/VitalDB; posible en MIMIC si todos
        # los intentos fallaron dentro de la ventana y no hay confirmación)
        logger.warning(
            "[survival] patient=%s: sin extubación exitosa con ventana=%.0fh; "
            "marcando como censored",
            pid, failure_window_h,
        )
        return {
            "patient_id": pid,
            "cohort": events.cohort,
            "t0_unix": events.t0_unix,
            "extubation_time_hours": events.record_end_hours,
            "event_type": "censored_no_extubation",
            "failure_window_hours": failure_window_h,
            "n_failed_attempts": sum(1 for a in classified if a.outcome == "failure"),
            "first_attempt_time_hours": (
                classified[0].time_rel_hours if classified else np.nan
            ),
            "selection_bias_note": "clinical_table",
            "dataset_version": dataset_version,
        }

    n_failed = sum(
        1 for a in classified
        if a.outcome == "failure" and a.attempt_index < first_success.attempt_index
    )
    first_attempt_h = classified[0].time_rel_hours if classified else np.nan

    # Nota de sesgo de selección
    if events.cohort in ("clinic", "vitaldb"):
        bias_note = "by_construction"
    else:
        bias_note = "clinical_table"

    return {
        "patient_id": pid,
        "cohort": events.cohort,
        "t0_unix": events.t0_unix,
        "extubation_time_hours": first_success.time_rel_hours,
        "event_type": "successful_extubation",
        "failure_window_hours": failure_window_h,
        "n_failed_attempts": n_failed,
        "first_attempt_time_hours": first_attempt_h,
        "selection_bias_note": bias_note,
        "dataset_version": dataset_version,
    }


def build_attempt_rows(
    events: ClinicalEvents,
    failure_window_h: float,
) -> list[dict]:
    """
    Construye las filas de la tabla auxiliar de intentos de extubación
    para un paciente y una ventana de fallo.
    """
    classified = classify_attempts(events, failure_window_h)
    rows = []
    for a in classified:
        rows.append({
            "patient_id": events.patient_id,
            "cohort": events.cohort,
            "failure_window_hours": failure_window_h,
            "attempt_index": a.attempt_index,
            "attempt_time_hours": a.time_rel_hours,
            "outcome": a.outcome,
            "reintubation_time_hours": (
                a.reintubation_time_rel_hours
                if a.reintubation_time_rel_hours is not None
                else np.nan
            ),
            "time_to_reintubation_hours": (
                a.reintubation_time_rel_hours - a.time_rel_hours
                if a.reintubation_time_rel_hours is not None
                else np.nan
            ),
        })
    return rows


# ── Construcción de tablas completas ──────────────────────────────────────────

def build_survival_tables(
    all_events: list[ClinicalEvents],
    failure_windows_h: list[float],
    dataset_version: str,
) -> dict[str, pd.DataFrame]:
    """
    Construye todas las tablas de supervivencia a partir de la lista de eventos.

    Args:
        all_events: Lista de ClinicalEvents de todos los pacientes
        failure_windows_h: Lista de ventanas de fallo (ej. [48.0, 72.0])
        dataset_version: Hash de la config

    Returns:
        Diccionario con claves:
          "survival_48h", "survival_72h", ... (una por ventana)
          "extubation_attempts"  (una sola tabla con attempts de todas las ventanas)
    """
    survival_dfs: dict[str, pd.DataFrame] = {}
    all_attempt_rows: list[dict] = []

    for fwh in failure_windows_h:
        key = f"survival_{int(fwh)}h"
        rows = []
        for events in all_events:
            try:
                row = build_survival_row(events, fwh, dataset_version)
                rows.append(row)
            except Exception as e:
                logger.error(
                    "[survival] patient=%s ventana=%.0fh error: %s",
                    events.patient_id, fwh, e,
                )

        df = pd.DataFrame(rows)
        if not df.empty:
            df["event_type"] = df["event_type"].astype("category")
            df["cohort"] = df["cohort"].astype("category")
            df["failure_window_hours"] = df["failure_window_hours"].astype(np.float32)
            df["n_failed_attempts"] = df["n_failed_attempts"].astype(np.int16)
            df["t0_unix"] = df["t0_unix"].astype(np.float64)
            df["extubation_time_hours"] = df["extubation_time_hours"].astype(np.float64)
            df["first_attempt_time_hours"] = df["first_attempt_time_hours"].astype(np.float64)
        survival_dfs[key] = df

        # Attempts (se acumulan de todas las ventanas)
        for events in all_events:
            try:
                all_attempt_rows.extend(build_attempt_rows(events, fwh))
            except Exception as e:
                logger.error(
                    "[survival] attempts patient=%s ventana=%.0fh error: %s",
                    events.patient_id, fwh, e,
                )

    # Tabla de intentos
    df_attempts = pd.DataFrame(all_attempt_rows)
    if not df_attempts.empty:
        df_attempts["outcome"] = df_attempts["outcome"].astype("category")
        df_attempts["cohort"] = df_attempts["cohort"].astype("category")
        df_attempts["failure_window_hours"] = df_attempts["failure_window_hours"].astype(np.float32)
        df_attempts["attempt_time_hours"] = df_attempts["attempt_time_hours"].astype(np.float64)
        df_attempts["reintubation_time_hours"] = df_attempts["reintubation_time_hours"].astype(np.float64)
        df_attempts["time_to_reintubation_hours"] = df_attempts["time_to_reintubation_hours"].astype(np.float64)
    survival_dfs["extubation_attempts"] = df_attempts

    # Resumen de log
    for key, df in survival_dfs.items():
        if "survival" in key and not df.empty:
            counts = df["event_type"].value_counts().to_dict()
            logger.info("[survival] %s → %d pacientes | %s", key, len(df), counts)

    return survival_dfs
