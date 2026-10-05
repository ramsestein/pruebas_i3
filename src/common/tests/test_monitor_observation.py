"""Tests de ``src/common/monitor_observation.py`` (Fase 1.6b, punto 4)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from src.common.episodes import Span, build_episodes
from src.common.monitor_observation import (
    HR_PHYSIOLOGICAL_RANGE,
    SPO2_PHYSIOLOGICAL_RANGE,
    physiological_extent,
    physiological_mask,
    physiological_spans,
    range_for_track,
)
from src.common.vital_signals import TrackProbe, VitalProbe, hr_span_from_probe


class TestRanges:
    def test_rango_por_pista(self):
        assert range_for_track("Intellivue/ECG_HR") == HR_PHYSIOLOGICAL_RANGE
        assert range_for_track("Intellivue/PLETH_SAT_O2") == SPO2_PHYSIOLOGICAL_RANGE
        assert range_for_track("Intellivue/AWP_WAV") is None

    def test_mascara_fisiologica(self):
        mask = physiological_mask([0.0, 60.0, 300.0, float("nan"), 250.0], 20.0, 250.0)
        assert mask.tolist() == [False, True, False, False, True]


class TestExtent:
    def test_extremo_de_los_valores_fisiologicos(self):
        t = [0, 1, 2, 3]
        v = [70, 0, 0, 80]
        lo, hi, n = physiological_extent(t, v, 20, 250)
        assert (lo, hi, n) == (0.0, 3.0, 2)

    def test_todo_plano_no_tiene_extremo(self):
        # 2 h de onda plana (FC = 0): NO es observación.
        lo, hi, n = physiological_extent([0, 1, 2], [0.0, 0.0, 0.0], 20, 250)
        assert lo is None and hi is None and n == 0

    def test_fuera_de_rango_por_arriba(self):
        lo, hi, n = physiological_extent([0, 1], [300.0, 400.0], 20, 250)
        assert n == 0


class TestSpans:
    def test_spans_de_series_fisiologicas(self):
        spans = physiological_spans(
            [([0, 1, 2, 5, 6], [70, 72, 75, 80, 82])], 20, 250, 2.0)
        assert [(s.start_h, s.end_h) for s in spans] == [(0.0, 6.0)]

    def test_onda_plana_no_genera_span(self):
        assert physiological_spans([([0, 1, 2], [0.0, 0.0, 0.0])], 20, 250, 2.0) == []


class TestProbeUsesPhysiology:
    def _probe(self, dt_max_phys):
        return VitalProbe(
            path=Path("x.vital"), dtstart=0.0, dtend=10.0,
            tracks={"Intellivue/ECG_HR": TrackProbe(
                name="Intellivue/ECG_HR", dt_min=0.0, dt_max=10.0, n_recs=100,
                srate=1.0, is_wave=False, dt_min_phys=0.0,
                dt_max_phys=dt_max_phys, n_phys=50)},
        )

    def test_usa_el_extremo_fisiologico(self):
        span = hr_span_from_probe(self._probe(6.0))
        assert (span.start_h, span.end_h) == (0.0, 6.0)

    def test_sin_datos_fisiologicos_cae_al_bruto(self):
        span = hr_span_from_probe(self._probe(None))
        assert (span.start_h, span.end_h) == (0.0, 10.0)


class TestEpisodeObservation:
    """Punto 4: 2 h de ondas planas tras la desconexión → end_of_record."""

    def test_ondas_planas_no_confirman_extubacion(self):
        # Ventilación 0-10 h. La FC fisiológica solo llega a 10.2 h; el monitor
        # (región de paciente) sigue hasta las 12 h con onda plana.
        episodes = build_episodes(
            [Span(0.0, 10.0)],
            hr_spans=[Span(0.0, 10.2)], spo2_spans=[Span(0.0, 10.2)],
            monitor_spans=[Span(0.0, 12.0)],
        )
        ep = episodes[0]
        assert ep.observation_end_h == 10.2

    def test_observacion_fisiologica_confirma_extubacion(self):
        episodes = build_episodes(
            [Span(0.0, 10.0)],
            hr_spans=[Span(0.0, 12.0)], spo2_spans=[Span(0.0, 12.0)],
            monitor_spans=[Span(0.0, 12.0)],
        )
        assert episodes[0].observation_end_h == 12.0
