"""
tests/test_eicu_census.py
=========================
Tests de las métricas de documentación y de los escenarios de selección de la
Fase 1.6a.
"""

from __future__ import annotations

import pytest

from src.common.eicu_census import (
    VERDICT_DENSE,
    VERDICT_IMPLAUSIBLE,
    VERDICT_OK,
    classify_plausibility,
    documentation_metrics,
    inter_adjustment_intervals,
    is_implausible,
    median_iqr,
    scenario_a,
    scenario_b,
    scenario_d,
    scenario_stats,
)


class TestDocMetrics:
    def test_documentation_metrics_basic(self):
        m = documentation_metrics(
            adj_offsets=[100.0, 340.0, 580.0], discharge_min=600.0,
            ventstartoffset=90.0, has_airway=True,
        )
        assert m["rc_invasive"] is True
        assert m["n_adj"] == 3
        assert m["inter_adj"] == [240.0, 240.0]
        assert m["ventstart_supported"] is True       # |100-90| <= 120
        assert m["last_adj_before_discharge_min"] == pytest.approx(20.0)
        assert m["last_adj_lt1h"] is True
        assert m["has_airway"] is True

    def test_negative_control_no_adjustments(self):
        m = documentation_metrics(
            adj_offsets=[], discharge_min=600.0, ventstartoffset=10.0,
            has_airway=False,
        )
        assert m["rc_invasive"] is False
        assert m["ventstart_supported"] is False
        assert m["last_adj_lt1h"] is False
        assert m["has_ventstart"] is True

    def test_ventstart_not_supported_when_far(self):
        m = documentation_metrics(
            adj_offsets=[1000.0], discharge_min=2000.0,
            ventstartoffset=10.0, has_airway=True,
        )
        assert m["ventstart_supported"] is False


class TestHelpers:
    def test_inter_adjustment_intervals_drop_duplicates(self):
        assert inter_adjustment_intervals([10, 10, 40, 100]) == [30.0, 60.0]

    def test_median_iqr(self):
        med, iqr = median_iqr([1, 2, 3, 4, 5])
        assert med == pytest.approx(3.0)
        assert iqr == pytest.approx(2.0)

    def test_median_iqr_empty(self):
        assert median_iqr([]) == (None, None)


class TestPlausibility:
    def test_implausible_above_threshold(self):
        assert is_implausible(700, 1000) is True
        assert is_implausible(600, 1000) is False

    def test_implausible_zero_icu(self):
        assert is_implausible(0, 0) is False

    def test_negative_control_low_ratio(self):
        assert is_implausible(100, 1000) is False


class TestPlausibilityVerdict:
    def test_ok_when_density_low(self):
        assert classify_plausibility(100, 1000, 0.9) == VERDICT_OK

    def test_dense_but_concordant(self):
        # 70 % de las estancias con ajustes invasivos, pero solo el 12 % sin
        # intubación APACHE -> denso, no permeable.
        assert classify_plausibility(700, 1000, 0.12) == VERDICT_DENSE

    def test_implausible_permeable(self):
        # 91 % de densidad y 87.5 % de las invasivas sin intubación -> permeable.
        assert classify_plausibility(912, 1000, 0.875) == VERDICT_IMPLAUSIBLE

    def test_ok_without_icu_stays(self):
        assert classify_plausibility(0, 0, 1.0) == VERDICT_OK

    def test_threshold_boundary(self):
        assert classify_plausibility(600, 1000, 0.99) == VERDICT_OK
        assert classify_plausibility(601, 1000, 0.99) == VERDICT_IMPLAUSIBLE
        assert classify_plausibility(601, 1000, 0.49) == VERDICT_DENSE


class TestScenarios:
    def _hosp(self):
        return {
            1: {"apache_vent": 100, "apache_vent_peak24": 30},   # >=10, 30% -> a
            2: {"apache_vent": 100, "apache_vent_peak24": 5},    # 5%   -> fuera
            3: {"apache_vent": 5,   "apache_vent_peak24": 5},    # <10  -> fuera
        }

    def test_scenario_a(self):
        assert scenario_a(self._hosp()) == {1}

    def _census(self):
        return {
            1: {"vent_stays": 100, "frac_rc_invasive": 0.9, "inter_adj_median_min": 120,
                "vars_ok50_n": 80},   # b, c, d
            2: {"vent_stays": 40,  "frac_rc_invasive": 0.9, "inter_adj_median_min": 120,
                "vars_ok50_n": 30},   # <50 estancias -> fuera
            3: {"vent_stays": 100, "frac_rc_invasive": 0.5, "inter_adj_median_min": 120,
                "vars_ok50_n": 80},   # <80% invasivos -> fuera
            4: {"vent_stays": 100, "frac_rc_invasive": 0.9, "inter_adj_median_min": 300,
                "vars_ok50_n": 50},   # mediana 5 h -> b(4h) fuera, b(6h) dentro
            5: {"vent_stays": 100, "frac_rc_invasive": 0.9, "inter_adj_median_min": 120,
                "vars_ok50_n": 10},   # d fuera (vars_ok 10%)
        }

    def test_scenario_b_4h(self):
        # 1 y 5 cumplen (≥50, ≥80% invasivos, mediana ≤4 h); 5 falla solo en d.
        assert scenario_b(self._census(), max_median_gap_min=240) == {1, 5}

    def test_scenario_b_6h(self):
        assert scenario_b(self._census(), max_median_gap_min=360) == {1, 4, 5}

    def test_scenario_d(self):
        assert scenario_d(self._census()) == {1}

    def test_scenario_stats(self):
        s = scenario_stats({1: 100, 2: 200, 3: 300}, {1, 2, 3})
        assert s["hospitals"] == 3
        assert s["vent_stays"] == 600
        assert s["max"] == 300
        assert s["largest_weight"] == pytest.approx(0.5)

    def test_scenario_stats_empty(self):
        s = scenario_stats({1: 100}, set())
        assert s["hospitals"] == 0
        assert s["vent_stays"] == 0
