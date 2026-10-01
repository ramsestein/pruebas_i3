"""
adapters/base.py
================
Interfaz abstracta común para todos los adaptadores de cohorte.

Los adaptadores exponen datos CRUDOS en sus unidades y frecuencias nativas.
No realizan transformaciones; eso es responsabilidad de harmonize/.

Contrato:
  - get_waveforms()  → dict[signal_name, WaveformRecord]
  - get_numerics()   → NumericsRecord
  - get_clinical_events() → ClinicalEvents
  - list_patients()  → list[str]

Señales garantizadas en get_waveforms():
  'ECG', 'PPG', 'ABP'
  (con available=False si el canal no existe para esta cohorte/paciente)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── Dataclasses de dominio ─────────────────────────────────────────────────────

@dataclass
class WaveformRecord:
    """
    Señal de alta frecuencia cruda para un canal de un paciente.

    Los timestamps están en segundos desde t0 (inicio de VM = 0.0).
    Si available=False, timestamps_unix y values son arrays vacíos.
    """
    patient_id: str
    signal_name: str             # 'ECG' | 'PPG' | 'ABP'
    fs_native: float             # Hz — frecuencia de muestreo nativa
    # Segundos desde t0 (t0=0.0). float64 para preservar precisión.
    timestamps_rel: np.ndarray   # shape (N,), dtype float64
    values: np.ndarray           # shape (N,), dtype float32
    units: str = ""
    available: bool = True       # False si el canal no existe para este paciente

    def __post_init__(self):
        if not self.available:
            self.timestamps_rel = np.empty(0, dtype=np.float64)
            self.values = np.empty(0, dtype=np.float32)
        else:
            self.timestamps_rel = np.asarray(self.timestamps_rel, dtype=np.float64)
            self.values = np.asarray(self.values, dtype=np.float32)

    @property
    def duration_seconds(self) -> float:
        if len(self.timestamps_rel) < 2:
            return 0.0
        return float(self.timestamps_rel[-1] - self.timestamps_rel[0])

    @property
    def n_samples(self) -> int:
        return len(self.values)


@dataclass
class NumericsRecord:
    """
    Series numéricas de baja frecuencia (constantes vitales, parámetros
    ventilatorios, etc.) para un paciente.

    El índice temporal está en segundos desde t0 y puede ser irregular.
    Columnas estándar (NaN donde no disponible):
      HR, SBP, DBP, MAP, SpO2, RR,
      FiO2, PEEP, TV, MV, PIP,
      vasopressor_norepinephrine, vasopressor_epinephrine,
      vasopressor_dopamine, lactate,
      SOFA_respiratory, SOFA_cardiovascular, SOFA_total
    """
    patient_id: str
    # Tiempo en segundos desde t0 (puede ser irregular)
    timestamps_rel: np.ndarray   # shape (N,), dtype float64
    # DataFrame con las columnas estándar; NaN donde no disponible
    data: pd.DataFrame           # index = RangeIndex, columnas = señales


@dataclass
class ExtubationAttempt:
    """Un intento de extubación individual."""
    attempt_index: int
    time_rel_hours: float        # Horas desde t0
    outcome: str                 # 'success' | 'failure'
    reintubation_time_rel_hours: Optional[float] = None  # Si outcome=='failure'


@dataclass
class ClinicalEvents:
    """
    Todos los marcadores temporales de eventos clínicos para un paciente.

    Tiempos en horas desde t0.
    t0_unix es el timestamp absoluto del inicio de VM (segundos epoch),
    necesario para cruzar con tablas clínicas externas.
    """
    patient_id: str
    cohort: str                  # 'mimic' | 'vitaldb' | 'clinic'
    t0_unix: float               # Epoch seconds del inicio de VM

    # Fin del registro (horas desde t0); siempre disponible
    record_end_hours: float

    # Extubación confirmada al fin del archivo (por construcción para
    # Clínic y VitalDB; verificada contra tablas clínicas para MIMIC)
    extubation_confirmed_hours: Optional[float] = None
    extubation_confirmed: bool = False

    # Intentos de extubación (incluye el exitoso final)
    extubation_attempts: list[ExtubationAttempt] = field(default_factory=list)

    # Edge cases (relevante principalmente para MIMIC)
    # Si vent_end coincide con DEATHTIME → no fue extubación real
    censored_no_extubation: bool = False
    censored_reason: Optional[str] = None   # ej. "death_at_vent_end"

    @property
    def n_failed_attempts(self) -> int:
        return sum(1 for a in self.extubation_attempts if a.outcome == "failure")

    @property
    def first_attempt_hours(self) -> Optional[float]:
        if not self.extubation_attempts:
            return None
        return self.extubation_attempts[0].time_rel_hours


# ── Interfaz abstracta ────────────────────────────────────────────────────────

class CohortAdapter(ABC):
    """
    Interfaz común que todos los adaptadores de cohorte deben implementar.

    Cada adaptador recibe el bloque de config correspondiente a su cohorte
    (channel_maps[cohort_name] + event_sources[cohort_name] + paths globales).
    """

    REQUIRED_WAVEFORMS = ("ECG", "PPG", "ABP")

    def __init__(self, config: dict):
        """
        Args:
            config: Diccionario con la config maestra completa (cargada desde YAML).
        """
        self.config = config
        self._channel_map: dict = config.get("channel_maps", {}).get(
            self.cohort_name, {}
        )
        self._event_source: dict = config.get("event_sources", {}).get(
            self.cohort_name, {}
        )

    # ── Métodos abstractos obligatorios ───────────────────────────────────────

    @abstractmethod
    def list_patients(self) -> list[str]:
        """
        Devuelve la lista de patient_ids disponibles en esta cohorte.
        Cada patient_id debe ser único dentro del proyecto (incluir prefijo
        de cohorte si es necesario para evitar colisiones).
        """
        ...

    @abstractmethod
    def get_waveforms(self, patient_id: str) -> dict[str, WaveformRecord]:
        """
        Devuelve un dict con exactamente las claves 'ECG', 'PPG', 'ABP'.
        Si un canal no está disponible, incluye un WaveformRecord con available=False.

        Los timestamps son segundos desde t0 (t0=0.0 = inicio de VM).
        """
        ...

    @abstractmethod
    def get_numerics(self, patient_id: str) -> NumericsRecord:
        """
        Devuelve las series numéricas del paciente.
        Timestamps en segundos desde t0.
        """
        ...

    @abstractmethod
    def get_clinical_events(self, patient_id: str) -> ClinicalEvents:
        """
        Devuelve todos los marcadores de eventos clínicos del paciente.
        """
        ...

    @property
    @abstractmethod
    def cohort_name(self) -> str:
        """Identificador de la cohorte: 'mimic' | 'vitaldb' | 'clinic'"""
        ...

    # ── Métodos de utilidad (heredados por todos los adaptadores) ─────────────

    def _resolve_channel(self, canonical_name: str) -> Optional[str]:
        """
        Resuelve el nombre canónico de un canal al nombre de track real
        según el channel_map de la config. Devuelve None si no está mapeado.
        """
        track = self._channel_map.get(canonical_name)
        if track is None:
            logger.debug(
                "[%s] Canal '%s' no mapeado en config → marcando unavailable",
                self.cohort_name, canonical_name,
            )
        return track

    def _unavailable_waveform(
        self, patient_id: str, signal_name: str, reason: str = ""
    ) -> WaveformRecord:
        """Crea un WaveformRecord marcado como no disponible."""
        if reason:
            logger.debug(
                "[%s] patient=%s canal=%s no disponible: %s",
                self.cohort_name, patient_id, signal_name, reason,
            )
        return WaveformRecord(
            patient_id=patient_id,
            signal_name=signal_name,
            fs_native=0.0,
            timestamps_rel=np.empty(0, dtype=np.float64),
            values=np.empty(0, dtype=np.float32),
            available=False,
        )

    def _validate_waveforms(self, waveforms: dict[str, WaveformRecord]) -> None:
        """Comprueba que el dict tiene exactamente las claves requeridas."""
        missing = set(self.REQUIRED_WAVEFORMS) - set(waveforms.keys())
        if missing:
            raise ValueError(
                f"[{self.cohort_name}] get_waveforms() debe devolver claves "
                f"{self.REQUIRED_WAVEFORMS}. Faltan: {missing}"
            )
