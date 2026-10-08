"""
tests/test_extubation.py
========================
Tests de la regla común de extubación confirmada (Fase 1.5, punto 0).

Cada caso lleva control negativo: la misma situación con la hora de observación
necesaria presente NO debe censurar.
"""

from __future__ import annotations

import pytest

from src.common.extubation import (
    CAUSE_DEATH,
    CAUSE_END_OF_RECORD,
    CAUSE_TRANSFER,
    MIN_OBSERVATION_TAIL_H,
    resolve_extubation,
)


class TestConfirmed:
    def test_tail_one_hour_is_extubation(self):
        d = resolve_extubation(last_vent_end_h=10.0, observation_end_h=11.0)
        assert d.is_extubation is True
        assert d.censor_cause is None
        assert d.tail_h == pytest.approx(1.0)

    def test_tail_above_one_hour_is_extubation(self):
        d = resolve_extubation(last_vent_end_h=10.0, observation_end_h=25.0)
        assert d.is_extubation is True

    def test_negative_control_tail_just_below_one_hour_is_not(self):
        d = resolve_extubation(last_vent_end_h=10.0, observation_end_h=10.9)
        assert d.is_extubation is False
        assert d.censor_cause == CAUSE_END_OF_RECORD

    def test_min_tail_is_configurable(self):
        d = resolve_extubation(
            last_vent_end_h=0.0, observation_end_h=0.5, min_tail_h=0.5
        )
        assert d.is_extubation is True
        assert MIN_OBSERVATION_TAIL_H == 1.0  # el valor por defecto no cambia


class TestTransferVentilated:
    def test_vent_end_20_min_before_discharge_is_transfer(self):
        """Caso exigido: fin de VM 20 min antes del alta → transfer_ventilated."""
        d = resolve_extubation(
            last_vent_end_h=47.0 + 40.0 / 60.0,   # 20 min antes de 48 h
            observation_end_h=48.0,
            stay_end_h=48.0,
        )
        assert d.is_extubation is False
        assert d.censor_cause == CAUSE_TRANSFER
        assert d.censor_time_h == pytest.approx(47.0 + 40.0 / 60.0)
        assert d.tail_h == pytest.approx(20.0 / 60.0)

    def test_vent_end_at_discharge_is_transfer(self):
        d = resolve_extubation(
            last_vent_end_h=30.0, observation_end_h=30.0, stay_end_h=30.0
        )
        assert d.censor_cause == CAUSE_TRANSFER

    def test_negative_control_stay_ends_long_after_vent_end_is_extubation(self):
        d = resolve_extubation(
            last_vent_end_h=10.0, observation_end_h=48.0, stay_end_h=48.0
        )
        assert d.is_extubation is True


class TestEndOfRecord:
    def test_signal_cohort_without_stay_boundary_is_end_of_record(self):
        """Clínic/VitalDB no tienen frontera de estancia → end_of_record."""
        d = resolve_extubation(
            last_vent_end_h=10.0, observation_end_h=10.2, stay_end_h=None
        )
        assert d.is_extubation is False
        assert d.censor_cause == CAUSE_END_OF_RECORD
        assert d.censor_time_h == pytest.approx(10.2)

    def test_negative_control_two_hours_of_monitor_is_extubation(self):
        d = resolve_extubation(
            last_vent_end_h=10.0, observation_end_h=12.0, stay_end_h=None
        )
        assert d.is_extubation is True


class TestDeath:
    def test_death_while_ventilated_censors_at_death(self):
        d = resolve_extubation(
            last_vent_end_h=10.0,
            observation_end_h=48.0,
            stay_end_h=48.0,
            death_h=12.0,
            died_ventilated=True,
        )
        assert d.is_extubation is False
        assert d.censor_cause == CAUSE_DEATH
        assert d.censor_time_h == pytest.approx(12.0)

    def test_death_deduced_when_at_or_before_vent_end(self):
        d = resolve_extubation(
            last_vent_end_h=12.0, observation_end_h=48.0, death_h=12.0
        )
        assert d.censor_cause == CAUSE_DEATH

    def test_negative_control_death_after_observation_is_not_death_at_vent(self):
        """La muerte después de la observación no censura la extubación."""
        d = resolve_extubation(
            last_vent_end_h=10.0,
            observation_end_h=11.5,
            stay_end_h=None,
            death_h=40.0,
            died_ventilated=False,
        )
        assert d.is_extubation is True


class TestValidation:
    def test_non_finite_raises(self):
        with pytest.raises(ValueError):
            resolve_extubation(
                last_vent_end_h=float("nan"), observation_end_h=1.0
            )
