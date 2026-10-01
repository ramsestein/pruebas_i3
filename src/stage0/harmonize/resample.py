"""
harmonize/resample.py
=====================
Resampleo de waveforms a la frecuencia objetivo con antialiasing correcto.

Usa scipy.signal.resample_poly, que incluye filtro antialiasing por diseño
cuando se baja la frecuencia (down-sampling). El ratio up/down se calcula
como la fracción irreducible más cercana a target_fs / fs_native.

Convención de entrada/salida:
  - Entrada: array float32 de muestras uniformemente espaciadas
  - Salida: array float32 resampleado + nuevo array de timestamps uniformes
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np
from scipy.signal import resample_poly

logger = logging.getLogger(__name__)

# Máximo factor de reducción de fracción up/down para evitar aritmética exótica
MAX_POLY_FACTOR = 1000


def _up_down_ratio(fs_native: float, fs_target: float) -> tuple[int, int]:
    """
    Calcula la fracción irreducible up/down para resample_poly.

    Ejemplo: 500 Hz → 125 Hz ⟹ up=1, down=4
             256 Hz → 125 Hz ⟹ up=125, down=256
    """
    # Trabajar con enteros redondeados para evitar errores de punto flotante
    # Multiplicar por 1000 para preservar decimales (ej. 62.5 Hz)
    scale = 1000
    num = round(fs_target * scale)
    den = round(fs_native * scale)
    g = math.gcd(num, den)
    up = num // g
    down = den // g

    # Limitar el tamaño de los factores
    if max(up, down) > MAX_POLY_FACTOR:
        # Aproximar con la fracción más simple posible
        ratio = fs_target / fs_native
        # Buscar numerador/denominador pequeños con error < 0.1%
        for d in range(1, MAX_POLY_FACTOR + 1):
            u = round(ratio * d)
            if u == 0:
                continue
            if abs(u / d - ratio) / ratio < 0.001:
                g2 = math.gcd(u, d)
                up, down = u // g2, d // g2
                if max(up, down) <= MAX_POLY_FACTOR:
                    break
        else:
            logger.warning(
                "No se encontró fracción simple para %.2f→%.2f Hz; "
                "usando up=%d down=%d",
                fs_native, fs_target, up, down,
            )

    return int(up), int(down)


def resample_signal(
    values: np.ndarray,
    fs_native: float,
    fs_target: float,
    timestamps_rel: Optional[np.ndarray] = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Resamplea una señal de fs_native a fs_target con antialiasing.

    Args:
        values: Array float32 de muestras (N,)
        fs_native: Frecuencia de muestreo nativa (Hz)
        fs_target: Frecuencia de muestreo objetivo (Hz)
        timestamps_rel: Array float64 de timestamps relativos (N,) en segundos.
                        Si None, se generan a partir de fs_native asumiendo
                        muestreo uniforme desde 0.

    Returns:
        (resampled_values, new_timestamps_rel)
        - resampled_values: float32 array de longitud M
        - new_timestamps_rel: float64 array de timestamps uniformes en segundos
    """
    if len(values) == 0:
        return np.empty(0, dtype=np.float32), np.empty(0, dtype=np.float64)

    values = np.asarray(values, dtype=np.float32)

    # Determinar el timestamp de inicio y duración
    if timestamps_rel is not None and len(timestamps_rel) > 0:
        t_start = float(timestamps_rel[0])
        # Duración total basada en el timestamps real (no asumida)
        t_end = float(timestamps_rel[-1])
        duration_s = t_end - t_start
    else:
        t_start = 0.0
        duration_s = (len(values) - 1) / fs_native

    # Si ya está a la frecuencia objetivo, devolver directamente
    if abs(fs_native - fs_target) < 0.01:
        n_out = len(values)
        new_times = np.linspace(t_start, t_start + duration_s, n_out, dtype=np.float64)
        return values.copy(), new_times

    # Calcular ratio
    up, down = _up_down_ratio(fs_native, fs_target)

    # Rellenar NaN antes del resampleo (resample_poly no soporta NaN)
    has_nan = np.isnan(values).any()
    if has_nan:
        values_filled = _fill_nan_linear(values)
    else:
        values_filled = values

    # Resampleo con antialiasing
    resampled = resample_poly(values_filled, up=up, down=down)
    resampled = np.asarray(resampled, dtype=np.float32)

    # Reconstruir máscara de NaN en la señal resampleada (interpolación de índices)
    if has_nan:
        nan_mask_orig = np.isnan(values).astype(np.float32)
        nan_mask_res = resample_poly(nan_mask_orig, up=up, down=down)
        # Si la máscara interpolada supera 0.5 → NaN en la salida
        resampled[nan_mask_res > 0.5] = np.nan

    # Número de muestras esperado con la nueva frecuencia
    n_expected = round(duration_s * fs_target) + 1
    # Ajustar si resample_poly produce una muestra de más o de menos
    if len(resampled) != n_expected:
        logger.debug(
            "Longitud resampleada %d ≠ esperada %d (diferencia %+d); ajustando",
            len(resampled), n_expected, len(resampled) - n_expected,
        )
        if len(resampled) > n_expected:
            resampled = resampled[:n_expected]
        else:
            pad = np.full(n_expected - len(resampled), np.nan, dtype=np.float32)
            resampled = np.concatenate([resampled, pad])

    new_times = np.linspace(t_start, t_start + duration_s, len(resampled), dtype=np.float64)

    logger.debug(
        "Resampleo %.2f→%.2f Hz (up=%d,down=%d): %d→%d muestras",
        fs_native, fs_target, up, down, len(values), len(resampled),
    )
    return resampled, new_times


