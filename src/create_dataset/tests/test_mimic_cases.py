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
    build_mimic_index,
    build_stay_events,
    observations_from_chunk,
    qc_intervals_vs_adjustments,
    vent_intervals_for_stay,
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
            "VALUE": ["12", "14", "5"],
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


class TestVentIntervals:
    """D1 sobre intervalos explícitos 225792 (corrección 1)."""

    def _proc(self, rows) -> pd.DataFrame:
        """rows: (stay, start_h, end_h)."""
        return pd.DataFrame([
            {"icustay_id": s, "start_unix": BASE + a * H, "end_unix": BASE + b * H}
            for s, a, b in rows
        ])

    def _extub(self, rows) -> pd.DataFrame:
        """rows: (stay, time_h)."""
        return pd.DataFrame([
            {"icustay_id": s, "start_unix": BASE + t * H} for s, t in rows
        ])

    def test_adjustments_inside_single_interval_one_attempt_no_failures(self):
        """PEEP/volumen cada 4 h dentro de un único intervalo → 1 intento, 0 fallos."""
        proc = self._proc([(100, 0.0, 24.0)])
        intervals = vent_intervals_for_stay(proc, self._extub([]), 100)
        assert len(intervals) == 1
        # Ajustes anotados cada 4 h: NO crean ni parten tramos.
        obs = _obs([(100, "PEEP", 224700, h) for h in range(0, 25, 4)]
                   + [(100, "TV_set", 224684, h) for h in range(0, 25, 4)]
                   + [(100, "HR", 220045, h) for h in range(0, 48)])
        assert len(vent_intervals_for_stay(proc, self._extub([]), 100)) == 1
        events = build_stay_events(_stay(intervals, [_h(0, 48)], []))
        assert events[0]["n_attempts"] == 1
        assert events[0]["labels"]["48h"]["n_failed_attempts"] == 0
        assert events[0]["labels"]["48h"]["event_type"] == "successful_extubation"

    def test_two_intervals_6h_apart_two_attempts_failure_at_48h(self):
        proc = self._proc([(100, 0.0, 1.0), (100, 7.0, 8.0)])
        intervals = vent_intervals_for_stay(proc, self._extub([]), 100)
        assert len(intervals) == 2
        events = build_stay_events(_stay(intervals, [_h(0, 48)], []))
        assert events[0]["n_attempts"] == 2
        assert events[0]["labels"]["48h"]["n_failed_attempts"] == 1

    def test_two_intervals_90min_apart_one_attempt(self):
        proc = self._proc([(100, 0.0, 1.0), (100, 2.5, 3.5)])  # hueco 1,5 h
        intervals = vent_intervals_for_stay(proc, self._extub([]), 100)
        assert len(intervals) == 1

    def test_negative_control_gap_over_2h_splits(self):
        proc = self._proc([(100, 0.0, 1.0), (100, 4.0, 5.0)])  # hueco 3 h
        assert len(vent_intervals_for_stay(proc, self._extub([]), 100)) == 2

    def test_extubation_event_confirms_interval_end(self):
        proc = self._proc([(100, 0.0, 10.0)])
        intervals = vent_intervals_for_stay(proc, self._extub([(100, 8.0)]), 100)
        assert intervals[0].end_h - BASE / H == pytest.approx(8.0, abs=1e-6)

    def test_invalid_interval_is_discarded(self):
        """Dato real: PROCEDUREEVENTS_MV con fin <= inicio no debe romper el build."""
        proc = pd.DataFrame({
            "icustay_id": [100, 100],
            "start_unix": [BASE + 10 * H, BASE + 20 * H],
            "end_unix": [BASE + 9 * H, BASE + 22 * H],   # el 1º es inválido
        })
        assert len(vent_intervals_for_stay(proc, self._extub([]), 100)) == 1

    def test_qc_counts_intervals_without_adjustments_and_outside(self):
        intervals = vent_intervals_for_stay(
            self._proc([(100, 0.0, 2.0), (100, 10.0, 12.0)]), self._extub([]), 100
        )
        obs = _obs([(100, "PEEP", 224700, 1.0), (100, "PEEP", 224700, 5.0)])
        no_adj, outside = qc_intervals_vs_adjustments(intervals, obs)
        assert no_adj == 1        # el 2º intervalo no tiene ningún ajuste
        assert outside == 1       # el ajuste de 5 h cae fuera de todo intervalo


class TestMetavisionCohort:
    def test_carevue_stays_are_excluded(self):
        icustays = pd.DataFrame({
            "icustay_id": [100, 200],
            "subject_id": [1, 2], "hadm_id": [10, 20],
            "intime_unix": [BASE, BASE], "outtime_unix": [BASE + 48 * H, BASE + 48 * H],
        })
        proc = pd.DataFrame({"icustay_id": [100], "start_unix": [BASE],
                             "end_unix": [BASE + 2 * H]})
        obs = _obs([(100, "PEEP", 224700, 1.0), (200, "PEEP", 224700, 1.0)])
        idx = build_mimic_index(icustays, obs, proc, metavision_stays={100})
        assert idx["total_stays_with_vent"] == 1
        assert idx["n_carevue_stays_excluded"] == 1
        assert all(e["icustay_id"] == 100 for e in idx["events"])


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


# ── D5 en MIMIC (corrección 3) ───────────────────────────────────────────────

