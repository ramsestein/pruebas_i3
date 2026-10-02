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
from ...common.d5_events import d5_censor_for_window
from ...common.labels import (
    AttemptOutcome,
    assign_label,
    attempts_from_pairs,
    classify_attempt,
)

logger = logging.getLogger(__name__)


# ── Clasificación de intentos ─────────────────────────────────────────────────

def classify_attempts(
    events: ClinicalEvents,
    failure_window_h: float,
) -> list[ExtubationAttempt]:
    """Re-clasifica los intentos aplicando la ventana de fallo.

    Delega la decisión en ``src.common.labels.classify_attempt`` (etiquetador
    ÚNICO del proyecto; Fase 1 corrección 4).
    """
    attempts = events.extubation_attempts
    if not attempts:
        return []

    classified: list[ExtubationAttempt] = []
    for attempt in attempts:
        outcome = classify_attempt(
            AttemptOutcome(attempt.attempt_index, attempt.time_rel_hours,
                           attempt.reintubation_time_rel_hours),
            failure_window_h,
            extubation_confirmed=events.extubation_confirmed,
        )
        classified.append(ExtubationAttempt(
            attempt_index=attempt.attempt_index,
            time_rel_hours=attempt.time_rel_hours,
            outcome=outcome,
            reintubation_time_rel_hours=(
                attempt.reintubation_time_rel_hours if outcome == "failure" else None
            ),
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
    """Construye una fila de supervivencia delegando en el etiquetador común.

    Fase 1 corrección 4: la decisión (éxito / fallo / censura, incluida la
    censura D5 por traqueostomía o extubación terminal) la toma
    ``src.common.labels``. Esta función solo la traduce al esquema de la tabla
    de supervivencia.
    """
    pid = events.patient_id
    attempts = events.extubation_attempts
    pairs = [
        (a.time_rel_hours, a.reintubation_time_rel_hours) for a in attempts
    ]
    last_disconnect_h = pairs[-1][0] if pairs else None

    # D5: traqueostomía y extubación terminal, resueltas por ventana.
    decision = d5_censor_for_window(
        failure_window_h=failure_window_h,
        last_disconnect_h=last_disconnect_h,
        trach_time_h=events.trach_time_hours,
        trach_time_unknown=events.trach_time_unknown,
        death_time_h=events.death_time_hours,
        died_ventilated=events.died_ventilated,
    )

    lab = assign_label(
        attempts_from_pairs(pairs),
        obs_end_h=events.record_end_hours,
        failure_window_h=failure_window_h,
        censor_cause=decision.censor_cause,
        censor_time_h=decision.censor_time_h,
    )

    if lab.event_type == "successful_extubation":
        event_type = "successful_extubation"
    else:
        event_type = "censored_no_extubation"
        logger.debug(
            "[survival] patient=%s ventana=%.0fh censurado (%s)",
            pid, failure_window_h, lab.censor_cause,
        )

    return {
        "patient_id": pid,
        "cohort": events.cohort,
        "t0_unix": events.t0_unix,
        "extubation_time_hours": (
            lab.extubation_time_h if lab.extubation_time_h is not None else np.nan
        ),
        "event_type": event_type,
        "censor_cause": lab.censor_cause,
        "failure_window_hours": failure_window_h,
        "n_failed_attempts": lab.n_failed_attempts,
        "first_attempt_time_hours": (
            lab.first_attempt_h if lab.first_attempt_h is not None else np.nan
        ),
        "selection_bias_note": (
            "by_construction" if events.cohort in ("clinic", "vitaldb")
            else "clinical_table"
        ),
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
