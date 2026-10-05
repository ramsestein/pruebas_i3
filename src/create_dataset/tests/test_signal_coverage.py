"""
tests/test_signal_coverage.py
=============================
Fase 1.6b — **punto 4**: cobertura por variable con D8 (LOCF 4 h) en las
cohortes con señal.

El test central es el que pidió el investigador: un evento en el que **HR, MAP y
RR difieren** en cobertura. La cobertura de cada variable se mide por separado y
``vars_ok`` exige que TODAS las obligatorias superen el umbral.
"""

from __future__ import annotations

import pytest

from src.common.eicu_levels import (
    MANDATORY_VARIABLES,
    CoverageResult,
    hourly_coverage,
    vars_ok_summary,
)
from src.common.vital_signals import coverage_fractions

SPAN_10H_MIN = [(0.0, 600.0)]


class TestCoverageFractionsUnits:
    """Fase 1.6b: las series vienen en HORAS y ``hourly_coverage`` usa MINUTOS."""

    def test_convierte_horas_a_minutos(self):
        # HR cada hora durante 10 h; MAP solo las 3 primeras horas.
        hr = ([float(h) for h in range(11)], [80.0] * 11)
        map_ = ([0.0, 1.0, 2.0], [70.0] * 3)
        rr = ([0.0], [14.0])
        fracs = coverage_fractions({"HR": hr, "MAP": map_, "RR": rr}, [(0.0, 10.0)])
        assert fracs["HR"] == pytest.approx(1.0)
        assert 0.5 < fracs["MAP"] < 0.8
        assert fracs["RR"] < 0.5

    def test_horas_no_dan_cobertura_uno(self):
        # Si NO se convirtiera, la rejilla daría un único punto y MAP saldría
        # con la misma cobertura que HR (el bug de unidades).
        hr = ([float(h) for h in range(11)], [80.0] * 11)
        map_ = ([0.0], [70.0])
        fracs = coverage_fractions({"HR": hr, "MAP": map_}, [(0.0, 10.0)])
        assert fracs["MAP"] < fracs["HR"]


def _series(values_per_hour: int, n: int, value: float = 80.0):
    """Serie horaria: un valor cada 60 min, ``n`` valores."""
    step = 60.0 * (10 / values_per_hour) if values_per_hour else 60.0
    return [i * step for i in range(n)], [value] * n


class TestCoveragePerVariable:
    def test_hr_map_rr_difieren(self):
        # HR anotada las 10 h; MAP solo las 3 primeras; RR solo al principio
        # (LOCF de 4 h: MAP cubre ~6 h y RR ~4 h).
        hr_t = [i * 60.0 for i in range(11)]
        map_t = [0.0, 60.0, 120.0]
        rr_t = [0.0]
        f_hr = hourly_coverage(hr_t, [80.0] * len(hr_t), SPAN_10H_MIN)
        f_map = hourly_coverage(map_t, [70.0] * len(map_t), SPAN_10H_MIN)
        f_rr = hourly_coverage(rr_t, [14.0] * len(rr_t), SPAN_10H_MIN)

        assert f_hr == pytest.approx(1.0)
        assert 0.5 < f_map < 0.8
        assert f_rr < 0.5
        # Las tres difieren: la cobertura es POR VARIABLE, no global.
        assert f_hr > f_map > f_rr

    def test_locf_de_4h_no_cubre_mas(self):
        # Un único valor a las 0 h: solo cubre hasta las 4 h (LOCF 4 h).
        f = hourly_coverage([0.0], [80.0], [(0.0, 600.0)], max_age_h=4.0)
        assert f == pytest.approx(5.0 / 11.0, abs=0.06)

    def test_sin_datos_es_cero(self):
        assert hourly_coverage([], [], SPAN_10H_MIN) == 0.0

    def test_ventana_vacia(self):
        assert hourly_coverage([0.0], [80.0], []) == 0.0


class TestVarsOk:
    def test_vars_ok_exige_todas(self):
        cov = CoverageResult({"HR": 1.0, "SpO2": 1.0, "MAP": 0.9,
                              "RR": 0.4, "FiO2": 1.0, "PEEP": 1.0})
        ok = vars_ok_summary(cov)
        assert ok["50%"] is False      # RR al 40 % lo tumba
        assert ok["80%"] is False

    def test_vars_ok_todas_por_encima(self):
        cov = CoverageResult({v: 0.9 for v in MANDATORY_VARIABLES})
        assert vars_ok_summary(cov)["50%"] is True
        assert vars_ok_summary(cov)["80%"] is True

    def test_umbral_es_exclusivo(self):
        # exactamente 0.5 NO supera el 50 % (se exige > 0.5).
        cov = CoverageResult({"HR": 0.5})
        assert cov.all_above(0.5) is False

    def test_sin_variables_no_es_ok(self):
        assert CoverageResult({}).all_above(0.5) is False
