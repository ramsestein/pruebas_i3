"""
tests/test_mimic_cases.py
=========================
Tests del builder de MIMIC (Fase 1.3) y del catálogo de itemids, con datos
sintéticos. Los itemids se validan además contra ``D_ITEMS`` real si está
disponible (si no, el test se salta).
"""

from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.common.episodes import Span
from src.create_dataset.build_mimic_cases import (
    StayInputs,
    build_stay_events,
    observations_from_chunk,
    vent_spans_for_stay,
)
from src.create_dataset.mimic_itemids import (
    MIMIC_CHART_ITEMIDS,
    MONITOR_CHART_KEYS,
    VENT_MARKER_KEYS,
    all_monitor_itemids,
    all_vent_marker_itemids,
    itemid_to_concept,
    iter_all_itemids,
    label_of,
)

H = 3600.0
BASE = 1_700_000_000.0  # epoch arbitrario


# ── Catálogo de itemids ──────────────────────────────────────────────────────

class TestItemids:
    def test_labels_not_empty_and_unique(self):
        seen: dict[int, str] = {}
        for iid, label in iter_all_itemids():
            assert label and label.strip(), f"itemid {iid} sin etiqueta"
            if iid in seen:
                assert seen[iid] == label, f"itemid {iid} con etiquetas distintas"
            seen[iid] = label

    def test_vent_and_monitor_disjoint(self):
        assert not (all_vent_marker_itemids() & all_monitor_itemids())

    def test_fio2_is_not_a_ventilation_marker(self):
        """Corrección 2: la FiO2 no marca ventilación (se anota con O2 terapia)."""
        for iid in (3420, 223835):
            assert iid not in all_vent_marker_itemids()
        assert itemid_to_concept()[3420] == "FiO2"

    def test_vent_mode_is_a_marker(self):
        for iid in (720, 223849):
            assert iid in all_vent_marker_itemids()

    def test_itemid_to_concept_maps_all(self):
        mapping = itemid_to_concept()
        for iid, _ in iter_all_itemids():
            assert iid in mapping

    def test_plateau_pressure_is_not_pip(self):
        """Control: 224696 es 'Plateau Pressure' y NO debe mapear a PIP."""
        assert 224696 not in all_vent_marker_itemids()
        assert label_of(224695) == "Peak Insp. Pressure"


# ── Agregación de CHARTEVENTS ────────────────────────────────────────────────

class TestObservations:
    def _df(self):
        return pd.DataFrame({
            "SUBJECT_ID": [1, 1, 1],
            "HADM_ID": [10, 10, 10],
            "ICUSTAY_ID": [100, 100, 100],
            "ITEMID": [224690, 224690, 224700],   # RR_V, RR_V, PEEP
            "CHARTTIME": ["2150-01-01 00:00:00", "2150-01-01 06:00:00",
                          "2150-01-01 03:00:00"],
            "VALUENUM": [12.0, 14.0, 5.0],
        })

    def test_keeps_each_observation_with_its_time(self):
        """Corrección 2: NO se resume a primer/último registro."""
        obs = observations_from_chunk(self._df())
        assert len(obs) == 3
        assert obs["t_unix"].is_monotonic_increasing is False  # orden original
        rr = obs[obs["CONCEPT"] == "RR_V"]
        assert len(rr) == 2
        assert rr["t_unix"].nunique() == 2

    def test_negative_control_unknown_itemid_dropped(self):
        df = self._df()
        df.loc[0, "ITEMID"] = 999999  # no catalogado
        obs = observations_from_chunk(df)
        assert 999999 not in obs["ITEMID"].values
        assert (obs["CONCEPT"] == "RR_V").sum() == 1


# ── Tramos de ventilación con huecos reales (D1) ─────────────────────────────

def _obs(rows) -> pd.DataFrame:
    """rows: lista de (stay, concept, itemid, horas desde BASE)."""
    return pd.DataFrame([
        {"SUBJECT_ID": 1, "HADM_ID": 10, "ICUSTAY_ID": stay,
         "ITEMID": iid, "CONCEPT": concept, "t_unix": BASE + h * H, "VALUENUM": 1.0}
        for stay, concept, iid, h in rows
    ])