class TestMimicD5:
    def _stay(self, vent, hr, **kw) -> StayInputs:
        base = _stay(vent, hr, [])
        for k, v in kw.items():
            setattr(base, k, v)
        return base

    def test_trach_before_extubation_censors(self):
        vent = [_h(0, 20)]
        hr = [_h(0, 48)]
        stay = self._stay(vent, hr, trach_unix=[BASE + 10 * H])
        ev = build_stay_events(stay)[0]
        assert ev["end_reason"] == "tracheostomy"
        assert ev["labels"]["48h"]["event_type"] == "censored_trach"
        assert ev["labels"]["48h"]["censor_time_h"] == pytest.approx(10.0)

    def test_death_within_window_censors_at_disconnect(self):
        vent = [_h(0, 20)]
        hr = [_h(0, 48)]
        stay = self._stay(vent, hr, death_unix=BASE + 30 * H)
        ev = build_stay_events(stay)[0]
        assert ev["labels"]["48h"]["event_type"] == "censored_terminal_extubation"
        assert ev["labels"]["48h"]["censor_time_h"] == pytest.approx(20.0)

    def test_negative_control_death_after_window_is_success(self):
        vent = [_h(0, 20)]
        hr = [_h(0, 48)]
        stay = self._stay(vent, hr, death_unix=BASE + 100 * H)
        ev = build_stay_events(stay)[0]
        assert ev["labels"]["48h"]["event_type"] == "successful_extubation"

    def test_trach_icd9_without_time_censors_at_last_vent(self):
        vent = [_h(0, 20)]
        hr = [_h(0, 48)]
        stay = self._stay(vent, hr, trach_icd9_no_time=True)
        ev = build_stay_events(stay)[0]
        assert ev["labels"]["48h"]["event_type"] == "censored_trach_time_unknown"
        assert ev["trach"]["icd9_marked_without_time"] is True

    def test_preexisting_trach_excludes_the_event(self):
        """Traqueostomía ANTES de t0 → exclusión de inicio, no censura."""
        vent = [_h(0, 20)]
        hr = [_h(0, 48)]
        stay = self._stay(vent, hr, trach_unix=[BASE - 5 * H])
        ev = build_stay_events(stay)[0]
        assert ev["excluded"] is True
        assert ev["exclusion_reason"] == "trach_preexisting"
        assert ev["end_reason"] == "excluded_trach_preexisting"


class TestCommonExtubationRule:
    """Fase 1.5, punto 0: extubación confirmada en MIMIC (fin de VM vs OUTTIME)."""

    def _stay(self, vent, hr, **kw) -> StayInputs:
        base = _stay(vent, hr, [])
        for k, v in kw.items():
            setattr(base, k, v)
        return base

    def test_vent_end_20min_before_discharge_is_transfer_ventilated(self):
        """Caso exigido: fin de VM 20 min antes del alta → transfer_ventilated."""
        vent = [_h(0, 47.0 + 40.0 / 60.0)]
        hr = [_h(0, 48)]
        ev = build_stay_events(self._stay(vent, hr))[0]
        assert ev["end_reason"] == "transfer_ventilated"
        assert ev["labels"]["48h"]["event_type"] == "censored_transfer_ventilated"
        assert ev["labels"]["48h"]["censor_time_h"] == pytest.approx(
            47.0 + 40.0 / 60.0
        )

    def test_negative_control_vent_end_2h_before_discharge_is_success(self):
        vent = [_h(0, 46.0)]
        hr = [_h(0, 48)]
        ev = build_stay_events(self._stay(vent, hr))[0]
        assert ev["end_reason"] == "extubation_observed"
        assert ev["labels"]["48h"]["event_type"] == "successful_extubation"

    def test_vent_end_at_outtime_is_transfer_ventilated(self):
        """Los eventos que terminaban en ``end_of_icu_stay`` pasan a censura."""
        vent = [_h(0, 48.0)]
        hr = [_h(0, 48)]
        ev = build_stay_events(self._stay(vent, hr))[0]
        assert ev["end_reason"] == "transfer_ventilated"
        assert ev["labels"]["48h"]["event_type"] == "censored_transfer_ventilated"


# ── Robusteza ante ICUSTAY_ID nulo (fallo real del build) ───────────────────

class TestNullableStayIds:
    def test_airway_trach_with_null_icustay_id_does_not_crash(self):
        """ICUSTAY_ID es nullable: las filas sin estancia no deben romper el build."""
        from src.create_dataset.build_mimic_cases import _airway_trach_unix
        obs = pd.DataFrame({
            "CONCEPT": ["AirwayType", "AirwayType"],
            "VALUE_RAW": ["Tracheostomy", "Tracheostomy"],
            "ICUSTAY_ID": pd.array([100, pd.NA], dtype="Int64"),
            "t_unix": [1.0, 2.0],
        })
        assert _airway_trach_unix(obs) == {100: [1.0]}

    def test_negative_control_valid_id_is_kept(self):
        from src.create_dataset.build_mimic_cases import _airway_trach_unix
        obs = pd.DataFrame({
            "CONCEPT": ["AirwayType"],
            "VALUE_RAW": ["Oral ETT"],
            "ICUSTAY_ID": pd.array([7], dtype="Int64"),
            "t_unix": [1.0],
        })
        assert _airway_trach_unix(obs) == {}


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
