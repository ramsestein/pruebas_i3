"""
common/monitor_observation.py
=============================
Fase 1.6b (punto 4) — **observación sin ventilador** con señal fisiológica.

La presencia de una pista de monitor no garantiza que haya paciente: tras una
desconexión (o una muerte) el box puede seguir grabando **ondas planas**
(FC = 0, SpO2 = 0/ausente). Una "observación sin ventilador" solo es válida si
contiene **FC o SpO2 con valores fisiológicos**:

- FC entre 20 y 250 lpm;
- SpO2 entre 50 y 100 %.

Sin esa condición, un evento cuya desconexión va seguida de ondas planas se
marcaría como extubación confirmada cuando en realidad es ``end_of_record``.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

import numpy as np

from src.common.episodes import Span, merge_spans

# Rangos fisiológicos (extremos inclusivos).
HR_PHYSIOLOGICAL_RANGE: tuple[float, float] = (20.0, 250.0)
SPO2_PHYSIOLOGICAL_RANGE: tuple[float, float] = (50.0, 100.0)

PHYSIOLOGICAL_RANGES: dict[str, tuple[float, float]] = {
    "HR": HR_PHYSIOLOGICAL_RANGE,
    "SpO2": SPO2_PHYSIOLOGICAL_RANGE,
}


def range_for_track(track_name: str) -> Optional[tuple[float, float]]:
    """Rango fisiológico de una pista, o ``None`` si no es una constante vital."""
    low = track_name.lower()
    if "sat_o2" in low or "spo2" in low:
        return SPO2_PHYSIOLOGICAL_RANGE
    if "hr" in low:
        return HR_PHYSIOLOGICAL_RANGE
    return None


def physiological_mask(
    values: Sequence[float] | np.ndarray,
    low: float,
    high: float,
) -> np.ndarray:
    """Máscara booleana de valores fisiológicos (NaN -> ``False``)."""
    arr = np.asarray(values, dtype=np.float64)
    return np.isfinite(arr) & (arr >= low) & (arr <= high)


def physiological_extent(
    times: Iterable[float],
    values: Sequence[float] | np.ndarray,
    low: float,
    high: float,
) -> tuple[Optional[float], Optional[float], int]:
    """``(t_min, t_max, n)`` de los valores fisiológicos (``None`` si no hay)."""
    t = np.asarray(list(times), dtype=np.float64)
    mask = physiological_mask(values, low, high) & np.isfinite(t)
    if not mask.any():
        return None, None, 0
    tt = t[mask]
    return float(tt.min()), float(tt.max()), int(mask.sum())


def physiological_spans(
    series: Iterable[tuple[Iterable[float], Sequence[float]]],
    low: float,
    high: float,
    gap_h: float,
) -> list[Span]:
    """Tramos de observación fisiológica a partir de series ``(t, v)``."""
    spans: list[Span] = []
    for times, values in series:
        lo, hi, n = physiological_extent(times, values, low, high)
        if lo is not None and hi is not None and hi > lo:
            spans.append(Span(lo, hi))
        elif lo is not None and n >= 1:
            spans.append(Span(lo, lo))
    return merge_spans(spans, gap_h)
