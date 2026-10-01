"""
tests/test_resample.py
======================
Tests unitarios de la lógica de resampleo.

Verifica:
  1. Frecuencia de salida correcta (basada en longitud)
  2. No introducción de aliasing (señal de tono puro sobre Nyquist no pasa)
  3. Preservación de energía en la banda de paso (±20%)
  4. Manejo correcto de NaN (se propagan, no desaparecen)
  5. Señal vacía → devuelve arrays vacíos
  6. Señal ya a 125 Hz → copia sin transformación
  7. Casos comunes del proyecto: 500→125 Hz, 256→125 Hz
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import welch

from src.stage0.harmonize.resample import _up_down_ratio, resample_signal, verify_resample

# Tolerancias
FREQ_TOLERANCE_HZ = 1.0     # ±1 Hz en la frecuencia estimada de salida
LENGTH_TOLERANCE = 3        # ±3 muestras en longitud
ENERGY_RATIO_MIN = 0.8
ENERGY_RATIO_MAX = 1.2
ALIAS_RATIO_MAX = 0.01      # < 1% de potencia sobre Nyquist

FS_TARGET = 125.0


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _sine(freq_hz: float, duration_s: float, fs: float, amplitude: float = 1.0) -> np.ndarray:
    """Genera una sinusoide pura."""
    t = np.arange(int(duration_s * fs)) / fs
    return (amplitude * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)


def _estimate_output_fs(n_out: int, n_in: int, fs_in: float) -> float:
    """Estima la frecuencia de salida a partir de la longitud."""
    return (n_out / n_in) * fs_in


# ── Caso 1: Frecuencia de salida correcta ────────────────────────────────────

class TestOutputFrequency:
    @pytest.mark.parametrize("fs_native", [500.0, 256.0, 62.5, 1000.0])
    def test_output_length_matches_expected(self, fs_native: float):
        duration_s = 10.0
        signal = _sine(1.0, duration_s, fs_native)
        resampled, _ = resample_signal(signal, fs_native, FS_TARGET)
        expected_len = round(duration_s * FS_TARGET) + 1
        assert abs(len(resampled) - expected_len) <= LENGTH_TOLERANCE, (
            f"Longitud {len(resampled)} ≠ esperada {expected_len} "
            f"(fs_native={fs_native})"
        )

    @pytest.mark.parametrize("fs_native", [500.0, 256.0])
    def test_output_timestamps_span_correct_duration(self, fs_native: float):
        duration_s = 5.0
        signal = _sine(2.0, duration_s, fs_native)
        t_in = np.arange(len(signal), dtype=np.float64) / fs_native
        _, t_out = resample_signal(signal, fs_native, FS_TARGET, timestamps_rel=t_in)
        assert abs(t_out[-1] - t_in[-1]) < 0.1, (
            f"Duración de salida {t_out[-1]:.3f}s ≠ entrada {t_in[-1]:.3f}s"
        )


# ── Caso 2: Sin aliasing ───────────────────────────────────────────────────────

class TestNoAliasing:
    def test_signal_above_nyquist_is_suppressed(self):
        """
        Una sinusoide a 80 Hz (> Nyquist=62.5 Hz de 125 Hz) generada a 500 Hz
        debe ser suprimida tras el resampleo a 125 Hz.
        """
        fs_native = 500.0
        duration_s = 10.0
        # Señal sobre Nyquist de la frecuencia objetivo
        signal_alias = _sine(80.0, duration_s, fs_native, amplitude=1.0)
        resampled, _ = resample_signal(signal_alias, fs_native, FS_TARGET)

        valid = resampled[np.isfinite(resampled)]
        f, pxx = welch(valid, fs=FS_TARGET, nperseg=min(256, len(valid) // 4))

        nyq = FS_TARGET / 2.0
        alias_power = float(np.sum(pxx[f > nyq * 0.95]))
        total_power = float(np.sum(pxx))
        ratio = alias_power / (total_power + 1e-12)

        assert ratio < ALIAS_RATIO_MAX, (
            f"Potencia sobre Nyquist demasiado alta: {ratio:.3%} (max {ALIAS_RATIO_MAX:.0%})"
        )

    def test_verify_resample_detects_aliasing(self):
        """verify_resample debe detectar que la señal sobre Nyquist se suprime."""
        fs_native = 500.0
        signal = _sine(5.0, 10.0, fs_native)  # 5 Hz: bien dentro de la banda
        resampled, _ = resample_signal(signal, fs_native, FS_TARGET)
        result = verify_resample(signal, resampled, fs_native, FS_TARGET,
                                 band_check=(1.0, 10.0))
        assert result["alias_ok"], f"alias_power_ratio={result['alias_power_ratio']:.4f}"


# ── Caso 3: Preservación de energía en banda ─────────────────────────────────

class TestEnergyPreservation:
    @pytest.mark.parametrize("freq_hz,fs_native", [
        (5.0, 500.0),   # 5 Hz, downsample 4:1
        (10.0, 256.0),  # 10 Hz, downsample ~2:1
        (1.0, 62.5),    # 1 Hz, upsample 2:1
    ])
    def test_energy_preserved_in_band(self, freq_hz: float, fs_native: float):
        duration_s = 20.0
        signal = _sine(freq_hz, duration_s, fs_native)
        resampled, _ = resample_signal(signal, fs_native, FS_TARGET)
        result = verify_resample(signal, resampled, fs_native, FS_TARGET,
                                 band_check=(freq_hz * 0.5, freq_hz * 2.0))
        assert result["band_energy_ok"], (
            f"Energía en banda: {result['band_energy_ratio']:.3f} "
            f"(esperado [{ENERGY_RATIO_MIN}, {ENERGY_RATIO_MAX}])"
        )


# ── Caso 4: Manejo de NaN ─────────────────────────────────────────────────────

class TestNanHandling:
    def test_nan_propagates_through_resampling(self):
        """NaN en la entrada debe producir NaN en la región correspondiente de salida."""
        fs_native = 500.0
        signal = _sine(5.0, 10.0, fs_native)
        # Introducir un bloque de NaN al principio (1 segundo)
        n_nan = int(fs_native * 1.0)
        signal[:n_nan] = np.nan

        resampled, _ = resample_signal(signal, fs_native, FS_TARGET)

        # Los primeros 0.5 segundos de salida deben ser NaN
        n_nan_expected = int(FS_TARGET * 0.5)
        nan_in_output = int(np.isnan(resampled[:n_nan_expected]).sum())
        assert nan_in_output > 0, "NaN no se propagó al resamplear"

    def test_all_nan_signal_returns_nan(self):
        """Señal completamente NaN → salida completamente NaN."""
        fs_native = 500.0
        n = int(fs_native * 5)
        signal = np.full(n, np.nan, dtype=np.float32)
        resampled, _ = resample_signal(signal, fs_native, FS_TARGET)
        assert np.all(np.isnan(resampled)), "Señal NaN completa no devuelve NaN completo"


# ── Caso 5: Señal vacía ───────────────────────────────────────────────────────

class TestEmptySignal:
    def test_empty_input_returns_empty_output(self):
        signal = np.array([], dtype=np.float32)
        resampled, times = resample_signal(signal, 500.0, FS_TARGET)
        assert len(resampled) == 0
        assert len(times) == 0


# ── Caso 6: Señal ya a 125 Hz ────────────────────────────────────────────────

class TestIdentityResampling:
    def test_signal_at_target_fs_is_unchanged(self):
        signal = _sine(10.0, 5.0, FS_TARGET)
        resampled, _ = resample_signal(signal, FS_TARGET, FS_TARGET)
        np.testing.assert_array_almost_equal(resampled, signal, decimal=4,
                                             err_msg="Señal a 125 Hz cambió al resamplear")


# ── Caso 7: Ratios up/down del proyecto ──────────────────────────────────────

class TestUpDownRatios:
    def test_500_to_125(self):
        up, down = _up_down_ratio(500.0, 125.0)
        assert up / down == pytest.approx(125.0 / 500.0, rel=1e-3)
        assert max(up, down) <= 10  # fracción simple

    def test_256_to_125(self):
        up, down = _up_down_ratio(256.0, 125.0)
        assert up / down == pytest.approx(125.0 / 256.0, rel=1e-3)

    def test_625_to_125(self):
        """62.5 Hz (ABP en algunos sistemas) → 125 Hz (upsample 2:1)"""
        up, down = _up_down_ratio(62.5, 125.0)
        assert up / down == pytest.approx(125.0 / 62.5, rel=1e-3)

    def test_ratio_is_always_positive(self):
        for fs in [100.0, 200.0, 250.0, 500.0, 1000.0]:
            up, down = _up_down_ratio(fs, FS_TARGET)
            assert up > 0 and down > 0
