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

from src.create_dataset.build_mimic_cases import (
    StayInputs,
    aggregate_vent_chartevents_df,
    build_stay_events,
)
from src.create_dataset.mimic_itemids import (
    MIMIC_CHART_ITEMIDS,
    MONITOR_CHART_KEYS,
    VENT_CHART_KEYS,
    all_monitor_itemids,
    all_vent_itemids,
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
        assert not (all_vent_itemids() & all_monitor_itemids())

    def test_itemid_to_concept_maps_all(self):
        mapping = itemid_to_concept()
        for iid, _ in iter_all_itemids():
            assert iid in mapping

    def test_plateau_pressure_is_not_pip(self):
        """Control: 224696 es 'Plateau Pressure' y NO debe mapear a PIP."""
        assert 224696 not in all_vent_itemids()
        assert label_of(224695) == "Peak Insp. Pressure"


# ── Agregación de CHARTEVENTS ────────────────────────────────────────────────

class TestAggregate:
    def _df(self):
        return pd.DataFrame({
            "SUBJECT_ID": [1, 1, 1],
            "HADM_ID": [10, 10, 10],
            "ICUSTAY_ID": [100, 100, 100],
            "ITEMID": [224690, 224690, 224700],   # RR_V, RR_V, PEEP
            "CHARTTIME": ["2150-01-01 00:00:00", "2150-01-01 06:00:00",
                          "2150-01-01 03:00:00"],
        })

    def test_aggregates_min_max_count(self):
        agg = aggregate_vent_chartevents_df(self._df())
        rr = agg[agg["CONCEPT"] == "RR_V"].iloc[0]
        assert rr["n"] == 2
        assert rr["t_max"] - rr["t_min"] == 6 * H
        assert set(agg["CONCEPT"]) == {"RR_V", "PEEP"}

    def test_negative_control_unknown_itemid_dropped(self):
        df = self._df()
        df.loc[0, "ITEMID"] = 999999  # no catalogado
        agg = aggregate_vent_chartevents_df(df)
        assert 999999 not in agg["ITEMID"].values
        assert agg[agg["CONCEPT"] == "RR_V"].iloc[0]["n"] == 1


# ── Segmentación de una estancia ─────────────────────────────────────────────

def _stay(vent, hr, spo2) -> StayInputs:
    return StayInputs(
        stay_id=100, subject_id=1, hadm_id=10,
        intime_unix=BASE, outtime_unix=BASE + 48 * H,
        vent_spans=vent, hr_spans=hr, spo2_spans=spo2,
    )


class TestStaySegmentation:
    def test_disconnect_90min_one_attempt(self):
        from src.common.episodes import Span
        vent = [Span(BASE, BASE + 1 * H), Span(BASE + 2.5 * H, BASE + 3.5 * H)]
        hr = [Span(BASE, BASE + 3.5 * H)]
        events = build_stay_events(_stay(vent, hr, []))
        assert len(events) == 1
        assert events[0]["n_attempts"] == 1
        assert events[0]["cohort"] == "mimic"
        assert events[0]["icustay_id"] == 100

    def test_negative_control_gap_3h_two_attempts(self):
        from src.common.episodes import Span
        vent = [Span(BASE, BASE + 1 * H), Span(BASE + 4 * H, BASE + 5 * H)]
        hr = [Span(BASE, BASE + 5 * H)]
        events = build_stay_events(_stay(vent, hr, []))
        assert len(events) == 1
        assert events[0]["n_attempts"] == 2

    def test_without_monitor_excluded(self):
        from src.common.episodes import Span
        events = build_stay_events(_stay([Span(BASE, BASE + 2 * H)], [], []))
        assert len(events) == 1
        assert events[0]["excluded"] is True
        assert events[0]["exclusion_reason"] == "ventilator_without_patient"

    def test_negative_control_with_monitor_not_excluded(self):
        from src.common.episodes import Span
        events = build_stay_events(
            _stay([Span(BASE, BASE + 2 * H)], [Span(BASE, BASE + 2 * H)], [])
        )
        assert events[0]["excluded"] is False

    def test_attempts_clipped_to_icu_stay(self):
        """Ningún evento/intento cruza el fin de la estancia (D2)."""
        from src.common.episodes import Span
        vent = [Span(BASE, BASE + 60 * H)]  # excede outtime (48 h)
        hr = [Span(BASE, BASE + 60 * H)]
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
