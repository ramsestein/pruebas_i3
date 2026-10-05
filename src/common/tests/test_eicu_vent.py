"""
tests/test_eicu_vent.py
=======================
Tests de la auditoría de ventilación de eICU (Fase 1.6a): clasificación de
etiquetas y concordancia entre fuentes.
"""

from __future__ import annotations

import pytest

from src.common.eicu_vent import (
    CAT_AMBIGUOUS,
    CAT_INVASIVE,
    CAT_NIV,
    CAT_O2,
    classify_airway,
    classify_careplan,
    classify_respchart_label,
    classify_treatment,
    cohen_kappa,
    any_invasive_evidence,
    overlap_counts,
    pairwise_concordance,
)


class TestLabelClassification:
    def test_respchart_invasive(self):
        for lbl in ("Mechanical Ventilator Mode", "PEEP", "Tidal Volume (set)",
                    "Peak Insp. Pressure", "Total RR", "Ventilator Type"):
            assert classify_respchart_label(lbl) == CAT_INVASIVE

    def test_respchart_niv_and_o2(self):
        assert classify_respchart_label("NIV Setting EPAP") == CAT_NIV
        assert classify_respchart_label("CPAP") == CAT_NIV
        assert classify_respchart_label("FiO2") == CAT_O2
        assert classify_respchart_label("LPM O2") == CAT_O2

    def test_negative_control_unknown_is_ambiguous(self):
        assert classify_respchart_label("Head of Bed Elevation") == CAT_AMBIGUOUS
        assert classify_respchart_label("") == CAT_AMBIGUOUS

    def test_airway(self):
        assert classify_airway("Oral ETT") == CAT_INVASIVE
        assert classify_airway("Nasal ETT") == CAT_INVASIVE
        assert classify_airway("Tracheostomy") == CAT_INVASIVE
        assert classify_airway("No Artificial Airway") == CAT_O2
        assert classify_airway("Other") == CAT_AMBIGUOUS
        assert classify_airway(None) == CAT_AMBIGUOUS

    def test_treatment(self):
        assert classify_treatment(
            "pulmonary|ventilation and oxygenation|mechanical ventilation") == CAT_INVASIVE
        assert classify_treatment(
            "pulmonary|ventilation and oxygenation|non-invasive ventilation") == CAT_NIV
        assert classify_treatment(
            "pulmonary|ventilation and oxygenation|oxygen therapy (< 40%)|nasal cannula") == CAT_O2
        assert classify_treatment("cardiac|arrhythmia|atrial fibrillation") == CAT_AMBIGUOUS

    def test_careplan(self):
        assert classify_careplan("Ventilation", "Mechanical ventilation") == CAT_INVASIVE
        assert classify_careplan("Airway", "Endotracheal tube") == CAT_INVASIVE
        assert classify_careplan("Ventilation", "CPAP/BiPAP") == CAT_NIV
        assert classify_careplan("Ventilation", "Oxygen therapy") == CAT_O2
        assert classify_careplan("DVT Prophylaxis", "SCDs") == CAT_AMBIGUOUS


class TestEvidence:
    def test_any_invasive_evidence(self):
        assert any_invasive_evidence({"rc_invasive": True}) is True
        assert any_invasive_evidence({"airway_trach": True}) is True
        assert any_invasive_evidence({"apache_vent": False, "cpg_vent": False}) is False
        assert any_invasive_evidence({}) is False


class TestKappa:
    def test_perfect_agreement(self):
        assert cohen_kappa([(True, True), (True, True), (False, False)]) == pytest.approx(1.0)

    def test_perfect_disagreement(self):
        assert cohen_kappa([(True, False), (False, True)]) == pytest.approx(-1.0)

    def test_chance_level(self):
        # a = [T,T,F,F], b = [T,F,T,F]
        assert cohen_kappa([(True, True), (True, False),
                            (False, True), (False, False)]) == pytest.approx(0.0)

    def test_all_negative_is_na_kappa_but_one(self):
        # Sin variabilidad, kappa no está definido; se devuelve 1.0 si todo coincide.
        assert cohen_kappa([(False, False), (False, False)]) == pytest.approx(1.0)

    def test_empty_is_nan(self):
        import math
        assert math.isnan(cohen_kappa([]))


class TestOverlap:
    def test_overlap_counts(self):
        u = {1, 2, 3, 4, 5}
        c = overlap_counts({1, 2, 3}, {2, 3, 4}, u)
        assert c["both"] == 2
        assert c["only_a"] == 1
        assert c["only_b"] == 1
        assert c["neither"] == 1
        assert c["union"] == 4
        assert c["jaccard"] == pytest.approx(2 / 4)

    def test_pairwise_concordance_shape(self):
        flags = {"a": {1, 2}, "b": {2, 3}, "c": {4}}
        universe = {1, 2, 3, 4, 5}
        res = pairwise_concordance(flags, universe)
        assert set(res) == {"a|b", "a|c", "b|c"}
        assert "kappa" in res["a|b"] and "kappa_union" in res["a|b"]
        assert res["a|b"]["both"] == 1