class TestVentSpansFromObservations:
    def _empty_proc(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["icustay_id", "start_unix", "end_unix"])

    def test_two_spans_6h_apart_two_attempts_failure_at_48h(self):
        """Dos tramos separados 6 h → 2 intentos con fallo a 48 h."""
        obs = _obs([
            (100, "RR_V", 224690, 0.0), (100, "RR_V", 224690, 1.0),
            (100, "PEEP", 224700, 0.5),
            (100, "RR_V", 224690, 7.0), (100, "RR_V", 224690, 8.0),
        ] + [(100, "HR", 220045, h) for h in range(0, 24)])
        spans = vent_spans_for_stay(obs, self._empty_proc(), 100)
        assert len(spans) == 2
        events = build_stay_events(_stay(spans, [], []))
        assert events[0]["n_attempts"] == 2
        lab = events[0]["labels"]["48h"]
        assert lab["n_failed_attempts"] == 1

    def test_fio2_after_extubation_does_not_prolong_ventilation(self):
        """La FiO2 anotada tras la extubación NO prolonga la ventilación."""
        obs = _obs([
            (100, "VentMode", 223849, 0.0), (100, "VentMode", 223849, 1.0),
            (100, "FiO2", 223835, 3.0),     # oxigenoterapia, no VM
        ])
        spans = vent_spans_for_stay(obs, self._empty_proc(), 100)
        assert len(spans) == 1
        assert spans[0].end_h - BASE / H == pytest.approx(1.0, abs=1e-6)

    def test_negative_control_gap_1h_is_one_span(self):
        obs = _obs([(100, "PEEP", 224700, 0.0), (100, "PEEP", 224700, 1.0),
                    (100, "PEEP", 224700, 2.0)])
        assert len(vent_spans_for_stay(obs, self._empty_proc(), 100)) == 1


# ── Segmentación de una estancia ─────────────────────────────────────────────

def _stay(vent, hr, spo2) -> StayInputs:
    """Los tramos van en HORAS desde epoch (api de build_stay_events)."""
    return StayInputs(
        stay_id=100, subject_id=1, hadm_id=10,
        intime_unix=BASE, outtime_unix=BASE + 48 * H,
        vent_spans=vent, hr_spans=hr, spo2_spans=spo2,
    )


def _h(t0_h: float, t1_h: float) -> Span:
    return Span(BASE / H + t0_h, BASE / H + t1_h)


class TestStaySegmentation:
    def test_disconnect_90min_one_attempt(self):
        vent = [_h(0, 1), _h(2.5, 3.5)]
        hr = [_h(0, 3.5)]
        events = build_stay_events(_stay(vent, hr, []))
        assert len(events) == 1
        assert events[0]["n_attempts"] == 1
        assert events[0]["cohort"] == "mimic"
        assert events[0]["icustay_id"] == 100

    def test_negative_control_gap_3h_two_attempts(self):
        vent = [_h(0, 1), _h(4, 5)]
        hr = [_h(0, 5)]
        events = build_stay_events(_stay(vent, hr, []))
        assert len(events) == 1
        assert events[0]["n_attempts"] == 2

    def test_without_monitor_excluded(self):
        events = build_stay_events(_stay([_h(0, 2)], [], []))
        assert len(events) == 1
        assert events[0]["excluded"] is True
        assert events[0]["exclusion_reason"] == "ventilator_without_patient"

    def test_negative_control_with_monitor_not_excluded(self):
        events = build_stay_events(_stay([_h(0, 2)], [_h(0, 2)], []))
        assert events[0]["excluded"] is False

    def test_attempts_clipped_to_icu_stay(self):
        """Ningún evento/intento cruza el fin de la estancia (D2)."""
        vent = [_h(0, 60)]  # excede outtime (48 h)
        hr = [_h(0, 60)]
        events = build_stay_events(_stay(vent, hr, []))
        assert len(events) == 1
        assert events[0]["duration_seconds"] <= 48 * H + 1


# ── Verificación contra D_ITEMS real (si existe) ─────────────────────────────

D_ITEMS_CANDIDATES = [
    Path("datasets/mimic3wdb/clinical/D_ITEMS.csv.gz"),
    Path("D:/data/mimiciii/D_ITEMS.csv.gz"),
]


@pytest.mark.parametrize("d_items", D_ITEMS_CANDIDATES)
def test_itemids_match_real_d_items(d_items: Path):
    if not d_items.exists():
        pytest.skip(f"D_ITEMS no disponible: {d_items}")
    with gzip.open(d_items, "rt") as fh:
        table = pd.read_csv(d_items, compression="gzip")
    table = table.set_index("ITEMID")
    for iid, label in iter_all_itemids():
        assert iid in table.index, f"itemid {iid} ausente en D_ITEMS"
        real = str(table.loc[iid, "LABEL"]).strip()
        assert real == label, (
            f"itemid {iid}: D_ITEMS='{real}' != catálogo='{label}'"
        )
