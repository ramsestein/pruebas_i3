"""
Tests de ``common/wave_derived.py`` (Fase 1.6d, punto 4).

Se construyen **ondas sintéticas** de presión (``AWP_WAV``) y flujo
(``FLOW_WAV``) con PIP/PEEP/RR/TV conocidos y se comprueba que el módulo los
recupera, que rechaza los artefactos (desconexión, tos, respiración incompleta)
y que la validación de Bland–Altman acepta/rechaza con los umbrales del punto 4.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.common.wave_derived import (
    AWP_SOURCE,
    FLOW_SOURCE,
    aggregate_by_minute,
    bland_altman,
    derive_from_waves,
    detect_breaths,
    fraction_within,
    median_by_minute,
    tidal_volumes,
    variable_is_usable,
)

FS = 125.0


def _synthetic_awp(
    *,
    rr_rpm: float = 15.0,
    peep: float = 5.0,
    pip: float = 20.0,
    duration_s: float = 60.0,
    fs: float = FS,
) -> tuple[np.ndarray, np.ndarray]:
    """Onda de presión con respiraciones cuadradas de PIP/PEEP conocidos."""
    t = np.arange(0.0, duration_s, 1.0 / fs)
    p = np.full_like(t, peep)
    period = 60.0 / rr_rpm
    t_insp, t_plateau, t_fall = 0.8, 0.2, 1.0
    start = 0.0
    while start + t_insp + t_plateau + t_fall < duration_s:
        m_rise = (t >= start) & (t < start + t_insp)
        p[m_rise] = peep + (pip - peep) * (t[m_rise] - start) / t_insp
        m_pl = (t >= start + t_insp) & (t < start + t_insp + t_plateau)
        p[m_pl] = pip
        m_fall = (t >= start + t_insp + t_plateau) & (
            t < start + t_insp + t_plateau + t_fall)
        p[m_fall] = pip - (pip - peep) * (
            t[m_fall] - start - t_insp - t_plateau) / t_fall
        start += period
    return t, p


class TestDetectBreaths:
    def test_recupera_pip_peep_rr(self):
        t, p = _synthetic_awp(rr_rpm=15.0, peep=5.0, pip=20.0)
        breaths = detect_breaths(t, p, fs=FS)
        valid = [b for b in breaths if not b.rejected]
        # 15 rpm durante ~58 s útiles -> 14-15 respiraciones.
        assert 14 <= len(valid) <= 15
        for b in valid:
            assert b.pip_cmh2o == pytest.approx(20.0, abs=0.6)
            assert b.peep_cmh2o == pytest.approx(5.0, abs=0.6)
        # RR ≈ 15 (detectadas por minuto).
        series = derive_from_waves(t, awp=p, fs=FS)
        assert series.rr_rpm
        first_minute = series.rr_rpm[min(series.rr_rpm)]
        assert first_minute == pytest.approx(15.0, abs=1.0)

    def test_peep_distinta_de_pip(self):
        t, p = _synthetic_awp(peep=8.0, pip=24.0)
        series = derive_from_waves(t, awp=p, fs=FS)
        minute = min(series.pip_cmh2o)
        assert series.pip_cmh2o[minute] == pytest.approx(24.0, abs=0.6)
        assert series.peep_cmh2o[minute] == pytest.approx(8.0, abs=0.6)
        assert series.peep_cmh2o[minute] != pytest.approx(
            series.pip_cmh2o[minute], abs=1.0)

    def test_desconexion_no_da_respiraciones_validas(self):
        t = np.arange(0.0, 30.0, 1.0 / FS)
        p = np.zeros_like(t)  # presión ≈ 0 -> circuito desconectado
        series = derive_from_waves(t, awp=p, fs=FS)
        assert series.disconnected
        assert series.n_breaths == 0
        assert not series.pip_cmh2o

    def test_tos_espiga_corta_rechazada(self):
        t, p = _synthetic_awp()
        # Espiga de 0.1 s (tos/aspiración) en medio del registro.
        m = (t >= 30.0) & (t < 30.1)
        p[m] = 40.0
        breaths = detect_breaths(t, p, fs=FS)
        reasons = [b.reject_reason for b in breaths if b.rejected]
        assert "too_short" in reasons
        valid = [b for b in breaths if not b.rejected]
        assert all(b.pip_cmh2o < 25.0 for b in valid)

    def test_respiracion_incompleta_en_el_borde(self):
        t, p = _synthetic_awp(duration_s=30.0)
        # Subida de presión en la última muestra sin bajada -> incompleta.
        p[-3:] = 25.0
        breaths = detect_breaths(t, p, fs=FS)
        assert any(b.reject_reason == "incomplete" for b in breaths)
        assert all(not b.rejected for b in breaths[:-1])


class TestTidalVolume:
    def _synthetic_flow(self, *, duration_s: float = 60.0, fs: float = FS,
                        tv_ml: float = 500.0, rr_rpm: float = 15.0,
                        drift_lpm: float = 0.0):
        """Flujo cuadrado (neto 0) con TV inspiratorio conocido + deriva lenta."""
        t = np.arange(0.0, duration_s, 1.0 / fs)
        f = np.zeros_like(t)
        period = 60.0 / rr_rpm
        # 1 s inspirando + 1 s espirando (mismo |flujo|) -> neto 0 por ciclo.
        f_insp_lpm = tv_ml / 1000.0 * 60.0 / 1.0  # L -> L/min en 1 s
        start = 0.0
        while start + 2.0 < duration_s:
            f[(t >= start) & (t < start + 1.0)] = f_insp_lpm
            f[(t >= start + 1.0) & (t < start + 2.0)] = -f_insp_lpm
            start += period
        f = f + drift_lpm
        return t, f

    def test_tv_recuperado(self):
        tf, f = self._synthetic_flow()
        segs = tidal_volumes(tf, f)
        assert segs
        assert np.median([tv for _, tv in segs]) == pytest.approx(500.0, rel=0.03)
        # PEEP/PIP derivadas de la presión siguen siendo independientes del TV.
        t, p = _synthetic_awp()
        series = derive_from_waves(t, awp=p, flow=f, fs=FS)
        assert series.tv_ml
        assert series.pip_cmh2o # noqa: E701

    def test_deriva_corregida(self):
        tf, f = self._synthetic_flow(drift_lpm=5.0)  # deriva +5 L/min
        segs = tidal_volumes(tf, f)
        assert segs
        # Sin corrección de deriva el TV sería ~583 mL (+16 %).
        assert np.median([tv for _, tv in segs]) == pytest.approx(500.0, rel=0.05)


class TestAggregation:
    def test_agregacion_por_minuto_mediana(self):
        # 3 respiraciones en el minuto 0 con PIP 10, 20, 30 -> mediana 20.
        dt = [1.0, 11.0, 21.0]
        series = aggregate_by_minute(
            dt, breath_values={"PIP": [10.0, 20.0, 30.0],
                               "PEEP": [5.0, 5.0, 5.0]})
        assert series.pip_cmh2o[0] == pytest.approx(20.0)
        assert series.rr_rpm[0] == pytest.approx(3.0)

    def test_valores_rechazados_no_entran(self):
        dt = [1.0, 11.0]
        series = aggregate_by_minute(
            dt, breath_values={"PIP": [10.0, None], "PEEP": [None, None]})
        assert series.pip_cmh2o[0] == pytest.approx(10.0)
        assert 0 not in series.peep_cmh2o

    def test_median_by_minute(self):
        # Dos minutos: minuto 0 con {10, 20} -> 15; minuto 1 con {100} -> 100.
        out = median_by_minute([1.0, 11.0, 61.0], [10.0, 20.0, 100.0])
        assert out[0] == pytest.approx(15.0)
        assert out[1] == pytest.approx(100.0)


class TestValidation:
    def test_bland_altman_sesgo_cero(self):
        pairs = [(10.0, 10.0), (20.0, 20.0), (30.0, 30.0)]
        ba = bland_altman(pairs)
        assert ba["bias"] == pytest.approx(0.0)
        assert ba["loa_low"] == pytest.approx(0.0)
        assert ba["loa_high"] == pytest.approx(0.0)

    def test_sesgo_sistematico(self):
        pairs = [(11.0, 10.0), (21.0, 20.0), (31.0, 30.0)]
        ba = bland_altman(pairs)
        assert ba["bias"] == pytest.approx(1.0)

    def test_fraccion_dentro_del_margen(self):
        pairs = [(10.0, 10.0), (10.5, 10.0), (20.0, 10.0)]
        assert fraction_within(pairs, 2.0) == pytest.approx(2.0 / 3.0)

    def test_tv_relativo(self):
        pairs = [(505.0, 500.0), (490.0, 500.0), (600.0, 500.0)]
        assert fraction_within(pairs, 0.10, relative=True) == pytest.approx(2 / 3)

    def test_variable_usable_aceptada(self):
        # Sesgo +0.5 cmH2O y 90 % de minutos dentro de ±2 -> usable.
        pairs = [(10.5, 10.0)] * 9 + [(15.0, 10.0)]
        rep = variable_is_usable("PEEP", pairs)
        assert rep["usable"] is True
        assert rep["fraction_within"] == pytest.approx(0.9)

    def test_variable_rechazada_por_sesgo(self):
        pairs = [(12.0, 10.0)] * 10  # sesgo +2 cmH2O > 1
        assert variable_is_usable("PIP", pairs)["usable"] is False

    def test_variable_rechazada_por_fraccion(self):
        # Sesgo pequeño (+0.5) pero solo el 50 % dentro del margen.
        pairs = [(10.5, 10.0)] * 5 + [(20.0, 10.0)] * 5
        rep = variable_is_usable("RR", pairs)
        assert rep["usable"] is False
        assert rep["fraction_within"] == pytest.approx(0.5)

    def test_origenes_de_variable(self):
        assert AWP_SOURCE == "AWP_WAV_derived"
        assert FLOW_SOURCE == "FLOW_WAV_derived"
