"""adapters/__init__.py — Exporta los tres adaptadores y la factoría."""

from .base import CohortAdapter, ClinicalEvents, ExtubationAttempt, NumericsRecord, WaveformRecord
from .clinic_adapter import ClinicAdapter
from .mimic_adapter import MimicAdapter
from .vitaldb_adapter import VitalDBAdapter
from .eicu_adapter import EicuAdapter


def get_adapter(cohort: str, config: dict) -> CohortAdapter:
    """
    Factoría: devuelve el adaptador correcto dado el nombre de cohorte.

    Args:
        cohort: 'mimic' | 'vitaldb' | 'clinic'
        config: Diccionario de configuración completo (cargado desde harmonize.yaml)
    """
    mapping = {
        "mimic": MimicAdapter,
        "vitaldb": VitalDBAdapter,
        "clinic": ClinicAdapter,
        "eicu": EicuAdapter,
    }
    if cohort not in mapping:
        raise ValueError(f"Cohorte '{cohort}' no reconocida. Opciones: {list(mapping)}")
    return mapping[cohort](config)


__all__ = [
    "CohortAdapter",
    "ClinicalEvents",
    "ExtubationAttempt",
    "NumericsRecord",
    "WaveformRecord",
    "ClinicAdapter",
    "MimicAdapter",
    "VitalDBAdapter",
    "EicuAdapter",
    "get_adapter",
]
