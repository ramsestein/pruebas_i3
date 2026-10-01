"""
tests/test_survival_logic.py
============================
Tests unitarios de la lógica del estimando de supervivencia.

Cubre los casos límite reales del dataset:
  1. Extubación directa sin intentos fallidos
  2. Múltiples fallos seguidos de extubación exitosa (reloj continuo desde t0)
  3. Fallo detectado correctamente: reintubación dentro de ventana de 48h
  4. Éxito detectado correctamente: sin reintubación en 48h
  5. Éxito con ventana de 72h pero fallo con ventana de 48h
     (reintubación entre 48h y 72h tras el intento)
  6. MIMIC edge case: censored_no_extubation

Convención de nombres de fixtures: describe_escenario
"""

from __future__ import annotations

import numpy as np
import pytest

from src.stage0.adapters.base import ClinicalEvents, ExtubationAttempt
from src.stage0.labeling.survival import (
    build_survival_row,
    classify_attempts,
    find_first_success,
)

DATASET_VERSION = "test_v0"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_events(
    patient_id: str = "p_test",
    cohort: str = "clinic",
    record_end_hours: float = 120.0,
    extubation_confirmed: bool = True,
    extubation_confirmed_hours: float | None = 120.0,
    attempts: list[ExtubationAttempt] | None = None,
    censored: bool = False,
    censored_reason: str | None = None,
) -> ClinicalEvents:
    return ClinicalEvents(
        patient_id=patient_id,
        cohort=cohort,
        t0_unix=0.0,
        record_end_hours=record_end_hours,
        extubation_confirmed=extubation_confirmed,
        extubation_confirmed_hours=extubation_confirmed_hours,
        extubation_attempts=attempts or [],
        censored_no_extubation=censored,
        censored_reason=censored_reason,
    )


# ── Caso 1: Extubación directa sin intentos fallidos ─────────────────────────

class TestDirectExtubation:
    def test_classify_single_success(self):
        events = _make_events(
            extubation_confirmed_hours=72.0,
            attempts=[
                ExtubationAttempt(0, time_rel_hours=72.0, outcome="success"),
            ],
        )
        classified = classify_attempts(events, failure_window_h=48.0)
        assert len(classified) == 1
        assert classified[0].outcome == "success"
        assert classified[0].time_rel_hours == 72.0

    def test_survival_row_single_success(self):
        events = _make_events(
            extubation_confirmed_hours=72.0,
            attempts=[
                ExtubationAttempt(0, time_rel_hours=72.0, outcome="success"),
            ],
        )
        row = build_survival_row(events, failure_window_h=48.0,
                                 dataset_version=DATASET_VERSION)
        assert row["event_type"] == "successful_extubation"
        assert row["extubation_time_hours"] == pytest.approx(72.0)
        assert row["n_failed_attempts"] == 0
        assert row["first_attempt_time_hours"] == pytest.approx(72.0)

    def test_selection_bias_note_clinic(self):
        events = _make_events(cohort="clinic", attempts=[
            ExtubationAttempt(0, time_rel_hours=48.0, outcome="success"),
        ])
        row = build_survival_row(events, 48.0, DATASET_VERSION)
        assert row["selection_bias_note"] == "by_construction"

    def test_selection_bias_note_mimic(self):
        events = _make_events(cohort="mimic", attempts=[
            ExtubationAttempt(0, time_rel_hours=48.0, outcome="success"),
        ])
        row = build_survival_row(events, 48.0, DATASET_VERSION)
        assert row["selection_bias_note"] == "clinical_table"


# ── Caso 2: Múltiples fallos seguidos de éxito (reloj continuo) ───────────────

