"""
common/short_events.py
======================
Clasificación por señal de eventos de ventilación de menos de 1 h (Fase 1.5,
punto 3).

**No** se usa la duración como criterio: un evento corto puede ser ventilación
invasiva real (p. ej. una prueba breve antes de extubar) o un artefacto. La
decisión usa:

- la **onda de presión de vía aérea** (AWP): amplitud pico-a-pico y número de
  ciclos (presión positiva cíclica = ventilación);
- la presencia de **volumen tidal** plausible.

Estados: ``ventilacion_invasiva_plausible`` | ``artefacto``.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

AWP_MIN_PTP_CMH2O: float = 5.0
AWP_MIN_CYCLES: int = 2
TV_MIN_ML: float = 50.0
TV_MAX_ML: float = 1500.0


def _awp_metrics(awp_v: np.ndarray) -> tuple[Optional[float], int]:
    finite = awp_v[np.isfinite(awp_v)] if awp_v.size else np.array([])
    if finite.size == 0:
        return None, 0
    ptp = float(np.nanmax(finite) - np.nanmin(finite))
    med = float(np.nanmedian(finite))
    above = finite > med
    cycles = int(np.sum(np.diff(above.astype(int)) == 1))
    return ptp, cycles


def classify_short_event(
    awp_values: np.ndarray,
    tv_values: np.ndarray,
) -> dict:
    """Clasifica un evento corto por su señal de ventilador."""
    awp_ptp, awp_cycles = _awp_metrics(np.asarray(awp_values, dtype=np.float64))
    tv_finite = np.asarray(tv_values, dtype=np.float64)
    tv_finite = tv_finite[np.isfinite(tv_finite)] if tv_finite.size else tv_finite
    tv_median = float(np.median(tv_finite)) if tv_finite.size else None

    reasons: list[str] = []
    if awp_ptp is None:
        reasons.append("sin_awp")
    else:
        reasons.append("awp_amplitud" if awp_ptp >= AWP_MIN_PTP_CMH2O else "awp_plana")
        if awp_cycles >= AWP_MIN_CYCLES:
            reasons.append("awp_ciclica")
    tv_ok = tv_median is not None and TV_MIN_ML <= tv_median <= TV_MAX_ML
    if tv_ok:
        reasons.append("tv_presente")

    plausible = (
        ("awp_amplitud" in reasons and "awp_ciclica" in reasons)
        or (tv_ok and awp_ptp is not None and awp_ptp >= 2.0)
    )
    return {
        "classification": (
            "ventilacion_invasiva_plausible" if plausible else "artefacto"
        ),
        "metrics": {
            "awp_n": int(np.asarray(awp_values).size),
            "awp_ptp": None if awp_ptp is None else round(awp_ptp, 2),
            "awp_cycles": int(awp_cycles),
            "tv_median": None if tv_median is None else round(tv_median, 1),
        },
        "reasons": reasons,
    }