def _fill_nan_linear(arr: np.ndarray) -> np.ndarray:
    """Interpolación lineal de NaN para permitir el resampleo."""
    import pandas as pd
    s = pd.Series(arr.astype(np.float64))
    filled = s.interpolate(method="linear", limit_direction="both").values
    return filled.astype(np.float32)


def verify_resample(
    original: np.ndarray,
    resampled: np.ndarray,
    fs_native: float,
    fs_target: float,
    band_check: Optional[tuple[float, float]] = None,
) -> dict:
    """
    Verificación de calidad del resampleo.

    Comprueba:
      1. Frecuencia de salida correcta (basada en longitud)
      2. No introducción de aliasing (potencia sobre fs_target/2 es < umbral)
      3. Preservación de energía en la banda de paso

    Args:
        original: Array original
        resampled: Array resampleado
        fs_native: Frecuencia nativa (Hz)
        fs_target: Frecuencia objetivo (Hz)
        band_check: (f_low, f_high) para verificar preservación de energía.
                    Si None, usa (0.5, fs_target/2 * 0.9).

    Returns:
        dict con métricas de verificación y 'passed' bool.
    """
    from scipy.signal import welch

    results: dict = {}

    # 1. Longitud esperada
    n_orig = len(original)
    n_res = len(resampled)
    dur_s = n_orig / fs_native
    n_expected = round(dur_s * fs_target) + 1
    len_ok = abs(n_res - n_expected) <= 2
    results["length_ok"] = bool(len_ok)
    results["length_resampled"] = n_res
    results["length_expected"] = n_expected

    # 2. Aliasing: potencia sobre fs_target/2 en la señal resampleada
    nyq_target = fs_target / 2.0
    f_res, pxx_res = welch(
        resampled[~np.isnan(resampled)],
        fs=fs_target,
        nperseg=min(256, len(resampled) // 4),
        scaling="spectrum",
    )
    mask_above_nyq = f_res > nyq_target * 0.95  # margen 5%
    total_power = np.sum(pxx_res)
    alias_power = np.sum(pxx_res[mask_above_nyq])
    alias_ratio = float(alias_power / total_power) if total_power > 0 else 0.0
    alias_ok = alias_ratio < 0.01  # < 1% de potencia sobre Nyquist
    results["alias_power_ratio"] = alias_ratio
    results["alias_ok"] = alias_ok

    # 3. Preservación de energía en la banda de paso
    if band_check is None:
        band_check = (0.5, nyq_target * 0.9)
    f_low, f_high = band_check

    f_orig, pxx_orig = welch(
        original[~np.isnan(original)],
        fs=fs_native,
        nperseg=min(256, len(original) // 4),
        scaling="spectrum",
    )
    band_orig = np.sum(pxx_orig[(f_orig >= f_low) & (f_orig <= f_high)])
    band_res = np.sum(pxx_res[(f_res >= f_low) & (f_res <= f_high)])
    energy_ratio = float(band_res / band_orig) if band_orig > 0 else np.nan
    energy_ok = 0.8 <= energy_ratio <= 1.2  # ±20% de energía
    results["band_energy_ratio"] = energy_ratio
    results["band_energy_ok"] = energy_ok
    results["band_hz"] = band_check

    results["passed"] = bool(len_ok and alias_ok and energy_ok)
    return results
