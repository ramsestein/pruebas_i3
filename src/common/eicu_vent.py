"""
common/eicu_vent.py
===================
Fase 1.6a — utilidades para auditar cómo registra eICU la ventilación.

Contiene, **sin calcular etiquetas de desenlace**:

1. Clasificación de las etiquetas/valores relacionados con la vía aérea o la
   ventilación en cuatro categorías: ``invasiva``, ``vni_alto_flujo``,
   ``oxigenoterapia`` y ``ambigua``.
2. Cálculo de la **concordancia entre fuentes** (matriz de solapamiento y kappa
   de Cohen por pares).

Las categorías se basan en la documentación oficial de eICU-CRD
(``respiratoryCare``, ``respiratoryCharting``, ``carePlanGeneral``,
``treatment``, ``apachePredVar``/``apacheApsVar``) y en los métodos publicados
(alistairewj/mechanical-power; veáse ``reports/fase1_6a/eicu_auditoria.md``).
"""

from __future__ import annotations

import re
from collections import Counter
from itertools import combinations
from typing import Iterable, Mapping, Sequence

CAT_INVASIVE = "invasiva"
CAT_NIV = "vni_alto_flujo"
CAT_O2 = "oxigenoterapia"
CAT_AMBIGUOUS = "ambigua"

CATEGORIES: tuple[str, ...] = (CAT_INVASIVE, CAT_NIV, CAT_O2, CAT_AMBIGUOUS)

# ── 1. Clasificación de etiquetas ────────────────────────────────────────────

# Etiquetas de ``respiratoryCharting`` que implican ventilación invasiva
# (modo de ventilador, PEEP, volumen tidal pautado, PIP, FR total del vent).
RESPCHART_INVASIVE: frozenset[str] = frozenset({
    "Mechanical Ventilator Mode",
    "Ventilator Support Mode",
    "Ventilator Type",
    "PEEP",
    "Tidal Volume (set)",
    "Set Vt (Servo,LTV)",
    "Set Vt (Drager)",
    "Vti",
    "Peak Insp. Pressure",
    "Peak Pressure",
    "Total RR",
    "Resp Rate Total",
    "f Total",
    "Exhaled MV",
    "Exhaled TV (machine)",
    "Minute Volume Set(L/min)",
    "Inspiratory Pressure, Set",
})

# ``respiratoryCharting``: VNI / alto flujo.
RESPCHART_NIV: frozenset[str] = frozenset({
    "NIV Setting EPAP", "NIV Setting Set_RR", "NIV Setting Total RR_5",
    "NIV Setting Spont Exp Vt_5", "NIV Setting Leak_",
    "NIV Pt/Vent Spont_TidalV", "NIV Pt/Vent Spont_Rate", "NIV Pt/Vent SpO2_5",
    "NIV Alarms Hi Press Alarm_5", "NIV Alarms Low Press Alarm",
    "Non-invasive Ventilation Mode", "Bipap Delivery Mode",
    "CPAP", "EPAP", "IPAP", "PEEP/CPAP", "B2: EPAP", "B1: IPAP",
    "B3: Est Mask Leak", "PS above PEEP",
})

# ``respiratoryCharting``: oxigenoterapia.
RESPCHART_O2: frozenset[str] = frozenset({
    "LPM O2", "O2 Device", "O2 Percentage", "Oxygen Delivery Method",
    "Oxygen Delivery Status", "Oxygen Flow Rate", "RETIRED O2 Device",
    "FiO2", "FIO2 (%)", "Set Fraction of Inspired Oxygen (FIO2)",
})

# ``respiratoryCare.airwayType``.
AIRWAY_INVASIVE: frozenset[str] = frozenset({
    "Oral ETT", "Nasal ETT", "Tracheostomy", "Cricothyrotomy",
    "Double-Lumen Tube", "Laryngectomy",
})
AIRWAY_NONE: frozenset[str] = frozenset({"No Artificial Airway"})

# Subcadenas de ``treatment.treatmentstring`` (jerárquicas con ``|``).
TREATMENT_INVASIVE: tuple[str, ...] = (
    "ventilation and oxygenation|mechanical ventilation",
    "radiologic procedures / bronchoscopy|endotracheal tube|insertion",
    "surgery / incision and drainage of thorax|tracheostomy",
)
TREATMENT_NIV: tuple[str, ...] = (
    "ventilation and oxygenation|non-invasive ventilation",
    "ventilation and oxygenation|cpap/peep therapy",
)
TREATMENT_O2: tuple[str, ...] = (
    "ventilation and oxygenation|oxygen therapy",
    "ventilation and oxygenation|oxygen delivery",
)

# ``carePlanGeneral.cplitemvalue`` (grupos Ventilation / Airway).
CAREPLAN_INVASIVE: tuple[str, ...] = ("mechanical ventilation", "intubat", "endotracheal", "ett", "tracheostomy")
CAREPLAN_NIV: tuple[str, ...] = ("non-invasive", "noninvasive", "cpap", "bipap")
CAREPLAN_O2: tuple[str, ...] = ("oxygen", "nasal cannula", "face mask", "high flow", "hfnc")

