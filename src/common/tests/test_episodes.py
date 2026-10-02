"""
tests/test_episodes.py
======================
Tests de la segmentación común de episodios (D1/D2/D4).

Cada comprobación lleva un control negativo que debe fallar si la regla se
rompe (nada de "disponible = >= 1 valor").
"""

from __future__ import annotations

import numpy as np
import pytest

from src.common.episodes import (
    DISCONNECT_GAP_H,
    Attempt,
    Episode,
    Span,
    build_episodes,
    episode_extubation_events,
    merge_spans,
    monitor_change_boundaries,
    monitor_span_boundaries,
    segment_attempts,
)
from src.stage0.adapters.base import ClinicalEvents, ExtubationAttempt
from src.stage0.labeling.survival import classify_attempts


def _dense_times(start: float, end: float, step: float = 0.25) -> list[float]:
    """Marcas de monitor densas en [start, end] (huecos << 1 h)."""
    n = int(round((end - start) / step)) + 1
    return [float(t) for t in np.linspace(start, end, n)]


# ── D1: fusión de desconexiones y separación de intentos ─────────────────────

class TestDisconnectionMerge:
    def test_gap_90min_merges_into_one_attempt(self):
        """Desconexión de 90 min (<= 2 h) -> 1 intento."""
        vent = [Span(0.0, 10.0), Span(11.5, 20.0)]  # hueco 1.5 h
        attempts = segment_attempts(vent, DISCONNECT_GAP_H)
        assert len(attempts) == 1
        assert attempts[0].start_h == 0.0
        assert attempts[0].end_h == 20.0

    def test_gap_exactly_2h_merges(self):
        """Frontera inclusiva: hueco exactamente 2 h se fusiona."""
        vent = [Span(0.0, 10.0), Span(12.0, 20.0)]
        assert len(segment_attempts(vent, 2.0)) == 1

    def test_negative_control_gap_3h_splits(self):
        """Control negativo: un hueco de 3 h (> 2 h) NO se fusiona."""
        vent = [Span(0.0, 10.0), Span(13.0, 20.0)]
        attempts = segment_attempts(vent, DISCONNECT_GAP_H)
        assert len(attempts) == 2
        assert [a.attempt_idx for a in attempts] == [0, 1]


# ── D1 + D3: mismo evento con dos intentos (fallo a 48 h) ────────────────────

class TestTwoAttemptsOneEpisode:
    def test_two_runs_6h_apart_continuous_monitor_is_one_episode(self):
        """Dos tramos separados 6 h con monitor continuo -> 1 evento, 2 intentos."""
        vent = [Span(0.0, 10.0), Span(16.0, 26.0)]
        episodes = build_episodes(
            vent, monitor_times_h=_dense_times(0.0, 26.0)
        )
        assert len(episodes) == 1
        assert episodes[0].n_attempts == 2
        assert episodes[0].start_h == 0.0
        assert episodes[0].end_h == 26.0

    def test_reintubation_within_48h_is_failure(self):
        """El 2.º intento a 6 h del 1.º clasifica como fallo a 48 h."""
        vent = [Span(0.0, 10.0), Span(16.0, 26.0)]
        ep = build_episodes(vent, monitor_times_h=_dense_times(0.0, 26.0))[0]

        events = episode_extubation_events(ep)
        assert events == [(10.0, 16.0), (26.0, None)]

        clinical = ClinicalEvents(
            patient_id="p1",
            cohort="clinic",
            t0_unix=0.0,
            record_end_hours=26.0,
            extubation_confirmed=True,
            extubation_confirmed_hours=26.0,
            extubation_attempts=[
                ExtubationAttempt(0, extub, "failure" if re is not None else "success", re)
                for extub, re in events
            ],
        )
        classified = classify_attempts(clinical, failure_window_h=48.0)
        assert classified[0].outcome == "failure"

    def test_negative_control_monitor_gap_splits_episode(self):
        """Control negativo: el mismo patrón con hueco de monitor > 1 h -> 2 eventos."""
        vent = [Span(0.0, 10.0), Span(16.0, 26.0)]
        times = _dense_times(0.0, 10.0) + _dense_times(16.0, 26.0)  # hueco 6 h
        episodes = build_episodes(vent, monitor_times_h=times)
        assert len(episodes) == 2
        assert all(ep.n_attempts == 1 for ep in episodes)


# ── D2: cambio de paciente ───────────────────────────────────────────────────

