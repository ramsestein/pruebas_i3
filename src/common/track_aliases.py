"""
common/track_aliases.py
=======================
Fase 1.6d — **punto 3**: tabla **única** de alias de pistas numéricas del monitor
Philips IntelliVue, compartida por:

- la **cobertura por variable** (``vital_signals.COVERAGE_TRACKS``);
- la **regla de observación fisiológica** (``monitor_observation.range_for_track``);
- los **adaptadores** de armonización (``stage0/adapters/*_adapter.py``).

El orden de cada lista es la **prioridad de resolución** (la primera pista
presente en el fichero manda). Las cajas de Clínic y VitalDB no usan los mismos
nombres: unas miden la presión arterial de forma invasiva (``ART_*``/``ABP_*``) y
otras no invasiva (``NIBP_*``); la FC aparece como ``ECG_HR``, ``PLETH_HR`` o
``HR``; la saturación siempre es ``PLETH_SAT_O2``.

Tener una sola tabla evita que la cobertura de FC y la de MAP se resuelvan con
listas distintas (lo que hacía que, con una rejilla horaria gruesa, dos
variables distintas colapsaran al mismo valor).
"""

from __future__ import annotations

# ── Numéricos del monitor ─────────────────────────────────────────────────────
HR: tuple[str, ...] = (
    "Intellivue/ECG_HR",   # "ECG Heart Rate"
    "Intellivue/PLETH_HR",  # "Pleth Heart Rate"
    "Intellivue/HR",       # "Heart Rate"
)
SPO2: tuple[str, ...] = ("Intellivue/PLETH_SAT_O2",)
MAP: tuple[str, ...] = (
    "Intellivue/ART_MEAN",   # invasiva (Clínic)
    "Intellivue/ABP_MEAN",   # invasiva (VitalDB / Clínic)
    "Intellivue/NIBP_MEAN",  # no invasiva
)
SBP: tuple[str, ...] = (
    "Intellivue/ART_SYS", "Intellivue/ABP_SYS", "Intellivue/NIBP_SYS")
DBP: tuple[str, ...] = (
    "Intellivue/ART_DIA", "Intellivue/ABP_DIA", "Intellivue/NIBP_DIA")
RR: tuple[str, ...] = ("Intellivue/RR", "Intellivue/VENT_RR")

# ── Numéricos del ventilador ──────────────────────────────────────────────────
VENT: dict[str, tuple[str, ...]] = {
    "RR": ("Intellivue/VENT_RR",),
    "FiO2": ("Intellivue/FIO2",),
    "PEEP": ("Intellivue/PEEP_CMH2O",),
    "TV": ("Intellivue/TV_EXP",),
    "MV": ("Intellivue/MV_EXP",),
    "PIP": ("Intellivue/PIP_CMH2O",),
}

# Variables con rango fisiológico conocido (regla de observación).
PHYSIOLOGICAL_VARIABLES: tuple[str, ...] = ("HR", "SpO2")

# Alias de Variables Vitales -> lista ordenada de pistas.
VITAL_ALIASES: dict[str, tuple[str, ...]] = {
    "HR": HR, "SpO2": SPO2, "MAP": MAP, "SBP": SBP, "DBP": DBP, "RR": RR,
}

# Pista -> variable (para resolver el rango fisiológico sin heurísticas de
# subcadena, que confundirían p. ej. "PLETH_HR" con cualquier cosa que contenga
# "hr").
TRACK_TO_VARIABLE: dict[str, str] = {
    track: var for var, tracks in VITAL_ALIASES.items() for track in tracks
}


def variable_of_track(track_name: str) -> str | None:
    """Variable vital de una pista (``None`` si no es una constante vital)."""
    return TRACK_TO_VARIABLE.get(track_name)
