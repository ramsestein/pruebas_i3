"""Tests de ``src/common/vent_intervals.py`` (Fase 1.6b, punto 1)."""

from __future__ import annotations

import pytest

from src.common.vent_intervals import (
    annotation_interval_h,
    intervals_from_annotations,
    subsample_times,
)


class TestIntervalsFromAnnotations:
    def test_una_anotacion_por_hora_sin_huecos(self):
        spans = intervals_from_annotations([0, 1, 2, 3], 2.0)
        assert len(spans) == 1
        assert (spans[0].start_h, spans[0].end_h) == (0.0, 3.0)

    def test_hueco_mayor_que_gap_cierra_episodio(self):
        spans = intervals_from_annotations([0, 1, 2, 10, 11], 4.0)
        assert [(s.start_h, s.end_h) for s in spans] == [(0.0, 2.0), (10.0, 11.0)]

    def test_hueco_igual_a_gap_mantiene(self):
        # El hueco es inclusivo: 0 -> 4 con G=4 es un único episodio.
        spans = intervals_from_annotations([0, 4], 4.0)
        assert len(spans) == 1
        assert (spans[0].start_h, spans[0].end_h) == (0.0, 4.0)

    def test_gap_menor_parte_el_episodio(self):
        spans = intervals_from_annotations([0, 4], 3.0)
        assert len(spans) == 2

    def test_gap_mas_grande_fusiona(self):
        # Mismo dato con G=2, 4, 6 y 8 -> de 2 intervalos a 1.
        times = [0, 1, 2, 7, 8]
        assert len(intervals_from_annotations(times, 2.0)) == 2
        assert len(intervals_from_annotations(times, 4.0)) == 2
        assert len(intervals_from_annotations(times, 6.0)) == 1
        assert len(intervals_from_annotations(times, 8.0)) == 1

    def test_singleton_se_conserva_por_defecto(self):
        spans = intervals_from_annotations([5.0], 4.0)
        assert len(spans) == 1
        assert spans[0].duration_h == 0.0

    def test_singleton_se_descarta_si_se_pide(self):
        spans = intervals_from_annotations([5.0], 4.0, keep_singletons=False)
        assert spans == []

    def test_descarta_singletons_pero_conserva_rachas(self):
        spans = intervals_from_annotations([0, 1, 20, 21], 2.0, keep_singletons=False)
        assert [(s.start_h, s.end_h) for s in spans] == [(0.0, 1.0), (20.0, 21.0)]

    def test_times_vacio(self):
        assert intervals_from_annotations([], 4.0) == []

    def test_ignora_no_finitos_y_desordenados(self):
        spans = intervals_from_annotations([2, float("nan"), 0, 1], 4.0)
        assert len(spans) == 1
        assert (spans[0].start_h, spans[0].end_h) == (0.0, 2.0)

    def test_gap_negativo_es_error(self):
        with pytest.raises(ValueError):
            intervals_from_annotations([0, 1], -1.0)


class TestSubsample:
    def test_submuestreo_a_2h(self):
        assert subsample_times([0, 1, 2, 3, 4, 5], 2.0) == [0.0, 2.0, 4.0]

    def test_submuestreo_a_4h(self):
        assert subsample_times([0, 1, 2, 3, 4, 5, 9], 4.0) == [0.0, 4.0, 9.0]

    def test_sin_submuestreo(self):
        assert subsample_times([3, 1, 2], 0) == [1.0, 2.0, 3.0]

    def test_submuestreo_conserva_el_primero_y_el_ultimo_lejano(self):
        assert subsample_times([0, 0.5, 100], 2.0) == [0.0, 100.0]


class TestAnnotationInterval:
    def test_intervalo_mediano(self):
        assert annotation_interval_h([0, 1, 2, 3]) == 1.0

    def test_una_sola_anotacion(self):
        assert annotation_interval_h([5.0]) is None

    def test_sin_anotaciones(self):
        assert annotation_interval_h([]) is None
