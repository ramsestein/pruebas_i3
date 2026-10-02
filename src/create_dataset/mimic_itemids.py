"""
create_dataset/mimic_itemids.py
===============================
Catálogo ÚNICO de itemids de MIMIC mencionados en el proyecto (Fase 1.3).

Todas las etiquetas están verificadas contra ``D_ITEMS`` de la copia local de
MIMIC-III (``LINKSTO = chartevents`` salvo donde se indique). La etiqueta
oficial va comentada junto a cada itemid (regla 7).

Un mismo concepto puede tener varios itemids (MIMIC-III tiene dos orígenes:
CareVue y Metavision). El primero de cada lista es el preferido.
"""

from __future__ import annotations

from typing import Iterable

# ── CHARTEVENTS ──────────────────────────────────────────────────────────────
# etiqueta oficial entre comillas (verificada en D_ITEMS)
MIMIC_CHART_ITEMIDS: dict[str, tuple[tuple[int, str], ...]] = {
    # "FIO2" (CareVue) / "Inspired O2 Fraction" (Metavision)
    "FiO2": ((3420, "FIO2"), (223835, "Inspired O2 Fraction")),
    # "PEEP" (CareVue) / "Total PEEP Level" (Metavision)
    "PEEP": ((505, "PEEP"), (224700, "Total PEEP Level")),
    # "Tidal Volume" (CareVue) / "Tidal Volume (Obser)" / "Tidal Volume (observed)"
    "TV": ((681, "Tidal Volume"), (682, "Tidal Volume (Obser)"),
           (224685, "Tidal Volume (observed)")),
    # "PIP" (CareVue) / "Peak Insp. Pressure" (Metavision).
    # OJO: 224696 ("Plateau Pressure") NO es PIP.
    "PIP": ((507, "PIP"), (224695, "Peak Insp. Pressure")),
    # "Respiratory Rate (Total)" (Metavision) / "Respiratory Rate" (CareVue).
    # La RR del ventilador NO es la impedancia del monitor (D7).
    "RR_V": ((224690, "Respiratory Rate (Total)"), (618, "Respiratory Rate")),
    # "Minute Volume" (Metavision) / "Minute Volume" (CareVue)
    "MV": ((224687, "Minute Volume"), (448, "Minute Volume")),
    # "Heart Rate" (Metavision) / "Heart Rate" (CareVue)
    "HR": ((220045, "Heart Rate"), (211, "Heart Rate")),
    # "O2 saturation pulseoxyphemetry" (Metavision) / "SpO2" (CareVue)
    "SpO2": ((220277, "O2 saturation pulseoxymetry"), (646, "SpO2")),
    # MAP invasiva -> no invasiva (D7)
    "MAP_invasive": ((220052, "Arterial Blood Pressure mean"),),
    "MAP_non_invasive": ((220181, "Non Invasive Blood Pressure mean"),),
}

# ── PROCEDUREEVENTS_MV ───────────────────────────────────────────────────────
# "Invasive Ventilation", LINKSTO = procedureevents_mv
PROCEDURE_ITEMIDS: dict[str, int] = {
    "invasive_ventilation": 225792,
}

# Pistas que definen "ajustes del ventilador" (D6).
VENT_CHART_KEYS: tuple[str, ...] = ("FiO2", "PEEP", "TV", "PIP", "RR_V", "MV")

# Pistas de monitor (D2/D4 y D7).
MONITOR_CHART_KEYS: tuple[str, ...] = ("HR", "SpO2", "MAP_invasive", "MAP_non_invasive")


def itemids_for(key: str) -> tuple[int, ...]:
    """Todos los itemids de CHARTEVENTS asociados a un concepto."""
    return tuple(iid for iid, _ in MIMIC_CHART_ITEMIDS[key])


def preferred_itemid(key: str) -> int:
    """Itemid preferido de CHARTEVENTS para un concepto."""
    return MIMIC_CHART_ITEMIDS[key][0][0]


def _union_itemids(keys: Iterable[str]) -> set[int]:
    out: set[int] = set()
    for key in keys:
        out.update(itemids_for(key))
    return out


def all_vent_itemids() -> set[int]:
    """Unión de los itemids de ajustes del ventilador (D6)."""
    return _union_itemids(VENT_CHART_KEYS)


def all_monitor_itemids() -> set[int]:
    """Unión de los itemids de monitor (HR, SpO2 y MAP)."""
    return _union_itemids(MONITOR_CHART_KEYS)


def all_catalogued_itemids() -> set[int]:
    """Unión de todos los itemids del catálogo."""
    return _union_itemids(VENT_CHART_KEYS + MONITOR_CHART_KEYS)


def itemid_to_concept() -> dict[int, str]:
    """Mapa inverso itemid -> concepto (para etiquetar filas crudas)."""
    mapping: dict[int, str] = {}
    for concept, pairs in MIMIC_CHART_ITEMIDS.items():
        for iid, _ in pairs:
            mapping.setdefault(iid, concept)
    return mapping


def label_of(itemid: int) -> str | None:
    """Etiqueta oficial del itemid, o ``None`` si no está catalogado."""
    for pairs in MIMIC_CHART_ITEMIDS.values():
        for iid, label in pairs:
            if iid == itemid:
                return label
    return None


def iter_all_itemids() -> Iterable[tuple[int, str]]:
    """Itera (itemid, etiqueta oficial) de todo el catálogo."""
    for pairs in MIMIC_CHART_ITEMIDS.values():
        yield from pairs
