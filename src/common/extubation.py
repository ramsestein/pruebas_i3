"""
common/extubation.py
====================
Regla común de **extubación confirmada** (Fase 1.5, punto 0), válida para las
cuatro cohortes (Clínic, VitalDB, MIMIC y eICU).

Regla
-----
Un fin de ventilación solo es una **extubación** si va seguido de al menos
**1 h de observación sin ventilador**:

- Clínic / VitalDB: la observación es el **monitor** (HR/SpO2/ECG/PLETH) de la
  región de paciente; si el monitor termina con la ventilación no hay hora.
- MIMIC / eICU: la observación es la **estancia** (hasta ``OUTTIME`` /
  ``unitdischargeoffset``).

Si la ventilación termina sin esa hora de observación, el evento se **censura**
con su causa:

- ``transfer_ventilated``: la observación termina porque acaba la estancia
  (alta o traslado del paciente aún ventilado).
- ``death_at_vent``: el paciente fallece estando ventilado.
- ``end_of_record``: se acaban los datos (fin del monitor / del registro) sin
  extubación ni alta.

Esta función es **pura** y agnóstica de cohorte: los builders solo tienen que
aportar los tiempos (en la misma unidad, normalmente horas desde t0) y si la
estancia aporta o no una frontera dura (``stay_end_h``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# Mínimo de observación sin ventilador para confirmar una extubación (1 h).
MIN_OBSERVATION_TAIL_H: float = 1.0

_EPS: float = 1e-9

# Causas de censura (vocabulario único para las 4 cohortes).
CAUSE_TRANSFER: str = "transfer_ventilated"
CAUSE_DEATH: str = "death_at_vent"
CAUSE_END_OF_RECORD: str = "end_of_record"


@dataclass(frozen=True)
class ExtubationDecision:
    """Resultado de aplicar la regla de extubación confirmada a un evento."""
    is_extubation: bool
    censor_cause: Optional[str]          # None si ``is_extubation`` es True
    censor_time_h: Optional[float]
    tail_h: float                        # horas de observación tras el fin de VM
    reason: str                          # etiqueta legible de la decisión


def resolve_extubation(
    *,
    last_vent_end_h: float,
    observation_end_h: float,
    stay_end_h: Optional[float] = None,
    death_h: Optional[float] = None,
    died_ventilated: Optional[bool] = None,
    min_tail_h: float = MIN_OBSERVATION_TAIL_H,
) -> ExtubationDecision:
    """Decide si un fin de ventilación es una extubación confirmada.

    Parámetros (todos en la misma unidad temporal, típicamente horas desde t0)
    -------------------------------------------------------------------------
    last_vent_end_h:
        Fin del último intento de ventilación.
    observation_end_h:
        Fin de la observación **sin ventilador** (fin del monitor en
        Clínic/VitalDB; fin de la estancia en MIMIC/eICU).
    stay_end_h:
        Frontera dura de la estancia (alta de UCI) en MIMIC/eICU. ``None`` en
        Clínic/VitalDB (no existe tabla de estancia).
    death_h:
        Hora de fallecimiento conocida (tabla o señal), o ``None``.
    died_ventilated:
        Si el paciente falleció **estando ventilado**. Si es ``None`` se deduce
        comparando ``death_h`` con ``last_vent_end_h``.
    min_tail_h:
        Mínimo de observación sin ventilador (por defecto 1 h).

    Orden de decisión
    -----------------
    1. Muerte ventilado  → ``death_at_vent``.
    2. ``tail_h >= min_tail_h`` → extubación confirmada.
    3. Observación terminada con la estancia (``observation_end_h`` alcanza
       ``stay_end_h``) → ``transfer_ventilated``.
    4. En otro caso → ``end_of_record``.
    """
    for name, value in (("last_vent_end_h", last_vent_end_h),
                        ("observation_end_h", observation_end_h)):
        if value is None or not math.isfinite(float(value)):
            raise ValueError(f"{name} debe ser finito, recibido {value!r}")

    last_vent_end_h = float(last_vent_end_h)
    observation_end_h = float(observation_end_h)
    tail_h = observation_end_h - last_vent_end_h

    # 1) Muerte ventilado.
    if death_h is not None and math.isfinite(float(death_h)):
        death_h = float(death_h)
        dv = (
            died_ventilated
            if died_ventilated is not None
            else (death_h <= last_vent_end_h + _EPS)
        )
        if dv or death_h <= last_vent_end_h + _EPS:
            return ExtubationDecision(
                is_extubation=False,
                censor_cause=CAUSE_DEATH,
                censor_time_h=death_h,
                tail_h=tail_h,
                reason="death_at_vent",
            )

    # 2) Extubación confirmada: hay la hora de observación sin ventilador.
    if tail_h >= min_tail_h - _EPS:
        return ExtubationDecision(
            is_extubation=True,
            censor_cause=None,
            censor_time_h=None,
            tail_h=tail_h,
            reason="extubation_confirmed",
        )

    # 3) La observación termina con la estancia -> traslado ventilado.
    if (
        stay_end_h is not None
        and math.isfinite(float(stay_end_h))
        and observation_end_h >= float(stay_end_h) - _EPS
    ):
        return ExtubationDecision(
            is_extubation=False,
            censor_cause=CAUSE_TRANSFER,
            censor_time_h=last_vent_end_h,
            tail_h=tail_h,
            reason="transfer_ventilated",
        )

    # 4) Fin de datos.
    return ExtubationDecision(
        is_extubation=False,
        censor_cause=CAUSE_END_OF_RECORD,
        censor_time_h=observation_end_h,
        tail_h=tail_h,
        reason="end_of_record",
    )
