"""
tests de las comprobaciones de Fase 0b.

Cada comprobación con `passed` tiene:
  * un caso verde (fixture correcto → VERDE),
  * un control negativo (fixture sintético roto → ROJO).

Regla de honestidad: un fixture roto DEBE dar passed=False; si no, el test
falla y la verificación no es honesta.
"""
from __future__ import annotations

import pytest

from scripts.verify.fase0b.checks import (
    channel_hour_coverage,
    check_channel_coverage,
    check_expected_channels,
    check_file_boundary_events,
    check_fused_vs_source,
    check_omitted_sources,
    count_below_threshold,
    segment_events_and_attempts,
    summarize,
)


# ── P1.1 duración fusionada vs fuente ────────────────────────────────────────

class TestFusedVsSource:
    def test_green_fused_matches_source(self):
        r = check_fused_vs_source(10.0, 10.0, tolerance=0.01)
        assert r["passed"] is True

    def test_red_broken_fused_shorter_than_source(self):
        r = check_fused_vs_source(5.0, 10.0, tolerance=0.01)
        assert r["passed"] is False
        assert r["rel_error"] == pytest.approx(0.5)

    def test_red_no_source(self):
        r = check_fused_vs_source(5.0, 0.0)
        assert r["passed"] is False

    def test_tolerance_boundary(self):
        # error relativo 1 % exacto → dentro de tolerancia
        r = check_fused_vs_source(10.1, 10.0, tolerance=0.01)
        assert r["passed"] is True


# ── P1.2 ficheros omitidos ───────────────────────────────────────────────────

class TestOmittedSources:
    def test_green_nothing_omitted(self):
        r = check_omitted_sources(["a", "b", "c"], ["a", "b", "c"])
        assert r["passed"] is True
        assert r["omitted"] == []

    def test_red_broken_one_omitted(self):
        r = check_omitted_sources(["a", "b", "c"], ["a", "b"])
        assert r["passed"] is False
        assert r["omitted"] == ["c"]


# ── P1.3 / P3 cobertura horaria por canal ────────────────────────────────────

class TestChannelHourCoverage:
    def test_green_full_coverage(self):
        # una muestra por hora durante 10 horas
        cov = channel_hour_coverage([0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5],
                                     t0_hours=0.0, tend_hours=10.0)
        assert cov == pytest.approx(1.0)

    def test_red_broken_only_first_hour(self):
        cov = channel_hour_coverage([0.5], t0_hours=0.0, tend_hours=100.0)
        assert cov == pytest.approx(0.01)

    def test_zero_when_no_samples_in_range(self):
        cov = channel_hour_coverage([200.0], t0_hours=0.0, tend_hours=10.0)
        assert cov == 0.0

    def test_never_uses_nonempty_as_available(self):
        # "al menos un valor no vacío" daría 1.0; la cobertura horaria no.
        cov = channel_hour_coverage([0.0], t0_hours=0.0, tend_hours=50.0)
        assert cov < 1.0


class TestCheckChannelCoverage:
    def test_green_all_channels_covered(self):
        r = check_channel_coverage({"RR": 0.9, "HR": 0.8})
        assert r["passed"] is True

    def test_red_broken_channel_zero(self):
        r = check_channel_coverage({"RR": 0.9, "HR": 0.0})
        assert r["passed"] is False
        assert "HR" in r["red_channels"]


# ── P3 lista de canales esperados (la misma en todas las cohortes) ───────────

class TestExpectedChannels:
    EXPECTED = ["RR", "HR", "SpO2", "PEEP", "MAP", "FiO2", "TV", "PIP", "ECG", "PPG", "ABP"]

    def test_green_all_present(self):
        r = check_expected_channels(self.EXPECTED, self.EXPECTED)
        assert r["passed"] is True

    def test_red_broken_missing_fio2(self):
        present = [c for c in self.EXPECTED if c != "FiO2"]
        r = check_expected_channels(present, self.EXPECTED)
        assert r["passed"] is False
        assert r["missing"] == ["FiO2"]


# ── P2 segmentación D1+D2 ────────────────────────────────────────────────────

class TestSegmentation:
    def test_green_disconnection_90min_is_one_attempt(self):
        # ventilador con un hueco de 90 min (<=2h) y monitor continuo
        vent = [(0.0, 10.0), (11.5, 20.0)]
        mon = [(0.0, 20.0)]
        seg = segment_events_and_attempts(vent, mon)
        assert seg["n_attempts_total"] == 1
        assert len(seg["events"]) == 1

    def test_green_two_gaps_6h_is_two_attempts_one_event(self):
        # dos tramos separados 6h (>2h) con monitor continuo → 2 intentos, 1 evento
        vent = [(0.0, 10.0), (16.0, 26.0)]
        mon = [(0.0, 26.0)]
        seg = segment_events_and_attempts(vent, mon)
        assert seg["n_attempts_total"] == 2
        assert len(seg["events"]) == 1

    def test_green_monitor_gap_2h_is_two_events(self):
        # hueco de monitor de 2h (>1h) divide en 2 eventos (D2)
        vent = [(0.0, 5.0), (8.0, 13.0)]
        mon = [(0.0, 5.0), (7.0, 13.0)]  # hueco 5->7 = 2h
        seg = segment_events_and_attempts(vent, mon)
        assert len(seg["events"]) >= 2

    def test_green_vent_without_monitor_excluded(self):
        # ventilador sin monitor simultáneo → no-paciente
        vent = [(0.0, 10.0)]
        mon = []
        seg = segment_events_and_attempts(vent, mon)
        assert seg["n_vent_nonpatient_excluded"] >= 1
        assert seg["n_attempts_total"] == 0


# ── P2 límites de fichero (informativo) ──────────────────────────────────────

class TestFileBoundary:
    def test_reports_boundary_counts(self):
        r = check_file_boundary_events(
            event_starts=[0.0, 100.0],
            event_ends=[50.0, 150.0],
            source_starts=[0.0],
            source_ends=[50.0],
            tolerance_hours=0.05,
        )
        assert r["n_events"] == 2
        assert r["n_start_at_source_boundary"] == 1
        assert r["n_end_at_source_boundary"] == 1


# ── P8 sesgo de selección ─────────────────────────────────────────────────────

class TestCountBelowThreshold:
    def test_counts_below(self):
        assert count_below_threshold([1.0, 2.0, 20.0], 12.0) == 2

    def test_none_below(self):
        assert count_below_threshold([12.0, 20.0], 12.0) == 0


class TestSummarize:
    def test_summary(self):
        s = summarize([1.0, 2.0, 3.0, 4.0, 5.0])
        assert s["n"] == 5
        assert s["min"] == 1.0
        assert s["max"] == 5.0
        assert s["median"] == 3.0
