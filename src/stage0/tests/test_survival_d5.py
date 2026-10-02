"""
tests/test_survival_d5.py
=========================
D5 a través de la ruta común (corrección 3 + 4): los campos D5 del
``ClinicalEvents`` (que rellena el adaptador de eICU) deben producir la censura
adecuada en la tabla de supervivencia, respetando el orden temporal.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.stage0.adapters.base import ClinicalEvents, ExtubationAttempt
from src.stage0.labeling.survival import build_survival_row

VERSION = "test_d5"


def _eicu_events(**kw) -> ClinicalEvents:
    base = dict(
        patient_id="eicu_1", cohort="eicu", t0_unix=0.0, record_end_hours=200.0,
        extubation_confirmed=True, extubation_confirmed_hours=20.0,
        extubation_attempts=[ExtubationAttempt(0, 20.0, "success")],
    )
    base.update(kw)
    return ClinicalEvents(**base)


def test_trach_before_extubation_censors():
    ev = _eicu_events(trach_time_hours=10.0)
    row = build_survival_row(ev, 48.0, VERSION)
    assert row["event_type"] == "censored_no_extubation"
    assert row["censor_cause"] == "trach"
    assert np.isnan(row["extubation_time_hours"])


def test_trach_after_success_keeps_success():
    ev = _eicu_events(trach_time_hours=100.0)
    row = build_survival_row(ev, 48.0, VERSION)
    assert row["event_type"] == "successful_extubation"
    assert row["extubation_time_hours"] == pytest.approx(20.0)


def test_death_within_window_censors_at_disconnect():
    ev = _eicu_events(death_time_hours=30.0)  # 10 h tras la desconexión
    row = build_survival_row(ev, 48.0, VERSION)
    assert row["event_type"] == "censored_no_extubation"
    assert row["censor_cause"] == "terminal_extubation"


def test_death_at_vent_censors():
    ev = _eicu_events(death_time_hours=20.0, died_ventilated=True)
    row = build_survival_row(ev, 48.0, VERSION)
    assert row["censor_cause"] == "death_at_vent"


def test_negative_control_no_d5_is_success():
    row = build_survival_row(_eicu_events(), 48.0, VERSION)
    assert row["event_type"] == "successful_extubation"
    assert row["censor_cause"] is None
