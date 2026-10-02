"""
common/d5_events.py
===================
Políticas D5: traqueostomía y extubación terminal → **CENSURA** (no exclusión).

Decisión (D5)
-------------
- El paciente aporta landmarks hasta el evento y se censura ahí:
  ``extubation_time_hours = NaN`` y ``event_type = censored_*`` con la causa.
- ``trach_handling`` y ``terminal_handling`` valen ``censor`` en la config.
- La variante ``exclude`` se calcula SOLO como sensibilidad y hay que reportar
  cuántos casos, cuántas horas y qué distribución de duración se perderían.

Detección por cohorte
---------------------
- eICU/MIMIC: tipo de vía aérea, procedimientos y códigos ICD-9 31.1 / 31.2x
  (y el equivalente en eICU).
- Clínic/VitalDB: NO es detectable con señales. Se documenta como limitación
  sin estimarla con tasas de otras cohortes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

# ICD-9-CM procedimientos de traqueostomía (etiquetas oficiales):
#   31.1  "Temporary tracheostomy"
#   31.21 "Mediastinal tracheostomy"
#   31.29 "Other permanent tracheostomy"
ICD9_TRACH_PREFIXES: tuple[str, ...] = ("311", "3121", "3129")

# Patrones textuales de traqueostomía (tablas de procedimientos/vía aérea).
# ``\b`` evita que "Endotracheal" (tubo endotraqueal) se detecte como traqueo.
TRACH_TEXT_PATTERN = re.compile(r"\btrach\w*", re.IGNORECASE)

CENSOR = "censor"
EXCLUDE = "exclude"


@dataclass(frozen=True)
class CensorDecision:
    """Resultado de aplicar una política D5 a un evento."""
    censor_cause: Optional[str]           # None si no aplica
    censor_time_h: Optional[float]        # horas desde t0
    excluded: bool = False


@dataclass
class SensitivityReport:
    """Cuánto se perdería con la variante ``exclude`` (sensibilidad)."""
    n_cases: int = 0
    total_hours: float = 0.0
    durations_h: list[float] = field(default_factory=list)

    @property
    def median_duration_h(self) -> Optional[float]:
        if not self.durations_h:
            return None
        s = sorted(self.durations_h)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


# ── Detección de traqueostomía ───────────────────────────────────────────────

def normalize_icd9(code: str) -> str:
    return re.sub(r"[^0-9]", "", str(code))


def is_trach_icd9(code: str) -> bool:
    """True si el código ICD-9 de procedimiento es de traqueostomía."""
    c = normalize_icd9(code)
    return any(c.startswith(p) for p in ICD9_TRACH_PREFIXES)


def is_trach_text(*values: Optional[str]) -> bool:
    """True si algún texto (vía aérea, procedimiento...) menciona traqueostomía."""
    return any(v and TRACH_TEXT_PATTERN.search(str(v)) for v in values)


def trach_time_h(trach_time_abs: Optional[float], t0_abs: float) -> Optional[float]:
    if trach_time_abs is None:
        return None
    return (float(trach_time_abs) - float(t0_abs)) / 3600.0


# ── Extubación terminal ──────────────────────────────────────────────────────

def terminal_from_death(
    death_abs: Optional[float],
    last_disconnect_abs: Optional[float],
    *,
    t0_abs: float,
    failure_window_h: float,
    died_ventilated: bool,
) -> CensorDecision:
    """D5 eICU/MIMIC.

    - Muerte dentro de la ventana de fallo tras la última desconexión y sin
      reintubación -> censura en la desconexión.
    - Muerte ventilado -> censura en la muerte.
    """
    if death_abs is None:
        return CensorDecision(None, None)
    if died_ventilated:
        return CensorDecision("death_at_vent", (death_abs - t0_abs) / 3600.0)
    if last_disconnect_abs is not None:
        gap_h = (death_abs - last_disconnect_abs) / 3600.0
        if 0.0 <= gap_h <= failure_window_h:
            return CensorDecision("terminal_extubation", (last_disconnect_abs - t0_abs) / 3600.0)
    return CensorDecision(None, None)


def terminal_from_signal_loss(
    *,
    asystole_or_hr_zero: bool,
    spo2_lost_without_recovery: bool,
    disconnect_abs: Optional[float],
    t0_abs: float,
) -> CensorDecision:
    """D5 Clínic/VitalDB: pérdida de constantes antes o en la desconexión."""
    if disconnect_abs is None:
        return CensorDecision(None, None)
    if asystole_or_hr_zero and spo2_lost_without_recovery:
        return CensorDecision("terminal_extubation", (disconnect_abs - t0_abs) / 3600.0)
    return CensorDecision(None, None)


# ── Aplicación de la política (censor vs exclude) ───────────────────────────

def apply_policy(decision: CensorDecision, handling: str) -> CensorDecision:
    """Aplica ``censor`` (principal) o ``exclude`` (sensibilidad)."""
    if decision.censor_cause is None:
        return decision
    if handling == EXCLUDE:
        return CensorDecision(decision.censor_cause, decision.censor_time_h, excluded=True)
    return decision


def sensitivity_report(
    censored_events: Iterable[tuple[float, float]],
) -> SensitivityReport:
    """Resume la sensibilidad ``exclude``.

    ``censored_events``: iterable de ``(duration_h, ventilated_h)`` de los
    eventos censurados. Reporta cuántos casos, cuántas horas y la distribución
    de duración que se perderían.
    """
    report = SensitivityReport()
    for duration_h, vent_h in censored_events:
        report.n_cases += 1
        report.total_hours += float(vent_h)
        report.durations_h.append(float(duration_h))
    return report


def threshold_simultaneous_shutdown(
    vent_end_h: float,
    monitor_end_h: float,
    *,
    tolerance_min: float = 15.0,
) -> bool:
    """D5 Clínic/VitalDB: ventilador y monitor se apagan a la vez (<= 15 min).

    Estos eventos son posibles muertes o traslados no visibles en esas cohortes
    y deben reportarse aparte.
    """
    return abs(vent_end_h - monitor_end_h) * 60.0 <= tolerance_min


# ── Detección por cohorte ────────────────────────────────────────────────────

def trach_time_from_offset_rows(
    offsets_min: Sequence[float],
) -> Optional[float]:
    """Hora (min desde admisión) más temprana de traqueostomía, o ``None``."""
    valid = [float(o) for o in offsets_min if o is not None and float(o) == float(o)]
    return min(valid) if valid else None


def eicu_terminal_decision(
    *,
    unit_discharge_status: Optional[str],
    unit_discharge_offset_min: Optional[float],
    last_disconnect_min: Optional[float],
    t0_min: float,
    failure_window_h: float,
) -> CensorDecision:
    """D5 en eICU: ``unitdischargestatus == 'Expired'``.

    - Muerte dentro de la ventana de fallo tras la última desconexión -> censura
      en la desconexión.
    - Muerte ventilado (sin desconexión posterior) -> censura en la muerte.
    """
    if str(unit_discharge_status or "").strip().lower() != "expired":
        return CensorDecision(None, None)
    if unit_discharge_offset_min is None:
        return CensorDecision(None, None)
    t_death_h = (float(unit_discharge_offset_min) - float(t0_min)) / 60.0
    if last_disconnect_min is None:
        return CensorDecision("death_at_vent", t_death_h)
    t_disc_h = (float(last_disconnect_min) - float(t0_min)) / 60.0
    if (t_death_h - t_disc_h) <= failure_window_h:
        return CensorDecision("terminal_extubation", t_disc_h)
    return CensorDecision(None, None)


def trach_decision(
    trach_times_h: Sequence[float],
    *,
    icd9_marked_without_time: bool,
    last_vent_end_h: float,
) -> CensorDecision:
    """Censura por traqueostomía (D5).

    Si solo hay marca ICD-9 sin hora, se censura en el último fin de
    ventilación y se reporta (``trach_time_unknown``).
    """
    if trach_times_h:
        return CensorDecision("trach", float(min(trach_times_h)))
    if icd9_marked_without_time:
        return CensorDecision("trach_time_unknown", float(last_vent_end_h))
    return CensorDecision(None, None)


def d5_censor_for_window(
    *,
    failure_window_h: float,
    last_disconnect_h: Optional[float],
    trach_time_h: Optional[float] = None,
    trach_time_unknown: bool = False,
    death_time_h: Optional[float] = None,
    died_ventilated: bool = False,
) -> CensorDecision:
    """Decisión D5 unificada (horas desde t0) para UNA ventana de fallo.

    Orden: primero traqueostomía; después muerte. La variante "muerte dentro de
    la ventana tras la desconexión" censura en la desconexión; "muerte
    ventilado" censura en la muerte.
    """
    dec = trach_decision(
        [trach_time_h] if trach_time_h is not None else [],
        icd9_marked_without_time=trach_time_unknown,
        last_vent_end_h=(last_disconnect_h if last_disconnect_h is not None else 0.0),
    )
    if dec.censor_cause is not None:
        return dec
    if death_time_h is not None:
        if died_ventilated or last_disconnect_h is None:
            return CensorDecision("death_at_vent", float(death_time_h))
        gap = float(death_time_h) - float(last_disconnect_h)
        if 0.0 <= gap <= failure_window_h:
            return CensorDecision("terminal_extubation", float(last_disconnect_h))
    return CensorDecision(None, None)
