"""
qc/sqi.py
=========
Índices de Calidad de Señal (SQI) por ventana para ECG, PPG y ABP.

Implementa:
  ECG: kurtosis-based SQI (bSQI proxy sin beat detection)
  PPG: skewness-SQI + perfusion index + template matching
  ABP: rango fisiológico + consistencia de presión de pulso

Cada función devuelve un array de SQI por sub-ventana de longitud `window_sec`,
con paso `step_sec`. El array tiene la misma longitud que el número de
sub-ventanas que caben en la señal de entrada.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from scipy import stats
from scipy.signal import find_peaks

logger = logging.getLogger(__name__)


# ── Utilidades compartidas ─────────────────────────────────────────────────────

def _iter_windows(
    signal: np.ndarray,
    fs: float,
    window_sec: float,
    step_sec: float,
):
    """
    Generador de sub-ventanas.
    Yield: (start_idx, end_idx, window_values)
    """
    n_win = int(window_sec * fs)
    n_step = int(step_sec * fs)
    if n_win <= 0 or n_step <= 0 or len(signal) < n_win:
        return
    start = 0
    while start + n_win <= len(signal):
        yield start, start + n_win, signal[start: start + n_win]
        start += n_step


def _valid_fraction(window: np.ndarray) -> float:
    """Fracción de muestras no-NaN en la ventana."""
    return float(np.isfinite(window).mean())


# ── ECG SQI ───────────────────────────────────────────────────────────────────

def ecg_sqi(
    signal: np.ndarray,
    fs: float,
    window_sec: float = 10.0,
    step_sec: float = 5.0,
    kurtosis_min: float = 5.0,
    kurtosis_max: float = 50.0,
) -> np.ndarray:
    """
    SQI de ECG basado en kurtosis (proxy del bSQI sin necesidad de detección de QRS).

    Un ECG limpio tiene alta kurtosis (picos R pronunciados).
    Señal de ruido o flatline → kurtosis baja o muy alta.

    Returns:
        Array float32 de SQI por sub-ventana ∈ [0, 1].
        0 = rechazar, 1 = calidad perfecta.
    """
    sqi_values = []

    for _, _, win in _iter_windows(signal, fs, window_sec, step_sec):
        valid = win[np.isfinite(win)]
        if len(valid) < int(fs * 2):  # mínimo 2 s de datos válidos
            sqi_values.append(0.0)
            continue

        kurt = float(stats.kurtosis(valid, fisher=True))  # exceso de kurtosis
        # Normalizar: kurtosis en [kurtosis_min, kurtosis_max] → SQI en [0, 1]
        if kurt < kurtosis_min or kurt > kurtosis_max:
            sqi = 0.0
        else:
            sqi = (kurt - kurtosis_min) / (kurtosis_max - kurtosis_min)
            sqi = float(np.clip(sqi, 0.0, 1.0))

        sqi_values.append(sqi)

    return np.array(sqi_values, dtype=np.float32)


# ── PPG SQI ───────────────────────────────────────────────────────────────────

def ppg_sqi(
    signal: np.ndarray,
    fs: float,
    window_sec: float = 10.0,
    step_sec: float = 5.0,
    skewness_min: float = 0.5,
    perfusion_min: float = 0.001,
    template_corr_min: float = 0.8,
) -> np.ndarray:
    """
    SQI de PPG combinando tres métricas:
      1. Skewness-SQI: señal PPG debe ser asimétrica positiva
      2. Perfusion index: AC/DC ratio mínimo
      3. Template matching: correlación con template medio del ciclo

    Returns:
        Array float32 de SQI ∈ [0, 1] por sub-ventana.
    """
    sqi_values = []

    # Calcular template global (promedio de un ciclo típico de ~1 s)
    # para el template matching; se hace sobre toda la señal
    global_template = _compute_ppg_template(signal, fs)

    for _, _, win in _iter_windows(signal, fs, window_sec, step_sec):
        valid = win[np.isfinite(win)]
        if len(valid) < int(fs * 2):
            sqi_values.append(0.0)
            continue

        # 1. Skewness
        skew = float(abs(stats.skew(valid)))
        skew_ok = skew >= skewness_min

        # 2. Perfusion index (AC = pp amplitude / DC = mean)
        dc = float(np.mean(np.abs(valid)))
        ac = float(np.ptp(valid))  # peak-to-peak
        perf = ac / (dc + 1e-9)
        perf_ok = perf >= perfusion_min

        # 3. Template matching
        if global_template is not None and len(global_template) > 0:
            # Reescalar la ventana al tamaño del template
            win_resampled = np.interp(
                np.linspace(0, 1, len(global_template)),
                np.linspace(0, 1, len(valid)),
                valid,
            )
            corr = float(np.corrcoef(win_resampled, global_template)[0, 1])
            corr = max(0.0, corr)  # ignorar correlaciones negativas
            template_ok = corr >= template_corr_min
        else:
            corr = 1.0
            template_ok = True

        # SQI combinado: promedio ponderado (1/3 cada componente)
        sqi = (float(skew_ok) + float(perf_ok) + float(template_ok)) / 3.0
        sqi_values.append(sqi)

    return np.array(sqi_values, dtype=np.float32)


def _compute_ppg_template(signal: np.ndarray, fs: float) -> Optional[np.ndarray]:
    """
    Extrae un template de ciclo PPG promediado.
    Detecta picos (sistólicos) con find_peaks y promedia los ciclos.
    """
    valid = signal[np.isfinite(signal)]
    if len(valid) < int(fs * 5):
        return None

    # Normalizar
    v_norm = (valid - valid.mean()) / (valid.std() + 1e-9)

    # Detección de picos sistólicos: prominencia mínima 0.3, distancia 0.4–2.0 s
    min_dist = int(fs * 0.4)
    max_dist = int(fs * 2.0)
    peaks, _ = find_peaks(v_norm, prominence=0.3, distance=min_dist)

    if len(peaks) < 3:
        return None

    # Extraer ciclos de longitud mediana
    intervals = np.diff(peaks)
    median_len = int(np.median(intervals))
    if median_len < 10:
        return None

    cycles = []
    for p in peaks[:-1]:
        start = p - median_len // 4
        end = start + median_len
        if start >= 0 and end < len(v_norm):
            cycle = v_norm[start:end]
            cycles.append(cycle)

    if not cycles:
        return None

    return np.mean(cycles, axis=0).astype(np.float32)


# ── ABP SQI ───────────────────────────────────────────────────────────────────

def abp_sqi(
    signal: np.ndarray,
    fs: float,
    window_sec: float = 10.0,
    step_sec: float = 5.0,
    sys_min: float = 40.0,
    sys_max: float = 300.0,
    dia_min: float = 10.0,
    dia_max: float = 180.0,
    pulse_pressure_min: float = 5.0,
) -> np.ndarray:
    """
    SQI de ABP basado en rango fisiológico y consistencia de presión de pulso.

    Criterios:
      - Sistólica (picos) en [sys_min, sys_max] mmHg
      - Diastólica (valles) en [dia_min, dia_max] mmHg
      - Presión de pulso (Sys - Dia) >= pulse_pressure_min mmHg
      - Al menos 70% de muestras válidas (no-NaN)

    Returns:
        Array float32 de SQI ∈ [0, 1] por sub-ventana.
    """
    sqi_values = []

    for _, _, win in _iter_windows(signal, fs, window_sec, step_sec):
        valid = win[np.isfinite(win)]
        if len(valid) < int(fs * 2):
            sqi_values.append(0.0)
            continue

        # Detectar picos y valles con find_peaks
        min_dist = max(1, int(fs * 0.3))  # mínimo 0.3 s entre latidos
        peaks, _ = find_peaks(valid, distance=min_dist)
        valleys, _ = find_peaks(-valid, distance=min_dist)

        if len(peaks) < 2 or len(valleys) < 2:
            sqi_values.append(0.0)
            continue

        sys_vals = valid[peaks]
        dia_vals = valid[valleys]

        # Verificar rangos fisiológicos
        sys_ok = bool(np.all((sys_vals >= sys_min) & (sys_vals <= sys_max)))
        dia_ok = bool(np.all((dia_vals >= dia_min) & (dia_vals <= dia_max)))

        # Presión de pulso
        sys_mean = float(np.mean(sys_vals))
        dia_mean = float(np.mean(dia_vals))
        pp = sys_mean - dia_mean
        pp_ok = pp >= pulse_pressure_min

        # Completeness
        valid_frac = _valid_fraction(win)
        completeness_ok = valid_frac >= 0.7

        # SQI: todos los criterios binarios → promedio
        sqi = (float(sys_ok) + float(dia_ok) + float(pp_ok) + float(completeness_ok)) / 4.0
        sqi_values.append(sqi)

    return np.array(sqi_values, dtype=np.float32)


# ── Dispatcher ─────────────────────────────────────────────────────────────────

def compute_sqi(
    signal_name: str,
    signal: np.ndarray,
    fs: float,
    sqi_config: dict,
) -> np.ndarray:
    """
    Calcula el SQI para una señal usando la función apropiada.

    Args:
        signal_name: 'ECG' | 'PPG' | 'ABP'
        signal: Array float32 de la señal
        fs: Frecuencia de muestreo (Hz)
        sqi_config: Bloque 'sqi' de harmonize.yaml

    Returns:
        Array float32 de SQI por sub-ventana.
    """
    window_sec = float(sqi_config.get("window_sec", 10.0))
    step_sec = float(sqi_config.get("step_sec", 5.0))

    if len(signal) == 0:
        return np.array([], dtype=np.float32)

    if signal_name == "ECG":
        cfg = sqi_config.get("ECG", {})
        return ecg_sqi(
            signal, fs, window_sec, step_sec,
            kurtosis_min=float(cfg.get("kurtosis_min", 5.0)),
            kurtosis_max=float(cfg.get("kurtosis_max", 50.0)),
        )

    elif signal_name == "PPG":
        cfg = sqi_config.get("PPG", {})
        return ppg_sqi(
            signal, fs, window_sec, step_sec,
            skewness_min=float(cfg.get("skewness_min", 0.5)),
            perfusion_min=float(cfg.get("perfusion_min", 0.001)),
            template_corr_min=float(cfg.get("template_corr_min", 0.8)),
        )

    elif signal_name == "ABP":
        cfg = sqi_config.get("ABP", {})
        return abp_sqi(
            signal, fs, window_sec, step_sec,
            sys_min=float(cfg.get("sys_min_mmhg", 40.0)),
            sys_max=float(cfg.get("sys_max_mmhg", 300.0)),
            dia_min=float(cfg.get("dia_min_mmhg", 10.0)),
            dia_max=float(cfg.get("dia_max_mmhg", 180.0)),
            pulse_pressure_min=float(cfg.get("pulse_pressure_min_mmhg", 5.0)),
        )

    else:
        logger.warning("SQI no implementado para señal '%s'", signal_name)
        return np.ones(0, dtype=np.float32)
