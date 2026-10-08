"""
tests/test_build_eicu_index.py
==============================
Tests del índice y la clasificación de eICU (Fase 1.5, punto 1) con datos
sintéticos. Cubren el esquema (``hospital_id``), los niveles A/B/C/D y el
resumen agregado.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.create_dataset.build_eicu_index import build_eicu_index


def _patients(rows) -> pd.DataFrame:
    return pd.DataFrame([
        {"patientunitstayid": pid, "hospitalid": hosp,
         "unitdischargeoffset": disch, "unitdischargestatus": status}
        for pid, hosp, disch, status in rows
    ])


def _respcare(rows) -> pd.DataFrame:
    return pd.DataFrame([
        {"patientunitstayid": pid, "ventstartoffset": vs, "ventendoffset": ve,
         "respcarestatusoffset": st, "airwaytype": aw}
        for pid, vs, ve, st, aw in rows
    ])


class TestSchema:
    def test_hospital_id_and_rule0_fields_present(self):
        patients = _patients([(1, 10, 2880, "Alive")])
        respcare = _respcare([(1, 0, 1200, 1200, "Oral ETT")])
        idx, summary = build_eicu_index(
            patients, respcare, {1: [1200 - 60]}, {}, {}, with_coverage=False,
        )
        assert idx["total_events"] == 1
        ev = idx["events"][0]
        assert ev["cohort"] == "eicu"
        assert ev["hospital_id"] == 10
        assert "labels" in ev and "48h" in ev["labels"]
        assert ev["level"] in ("A", "B", "C", "D")
        assert ev["level"] in summary["levels"]


class TestLevels:
    def test_documented_coherent_is_A(self):
        patients = _patients([(1, 10, 2880, "Alive")])
        # fin documentado a 1200 min; ajuste invasivo a 1140 min (1 h antes).
        respcare = _respcare([(1, 0, 1200, 1200, "Oral ETT")])
        idx, _ = build_eicu_index(
            patients, respcare, {1: [1140]}, {}, {}, with_coverage=False,
        )
        assert idx["events"][0]["level"] == "A"

    def test_documented_without_adjustments_is_B(self):
        patients = _patients([(1, 10, 2880, "Alive")])
        respcare = _respcare([(1, 0, 1200, 1200, "Oral ETT")])
        idx, _ = build_eicu_index(patients, respcare, {}, {}, {}, with_coverage=False)
        assert idx["events"][0]["level"] == "B"

    def test_imputed_end_recoverable_is_C(self):
        patients = _patients([(1, 10, 2880, "Alive")])
        # ventendoffset=0 -> imputado al último respcarestatusoffset (2880).
        respcare = _respcare([(1, 0, 0, 2880, "Oral ETT")])
        idx, _ = build_eicu_index(
            patients, respcare, {1: [1200]}, {}, {}, with_coverage=False,
        )
        ev = idx["events"][0]
        assert ev["level"] == "C"
        assert ev["proposed_extubation_h"] == pytest.approx(20.0)
        assert ev["proposed_shift_h"] == pytest.approx(28.0)

    def test_imputed_without_adjustments_is_D(self):
        patients = _patients([(1, 10, 2880, "Alive")])
        respcare = _respcare([(1, 0, 0, 2880, "Oral ETT")])
        idx, _ = build_eicu_index(patients, respcare, {}, {}, {}, with_coverage=False)
        assert idx["events"][0]["level"] == "D"

    def test_preexisting_trach_is_excluded(self):
        patients = _patients([(1, 10, 2880, "Alive")])
        respcare = _respcare([
            (1, 0, 1200, 1200, "Oral ETT"),
            (1, 0, 0, -300, "Tracheostomy"),
        ])
        idx, _ = build_eicu_index(patients, respcare, {}, {}, {}, with_coverage=False)
        assert idx["total_events"] == 0
        assert idx["excluded_events"][0]["exclusion_reason"] == "trach_preexisting"


class TestSummary:
    def test_levels_by_hospital_and_vars_ok(self):
        patients = _patients([(1, 10, 2880, "Alive"), (2, 11, 2880, "Alive")])
        respcare = _respcare([
            (1, 0, 1200, 1200, "Oral ETT"),
            (2, 0, 0, 2880, "Oral ETT"),
        ])
        idx, summary = build_eicu_index(
            patients, respcare, {1: [1140]}, {}, {}, with_coverage=False,
        )
        assert summary["levels"]["A"] == 1
        assert summary["levels"]["D"] == 1
        assert summary["levels_by_hospital"][10]["A"] == 1
        assert summary["vars_ok_50"]["A"] == 0  # sin cobertura calculada
