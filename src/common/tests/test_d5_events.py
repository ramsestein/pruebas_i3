"""
tests/test_d5_events.py
=======================
Tests de las políticas D5 (traqueostomía y extubación terminal → censura).
"""

from __future__ import annotations

import pytest

from src.common.d5_events import (
    CENSOR,
    EXCLUDE,
    CensorDecision,
    apply_policy,
    eicu_terminal_decision,
    is_trach_icd9,
    is_trach_text,
    sensitivity_report,
    terminal_from_death,
    terminal_from_signal_loss,
    threshold_simultaneous_shutdown,
    trach_decision,
    trach_time_from_offset_rows,
)

H = 3600.0
T0 = 1_700_000_000.0


class TestTrachDetection:
    @pytest.mark.parametrize("code", ["31.1", "311", "31.21", "3121", "31.29", "3129"])
    def test_icd9_trach(self, code):
        assert is_trach_icd9(code) is True

    def test_negative_control_mechanical_ventilation(self):
        assert is_trach_icd9("96.72") is False

    def test_text_detection(self):
        assert is_trach_text("Tracheostomy tube") is True
        assert is_trach_text(None, "Percutaneous tracheostomy") is True

    def test_negative_control_text(self):
        assert is_trach_text("Endotracheal tube", None) is False


class TestTerminalDeath:
    def test_death_within_window_censors_at_disconnect(self):
        dec = terminal_from_death(
            T0 + 24 * H, T0 + 20 * H,
            t0_abs=T0, failure_window_h=48.0, died_ventilated=False,
        )
        assert dec.censor_cause == "terminal_extubation"
        assert dec.censor_time_h == pytest.approx(20.0)

    def test_negative_control_death_outside_window(self):
        dec = terminal_from_death(
            T0 + 200 * H, T0 + 20 * H,
            t0_abs=T0, failure_window_h=48.0, died_ventilated=False,
        )
        assert dec.censor_cause is None

    def test_died_ventilated_censors_at_death(self):
        dec = terminal_from_death(
            T0 + 30 * H, None,
            t0_abs=T0, failure_window_h=48.0, died_ventilated=True,
        )
        assert dec.censor_cause == "death_at_vent"
        assert dec.censor_time_h == pytest.approx(30.0)

    def test_negative_control_no_death(self):
        assert terminal_from_death(
            None, None, t0_abs=T0, failure_window_h=48.0, died_ventilated=False,
        ).censor_cause is None


class TestTerminalSignal:
    def test_asystole_and_spo2_loss_censors(self):
        dec = terminal_from_signal_loss(
            asystole_or_hr_zero=True, spo2_lost_without_recovery=True,
            disconnect_abs=T0 + 12 * H, t0_abs=T0,
        )
        assert dec.censor_cause == "terminal_extubation"
        assert dec.censor_time_h == pytest.approx(12.0)

    def test_negative_control_only_hr_zero(self):
        dec = terminal_from_signal_loss(
            asystole_or_hr_zero=True, spo2_lost_without_recovery=False,
            disconnect_abs=T0 + 12 * H, t0_abs=T0,
        )
        assert dec.censor_cause is None


class TestPolicy:
    def test_censor_keeps_event(self):
        d = CensorDecision("trach", 50.0)
        assert apply_policy(d, CENSOR).excluded is False
        assert apply_policy(d, CENSOR).censor_cause == "trach"

    def test_exclude_marks_excluded(self):
        d = CensorDecision("trach", 50.0)
        out = apply_policy(d, EXCLUDE)
        assert out.excluded is True
        assert out.censor_cause == "trach"

    def test_policy_noop_without_event(self):
        assert apply_policy(CensorDecision(None, None), EXCLUDE).excluded is False


class TestSensitivity:
    def test_report_counts_and_median(self):
        rep = sensitivity_report([(100.0, 90.0), (50.0, 40.0), (200.0, 180.0)])
        assert rep.n_cases == 3
        assert rep.total_hours == pytest.approx(310.0)
        assert rep.median_duration_h == pytest.approx(100.0)

    def test_negative_control_empty(self):
        rep = sensitivity_report([])
        assert rep.n_cases == 0
        assert rep.median_duration_h is None


class TestSimultaneousShutdown:
    def test_within_15min(self):
        assert threshold_simultaneous_shutdown(10.0, 10.2) is True

    def test_negative_control_beyond_15min(self):
        assert threshold_simultaneous_shutdown(10.0, 11.0) is False


# ── Detección por cohorte ────────────────────────────────────────────────────

class TestCohortDetection:
    def test_trach_time_is_earliest(self):
        assert trach_time_from_offset_rows([500.0, 120.0, None]) == 120.0

    def test_negative_control_trach_time_none(self):
        assert trach_time_from_offset_rows([]) is None
        assert trach_time_from_offset_rows([None]) is None

    def test_trach_decision_with_time(self):
        d = trach_decision([50.0], icd9_marked_without_time=False, last_vent_end_h=99.0)
        assert d.censor_cause == "trach"
        assert d.censor_time_h == 50.0

    def test_trach_decision_icd9_without_time(self):
        d = trach_decision([], icd9_marked_without_time=True, last_vent_end_h=99.0)
        assert d.censor_cause == "trach_time_unknown"
        assert d.censor_time_h == 99.0

    def test_trach_decision_preexisting_is_exclusion(self):
        """Traqueostomía ANTES de t0 → exclusión, no censura."""
        d = trach_decision([-5.0], icd9_marked_without_time=False, last_vent_end_h=10.0)
        assert d.censor_cause == "trach_preexisting"
        assert d.excluded is True

    def test_negative_control_trach_during_episode_censors(self):
        d = trach_decision([5.0], icd9_marked_without_time=False, last_vent_end_h=10.0)
        assert d.censor_cause == "trach"
        assert d.excluded is False

    def test_d5_censor_for_window_preexisting(self):
        from src.common.d5_events import d5_censor_for_window
        d = d5_censor_for_window(
            failure_window_h=48.0, last_disconnect_h=10.0, trach_time_h=-2.0,
        )
        assert d.excluded is True and d.censor_cause == "trach_preexisting"

    def test_negative_control_trach_decision_none(self):
        d = trach_decision([], icd9_marked_without_time=False, last_vent_end_h=99.0)
        assert d.censor_cause is None

    def test_eicu_expired_within_window_censors_at_disconnect(self):
        d = eicu_terminal_decision(
            unit_discharge_status="Expired",
            unit_discharge_offset_min=1000.0,   # t0=600 -> 400 min = 6.67 h
            last_disconnect_min=900.0,          # 300 min = 5 h
            t0_min=600.0, failure_window_h=48.0,
        )
        assert d.censor_cause == "terminal_extubation"
        assert d.censor_time_h == pytest.approx(5.0)

    def test_eicu_negative_control_alive(self):
        d = eicu_terminal_decision(
            unit_discharge_status="Alive",
            unit_discharge_offset_min=1000.0,
            last_disconnect_min=900.0,
            t0_min=600.0, failure_window_h=48.0,
        )
        assert d.censor_cause is None

    def test_eicu_death_at_vent_when_no_disconnect(self):
        d = eicu_terminal_decision(
            unit_discharge_status="Expired",
            unit_discharge_offset_min=1200.0,
            last_disconnect_min=None,
            t0_min=600.0, failure_window_h=48.0,
        )
        assert d.censor_cause == "death_at_vent"