_NIV_RX = re.compile(r"\bniv\b|non[- ]?invasive|bipap|\bcpap\b|\bepap\b|\bipap\b|high[- ]?flow|hfnc", re.I)
_O2_RX = re.compile(r"oxygen|\bo2\b|lpm|nasal cannula|face mask|venturi|oxigen", re.I)
_INV_VENT_RX = re.compile(r"mechanical vent|ventilator|invasive|\bett\b|endotracheal|tracheostom|"
                          r"tidal volume|peak insp|\bpeep\b|\bpip\b|vent(ilator)? mode", re.I)


def _match(label: str, needles: Iterable[str]) -> bool:
    low = label.lower()
    return any(n.lower() in low for n in needles)


def classify_respchart_label(label: str) -> str:
    """Categoría de una etiqueta de ``respiratoryCharting``."""
    if not isinstance(label, str) or not label.strip():
        return CAT_AMBIGUOUS
    if label in RESPCHART_INVASIVE:
        return CAT_INVASIVE
    if label in RESPCHART_NIV or _NIV_RX.search(label):
        return CAT_NIV
    if label in RESPCHART_O2 or _O2_RX.search(label):
        return CAT_O2
    return CAT_AMBIGUOUS


def classify_airway(airway: str) -> str:
    """Categoría de ``respiratoryCare.airwayType``."""
    if not isinstance(airway, str) or not airway.strip():
        return CAT_AMBIGUOUS
    if airway in AIRWAY_INVASIVE:
        return CAT_INVASIVE
    if airway in AIRWAY_NONE:
        return CAT_O2
    return CAT_AMBIGUOUS


def classify_treatment(treatmentstring: str) -> str:
    """Categoría de ``treatment.treatmentstring``."""
    if not isinstance(treatmentstring, str) or not treatmentstring.strip():
        return CAT_AMBIGUOUS
    if _match(treatmentstring, TREATMENT_INVASIVE):
        return CAT_INVASIVE
    if _match(treatmentstring, TREATMENT_NIV):
        return CAT_NIV
    if _match(treatmentstring, TREATMENT_O2):
        return CAT_O2
    return CAT_AMBIGUOUS


def classify_careplan(group: str, value: str) -> str:
    """Categoría de ``carePlanGeneral`` (``cplgroup`` + ``cplitemvalue``)."""
    g = (group or "").lower()
    v = (value or "").lower()
    if g not in ("ventilation", "airway"):
        return CAT_AMBIGUOUS
    if _match(v, CAREPLAN_INVASIVE):
        return CAT_INVASIVE
    if _match(v, CAREPLAN_NIV):
        return CAT_NIV
    if _match(v, CAREPLAN_O2):
        return CAT_O2
    return CAT_AMBIGUOUS


# ── 2. Evidencia invasiva por estancia ───────────────────────────────────────

EVIDENCE_FLAGS: tuple[str, ...] = (
    "apache_vent", "apache_intub", "airway_ett", "airway_trach",
    "rc_invasive", "cpg_vent", "tx_vent",
)

INVASIVE_FLAGS: tuple[str, ...] = (
    "apache_vent", "apache_intub", "airway_ett", "airway_trach",
    "rc_invasive", "cpg_vent", "tx_vent",
)


def any_invasive_evidence(flags: Mapping[str, bool]) -> bool:
    """True si la estancia tiene al menos una bandera invasiva."""
    return any(bool(flags.get(f)) for f in INVASIVE_FLAGS)


# ── 3. Concordancia entre fuentes ────────────────────────────────────────────

def cohen_kappa(pairs: Sequence[tuple[bool, bool]]) -> float:
    """Kappa de Cohen para una secuencia de pares ``(a, b)`` booleanos."""
    n = len(pairs)
    if n == 0:
        return float("nan")
    n11 = sum(1 for a, b in pairs if a and b)
    n10 = sum(1 for a, b in pairs if a and not b)
    n01 = sum(1 for a, b in pairs if b and not a)
    n00 = sum(1 for a, b in pairs if not a and not b)
    po = (n11 + n00) / n
    pa1 = (n11 + n10) / n
    pb1 = (n11 + n01) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    if abs(1 - pe) < 1e-12:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def overlap_counts(a: set[int], b: set[int], universe: set[int]) -> dict:
    """Conteos 2x2 de dos conjuntos de estancias dentro de un universo."""
    return {
        "both": len(a & b),
        "only_a": len(a - b),
        "only_b": len(b - a),
        "neither": len(universe - (a | b)),
        "union": len(a | b),
        "jaccard": (len(a & b) / len(a | b)) if (a | b) else float("nan"),
    }


def pairwise_concordance(
    flags: Mapping[str, set[int]],
    universe: set[int],
    *,
    names: Sequence[str] | None = None,
) -> dict:
    """Matriz de solapamiento y kappa por pares de fuentes.

    ``flags``: nombre de fuente -> conjunto de estancias con la bandera activa.
    ``universe``: todas las estancias consideradas (los ``neither`` se cuentan
    contra este universo).
    """
    names = list(names or flags.keys())
    out: dict[str, dict] = {}
    for a, b in combinations(names, 2):
        A, B = set(flags.get(a, set())), set(flags.get(b, set()))
        counts = overlap_counts(A, B, universe)
        pairs = [
            (sid in A, sid in B) for sid in universe
        ]
        counts["kappa"] = cohen_kappa(pairs)
        # kappa sobre la unión de positivos (set informativo, sin n00 dominante)
        pos = A | B
        counts["kappa_union"] = cohen_kappa([(sid in A, sid in B) for sid in pos])
        out[f"{a}|{b}"] = counts
    return out
