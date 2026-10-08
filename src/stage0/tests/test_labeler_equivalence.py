"""
tests/test_labeler_equivalence.py
=================================
Corrección 4: `src/stage0/labeling/survival.py` debe delegar en el etiquetador
único `src/common/labels.py`. Este test comprueba que, sobre los MISMOS
intentos, ambas rutas producen la misma decisión.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.common.labels import (
    assign_label,
    attempts_from_pairs,
    classify_attempt,
)
from src.stage0.adapters.base import ClinicalEvents, ExtubationAttempt
from src.stage0.labeling.survival import build_survival_row, classify_attempts

VERSION = "test_equiv"

# (extubación_h, reintubación_h|None)
CASES: list[list[tuple[float, float | None]]] = [
    [(72.0, None)],
    [(10.0, 16.0), (100.0, None)],
    [(24.0, 36.0), (60.0, 72.0), (96.0, None)],
    [(40.0, 90.0), (100.0, None)],          # 50 h: éxito a 48 h, fallo a 72 h
    [(10.0, 58.0), (100.0, None)],          # frontera exacta de 48 h
    [(0.0, 20.0), (30.0, 40.0)],            # todos fallan -> censura
]


def _events(pairs, confirmed=True, record_end=500.0) -> ClinicalEvents:
    return ClinicalEvents(
        patient_id="p", cohort="mimic", t0_unix=0.0, record_end_hours=record_end,
        extubation_confirmed=confirmed,
        extubation_confirmed_hours=(pairs[-1][0] if pairs else None),
        extubation_attempts=[
            ExtubationAttempt(i, e, "unknown", r) for i, (e, r) in enumerate(pairs)
        ],
    )


@pytest.mark.parametrize("window", [48.0, 72.0])
@pytest.mark.parametrize("pairs", CASES)
def test_survival_row_matches_common_labeler(pairs, window):
    events = _events(pairs)
    row = build_survival_row(events, window, VERSION)
    lab = assign_label(
        attempts_from_pairs(pairs), obs_end_h=events.record_end_hours,
        failure_window_h=window,
    )

    expected_event_type = (
        "successful_extubation"
        if lab.event_type == "successful_extubation"
        else "censored_no_extubation"
    )
    assert row["event_type"] == expected_event_type
    assert row["n_failed_attempts"] == lab.n_failed_attempts
    if lab.extubation_time_h is None:
        assert np.isnan(row["extubation_time_hours"])
    else:
        assert row["extubation_time_hours"] == pytest.approx(lab.extubation_time_h)


@pytest.mark.parametrize("window", [48.0, 72.0])
@pytest.mark.parametrize("pairs", CASES)
def test_classify_attempts_matches_classify_attempt(pairs, window):
    events = _events(pairs)
    classified = classify_attempts(events, window)
    for att, (e, r) in zip(classified, pairs):
        expected = classify_attempt(
            attempts_from_pairs([(e, r)])[0], window, extubation_confirmed=True
        )
        assert att.outcome == expected


def test_negative_control_different_windows_differ_where_expected():
    """Control: la ruta común SÍ distingue ventanas (no es un no-op)."""
    pairs = [(40.0, 90.0), (100.0, None)]
    assert build_survival_row(_events(pairs), 48.0, VERSION)["extubation_time_hours"] == pytest.approx(40.0)
    assert build_survival_row(_events(pairs), 72.0, VERSION)["extubation_time_hours"] == pytest.approx(100.0)
