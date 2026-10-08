"""
tests/test_eicu_rules.py
========================
Tests de las reglas eICU compartidas (Fase 1.4). Cada comprobación con control
negativo.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.common.eicu_rules import (
    DOSE_UNKNOWN_SENTINEL,
    MAP_INVASIVE_TRACK,
    MAP_NONINVASIVE_TRACK,
    add_vasopressor_series,
    drug_kind,
    eicu_t0_minutes,
    merge_vent_episodes,
    pick_map_source,
    sanitize_vent_episodes,
)


def _vent(rows, pid=1) -> pd.DataFrame:
    return pd.DataFrame(
        [{"patientunitstayid": pid, "ventstartoffset": s, "ventendoffset": e}
         for s, e in rows]
    )


# ── t0 y fusión de episodios ─────────────────────────────────────────────────

class TestT0:
    def test_t0_is_first_vent_start(self):
        df = _vent([(600, 1200), (1300, 2000)])
        assert eicu_t0_minutes(df) == 600

    def test_t0_merges_disconnections(self):
        # hueco 90 min <= 2 h -> un único episodio -> t0=600
        assert eicu_t0_minutes(_vent([(600, 1200), (1290, 2000)])) == 600

    def test_negative_control_empty(self):
        assert eicu_t0_minutes(pd.DataFrame(columns=["patientunitstayid"])) is None

    def test_merge_splits_large_gap(self):
        out = merge_vent_episodes(_vent([(0, 100), (400, 500)]))
        assert len(out) == 2

    def test_merge_joins_small_gap(self):
        out = merge_vent_episodes(_vent([(0, 100), (200, 300)]))  # gap 100 min
        assert len(out) == 1
        assert out.iloc[0]["ventendoffset"] == 300


# ── Anomalías de duración/hueco ──────────────────────────────────────────────

class TestSanitize:
    def test_valid_episode_untouched(self):
        res = sanitize_vent_episodes(_vent([(100, 500)]), unit_discharge_offset_min=1000)
        assert res.anomalies == []
        assert len(res.episodes) == 1
        assert res.episodes.iloc[0]["ventendoffset"] == 500

    def test_end_after_discharge_clipped_and_reported(self):
        res = sanitize_vent_episodes(_vent([(100, 5000)]), unit_discharge_offset_min=1000)
        assert res.episodes.iloc[0]["ventendoffset"] == 1000
        assert any(a.kind == "vent_end_after_discharge" for a in res.anomalies)

    def test_impossible_duration_dropped(self):
        # 25 713 h -> > 60 días -> descartado
        res = sanitize_vent_episodes(_vent([(0, 25713 * 60)]), unit_discharge_offset_min=10**9)
        assert res.episodes.empty
        assert any(a.kind == "vent_duration_impossible" for a in res.anomalies)

    def test_negative_control_zero_duration_dropped(self):
        res = sanitize_vent_episodes(_vent([(500, 500)]), unit_discharge_offset_min=1000)
        assert res.episodes.empty
        assert any(a.kind == "vent_zero_or_negative_duration" for a in res.anomalies)


# ── Regex de fármacos ────────────────────────────────────────────────────────

class TestDrugKind:
    def test_norepinephrine_is_not_epinephrine(self):
        assert drug_kind("Norepinephrine") == "norepinephrine"
        assert drug_kind("norepinephrine bitartrate") == "norepinephrine"

    def test_epinephrine_detected(self):
        assert drug_kind("Epinephrine") == "epinephrine"

    def test_dopamine_detected(self):
        assert drug_kind("Dopamine HCl") == "dopamine"

    def test_negative_control_other_drug(self):
        assert drug_kind("Propofol") is None
        assert drug_kind(None) is None


# ── Prioridad infusión > medicación ─────────────────────────────────────────

class TestVasopressorPriority:
    def _store(self):
        store: dict = {}
        inf = pd.DataFrame([{"drugname": "Norepinephrine", "infusionoffset": 100,
                             "drugrate": 0.05}])
        add_vasopressor_series(store, inf, offset_col="infusionoffset",
                               value_col="drugrate", t0_minutes=0, overwrite=True)
        return store

    def test_medication_does_not_overwrite_infusion(self):
        store = self._store()
        med = pd.DataFrame([{"drugname": "Norepinephrine", "drugstartoffset": 200,
                             "dosage": 2.0}])
        add_vasopressor_series(store, med, offset_col="drugstartoffset",
                               value_col="dosage", t0_minutes=0, overwrite=False)
        times, vals = store["eICU/Norepinephrine"]
        assert len(times) == 1  # el bolo se ha ignorado

    def test_negative_control_overwrite_allows_append(self):
        store = self._store()
        med = pd.DataFrame([{"drugname": "Norepinephrine", "drugstartoffset": 200,
                             "dosage": 2.0}])
        add_vasopressor_series(store, med, offset_col="drugstartoffset",
                               value_col="dosage", t0_minutes=0, overwrite=True)
        assert len(store["eICU/Norepinephrine"][0]) == 2

    def test_unknown_dose_uses_sentinel(self):
        store: dict = {}
        df = pd.DataFrame([{"drugname": "Dopamine", "infusionoffset": 0, "drugrate": None}])
        add_vasopressor_series(store, df, offset_col="infusionoffset",
                               value_col="drugrate", t0_minutes=0, overwrite=True)
        assert store["eICU/Dopamine"][1] == [DOSE_UNKNOWN_SENTINEL]


# ── MAP: invasiva -> no invasiva (D7) ───────────────────────────────────────

class TestMap:
    def test_prefers_invasive(self):
        assert pick_map_source([MAP_INVASIVE_TRACK, MAP_NONINVASIVE_TRACK]) == MAP_INVASIVE_TRACK

    def test_falls_back_to_noninvasive(self):
        assert pick_map_source([MAP_NONINVASIVE_TRACK]) == MAP_NONINVASIVE_TRACK

    def test_negative_control_none(self):
        assert pick_map_source(["eICU/HR"]) is None
