"""
tests/test_eicu_levels.py
=========================
Tests de la clasificación de completitud de eICU (A/B/C/D) y de la cobertura de
variables con LOCF (Fase 1.5, punto 1). Cada nivel lleva control negativo.
"""

from __future__ import annotations

import pytest

from src.common.eicu_levels import (
    LEVEL_A,
    LEVEL_B,
    LEVEL_C,
    LEVEL_D,
    CoverageResult,
    StayVentInputs,
    classify_eicu_stay,
    hourly_coverage,
    is_invasive_adjustment,
    vars_ok_summary,
)


class TestInvasiveAdjustments:
    def test_accepts_mode_peep_tv_pip_and_total_rr(self):
        for label in ("Mechanical Ventilator Mode", "PEEP",
                      "Tidal Volume (set)", "Peak Insp. Pressure", "Total RR"):
            assert is_invasive_adjustment(label)

    def test_negative_control_fio2_and_niv_are_excluded(self):
        for label in ("FiO2", "FIO2 (%)", "NIV Setting EPAP", "CPAP",
                      "LPM O2", "PEEP/CPAP", "IPAP", "Non-invasive Ventilation Mode"):
            assert not is_invasive_adjustment(label)


def _inp(**kw) -> StayVentInputs:
    base = dict(
        start_h=0.0, end_h=20.0, end_documented=True, discharge_h=48.0,
    )
    base.update(kw)
    return StayVentInputs(**base)


class TestLevelA:
    def test_documented_valid_coherent_is_A(self):
        lv = classify_eicu_stay(_inp(last_invasive_adj_h=18.0))
        assert lv.level == LEVEL_A
        assert lv.outcome == "extubación"
        assert lv.extubation_h == pytest.approx(20.0)

    def test_negative_control_adjustment_too_old_is_not_A(self):
        """Último ajuste a 6 h del fin → incoherente (no A)."""
        lv = classify_eicu_stay(_inp(last_invasive_adj_h=14.0))
        assert lv.level != LEVEL_A

    def test_valid_censor_death_is_A_if_coherent(self):
        lv = classify_eicu_stay(_inp(
            end_h=30.0, discharge_h=30.0, death_h=30.0,
            died_ventilated=True, last_invasive_adj_h=28.0,
        ))
        assert lv.level == LEVEL_A
        assert lv.outcome == "censura citable"
        assert lv.extubation_h is None


class TestLevelB:
    def test_documented_without_adjustments_is_B(self):
        lv = classify_eicu_stay(_inp(last_invasive_adj_h=None))
        assert lv.level == LEVEL_B

    def test_negative_control_with_adjustments_is_A(self):
        lv = classify_eicu_stay(_inp(last_invasive_adj_h=19.0))
        assert lv.level == LEVEL_A


class TestLevelC:
    def test_imputed_end_recovered_by_last_adjustment_is_C(self):
        lv = classify_eicu_stay(_inp(
            end_h=48.0, end_documented=False, discharge_h=48.0,
            last_invasive_adj_h=10.0,
        ))
        assert lv.level == LEVEL_C
        assert lv.extubation_h == pytest.approx(10.0)
        assert lv.proposed_shift_h == pytest.approx(38.0)

    def test_incoherent_documented_end_is_C_when_recoverable(self):
        lv = classify_eicu_stay(_inp(
            end_h=20.0, last_invasive_adj_h=8.0, discharge_h=48.0,
        ))
        assert lv.level == LEVEL_C

    def test_negative_control_adjustment_within_1h_of_discharge_is_not_C(self):
        lv = classify_eicu_stay(_inp(
            end_h=48.0, end_documented=False, discharge_h=48.0,
            last_invasive_adj_h=47.5,
        ))
        assert lv.level == LEVEL_D


class TestLevelD:
    def test_imputed_without_adjustments_is_D(self):
        lv = classify_eicu_stay(_inp(
            end_h=48.0, end_documented=False, discharge_h=48.0,
            last_invasive_adj_h=None,
        ))
        assert lv.level == LEVEL_D

    def test_end_after_discharge_without_adjustments_is_D(self):
        lv = classify_eicu_stay(_inp(
            end_h=60.0, discharge_h=48.0, last_invasive_adj_h=None,
        ))
        assert lv.level == LEVEL_D

    def test_negative_control_end_before_discharge_recoverable_is_C(self):
        lv = classify_eicu_stay(_inp(
            end_h=60.0, discharge_h=48.0, last_invasive_adj_h=40.0,
        ))
        assert lv.level == LEVEL_C


class TestCoverage:
    def test_hourly_coverage_counts_locf_within_4h(self):
        # Observaciones en t=0 y t=600 min (10 h); intervalo ventilado 0..12 h.
        frac = hourly_coverage([0.0, 600.0], [80.0, 90.0], [(0.0, 720.0)])
        # 13 puntos (0..720 paso 60). t=600 (10h) cubre 600..840 -> 600,660,720.
        # t=0 cubre 0..240 -> 0,60,120,180,240. Hueco 300..540 sin dato.
        assert frac == pytest.approx(8.0 / 13.0)

    def test_negative_control_stale_observation_not_counted(self):
        # Una única observación en t=0 solo cubre hasta 240 min.
        frac = hourly_coverage([0.0], [80.0], [(300.0, 600.0)])
        assert frac == pytest.approx(0.0)

    def test_nan_values_are_ignored(self):
        frac = hourly_coverage([0.0, 60.0], [float("nan"), 90.0], [(60.0, 60.0)])
        assert frac == pytest.approx(1.0)

    def test_vars_ok_thresholds(self):
        cov = CoverageResult(fractions={
            "HR": 0.9, "SpO2": 0.9, "MAP": 0.9, "RR": 0.9, "FiO2": 0.6, "PEEP": 0.55,
        })
        ok = vars_ok_summary(cov)
        assert ok["50%"] is True
        assert ok["80%"] is False