class TestMultipleFailuresThenSuccess:
    """
    Paciente con 2 intentos fallidos y 1 exitoso.
    t0=0, fallo1 a 24h (reintubación a 36h), fallo2 a 60h (reintubación a 72h),
    éxito a 96h. Con ventana 48h, los fallos son dentro de ventana.
    El tiempo hasta extubación exitosa = 96h.
    """

    def _make(self, failure_window_h: float = 48.0) -> ClinicalEvents:
        return _make_events(
            extubation_confirmed_hours=96.0,
            record_end_hours=96.0,
            attempts=[
                ExtubationAttempt(0, time_rel_hours=24.0, outcome="failure",
                                  reintubation_time_rel_hours=36.0),
                ExtubationAttempt(1, time_rel_hours=60.0, outcome="failure",
                                  reintubation_time_rel_hours=72.0),
                ExtubationAttempt(2, time_rel_hours=96.0, outcome="success"),
            ],
        )

    def test_classify_two_failures_one_success(self):
        events = self._make(48.0)
        classified = classify_attempts(events, failure_window_h=48.0)
        outcomes = [a.outcome for a in classified]
        assert outcomes == ["failure", "failure", "success"]

    def test_extubation_time_is_last_attempt(self):
        events = self._make(48.0)
        row = build_survival_row(events, 48.0, DATASET_VERSION)
        assert row["event_type"] == "successful_extubation"
        assert row["extubation_time_hours"] == pytest.approx(96.0)

    def test_n_failed_attempts_is_two(self):
        events = self._make(48.0)
        row = build_survival_row(events, 48.0, DATASET_VERSION)
        assert row["n_failed_attempts"] == 2

    def test_first_attempt_is_earliest(self):
        events = self._make(48.0)
        row = build_survival_row(events, 48.0, DATASET_VERSION)
        assert row["first_attempt_time_hours"] == pytest.approx(24.0)

    def test_clock_never_resets(self):
        """El tiempo hasta extubación es desde t0, no desde el último intento."""
        events = self._make(48.0)
        row = build_survival_row(events, 48.0, DATASET_VERSION)
        # Si el reloj se reiniciara, sería 96-60=36 h (desde el último fallo)
        # Debe ser 96h (desde t0)
        assert row["extubation_time_hours"] == pytest.approx(96.0)


# ── Caso 3: Fallo detectado correctamente por ventana de 48h ─────────────────

class TestFailureDetectionByWindow:
    """
    Un solo intento, reintubación a 30h después → dentro de 48h → fallo.
    Con ventana 72h → fallo también (30h < 72h).
    """

    def _events_with_one_attempt(self, reintub_delay_h: float) -> ClinicalEvents:
        attempt_h = 40.0
        return _make_events(
            extubation_confirmed_hours=attempt_h + reintub_delay_h + 10,
            record_end_hours=attempt_h + reintub_delay_h + 10,
            attempts=[
                ExtubationAttempt(
                    0, time_rel_hours=attempt_h, outcome="failure",
                    reintubation_time_rel_hours=attempt_h + reintub_delay_h,
                ),
                ExtubationAttempt(
                    1, time_rel_hours=attempt_h + reintub_delay_h + 10,
                    outcome="success",
                ),
            ],
        )

    def test_reintubation_within_48h_is_failure(self):
        events = self._events_with_one_attempt(reintub_delay_h=30.0)
        classified = classify_attempts(events, failure_window_h=48.0)
        assert classified[0].outcome == "failure"

    def test_reintubation_within_72h_is_failure(self):
        events = self._events_with_one_attempt(reintub_delay_h=30.0)
        classified = classify_attempts(events, failure_window_h=72.0)
        assert classified[0].outcome == "failure"


# ── Caso 4: Éxito detectado correctamente (sin reintubación en 48h) ──────────

class TestSuccessDetection:
    def test_no_reintubation_is_success(self):
        events = _make_events(
            extubation_confirmed_hours=48.0,
            attempts=[
                ExtubationAttempt(0, time_rel_hours=48.0, outcome="success"),
            ],
        )
        classified = classify_attempts(events, failure_window_h=48.0)
        assert classified[0].outcome == "success"

    def test_survival_row_marks_successful_extubation(self):
        events = _make_events(
            extubation_confirmed_hours=48.0,
            attempts=[
                ExtubationAttempt(0, time_rel_hours=48.0, outcome="success"),
            ],
        )
        row = build_survival_row(events, 48.0, DATASET_VERSION)
        assert row["event_type"] == "successful_extubation"
        assert row["n_failed_attempts"] == 0


# ── Caso 5: Diferencia entre ventana 48h y 72h ───────────────────────────────

