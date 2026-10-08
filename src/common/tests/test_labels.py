"""
tests/test_labels.py
====================
Tests del etiquetado D3 (``src/common/labels.py``), con control negativo.
"""

from __future__ import annotations

import pytest

from src.common.labels import (
    AttemptOutcome,
    assign_label,
    assign_labels_all_windows,
    attempts_from_pairs,
    count_failures,
    evaluate_attempts,
)


class TestSuccess:
    def test_direct_success(self):
        att = attempts_from_pairs([(72.0, None)])
        lab = assign_label(att, obs_end_h=200.0, failure_window_h=48.0)
        assert lab.event_type == "successful_extubation"
        assert lab.extubation_time_h == 72.0
        assert lab.n_failed_attempts == 0
        assert lab.is_at_risk_until_h == 72.0

    def test_failure_then_success(self):
        att = attempts_from_pairs([(10.0, 16.0), (100.0, None)])
        lab = assign_label(att, obs_end_h=200.0, failure_window_h=48.0)
        assert lab.event_type == "successful_extubation"
        assert lab.extubation_time_h == 100.0
        assert lab.n_failed_attempts == 1

    def test_negative_control_reintubation_exactly_at_window_is_failure(self):
        # 10 -> 58 (48 h exactas) debe contar como fallo (frontera inclusiva).
        att = attempts_from_pairs([(10.0, 58.0), (100.0, None)])
        assert count_failures(att, 48.0) == 1
        assert evaluate_attempts(att, 48.0)[0] == 1


class TestWindows:
    def test_reintubation_between_48_and_72_splits_windows(self):
        att = attempts_from_pairs([(0.0, 60.0), (120.0, None)])

        lab48 = assign_label(att, obs_end_h=200.0, failure_window_h=48.0)
        assert lab48.event_type == "successful_extubation"
        assert lab48.extubation_time_h == 0.0

        lab72 = assign_label(att, obs_end_h=200.0, failure_window_h=72.0)
        assert lab72.event_type == "successful_extubation"
        assert lab72.extubation_time_h == 120.0
        assert lab72.n_failed_attempts == 1

    def test_all_windows_keys(self):
        att = attempts_from_pairs([(5.0, None)])
        labels = assign_labels_all_windows(att, obs_end_h=100.0)
        assert set(labels) == {"48h", "72h"}


class TestCensoring:
    def test_no_attempts_is_censored(self):
        lab = assign_label([], obs_end_h=50.0, failure_window_h=48.0)
        assert lab.event_type == "censored_no_extubation"
        assert lab.extubation_time_h is None
        assert lab.is_at_risk_until_h == 50.0

    def test_all_attempts_fail_is_censored(self):
        att = attempts_from_pairs([(10.0, 20.0), (30.0, 40.0)])
        lab = assign_label(att, obs_end_h=50.0, failure_window_h=48.0)
        assert lab.event_type == "censored_no_extubation"
        assert lab.extubation_time_h is None
        assert lab.n_failed_attempts == 2  # los fallos NO se pierden

    def test_explicit_censor_cause_keeps_previous_failures(self):
        # Un fallo previo y luego una traqueostomía ANTES del éxito → censura.
        att = attempts_from_pairs([(10.0, 20.0), (40.0, None)])
        lab = assign_label(
            att, obs_end_h=60.0, failure_window_h=48.0,
            censor_cause="trach", censor_time_h=30.0,
        )
        assert lab.event_type == "censored_trach"
        assert lab.extubation_time_h is None
        assert lab.censor_time_h == 30.0
        assert lab.n_failed_attempts == 1

    def test_censor_after_consolidated_success_keeps_success(self):
        """D5: éxito el día 3, reintubación el día 10 y muerte el día 20 → éxito."""
        att = attempts_from_pairs([(72.0, 240.0)])  # día 3 -> día 10
        lab = assign_label(
            att, obs_end_h=480.0, failure_window_h=48.0,
            censor_cause="death_at_vent", censor_time_h=480.0,  # día 20
        )
        assert lab.event_type == "successful_extubation"
        assert lab.extubation_time_h == 72.0

    def test_censor_before_success_applies(self):
        """Control: la misma censura ANTES del éxito sí censura."""
        att = attempts_from_pairs([(480.0, None)])
        lab = assign_label(
            att, obs_end_h=600.0, failure_window_h=48.0,
            censor_cause="death_at_vent", censor_time_h=100.0,
        )
        assert lab.event_type == "censored_death_at_vent"

    def test_negative_control_no_censor_uses_success(self):
        att = attempts_from_pairs([(10.0, 20.0), (40.0, None)])
        lab = assign_label(att, obs_end_h=60.0, failure_window_h=48.0)
        assert lab.event_type == "successful_extubation"

    def test_muerte_ventilado_censura_en_la_muerte(self):
        att = attempts_from_pairs([(10.0, None)])  # nunca se extuba (edge)
        lab = assign_label(
            att, obs_end_h=10.0, failure_window_h=48.0,
            censor_cause="death_at_vent", censor_time_h=10.0,
        )
        assert lab.event_type == "censored_death_at_vent"
        assert lab.is_at_risk_until_h == 10.0
