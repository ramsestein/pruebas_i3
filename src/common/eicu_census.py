"""
common/eicu_census.py
=====================
Fase 1.6a — métricas de **documentación de la ventilación** por hospital y
**escenarios de selección** (propuestas, sin aplicar).

No calcula etiquetas de desenlace: solo documentación y cobertura.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional, Sequence

import numpy as np

# LOCF de la Fase 1.6a (D8): 2 h para constantes, 12 h para ajustes.
LOCF_CONSTANT_H: float = 2.0
LOCF_SETTING_H: float = 12.0

# Umbral de plausibilidad: rc_invasive/icu_stays_total por encima de esto se
# considera implausible (todo el hospital marcado como ventilado).
IMPLAUSIBLE_RC_ICU_RATIO: float = 0.6


def is_implausible(
    rc_invasive_stays: int,
    icu_stays_total: int,
    threshold: float = IMPLAUSIBLE_RC_ICU_RATIO,
) -> bool:
    """True si ``rc_invasive/icu_stays_total`` supera ``threshold``."""
    if not icu_stays_total:
        return False
    return (rc_invasive_stays / icu_stays_total) > threshold


# Veredictos de plausibilidad del punto 2 de la Fase 1.6a-bis.
VERDICT_OK = "ok"
VERDICT_DENSE = "concentrado_concordante"
VERDICT_IMPLAUSIBLE = "implausible_permeable"

# Fracción de estancias con ajustes invasivos que NO tienen intubación APACHE
# a partir de la cual el etiquetado se considera permeable (marca como invasiva
# a pacientes que no están intubados).
PERMEABLE_FRAC_WITHOUT_INTUB: float = 0.5


def classify_plausibility(
    rc_invasive_stays: int,
    icu_stays_total: int,
    frac_without_intub: float | None,
    threshold: float = IMPLAUSIBLE_RC_ICU_RATIO,
    permeable_frac: float = PERMEABLE_FRAC_WITHOUT_INTUB,
) -> str:
    """Veredicto de plausibilidad de un hospital.

    - ``VERDICT_OK``: ``rc_invasive/icu`` dentro de lo esperado.
    - ``VERDICT_DENSE``: el hospital marca casi todas sus estancias como
      ventiladas (``rc_invasive/icu > threshold``) pero concuerda con APACHE
      (pocas estancias invasivas sin intubación). No se descarta, se vigila.
    - ``VERDICT_IMPLAUSIBLE``: además de denso, etiqueta como invasivas una
      mayoría de estancias sin intubación APACHE ni plan de ventilación: el
      etiquetado es permeable y no sirve como evidencia de ventilación.
    """
    if not icu_stays_total or (rc_invasive_stays / icu_stays_total) <= threshold:
        return VERDICT_OK
    if frac_without_intub is not None and frac_without_intub >= permeable_frac:
        return VERDICT_IMPLAUSIBLE
    return VERDICT_DENSE


def median_iqr(values: Sequence[float]) -> tuple[Optional[float], Optional[float]]:
    """Mediana y rango intercuartílico (Q1–Q3) de una lista de valores."""
    arr = np.asarray([v for v in values if v is not None and np.isfinite(v)], dtype=float)
    if arr.size == 0:
        return None, None
    q1, med, q3 = np.percentile(arr, [25, 50, 75])
    return float(med), float(q3 - q1)


def inter_adjustment_intervals(offsets: Iterable[float]) -> list[float]:
    """Intervalos (min) entre ajustes invasivos consecutivos de una estancia."""
    arr = sorted(float(x) for x in offsets if x is not None and np.isfinite(x))
    return [b - a for a, b in zip(arr, arr[1:]) if b - a > 0]


def documentation_metrics(
    *,
    adj_offsets: Sequence[float],
    discharge_min: float,
    ventstartoffset: Optional[float],
    has_airway: bool,
    support_window_min: float = 120.0,
) -> dict:
    """Métricas de documentación de UNA estancia ventilada.

    - ``rc_invasive``: hay ≥ 1 ajuste invasivo.
    - ``n_adj``: nº de ajustes invasivos.
    - ``inter_adj``: intervalos entre ajustes consecutivos (min).
    - ``ventstart_supported``: el ``ventstartoffset`` tiene evidencia invasiva a
      ± ``support_window_min`` (2 h).
    - ``last_adj_before_discharge_min`` / ``last_adj_lt1h``: último ajuste a
      < 1 h del alta.
    """
    adj = sorted(float(x) for x in adj_offsets if x is not None and np.isfinite(x))
    sup = False
    if ventstartoffset is not None and np.isfinite(ventstartoffset) and adj:
        sup = any(abs(a - float(ventstartoffset)) <= support_window_min for a in adj)
    last_gap = None
    if adj:
        last_gap = float(discharge_min) - adj[-1]
    return {
        "rc_invasive": bool(adj),
        "n_adj": len(adj),
        "inter_adj": inter_adjustment_intervals(adj),
        "ventstart_supported": bool(sup),
        "has_ventstart": bool(ventstartoffset is not None and np.isfinite(ventstartoffset)),
        "last_adj_before_discharge_min": last_gap,
        "last_adj_lt1h": bool(last_gap is not None and 0.0 <= last_gap < 60.0),
        "has_airway": bool(has_airway),
    }


# ── Escenarios de selección (propuestas) ─────────────────────────────────────

def scenario_a(
    hosp: Mapping[str, dict],
    *,
    min_patients: int = 10,
    min_frac_peak: float = 0.10,
) -> set[int]:
    """Réplica de mechanical-power: ≥10 pacientes y ≥10 % con presión pico.

    ``hosp[h]`` debe tener ``apache_vent``, ``apache_vent_invasive`` y
    ``apache_vent_peak24``.
    """
    keep: set[int] = set()
    for h, m in hosp.items():
        n = m.get("apache_vent", 0)
        if n < min_patients:
            continue
        frac = (m.get("apache_vent_peak24", 0) / n) if n else 0.0
        if frac >= min_frac_peak:
            keep.add(h)
    return keep


def scenario_b(c: Mapping[str, dict], *, max_median_gap_min: float) -> set[int]:
    """≥50 estancias ventiladas con ≥80 % con ajustes invasivos e intervalo mediano ≤ X."""
    keep: set[int] = set()
    for h, m in c.items():
        if m.get("vent_stays", 0) < 50:
            continue
        if m.get("frac_rc_invasive", 0.0) < 0.80:
            continue
        med = m.get("inter_adj_median_min")
        if med is not None and med <= max_median_gap_min:
            keep.add(h)
    return keep


def scenario_d(c: Mapping[str, dict], *, vars_ok_min_frac: float = 0.70) -> set[int]:
    """Como b (≤4 h) y además `vars_ok` al 50 % en ≥ X % de las estancias."""
    keep = scenario_b(c, max_median_gap_min=4 * 60.0)
    return {
        h for h in keep
        if (c[h].get("vars_ok50_n", 0) / max(c[h].get("vent_stays", 1), 1))
        >= vars_ok_min_frac
    }


def scenario_stats(
    stays_per_hospital: Mapping[int, int],
   selected: Iterable[int],
) -> dict:
    """Resumen de un escenario: nº hospitales, estancias y distribución."""
    sel = [int(h) for h in selected]
    counts = np.asarray([stays_per_hospital.get(h, 0) for h in sel], dtype=float)
    if counts.size == 0:
        return {"hospitals": 0, "vent_stays": 0, "min": 0, "p25": 0,
                "median": 0, "p75": 0, "max": 0, "largest_weight": 0.0}
    total = counts.sum()
    q1, med, q3 = np.percentile(counts, [25, 50, 75])
    return {
        "hospitals": int(len(sel)),
        "vent_stays": int(total),
        "min": int(counts.min()),
        "p25": float(q1), "median": float(med), "p75": float(q3),
        "max": int(counts.max()),
        "largest_weight": float(counts.max() / total) if total else 0.0,
    }
