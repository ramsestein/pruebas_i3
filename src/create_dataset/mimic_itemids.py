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
    # --- Marcadores ESPECÍFICOS de ventilación invasiva (D6, corrección 2) ---
    # "Ventilator Mode" (CareVue) / "Ventilator Mode" (Metavision)
    "VentMode": ((720, "Ventilator Mode"), (223849, "Ventilator Mode")),
    # "PEEP" (CareVue) / "PEEP Set" (CareVue) / "Total PEEP Level" (CareVue) /
    # "PEEP set" (Metavision) / "Total PEEP Level" (Metavision).
    # CORRECCIÓN Fase 1.6c (punto 1): faltaban 506 y 220339, que es el itemid
    # CANÓNICO de PEEP en MetaVision; sin él la cobertura de PEEP caía al 40 %.
    "PEEP": ((505, "PEEP"), (506, "PEEP Set"), (686, "Total PEEP Level"),
             (224700, "Total PEEP Level"), (220339, "PEEP set")),
    # Volumen tidal PAUTADO: "Tidal Volume (Set)" / "Tidal Volume (set)"
    "TV_set": ((683, "Tidal Volume (Set)"), (224684, "Tidal Volume (set)")),
    # Volumen tidal OBSERVADO: "Tidal Volume" / "Tidal Volume (observed)" /
    # "Tidal Volume (spontaneous)" / "Tidal Volume (Obser)" (CareVue)
    "TV_observed": ((681, "Tidal Volume"), (682, "Tidal Volume (Obser)"),
                    (224685, "Tidal Volume (observed)"),
                    (224686, "Tidal Volume (spontaneous)")),
    # "PIP" (CareVue) / "Peak Insp. Pressure" (CareVue) / "Peak Insp. Pressure" (Metavision).
    # OJO: 224696 ("Plateau Pressure") NO es PIP.
    "PIP": ((507, "PIP"), (535, "Peak Insp. Pressure"), (224695, "Peak Insp. Pressure")),
    # FR TOTAL del ventilador (no la del monitor): "Respiratory Rate (Total)".
    # Fase 1.6c (punto 1): se mantienen FUERA 220210/618 ("Respiratory Rate" del
    # monitor) para no mezclar dos variables distintas; 224688 es la FR PAUTADA
    # del ventilador y tampoco es la FR total medida.
    "RR_V": ((224690, "Respiratory Rate (Total)"),),
    # --- NO marcan ventilación ---
    # "FIO2" / "Inspired O2 Fraction": se anota también con oxigenoterapia (corrección 2).
    "FiO2": ((3420, "FIO2"), (223835, "Inspired O2 Fraction")),
    # "Minute Volume": no es marcador específico exigido (corrección 2).
    "MV": ((448, "Minute Volume"), (224687, "Minute Volume")),
    # --- Monitor (D2/D4 y D7) ---
    "HR": ((220045, "Heart Rate"), (211, "Heart Rate")),
    "SpO2": ((220277, "O2 saturation pulseoxymetry"), (646, "SpO2")),
    "MAP_invasive": ((220052, "Arterial Blood Pressure mean"),),
    "MAP_non_invasive": ((220181, "Non Invasive Blood Pressure mean"),),
    # --- Vía aérea (D5) ---
    "AirwayType": ((40, "Airway Type"), (223836, "Airway Type")),
}

# ── PROCEDUREEVENTS_MV ───────────────────────────────────────────────────────
PROCEDURE_ITEMIDS: dict[str, int] = {
    # "Invasive Ventilation"
    "invasive_ventilation": 225792,
    # "Percutaneous Tracheostomy" / "Open Tracheostomy" (D5; con hora)
    "trach_percutaneous": 225448,
    "trach_open": 226237,
}
TRACH_PROCEDURE_ITEMIDS: frozenset[int] = frozenset({225448, 226237})

# Eventos que CONFIRMAN el final de un intervalo de ventilación invasiva:
#   "Extubation", "Unplanned Extubation (patient-initiated)",
#   "Unplanned Extubation (non-patient initiated)"  (LINKSTO=procedureevents_mv)
EXTUBATION_PROCEDURE_ITEMIDS: frozenset[int] = frozenset({227194, 225468, 225477})

# Marcadores ESPECÍFICOS de ventilación invasiva (D6). La FiO2 NO está aquí.
VENT_MARKER_KEYS: tuple[str, ...] = (
    "VentMode", "PEEP", "TV_set", "TV_observed", "PIP", "RR_V",
)
# Pistas anotadas que NO marcan ventilación por sí solas.
NON_MARKER_VENT_KEYS: tuple[str, ...] = ("FiO2", "MV")

# Pistas de monitor (D2/D4 y D7).
MONITOR_CHART_KEYS: tuple[str, ...] = ("HR", "SpO2", "MAP_invasive", "MAP_non_invasive")

# Pistas de vía aérea (D5).
AIRWAY_CHART_KEYS: tuple[str, ...] = ("AirwayType",)

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


def all_vent_marker_itemids() -> set[int]:
    """Itemids que SÍ marcan ventilación invasiva (D6, corrección 2).

    Incluye modo ventilatorio, PEEP, volumen tidal pautado/observado, PIP y FR
    total del ventilador. NO incluye la FiO2 (se anota con oxigenoterapia).
    """
    return _union_itemids(VENT_MARKER_KEYS)


def all_monitor_itemids() -> set[int]:
    """Unión de los itemids de monitor (HR, SpO2 y MAP)."""
    return _union_itemids(MONITOR_CHART_KEYS)


def all_airway_itemids() -> set[int]:
    """Itemids de tipo de vía aérea (D5)."""
    return _union_itemids(AIRWAY_CHART_KEYS)


def all_catalogued_itemids() -> set[int]:
    """Unión de todos los itemids que se conservan de CHARTEVENTS."""
    return _union_itemids(
        VENT_MARKER_KEYS + NON_MARKER_VENT_KEYS + MONITOR_CHART_KEYS + AIRWAY_CHART_KEYS
    )


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