class TestWindowDifference48vs72:
    """
    Intento a 40h, reintubación a 40+50=90h.
    Delay = 50h > 48h → con ventana 48h es ÉXITO
    Delay = 50h < 72h → con ventana 72h es FALLO
    """

    def _events(self) -> ClinicalEvents:
        return _make_events(
            extubation_confirmed_hours=100.0,
            record_end_hours=100.0,
            attempts=[
                ExtubationAttempt(
                    0, time_rel_hours=40.0, outcome="failure",
                    reintubation_time_rel_hours=90.0,  # delay=50h
                ),
                ExtubationAttempt(
                    1, time_rel_hours=100.0, outcome="success",
                ),
            ],
        )

    def test_window_48h_classifies_as_success(self):
        """50h > 48h → el primer intento es éxito con ventana 48h"""
        events = self._events()
        classified = classify_attempts(events, failure_window_h=48.0)
        assert classified[0].outcome == "success"

    def test_window_72h_classifies_as_failure(self):
        """50h < 72h → el primer intento es fallo con ventana 72h"""
        events = self._events()
        classified = classify_attempts(events, failure_window_h=72.0)
        assert classified[0].outcome == "failure"

    def test_extubation_time_differs_between_windows(self):
        """Con 48h el evento es a 40h; con 72h el evento es a 100h"""
        events = self._events()
        row_48 = build_survival_row(events, 48.0, DATASET_VERSION)
        row_72 = build_survival_row(events, 72.0, DATASET_VERSION)
        assert row_48["extubation_time_hours"] == pytest.approx(40.0)
        assert row_72["extubation_time_hours"] == pytest.approx(100.0)


# ── Caso 6: MIMIC edge case — censored_no_extubation ─────────────────────────

class TestMimicCensored:
    def test_censored_row_has_correct_event_type(self):
        events = _make_events(
            cohort="mimic",
            extubation_confirmed=False,
            extubation_confirmed_hours=None,
            censored=True,
            censored_reason="death_at_vent_end",
            attempts=[],
        )
        row = build_survival_row(events, 48.0, DATASET_VERSION)
        assert row["event_type"] == "censored_no_extubation"

    def test_censored_row_has_nan_extubation_time(self):
        events = _make_events(
            cohort="mimic",
            extubation_confirmed=False,
            extubation_confirmed_hours=None,
            censored=True,
            censored_reason="death_at_vent_end",
            attempts=[],
        )
        row = build_survival_row(events, 48.0, DATASET_VERSION)
        assert np.isnan(row["extubation_time_hours"])

    def test_censored_has_zero_failed_attempts(self):
        events = _make_events(
            cohort="mimic",
            extubation_confirmed=False,
            extubation_confirmed_hours=None,
            censored=True,
            censored_reason="death_at_vent_end",
            attempts=[],
        )
        row = build_survival_row(events, 48.0, DATASET_VERSION)
        assert row["n_failed_attempts"] == 0

    def test_find_first_success_returns_none_for_empty_attempts(self):
        classified = []
        assert find_first_success(classified) is None


# ── Invariantes generales ─────────────────────────────────────────────────────

class TestInvariants:
    def test_extubation_time_always_positive(self):
        events = _make_events(
            extubation_confirmed_hours=10.0,
            attempts=[
                ExtubationAttempt(0, time_rel_hours=10.0, outcome="success"),
            ],
        )
        row = build_survival_row(events, 48.0, DATASET_VERSION)
        assert row["extubation_time_hours"] > 0

    def test_n_failed_attempts_nonnegative(self):
        events = _make_events(
            extubation_confirmed_hours=5.0,
            attempts=[
                ExtubationAttempt(0, time_rel_hours=5.0, outcome="success"),
            ],
        )
        row = build_survival_row(events, 48.0, DATASET_VERSION)
        assert row["n_failed_attempts"] >= 0

    def test_first_attempt_le_extubation_time(self):
        events = _make_events(
            extubation_confirmed_hours=96.0,
            attempts=[
                ExtubationAttempt(0, time_rel_hours=24.0, outcome="failure",
                                  reintubation_time_rel_hours=36.0),
                ExtubationAttempt(1, time_rel_hours=96.0, outcome="success"),
            ],
        )
        row = build_survival_row(events, 48.0, DATASET_VERSION)
        assert row["first_attempt_time_hours"] <= row["extubation_time_hours"]
