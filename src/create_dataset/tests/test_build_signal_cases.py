"""
tests/test_build_signal_cases.py
================================
Tests del builder de casos con señal (Clínic/VitalDB) usando ficheros ``.vital``
sintéticos. Cada comprobación lleva control negativo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import vitaldb

from src.create_dataset.build_signal_cases import (
    CLINIC_SPEC,
    VITALDB_SPEC,
    build_cohort_index,
    run_cohort,
    scan_source_files,
    segment_box,
)
from src.common.timeutils import to_epoch_utc

BASE = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _fname(token: str, dt: datetime) -> str:
    return f"{token}_{dt.strftime('%y%m%d_%H%M%S')}.vital"


def _recs(dt_unix: float, duration_s: float, step_s: float = 600.0):
    t = np.arange(0.0, duration_s + 1e-9, step_s)
    return [{"dt": dt_unix + float(x), "val": 1.0} for x in t]


def write_hour(
    path: Path,
    dt: datetime,
    *,
    monitor: bool = True,
    vent: bool = False,
    duration_s: float = 3599.0,
) -> Path:
    """Escribe un .vital sintético de ~1 h."""
    dt_unix = to_epoch_utc(dt)
    vf = vitaldb.VitalFile()
    vf.dtstart = dt_unix
    vf.dtend = dt_unix + duration_s
    if monitor:
        vf.add_track("Intellivue/ECG_HR", _recs(dt_unix, duration_s))
        vf.add_track("Intellivue/PLETH_SAT_O2", _recs(dt_unix, duration_s))
    if vent:
        vf.add_track("Intellivue/TV_EXP", _recs(dt_unix, duration_s))
        vf.add_track("Intellivue/VENT_RR", _recs(dt_unix, duration_s))
    path.parent.mkdir(parents=True, exist_ok=True)
    vf.to_vital(str(path))
    return path


def make_box(
    raw_dir: Path,
    box: str,
    hours: list[int],
    vent_hours: list[int],
    *,
    token: str = "tok1",
    extra_vent_offsets: dict[int, float] | None = None,
) -> None:
    """Crea ficheros horarios con monitor y vent en las horas indicadas."""
    for h in hours:
        dt = BASE + timedelta(hours=h)
        write_hour(
            raw_dir / box / _fname(token, dt), dt,
            monitor=True, vent=(h in vent_hours),
        )
    for h, offset_min in (extra_vent_offsets or {}).items():
        dt = BASE + timedelta(hours=h, minutes=offset_min)
        write_hour(
            raw_dir / box / _fname(token, dt), dt, monitor=True, vent=True
        )


# ── Escaneo ──────────────────────────────────────────────────────────────────

class TestScan:
    def test_scan_clinic_groups_by_top_dir(self, tmp_path: Path):
        make_box(tmp_path, "box2", [0, 1], [0])
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        assert list(boxes) == ["box2"]
        assert len(boxes["box2"]) == 2
        assert boxes["box2"][0].dt_unix < boxes["box2"][1].dt_unix

    def test_scan_excludes_dataset_clinic(self, tmp_path: Path):
        make_box(tmp_path, "box2", [0], [0])
        write_hour(tmp_path / "dataset_clinic" / _fname("x", BASE), BASE)
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        assert "dataset_clinic" not in boxes

    def test_scan_vitaldb_flat_by_filename(self, tmp_path: Path):
        dt = BASE
        write_hour(tmp_path / _fname("SICU1_01", dt), dt, vent=True)
        boxes = scan_source_files(tmp_path, VITALDB_SPEC)
        assert list(boxes) == ["SICU1_01"]


# ── D1: desconexión ──────────────────────────────────────────────────────────

class TestDisconnection:
    def test_gap_90min_is_one_attempt(self, tmp_path: Path):
        make_box(tmp_path, "box2", [0, 1, 2, 3, 4], [0],
                 extra_vent_offsets={2: 30})
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"])
        assert len(seg.episodes) == 1
        assert seg.episodes[0].n_attempts == 1

    def test_negative_control_gap_3h_two_attempts(self, tmp_path: Path):
        make_box(tmp_path, "box2", [0, 1, 2, 3, 4], [0, 4])
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"])
        assert len(seg.episodes) == 1
        assert seg.episodes[0].n_attempts == 2


# ── D1+D2: 6 h con monitor continuo vs hueco de monitor ──────────────────────

class TestSameEventVsPatientChange:
    def test_6h_gap_continuous_monitor_one_event_two_attempts(self, tmp_path: Path):
        make_box(tmp_path, "box2", list(range(0, 9)), [0, 7])
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"])
        assert len(seg.episodes) == 1
        assert seg.episodes[0].n_attempts == 2

    def test_monitor_gap_2h_two_events(self, tmp_path: Path):
        # Monitor en 0-1 y en 4-5; vent en 0 y 5 -> hueco de monitor 2 h.
        make_box(tmp_path, "box2", [0, 1, 4, 5], [0, 5])
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"])
        assert len(seg.episodes) == 2

    def test_negative_control_continuous_monitor_one_event(self, tmp_path: Path):
        make_box(tmp_path, "box2", [0, 1, 2, 3, 4, 5], [0, 5])
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"])
        assert len(seg.episodes) == 1


# ── D2: tres pacientes en el mismo box ───────────────────────────────────────

class TestThreePatients:
    def test_three_patients_three_events(self, tmp_path: Path):
        make_box(tmp_path, "box2", [0, 1, 48, 49, 100, 101], [0, 48, 100])
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"])
        assert len(seg.episodes) == 3

    def test_negative_control_two_patients_two_events(self, tmp_path: Path):
        make_box(tmp_path, "box2", [0, 1, 48, 49], [0, 48])
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"])
        assert len(seg.episodes) == 2


# ── D4: ventilador sin paciente ──────────────────────────────────────────────

class TestNoPatient:
    def test_vent_without_monitor_excluded(self, tmp_path: Path):
        dt = BASE
        write_hour(tmp_path / "box2" / _fname("t", dt), dt,
                   monitor=False, vent=True)
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"])
        assert seg.episodes == []
        assert len(seg.excluded_episodes) == 1
        assert seg.excluded_episodes[0].exclusion_reason == "ventilator_without_patient"

    def test_negative_control_with_monitor_not_excluded(self, tmp_path: Path):
        dt = BASE
        write_hour(tmp_path / "box2" / _fname("t", dt), dt,
                   monitor=True, vent=True)
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"])
        assert len(seg.episodes) == 1
        assert seg.excluded_episodes == []


# ── Índice y fusión ──────────────────────────────────────────────────────────

class TestIndexAndMerge:
    def _config(self, tmp_path: Path) -> dict:
        return {
            "version": "0.2.0",
            "paths": {
                "clinic_raw_dir": str(tmp_path / "raw"),
                "clinic_cases_out": str(tmp_path / "out"),
            },
        }

    def test_index_has_required_fields(self, tmp_path: Path):
        make_box(tmp_path / "raw", "box2", [0, 1], [0])
        boxes = scan_source_files(tmp_path / "raw", CLINIC_SPEC)
        segs = [(b, segment_box(b, f)) for b, f in boxes.items()]
        idx = build_cohort_index(segs, cohort="clinic", spec=CLINIC_SPEC, merged=False)
        assert idx["total_events"] == 1
        ev = idx["events"][0]
        for key in ("t0_unix", "t0_source", "arrived_ventilated", "attempts",
                    "end_reason", "labels", "source_files", "source_tokens"):
            assert key in ev
        assert ev["arrived_ventilated"] is True
        assert ev["t0_source"] == "already_ventilated_at_record_start"

    def test_labels_d3_are_consistent_with_attempts(self, tmp_path: Path):
        """D3: un fallo a 48 h seguido de éxito debe etiquetarse así."""
        make_box(tmp_path / "raw", "box2", list(range(0, 9)), [0, 7])
        boxes = scan_source_files(tmp_path / "raw", CLINIC_SPEC)
        segs = [(b, segment_box(b, f)) for b, f in boxes.items()]
        idx = build_cohort_index(segs, cohort="clinic", spec=CLINIC_SPEC, merged=False)
        ev = idx["events"][0]
        assert ev["n_attempts"] == 2
        lab = ev["labels"]["48h"]
        assert lab["event_type"] == "successful_extubation"
        assert lab["n_failed_attempts"] == 1
        assert lab["first_attempt_h"] == pytest.approx(
            ev["attempts"][0]["vent_end_h"], abs=1e-6
        )

    def test_run_cohort_merges_and_aborts_on_existing_version(self, tmp_path: Path):
        make_box(tmp_path / "raw", "box2", [0, 1], [0])
        cfg = self._config(tmp_path)
        res = run_cohort(cfg, "clinic", do_merge=True)
        assert res["index"]["total_events"] == 1
        out = Path(res["output_dir"])
        merged = list(out.glob("*.vital"))
        assert len(merged) == 1
        # Volver a construir la misma versión debe abortar (inmutable).
        with pytest.raises(FileExistsError):
            run_cohort(cfg, "clinic", do_merge=True)
