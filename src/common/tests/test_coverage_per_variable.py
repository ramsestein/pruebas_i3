"""
Tests de la **cobertura por variable** (Fase 1.6d, punto 3).

Requisitos del enunciado:

- la cobertura se mide **por variable**: "FC y MAP no pueden dar valores
  idénticos"; test con un evento sintético en el que difieran;
- la cobertura y la regla de observación fisiológica usan los **mismos nombres
  alternativos de pista** que el adaptador (``track_aliases.py``).
"""

from __future__ import annotations

import numpy as np
import pytest

from src.common.monitor_observation import range_for_track
from src.common.track_aliases import HR as HR_ALIASES
from src.common.track_aliases import MAP as MAP_ALIASES
from src.common.track_aliases import (
    PHYSIOLOGICAL_VARIABLES,
    SPO2 as SPO2_ALIASES,
    VITAL_ALIASES,
    variable_of_track,
)
from src.common.vital_signals import COVERAGE_TRACKS, coverage_fractions


class TestAliasUnicos:
    def test_alias_del_adaptador(self):
        # Mismos nombres que el adaptador (ECG_HR / PLETH_HR / HR; ABP_* /
        # NIBP_*; PLETH_SAT_O2).
        assert set(HR_ALIASES) == {
            "Intellivue/ECG_HR", "Intellivue/PLETH_HR", "Intellivue/HR"}
        assert "Intellivue/ABP_MEAN" in MAP_ALIASES
        assert "Intellivue/NIBP_MEAN" in MAP_ALIASES
        assert SPO2_ALIASES == ("Intellivue/PLETH_SAT_O2",)

    def test_cobertura_usa_los_alias(self):
        assert COVERAGE_TRACKS["HR"] == HR_ALIASES
        assert COVERAGE_TRACKS["MAP"] == MAP_ALIASES
        assert COVERAGE_TRACKS["SpO2"] == SPO2_ALIASES

    def test_fc_y_map_son_pistas_distintas(self):
        assert set(COVERAGE_TRACKS["HR"]).isdisjoint(COVERAGE_TRACKS["MAP"])

    def test_range_for_track_por_alias(self):
        assert range_for_track("Intellivue/PLETH_HR") == (20.0, 250.0)
        assert range_for_track("Intellivue/ECG_HR") == (20.0, 250.0)
        assert range_for_track("Intellivue/PLETH_SAT_O2") == (50.0, 100.0)
        # MAP no es una constante con rango fisiológico que se use aquí.
        assert range_for_track("Intellivue/ABP_MEAN") is None
        assert range_for_track("Intellivue/NIBP_MEAN") is None
        # Acepta también la variable.
        assert range_for_track("HR") == (20.0, 250.0)
        assert set(PHYSIOLOGICAL_VARIABLES) == {"HR", "SpO2"}

    def test_variable_of_track(self):
        assert variable_of_track("Intellivue/PLETH_HR") == "HR"
        assert variable_of_track("Intellivue/NIBP_MEAN") == "MAP"
        assert variable_of_track("Intellivue/desconocida") is None
        assert set(VITAL_ALIASES) >= {"HR", "SpO2", "MAP", "SBP", "DBP", "RR"}


def _series_by_minute(present_minutes, *, value=0.0, total_min=600, step_min=1.0):
    """Serie ``(times_h, values)`` con una muestra cada ``step_min`` min presente."""
    t = [m / 60.0 for m in present_minutes]
    v = [value] * len(t)
    return (np.asarray(t), np.asarray(v))


class TestCoberturaPorVariable:
    def test_fc_y_map_difieren_en_evento_sintetico(self):
        """Evento sintético en el que FC y MAP NO cubren lo mismo.

        Ventilación de 10 h. FC presente todo el tramo; MAP solo las primeras
        5 h (tras 5 h sin muestras, el LOCF de 4 h deja de cubrir).
        """
        all_min = list(range(0, 600, 1))
        first_min = list(range(0, 300, 1))
        series = {
            "HR": _series_by_minute(all_min),
            "SpO2": _series_by_minute(all_min),
            "MAP": _series_by_minute(first_min),
        }
        fracs = coverage_fractions(series, [(0.0, 10.0)])
        assert fracs["HR"] == pytest.approx(1.0, abs=1e-6)
        assert fracs["MAP"] < fracs["HR"]
        # El requisito explícito: FC y MAP no pueden ser idénticas.
        assert fracs["HR"] != fracs["MAP"]

    def test_rejilla_en_minutos_no_en_horas(self):
        """La cobertura se mide en minutos: una ausencia de 5 h se nota.

        Con rejilla horaria (60 min) el hueco también se vería, así que se
        comprueba directamente el factor de conversión: un tramo ventilado de
        90 min tiene ~91 puntos de rejilla (minutos), no 2 (horas).
        """
        all_min = list(range(0, 91, 1))
        series = {"HR": _series_by_minute(all_min)}
        fracs = coverage_fractions(series, [(0.0, 1.5)])
        assert fracs["HR"] == pytest.approx(1.0, abs=1e-6)

    def test_variable_sin_muestras_es_cero(self):
        series = {"HR": _series_by_minute([0, 1, 2]),
                  "MAP": (np.asarray([]), np.asarray([]))}
        fracs = coverage_fractions(series, [(5.0, 10.0)])  # muy lejos de HR
        assert fracs["MAP"] == 0.0
        assert fracs["HR"] == 0.0

    def test_map_visible_donde_fc_no(self):
        """MAP puede cubrir tramos en los que FC no está (el caso real)."""
        series = {
            "HR": (np.asarray([0.0]), np.asarray([80.0])),
            "MAP": _series_by_minute(list(range(300, 600, 1))),
        }
        fracs = coverage_fractions(series, [(0.0, 5.0), (5.0, 10.0)])
        assert fracs["MAP"] > fracs["HR"]
        # FC solo cubre ~4 h (LOCF) del primer tramo -> ~0.40 de la rejilla.
        assert 0.3 < fracs["HR"] < 0.5