class TestPatientChange:
    def test_monitor_gap_2h_splits_into_two_events(self):
        """Hueco de monitor de 2 h (> 1 h) -> 2 eventos."""
        vent = [Span(0.0, 10.0), Span(16.0, 26.0)]
        times = _dense_times(0.0, 10.5) + _dense_times(12.5, 26.0)  # hueco 2 h
        episodes = build_episodes(vent, monitor_times_h=times)
        assert len(episodes) == 2
        assert [ep.n_attempts for ep in episodes] == [1, 1]

    def test_negative_control_gap_1h_is_same_patient(self):
        """Control negativo: hueco de exactamente 1 h NO es cambio de paciente."""
        vent = [Span(0.0, 10.0), Span(16.0, 26.0)]
        times = _dense_times(0.0, 10.5) + _dense_times(11.5, 26.0)  # hueco 1 h
        episodes = build_episodes(vent, monitor_times_h=times)
        assert len(episodes) == 1

    def test_three_patients_in_monthly_file(self):
        """Fichero mensual con 3 pacientes -> 3 eventos."""
        vent = [Span(0.0, 10.0), Span(48.0, 60.0), Span(100.0, 120.0)]
        times = (
            _dense_times(0.0, 10.0)
            + _dense_times(48.0, 60.0)
            + _dense_times(100.0, 120.0)
        )
        episodes = build_episodes(vent, monitor_times_h=times)
        assert len(episodes) == 3
        assert [round(ep.start_h) for ep in episodes] == [0, 48, 100]

    def test_negative_control_two_patients_two_events(self):
        """Control negativo: con 2 pacientes solo hay 2 eventos."""
        vent = [Span(0.0, 10.0), Span(48.0, 60.0)]
        times = _dense_times(0.0, 10.0) + _dense_times(48.0, 60.0)
        assert len(build_episodes(vent, monitor_times_h=times)) == 2

    def test_stay_bounds_are_hard_boundaries(self):
        """eICU/MIMIC: las fronteras de estancia separan eventos."""
        vent = [Span(0.0, 10.0), Span(20.0, 30.0)]
        episodes = build_episodes(
            vent, stay_bounds=[(0.0, 12.0), (19.0, 31.0)]
        )
        assert len(episodes) == 2


# ── D4: actividad de ventilador sin paciente ─────────────────────────────────

class TestVentilatorWithoutPatient:
    def test_vent_without_monitor_is_excluded(self):
        """Ventilador sin HR ni SpO2 en todo el tramo -> excluido."""
        episodes = build_episodes(
            [Span(0.0, 10.0)], hr_spans=[], spo2_spans=[]
        )
        assert len(episodes) == 1
        assert episodes[0].excluded is True
        assert episodes[0].exclusion_reason == "ventilator_without_patient"
        assert episodes[0].no_patient_fraction == pytest.approx(1.0)

    def test_negative_control_with_monitor_not_excluded(self):
        """Control negativo: el mismo tramo con HR presente NO se excluye."""
        episodes = build_episodes(
            [Span(0.0, 10.0)],
            hr_spans=[Span(0.0, 10.0)],
            spo2_spans=[],
        )
        assert episodes[0].excluded is False
        assert episodes[0].no_patient_fraction == pytest.approx(0.0)

    def test_threshold_80_percent(self):
        """Se excluye a partir del 80 % sin paciente, no antes."""
        # HR presente solo 1 h de 10 -> 90 % sin paciente -> excluido.
        ep_excluded = build_episodes(
            [Span(0.0, 10.0)], hr_spans=[Span(0.0, 1.0)], spo2_spans=[]
        )[0]
        assert ep_excluded.excluded is True

        # HR presente 3 h de 10 -> 70 % sin paciente -> NO excluido.
        ep_kept = build_episodes(
            [Span(0.0, 10.0)], hr_spans=[Span(0.0, 3.0)], spo2_spans=[]
        )[0]
        assert ep_kept.excluded is False
        assert ep_kept.no_patient_fraction == pytest.approx(0.7)

    def test_hr_or_spo2_presence_is_enough(self):
        """Basta HR o SpO2 (no ambas) para considerar que hay paciente."""
        ep = build_episodes(
            [Span(0.0, 10.0)],
            hr_spans=[],
            spo2_spans=[Span(0.0, 8.5)],  # 85 % cubierto -> 15 % sin paciente
        )[0]
        assert ep.excluded is False
        assert ep.no_patient_fraction == pytest.approx(0.15)


# ── Utilidades ───────────────────────────────────────────────────────────────

class TestHelpers:
    def test_merge_spans_drops_zero_length(self):
        assert merge_spans([Span(5.0, 5.0)], 1.0) == []

    def test_monitor_boundaries_gap_exactly_1h_is_none(self):
        assert monitor_change_boundaries([0.0, 1.0, 2.0], 1.0) == []

    def test_monitor_boundaries_gap_above_1h(self):
        b = monitor_change_boundaries([0.0, 2.0], 1.0)
        assert b == [1.0]

    def test_monitor_span_boundaries(self):
        # Presencia [0,10] y [13,26] -> hueco 3 h > 1 h -> corte en 11.5.
        b = monitor_span_boundaries([Span(0.0, 10.0), Span(13.0, 26.0)], 1.0)
        assert b == [11.5]

    def test_build_episodes_with_monitor_spans(self):
        vent = [Span(0.0, 10.0), Span(16.0, 26.0)]
        spans = [Span(0.0, 10.5), Span(12.5, 26.0)]  # hueco 2 h
        eps = build_episodes(vent, monitor_spans=spans)
        assert len(eps) == 2

    def test_span_rejects_inverted(self):
        with pytest.raises(ValueError):
            Span(10.0, 5.0)
