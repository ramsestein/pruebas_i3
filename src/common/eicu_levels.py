"""
common/eicu_levels.py
=====================
Clasificación de la **completitud** de cada estancia ventilada de eICU
(Fase 1.5, punto 1) y medición de **cobertura de variables**.

Niveles (uno por estancia)
--------------------------
- **A — completa**: ``ventstartoffset`` y ``ventendoffset`` documentados (no
  imputados), ``fin > inicio`` y ``fin <= alta``; extubación confirmada según la
  regla común (``src/common/extubation.py``) o censura con causa válida (muerte,
  traqueostomía, traslado ventilado); y el último **ajuste de ventilación
  invasiva** de ``respiratoryCharting`` está a ``<= 4 h`` del ``ventendoffset``.
- **B — documentada sin verificar**: como A, pero **sin** ajustes invasivos con
  los que contrastar.
- **C — rescatable**: fin imputado o incoherente, pero el último ajuste invasivo
  anotado está a ``>= 1 h`` del alta → se propone ese momento como extubación
  (se reporta la diferencia con el fin original).
- **D — no fiable**: fin imputado sin forma de recuperarlo, fin posterior al alta
  sin ajustes que lo acoten, o ventilación sin ningún ajuste invasivo anotado.

Ajuste de ventilación **invasiva** (``respiratoryCharting``)
------------------------------------------------------------
Se aceptan etiquetas de **modo**, **PEEP**, **volumen tidal pautado**, **PIP** y
**FR total del ventilador**. Se excluyen explícitamente la **FiO2** y todo lo
relativo a **VNI/alto flujo** (CPAP/EPAP/IPAP/BiPAP/NIV, cánulas de alto flujo).

Cobertura de variables
----------------------
Por estancia se mide, sobre las **horas ventiladas**, la fracción de horas con
un valor útil de cada variable obligatoria (HR, SpO2, MAP, RR, FiO2, PEEP).
Un valor es útil si hay una observación a ``<= D8`` horas (LOCF, por defecto
4 h en ``src/stage2/config.py``). ``vars_ok`` si TODAS superan el 50 %
(reportado también con el umbral del 80 %).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np

from .extubation import CAUSE_DEATH, CAUSE_TRANSFER, resolve_extubation

# LOCF: antigüedad máxima de una observación para considerarla útil (D8).
LOCF_MAX_AGE_H: float = 4.0

# Ventana de coherencia entre el último ajuste invasivo y el fin de ventilación.
COHERENCE_WINDOW_H: float = 4.0

# Mínimo de separación del último ajuste invasivo frente al alta para recuperar
# el fin de ventilación (nivel C).
RECOVERY_MIN_GAP_H: float = 1.0

LEVEL_A, LEVEL_B, LEVEL_C, LEVEL_D = "A", "B", "C", "D"

# ── Ajustes de ventilación invasiva ──────────────────────────────────────────
# Etiquetas EXACTAS de ``respiratoryCharting.respchartvaluelabel`` que
# representan un ajuste de ventilación invasiva.
INVASIVE_ADJUSTMENT_LABELS: frozenset[str] = frozenset({
    # Modo
    "Mechanical Ventilator Mode",
    "Ventilator Support Mode",
    # PEEP
    "PEEP",
    # Volumen tidal pautado
    "Tidal Volume (set)",
    "Set Vt (Servo,LTV)",
    "Set Vt (Drager)",
    "Vti",
    # PIP
    "Peak Insp. Pressure",
    # FR total del ventilador
    "Total RR",
    "Resp Rate Total",
    "f Total",
})

# Etiquetas EXCLUIDAS aunque contengan términos parecidos (documentadas aquí
# para que la decisión no quede implícita).
EXCLUDED_ADJUSTMENT_NOTES: tuple[str, ...] = (
    "FiO2 / FIO2 / O2 Percentage / O2 Device / LPM O2 / Oxygen Flow Rate",
    "NIV Setting*, NIV Pt/Vent*, Non-invasive Ventilation Mode",
    "CPAP / EPAP / IPAP / Bipap Delivery Mode / PEEP/CPAP",
)


def is_invasive_adjustment(label: object) -> bool:
    """True si la etiqueta de ``respiratoryCharting`` es un ajuste invasivo."""
    return isinstance(label, str) and label in INVASIVE_ADJUSTMENT_LABELS


# ── Clasificación ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StayVentInputs:
    """Datos de UNA estancia ventilada para clasificarla (horas desde el alta).

    Las horas se dan **relativas a la admisión** (offset/60). El llamador decide
    qué ``end_h`` usa: el documentado si existe, o el imputado si no.
    """
    start_h: float
    end_h: float
    end_documented: bool
    discharge_h: float
    death_h: Optional[float] = None
    died_ventilated: bool = False
    trach_h: Optional[float] = None
    last_invasive_adj_h: Optional[float] = None


@dataclass(frozen=True)
class StayLevel:
    level: str
    reason: str
    outcome: str                     # extubación | censura citable | no valorable
    extubation_h: Optional[float]    # confirmada (A/B) o propuesta (C)
    proposed_shift_h: Optional[float]  # C: end_h - last_adj_h (positivo si el
                                       # fin original era POSTERIOR al real)
    last_invasive_adj_h: Optional[float] = None


def _rule0_outcome(inp: StayVentInputs):
    """Aplica la regla común de extubación confirmada (punto 0)."""
    return resolve_extubation(
        last_vent_end_h=inp.end_h,
        observation_end_h=inp.discharge_h,
        stay_end_h=inp.discharge_h,
        death_h=inp.death_h,
        died_ventilated=inp.died_ventilated,
    )


def classify_eicu_stay(inp: StayVentInputs) -> StayLevel:
    """Asigna el nivel A/B/C/D a una estancia ventilada de eICU."""
    eps = 1e-9
    ext = _rule0_outcome(inp)

    has_trach = inp.trach_h is not None and inp.trach_h <= inp.end_h + eps
    valid_outcome = (
        ext.is_extubation
        or ext.censor_cause in (CAUSE_DEATH, CAUSE_TRANSFER)
        or has_trach
    )
    outcome = (
        "extubación" if ext.is_extubation
        else ("censura citable" if valid_outcome else "no valorable")
    )

    has_adj = inp.last_invasive_adj_h is not None and np.isfinite(
        inp.last_invasive_adj_h
    )
    adj = float(inp.last_invasive_adj_h) if has_adj else None

    valid_range = (
        inp.end_h > inp.start_h + eps
        and inp.end_h <= inp.discharge_h + eps
    )
    documented = bool(inp.end_documented and valid_range)

    coherent = (
        has_adj
        and 0.0 <= (inp.end_h - adj) <= COHERENCE_WINDOW_H + eps
    )
    recoverable = (
        has_adj
        and adj <= inp.end_h + eps
        and inp.start_h - eps <= adj
        and (inp.discharge_h - adj) >= RECOVERY_MIN_GAP_H - eps
    )

    confirmed_ext_h = inp.end_h if ext.is_extubation else None

    if documented and valid_outcome:
        if not has_adj:
            return StayLevel(LEVEL_B, "documentada_sin_ajustes", outcome,
                             confirmed_ext_h, None, None)
        if coherent:
            return StayLevel(
                LEVEL_A, "documentada_y_verificada", outcome,
                confirmed_ext_h, None, adj,
            )
        # Documentada, pero el fin NO es coherente con el último ajuste.
        if recoverable:
            return StayLevel(
                LEVEL_C, "documentada_incoherente_rescatable", outcome,
                adj, inp.end_h - adj, adj,
            )
        return StayLevel(LEVEL_D, "documentada_incoherente_no_rescatable",
                         outcome, None, None, adj)

    # Fin imputado o fuera de rango (fin > alta).
    if recoverable:
        return StayLevel(
            LEVEL_C, "fin_imputado_rescatable", outcome,
            adj, inp.end_h - adj, adj,
        )
    return StayLevel(LEVEL_D, "fin_imputado_no_rescatable", outcome, None, None, adj)


# ── Cobertura de variables (LOCF) ────────────────────────────────────────────

@dataclass
class CoverageResult:
    """Fracción de horas ventiladas con valor útil, por variable."""
    fractions: dict[str, float] = field(default_factory=dict)

    def all_above(self, threshold: float) -> bool:
        return bool(self.fractions) and all(
            v > threshold for v in self.fractions.values()
        )


def hourly_coverage(
    times_min: Sequence[float],
    values: Sequence[float],
    vent_spans_min: Sequence[tuple[float, float]],
    *,
    max_age_h: float = LOCF_MAX_AGE_H,
) -> float:
    """Fracción de horas ventiladas con un valor útil (LOCF <= ``max_age_h``).

    ``times_min`` / ``values``: observaciones (min desde la admisión).
    ``vent_spans_min``: intervalos ventilados ``(inicio, fin)`` (min).
    """
    if not vent_spans_min:
        return 0.0
    t = np.asarray(times_min, dtype=np.float64)
    v = np.asarray(values, dtype=np.float64)
    ok = np.isfinite(t) & np.isfinite(v)
    t, v = t[ok], v[ok]
    order = np.argsort(t, kind="stable")
    t, v = t[order], v[order]
    max_age_min = max_age_h * 60.0

    n_total = 0
    n_valid = 0
    for start, end in vent_spans_min:
        if not (np.isfinite(start) and np.isfinite(end)) or end < start:
            continue
        # Rejilla horaria [start, start+60, ..., end].
        grid = np.arange(float(start), float(end) + 1e-9, 60.0)
        for g in grid:
            n_total += 1
            if t.size == 0:
                continue
            idx = int(np.searchsorted(t, g, side="right")) - 1
            if idx >= 0 and t[idx] >= g - max_age_min:
                n_valid += 1
    return float(n_valid / n_total) if n_total else 0.0


MANDATORY_VARIABLES: tuple[str, ...] = ("HR", "SpO2", "MAP", "RR", "FiO2", "PEEP")


def vars_ok_summary(
    coverage: CoverageResult,
    *,
    thresholds: Sequence[float] = (0.5, 0.8),
) -> dict[str, bool]:
    """``vars_ok`` para cada umbral (por defecto 50 % y 80 %)."""
    return {f"{int(th * 100)}%": coverage.all_above(th) for th in thresholds}
