"""
Tests de ``common/transfer_validation.py`` (Fase 1.6d, punto 2): validación de
``transfer_ventilated`` con el destino del alta en eICU.
"""

from __future__ import annotations

import pytest

from src.common.transfer_validation import (
    INCOMPATIBLE_DESTINATIONS,
    is_incompatible_destination,
    propose_transfer_reclassification,
)


class TestDestino:
    @pytest.mark.parametrize("dest", ["Home", "Floor", "hospice", "Other",
                                      "Assisted Living", "Home with home health"])
    def test_incompatibles(self, dest):
        assert is_incompatible_destination(dest) is True

    @pytest.mark.parametrize("dest", ["Step-Down Unit (SDU)", "Other ICU",
                                      "Skilled Nursing Facility", "Death",
                                      "Rehabilitation"])
    def test_compatibles(self, dest):
        assert is_incompatible_destination(dest) is False

    def test_none(self):
        assert is_incompatible_destination(None) is False

    def test_normaliza_espacios_y_mayusculas(self):
        assert is_incompatible_destination("  HOME  ") is True


class TestReglaPropuesta:
    def test_reclasifica_a_end_of_record(self):
        assert propose_transfer_reclassification(
            "transfer_ventilated", "Home", 3.5) == "end_of_record"

    def test_no_reclasifica_otra_causa(self):
        assert propose_transfer_reclassification(
            "death_at_vent", "Home", 3.5) is None

    def test_no_reclasifica_destino_compatible(self):
        assert propose_transfer_reclassification(
            "transfer_ventilated", "Other ICU", 3.5) is None

    def test_no_reclasifica_si_el_ultimo_ajuste_es_reciente(self):
        # Ajuste invasivo 0.5 h antes del alta: seguía ventilado.
        assert propose_transfer_reclassification(
            "transfer_ventilated", "Home", 0.5) is None

    def test_gap_desconocido_no_reclasifica(self):
        assert propose_transfer_reclassification(
            "transfer_ventilated", "Home", None) is None

    def test_umbral_configurable(self):
        assert propose_transfer_reclassification(
            "transfer_ventilated", "Floor", 0.5, min_gap_h=0.25) == "end_of_record"

    def test_conjunto_no_vacio(self):
        assert "home" in INCOMPATIBLE_DESTINATIONS
