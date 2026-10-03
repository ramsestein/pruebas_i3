"""
common/signal_death.py
======================
Detección de muerte por señales en Clínic/VitalDB (Fase 1, ajuste 2).

Regla
-----
Muerte por señales = FC 0 (o asistolia) **mantenida >= 10 min**, **sin
recuperación hasta el final del monitor**, y además al menos una de:
  a) deterioro progresivo en los 30 min previos (bradicardia con descenso
     progresivo de FC, desaturación o MAP < 40);
  b) pérdida simultánea de pulsatilidad en ABP o PPG.

Una caída brusca de FC normal a 0, con el resto de canales normales, es una
**desconexión**, NO una muerte.

La hora de muerte detectada se usa EXACTAMENTE como DEATHTIME en eICU/MIMIC
(``d5_censor_for_window``): muerte ventilado → ``death_at_vent``; muerte dentro
de la ventana de fallo tras la última desconexión → ``terminal_extubation``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

# Umbrales (documentados; mismos para las 4 cohortes).
HR_ZERO_BPM: float = 5.0          # FC considerada "0/asistolia"
MIN_ZERO_MIN: float = 10.0        # duración mínima de la FC 0 mantenida
DETERIORATION_WINDOW_MIN: float = 30.0
MAP_LOW_MMHG: float = 40.0
SPO2_DROP_POINTS: float = 5.0     # caída de SpO2 respecto al máximo de la ventana
SPO2_LOW_PCT: float = 90.0
BRADYCARDIA_BPM: float = 40.0
PROGRESSIVE_DROP_BPM: float = 20.0
ABRUPT_DESCENT_MIN: float = 2.0   # descenso "brusco" (desconexión)
AMP_PRESENT_MMHG: float = 5.0     # pulsatilidad presente
AMP_LOST_MMHG: float = 1.0        # pulsatilidad perdida

NOT_DEATH = "not_death"


@dataclass
class Series:
    """Serie (t_h, v) ordenada por tiempo."""
    t: np.ndarray
    v: np.ndarray

    @staticmethod
    def of(pairs: Sequence[tuple[float, float]]) -> "Series":
        if not pairs:
            return Series(np.empty(0), np.empty(0))
        a = np.asarray(sorted(pairs), dtype=np.float64)
        return Series(a[:, 0], a[:, 1])

    @property
    def empty(self) -> bool:
        return self.t.size == 0

    def window(self, t0_h: float, t1_h: float) -> "Series":
        m = (self.t >= t0_h) & (self.t <= t1_h)
        return Series(self.t[m], self.v[m])

    def value_at(self, t_h: float, tol_min: float = 2.0) -> Optional[float]:
        if self.empty:
            return None
        m = np.abs(self.t - t_h) <= tol_min / 60.0
        return float(self.v[m].max()) if m.any() else None


@dataclass
class DeathDecision:
    is_death: bool
    death_time_h: Optional[float] = None
    reason: str = NOT_DEATH
    detail: dict = field(default_factory=dict)


def detect_signal_death(
    hr: Series,
    *,
    spo2: Optional[Series] = None,
    map_: Optional[Series] = None,
    amp_abp: Optional[Series] = None,
    amp_ppg: Optional[Series] = None,
    hr_zero_bpm: float = HR_ZERO_BPM,
    min_zero_min: float = MIN_ZERO_MIN,
    det_window_min: float = DETERIORATION_WINDOW_MIN,
    map_low: float = MAP_LOW_MMHG,
    spo2_drop: float = SPO2_DROP_POINTS,
    spo2_low: float = SPO2_LOW_PCT,
    brady_bpm: float = BRADYCARDIA_BPM,
    progressive_drop: float = PROGRESSIVE_DROP_BPM,
    abrupt_min: float = ABRUPT_DESCENT_MIN,
    amp_present: float = AMP_PRESENT_MMHG,
    amp_lost: float = AMP_LOST_MMHG,
) -> DeathDecision:
    """Aplica la regla de muerte por señales (ver docstring del módulo)."""
    if hr is None or hr.empty:
        return DeathDecision(False, None, "no_hr")

    t = hr.t
    v = hr.v
    below = v <= hr_zero_bpm

    # La racha final bajo el umbral debe llegar hasta el FINAL del monitor.
    if not bool(below[-1]):
        return DeathDecision(False, None, "hr_not_zero_at_end")

    i = len(below) - 1
    while i > 0 and below[i - 1]:
        i -= 1
    death_t = float(t[i])
    zero_min = (float(t[-1]) - death_t) * 60.0
    if zero_min < min_zero_min:
        return DeathDecision(False, None, "zero_run_too_short",
                             {"zero_min": zero_min})

    # (a) Deterioro progresivo en la ventana previa (sin incluir la racha cero).
    win = hr.window(death_t - det_window_min / 60.0, death_t - 1e-9)
    det_a: list[str] = []
    if not win.empty:
        hi, lo = float(win.v.max()), float(win.v.min())
        # Momento de la última FC "normal" antes de la racha cero.
        normal = hr.t[hr.v >= 50.0]
        normal_before = normal[normal <= death_t]
        descent_min = (
            (death_t - float(normal_before[-1])) * 60.0
            if normal_before.size else float("inf")
        )
        if hi >= 50.0 and (hi - lo) >= progressive_drop and descent_min > abrupt_min:
            det_a.append("descenso_progresivo_fc")
        elif lo < brady_bpm:
            det_a.append("bradicardia")
    else:
        descent_min = float("inf")

    if spo2 is not None and not spo2.empty:
        sw = spo2.window(death_t - det_window_min / 60.0, death_t)
        if not sw.empty:
            if float(sw.v.max()) - float(sw.v.min()) >= spo2_drop:
                det_a.append("desaturacion")
            elif float(sw.v.min()) < spo2_low:
                det_a.append("spo2_baja")
    if map_ is not None and not map_.empty:
        mw = map_.window(death_t - det_window_min / 60.0, death_t)
        if not mw.empty and float(mw.v.min()) < map_low:
            det_a.append("map_baja")

    # (b) Pérdida simultánea de pulsatilidad (ABP o PPG).
    det_b: list[str] = []
    for name, amp in (("abp", amp_abp), ("ppg", amp_ppg)):
        if amp is None or amp.empty:
            continue
        before = amp.window(death_t - det_window_min / 60.0, death_t - 1.0 / 60.0)
        at = amp.window(death_t, min(death_t + 2.0 / 60.0, float(amp.t[-1])))
        if (not before.empty and float(before.v.max()) >= amp_present
                and not at.empty and float(at.v.max()) < amp_lost):
            det_b.append(f"pulsatilidad_{name}")

    if det_a or det_b:
        return DeathDecision(True, death_t, "deterioration" if det_a else "pulsatility_loss",
                             {"det_a": det_a, "det_b": det_b, "zero_min": zero_min})
    return DeathDecision(False, None, "abrupt_drop_disconnection",
                         {"zero_min": zero_min})
