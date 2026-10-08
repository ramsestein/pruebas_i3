"""
Tests de ``common/end_reasons.py`` (Fase 1.6d, punto 6): vocabulario único de
``end_reason`` en las 4 cohortes.
"""

from __future__ import annotations

import pytest

from src.common.end_reasons import (
    DEATH_AT_VENT,
    END_OF_RECORD,
    END_REASONS,
    EXTUBATION_OBSERVED,
    TERMINAL_EXTUBATION,
    TRACHEOSTOMY,
    TRANSFER_VENTILATED,
    canonical_end_reason,
    end_reason_from_causes,
    is_canonical,
)


class TestVocabulario:
    def test_seis_valores_canonicos(self):
        assert set(END_REASONS) == {
            "extubation_observed", "transfer_ventilated", "death_at_vent",
            "terminal_extubation", "tracheostomy", "end_of_record",
        }
        assert len(END_REASONS) == 6

    def test_normalizacion_de_causas_internas(self):
        assert canonical_end_reason("trach") == TRACHEOSTOMY
        assert canonical_end_reason("trach_time_unknown") == TRACHEOSTOMY
        assert canonical_end_reason("death") == DEATH_AT_VENT
        assert canonical_end_reason("transfer_ventilated") == TRANSFER_VENTILATED
        assert canonical_end_reason("terminal_extubation") == TERMINAL_EXTUBATION
        assert canonical_end_reason("end_of_record") == END_OF_RECORD
        assert canonical_end_reason(None) is None
        # Un valor desconocido se devuelve tal cual (fuera de vocabulario).
        assert canonical_end_reason("excluded_trach_preexisting") == (
            "excluded_trach_preexisting")

    def test_is_canonical(self):
        assert all(is_canonical(r) for r in END_REASONS)
        assert not is_canonical("death")
        assert not is_canonical("trach")
        assert not is_canonical(None)


class TestPrioridad:
    def test_muerte_ventilado_manda(self):
        assert end_reason_from_causes(
            ["trach", "terminal_extubation", "death_at_vent"],
            is_extubation=False) == DEATH_AT_VENT

    def test_terminal_antes_que_trach(self):
        assert end_reason_from_causes(
            ["trach", "terminal_extubation"],
            is_extubation=False) == TERMINAL_EXTUBATION

    def test_tracheostomia_sin_muerte(self):
        assert end_reason_from_causes(
            ["trach"], is_extubation=False) == TRACHEOSTOMY
        assert end_reason_from_causes(
            ["trach_time_unknown"], is_extubation=False) == TRACHEOSTOMY

    def test_extubacion_confirmada(self):
        assert end_reason_from_causes(
            [], is_extubation=True) == EXTUBATION_OBSERVED

    def test_fallback_de_la_regla_0(self):
        assert end_reason_from_causes(
            [], is_extubation=False,
            fallback="transfer_ventilated") == TRANSFER_VENTILATED
        assert end_reason_from_causes(
            [], is_extubation=False, fallback=None) == END_OF_RECORD

    def test_salida_siempre_canonica(self):
        for causes in ([], ["trach"], ["death_at_vent"], ["terminal_extubation"],
                       ["transfer_ventilated"], ["end_of_record"]):
            for is_ext in (True, False):
                assert is_canonical(end_reason_from_causes(
                    causes, is_extubation=is_ext, fallback="end_of_record"))
