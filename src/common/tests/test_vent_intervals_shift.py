"""Tests de la etiqueta de 3 clases y la corrección por estrato (Fase 1.6c, punto 3)."""

from __future__ import annotations

import numpy as np
import pytest

from src.common.episodes import Span
from src.common.vent_intervals import (
    LABEL3_CENSORED,
    LABEL3_CLASSES,
    LABEL3_SUCCESS,
    LABEL3_SUCCESS_AFTER_FAILURE,
    confusion_matrix,
    correction_verdict,
    label3,
    shift_interval_ends,
)


class TestLabel3:
    def test_exito_sin_fallos(self):
        assert label3("successful_extubation", 0) == LABEL3_SUCCESS

    def test_exito_con_fallo_previo(self):
        assert label3("successful_extubation", 2) == LABEL3_SUCCESS_AFTER_FAILURE

    def test_censura_por_cualquier_causa(self):
        assert label3("censored_end_of_record", 0) == LABEL3_CENSORED
        assert label3("censored_terminal_extubation", 3) == LABEL3_CENSORED

    def test_sin_etiqueta(self):
        assert label3(None, 0) == LABEL3_CENSORED

    def test_clases(self):
        assert LABEL3_CLASSES == (LABEL3_SUCCESS, LABEL3_SUCCESS_AFTER_FAILURE,
                                  LABEL3_CENSORED)


class TestShiftIntervalEnds:
    def test_suma_al_fin_sin_tocar_el_inicio(self):
        spans = [Span(0.0, 10.0), Span(20.0, 30.0)]
        out = shift_interval_ends(spans, 2.0)
        assert [(s.start_h, s.end_h) for s in out] == [(0.0, 12.0), (20.0, 32.0)]

    def test_desplazamiento_negativo_acorta(self):
        out = shift_interval_ends([Span(0.0, 10.0)], -1.5)
        assert [(s.start_h, s.end_h) for s in out] == [(0.0, 8.5)]

    def test_no_invierte_con_desplazamiento_grande(self):
        out = shift_interval_ends([Span(0.0, 10.0)], -50.0, min_duration_h=0.0)
        assert out[0].end_h == pytest.approx(0.0)
        assert out[0].duration_h >= 0.0

    def test_duracion_minima(self):
        out = shift_interval_ends([Span(0.0, 10.0)], -50.0, min_duration_h=1.0)
        assert out[0].duration_h == pytest.approx(1.0)

    def test_sin_desplazamiento_no_cambia(self):
        spans = [Span(1.0, 2.0)]
        assert shift_interval_ends(spans, 0.0)[0] == spans[0]


class TestSignoDeLaCorreccion:
    """La corrección debe ir **contra** el sesgo medido, no a favor.

    ``end_error`` = fin_reconstruido − fin_real. Si los ajustes dejan de
    anotarse antes de la desconexión, la mediana es negativa (el fin se queda
    corto) y la corrección que se suma es ``-mediana``. Sumar la mediana tal
    cual duplicaba el sesgo (el error mediano pasaba de -1.23 h a -2.45 h en
    MIMIC, Fase 1.6c punto 3).
    """

    @staticmethod
    def _mediana_error(recons: list[Span], real_end: float) -> float:
        return float(np.median([sp.end_h - real_end for sp in recons]))

    def test_la_correccion_reduce_el_sesgo(self):
        real_end = 10.0
        crudos = [Span(0.0, 8.0), Span(1.0, 8.5), Span(2.0, 9.5)]
        sesgo = self._mediana_error(crudos, real_end)
        assert sesgo < 0  # el fin reconstruido se queda corto
        corregidos = shift_interval_ends(crudos, -sesgo)
        assert abs(self._mediana_error(corregidos, real_end)) < 1e-9

    def test_sumar_la_mediana_tal_cual_empeora_el_sesgo(self):
        real_end = 10.0
        crudos = [Span(0.0, 8.0), Span(1.0, 8.5), Span(2.0, 9.5)]
        sesgo = self._mediana_error(crudos, real_end)
        mal = shift_interval_ends(crudos, sesgo)
        assert self._mediana_error(mal, real_end) == pytest.approx(2.0 * sesgo)

    def test_no_acorta_por_debajo_de_la_duracion_minima(self):
        out = shift_interval_ends([Span(0.0, 1.0)], -5.0, min_duration_h=0.5)
        assert out[0].duration_h == pytest.approx(0.5)


class TestConfusionMatrix:
    def test_matriz_3x3(self):
        pred = ["exito", "exito", "censura", "exito_con_fallo_previo"]
        ref = ["exito", "censura", "censura", "exito"]
        m = confusion_matrix(pred, ref)
        assert m["exito"]["exito"] == 1
        assert m["exito"]["censura"] == 1
        assert m["censura"]["censura"] == 1
        assert m["exito_con_fallo_previo"]["exito"] == 1
        assert sum(sum(v.values()) for v in m.values()) == 4

    def test_ignora_clases_desconocidas(self):
        m = confusion_matrix(["otra"], ["exito"])
        assert sum(sum(v.values()) for v in m.values()) == 0


class TestCorrectionVerdict:
    def test_aprueba_si_mejora_en_todos_los_estratos(self):
        v = correction_verdict({1.0: 85.0, 2.0: 83.0}, {1.0: 88.0, 2.0: 86.0})
        assert v["approved"] is True
        assert v["gains_pct"] == {1.0: 3.0, 2.0: 3.0}

    def test_rechaza_si_empeora_en_un_estrato(self):
        v = correction_verdict({1.0: 85.0, 2.0: 83.0}, {1.0: 88.0, 2.0: 82.0})
        assert v["approved"] is False

    def test_rechaza_sin_ganancia(self):
        v = correction_verdict({1.0: 85.0}, {1.0: 85.0})
        assert v["approved"] is False

    def test_sin_estratos_comunes(self):
        v = correction_verdict({1.0: 85.0}, {2.0: 88.0})
        assert v["approved"] is False and v["gains_pct"] == {}
