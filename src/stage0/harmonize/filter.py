"""
harmonize/filter.py
===================
Filtrado paso-banda de fase cero (sosfiltfilt) para ECG, PPG y ABP.

Usa Butterworth de orden configurable con diseño sosfilt para
estabilidad numérica. sosfiltfilt aplica el filtro en dos pasadas
(forward + backward) → fase cero y orden efectivo 2×N.

Parámetros por defecto (de config):
  ECG: 0.5–40 Hz, orden 4
  PPG: 0.5–8 Hz,  orden 4
  ABP: 0.5–20 Hz, orden 4
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from scipy.signal import butter, sosfiltfilt

logger = logging.getLogger(__name__)


def bandpass_filter(
    values: np.ndarray,
    fs: float,
    lowcut: float,
    highcut: float,
    order: int = 4,
) -> np.ndarray:
    """
    Aplica un filtro paso-banda Butterworth de fase cero.

    Args:
        values: Array float32 de muestras (N,)
        fs: Frecuencia de muestreo (Hz)
        lowcut: Frecuencia de corte inferior (Hz)
        highcut: Frecuencia de corte superior (Hz)
        order: Orden del filtro Butterworth

    Returns:
        Array float32 filtrado, misma longitud que la entrada.
        NaN se propagan (se rellena temporalmente para el filtrado,
        luego se restaura la máscara de NaN).
    """
    if len(values) == 0:
        return values.copy()

    values = np.asarray(values, dtype=np.float32)
    nyq = fs / 2.0

    # Validar frecuencias de corte
    if lowcut >= nyq or highcut >= nyq:
        logger.warning(
            "Frecuencias de corte (%.2f–%.2f Hz) fuera del rango de Nyquist (%.2f Hz); "
            "devolviendo señal sin filtrar",
            lowcut, highcut, nyq,
        )
        return values.copy()

    if lowcut <= 0 or highcut <= 0 or lowcut >= highcut:
        raise ValueError(
            f"Frecuencias de corte inválidas: lowcut={lowcut}, highcut={highcut}"
        )

    # Diseño del filtro en formato SOS (Second Order Sections)
    sos = butter(order, [lowcut / nyq, highcut / nyq], btype="band", output="sos")

    # Máscara de NaN: rellenar antes de filtrar, restaurar después
    nan_mask = np.isnan(values)
    if nan_mask.any():
        values_filled = _fill_nan(values)
    else:
        values_filled = values

    # Filtrado de fase cero
    # padlen mínimo: 3 * max(len de sección SOS)
    padlen = min(3 * (sos.shape[0] * 6), len(values_filled) - 1)
    if padlen <= 0:
        logger.warning("Señal demasiado corta para filtrar (%d muestras)", len(values))
        return values.copy()

    try:
        if len(values_filled) > _CHUNK_SIZE:
            filtered = _chunked_sosfiltfilt(sos, values_filled.astype(np.float64))
        else:
            filtered = sosfiltfilt(sos, values_filled.astype(np.float64), padlen=padlen)
    except Exception as e:
        logger.warning("Error en sosfiltfilt: %s; devolviendo señal sin filtrar", e)
        return values.copy()

    filtered = filtered.astype(np.float32)

    # Restaurar NaN donde estaban
    if nan_mask.any():
        filtered[nan_mask] = np.nan

    return filtered


_CHUNK_SIZE = 2_000_000  # muestras por bloque para filtrar señales muy largas


def _chunked_sosfiltfilt(sos, x: np.ndarray, chunk: int = _CHUNK_SIZE) -> np.ndarray:
    """sosfiltfilt por bloques con solape para evitar OOM en señales largas."""
    n = len(x)
    pad = 3 * (sos.shape[0] * 6)
    out = np.empty(n, dtype=np.float64)
    for start in range(0, n, chunk):
        lo = max(0, start - pad)
        hi = min(n, start + chunk + pad)
        fseg = sosfiltfilt(sos, x[lo:hi])
        o_lo = start - lo
        o_hi = o_lo + min(chunk, n - start)
        out[start:start + (o_hi - o_lo)] = fseg[o_lo:o_hi]
    return out


def _fill_nan(arr: np.ndarray) -> np.ndarray:
    """Interpolación lineal simple para rellenar NaN antes del filtrado."""
    import pandas as pd
    s = pd.Series(arr.astype(np.float64))
    return s.interpolate(method="linear", limit_direction="both").values.astype(np.float32)


def apply_filters_from_config(
    waveforms: dict[str, np.ndarray],
    fs: float,
    filter_config: dict,
) -> dict[str, np.ndarray]:
    """
    Aplica los filtros configurados a cada señal.

    Args:
        waveforms: Dict signal_name → array float32 ya resampleado
        fs: Frecuencia de muestreo (igual para todos, ya resampleado)
        filter_config: Bloque 'filters' de harmonize.yaml

    Returns:
        Dict signal_name → array float32 filtrado
    """
    filtered: dict[str, np.ndarray] = {}
    for signal_name, values in waveforms.items():
        cfg = filter_config.get(signal_name)
        if cfg is None:
            logger.debug("Sin config de filtro para '%s'; copiando sin filtrar", signal_name)
            filtered[signal_name] = values.copy() if values is not None else values
            continue

        filtered[signal_name] = bandpass_filter(
            values=values,
            fs=fs,
            lowcut=float(cfg["lowcut_hz"]),
            highcut=float(cfg["highcut_hz"]),
            order=int(cfg.get("order", 4)),
        )
        logger.debug(
            "Filtrado %s: %.2f–%.2f Hz (orden %d)",
            signal_name,
            cfg["lowcut_hz"],
            cfg["highcut_hz"],
            cfg.get("order", 4),
        )

    return filtered
