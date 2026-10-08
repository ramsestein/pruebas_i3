"""
tests/test_short_events.py
==========================
Tests de la clasificación por señal de eventos < 1 h (Fase 1.5, punto 3).
"""

from __future__ import annotations

import numpy as np

from src.common.short_events import classify_short_event


def _cyclic_awp(n_cycles: int = 5, ptp: float = 15.0, samples: int = 500):
    t = np.linspace(0, n_cycles * 2 * np.pi, samples)
    return ptp / 2.0 * np.sin(t) + ptp / 2.0


class TestClassifyShortEvent:
    def test_cyclic_awp_with_tv_is_plausible(self):
        res = classify_short_event(_cyclic_awp(), np.full(5, 450.0))
        assert res["classification"] == "ventilacion_invasiva_plausible"
        assert "awp_ciclica" in res["reasons"]

    def test_negative_control_flat_awp_is_artifact(self):
        res = classify_short_event(np.zeros(200), np.array([]))
        assert res["classification"] == "artefacto"
        assert "awp_plana" in res["reasons"]

    def test_no_awp_no_tv_is_artifact(self):
        res = classify_short_event(np.array([]), np.array([]))
        assert res["classification"] == "artefacto"
        assert "sin_awp" in res["reasons"]

    def test_tv_alone_with_small_awp_movement_is_plausible(self):
        awp = _cyclic_awp(n_cycles=4, ptp=3.0)
        res = classify_short_event(awp, np.full(6, 500.0))
        assert res["classification"] == "ventilacion_invasiva_plausible"

    def test_negative_control_absurd_tv_is_artifact(self):
        """TV de 5000 mL es fisiológicamente imposible → no cuenta como señal."""
        res = classify_short_event(np.zeros(100), np.full(4, 5000.0))
        assert res["classification"] == "artefacto"


class TestMarkersWithoutAwp:
    """Fase 1.6b (punto 3): sin onda AWP, los AJUSTES (D6) bastan."""

    def test_solo_ajustes_es_plausible(self):
        # Caso real de Clínic: PEEP + PIP + VENT_RR + TV sin onda de presión.
        markers = {
            "Intellivue/PEEP_CMH2O": 120,
            "Intellivue/PIP_CMH2O": 90,
            "Intellivue/VENT_RR": 120,
            "Intellivue/TV_EXP": 118,
        }
        res = classify_short_event(np.array([]), np.array([]),
                                   marker_tracks=markers)
        assert res["classification"] == "ventilacion_invasiva_plausible"
        assert "ajustes_presentes" in res["reasons"]
        assert res["metrics"]["n_marker_tracks"] == 4

    def test_un_solo_marcador_no_basta(self):
        res = classify_short_event(np.array([]), np.array([]), marker_tracks={
            "Intellivue/PEEP_CMH2O": 50})
        assert res["classification"] == "artefacto"
        assert res["metrics"]["n_marker_tracks"] == 1

    def test_la_fio2_no_es_marcador(self):
        # FiO2 se anota también con oxigenoterapia: NO marca ventilación (D6).
        res = classify_short_event(np.array([]), np.array([]), marker_tracks={
            "Intellivue/FIO2": 200, "Intellivue/MV_EXP": 200})
        assert res["classification"] == "artefacto"

    def test_marcadores_sin_registros_no_cuentan(self):
        res = classify_short_event(np.array([]), np.array([]), marker_tracks={
            "Intellivue/PEEP_CMH2O": 0, "Intellivue/PIP_CMH2O": 0})
        assert res["classification"] == "artefacto"

    def test_onda_plana_con_ajustes_sigue_siendo_plausible(self):
        res = classify_short_event(np.zeros(100), np.array([]), marker_tracks={
            "Intellivue/PEEP_CMH2O": 10, "Intellivue/VENT_RR": 10})
        assert res["classification"] == "ventilacion_invasiva_plausible"
