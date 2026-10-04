"""
tests/test_missing_files.py
===========================
Tests del tratamiento de ficheros ilegibles de VitalDB/Clínic como "sin dato"
(Fase 1.5, punto 2): no crean huecos de ventilador (D1) ni cortes de paciente
(D2), y los eventos que los atraviesan se marcan con ``has_missing_files``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import vitaldb

from src.common.episodes import Attempt, Episode, Span
from src.common.timeutils import to_epoch_utc
from src.common.vital_signals import probe_vital_file
from src.create_dataset.build_signal_cases import (
    CLINIC_SPEC,
    build_cohort_index,
    classify_vitaldb_event,
    scan_source_files,
    segment_box,
)

BASE = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _fname(token: str, dt: datetime) -> str:
    return f"{token}_{dt.strftime('%y%m%d_%H%M%S')}.vital"


def _recs(dt_unix: float, duration_s: float, step_s: float = 600.0,
          value: float = 1.0):
    t = np.arange(0.0, duration_s + 1e-9, step_s)
    return [{"dt": dt_unix + float(x), "val": value} for x in t]


def write_hour(path: Path, dt: datetime, *, monitor: bool = True,
               vent: bool = False, duration_s: float = 3599.0) -> Path:
    dt_unix = to_epoch_utc(dt)
    vf = vitaldb.VitalFile()
    vf.dtstart = dt_unix
    vf.dtend = dt_unix + duration_s
    if monitor:
        vf.add_track("Intellivue/ECG_HR", _recs(dt_unix, duration_s, value=80.0))
        vf.add_track("Intellivue/PLETH_SAT_O2", _recs(dt_unix, duration_s, value=98.0))
    if vent:
        vf.add_track("Intellivue/TV_EXP", _recs(dt_unix, duration_s, value=500.0))
        vf.add_track("Intellivue/VENT_RR", _recs(dt_unix, duration_s, value=14.0))
    path.parent.mkdir(parents=True, exist_ok=True)
    vf.to_vital(str(path))
    return path


def make_box(raw_dir: Path, box: str, hours, vent_hours) -> None:
    for h in hours:
        dt = BASE + timedelta(hours=h)
        write_hour(raw_dir / box / _fname("tok1", dt), dt,
                   monitor=True, vent=(h in vent_hours))


def _probe_factory(unreadable_hours: set[int]):
    """Devuelve un ``probe_fn`` que falla en las horas indicadas."""
    def _probe(path: Path):
        for h in unreadable_hours:
            stamp = (BASE + timedelta(hours=h)).strftime("%y%m%d_%H%M%S")
            if stamp in path.name:
                return None
        return probe_vital_file(path)
    return _probe


class TestUnreadableFilesDoNotCreateGaps:
    def test_two_unreadable_files_mid_vent_one_attempt(self, tmp_path: Path):
        """Dos ficheros ilegibles en mitad de una ventilación → 1 intento."""
        make_box(tmp_path, "box2", list(range(0, 5)), [0, 1, 4])
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"], probe_fn=_probe_factory({2, 3}))
        assert len(seg.episodes) == 1
        assert seg.episodes[0].n_attempts == 1
        assert len(seg.missing_spans) >= 1

    def test_negative_control_without_missing_splits_in_two(self, tmp_path: Path):
        """Control negativo: si las horas 2-3 NO son ilegibles, son 2 intentos."""
        make_box(tmp_path, "box2", list(range(0, 5)), [0, 1, 4])
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"])
        assert seg.episodes[0].n_attempts == 2


class TestClassifyVitaldbEvent:
    def _episode(self) -> Episode:
        return Episode(
            attempts=[Attempt(0, start_h=0.0, end_h=10.0)],
            region_start_h=0.0, region_end_h=20.0,
        )

    def test_no_missing_is_A(self):
        level, hours = classify_vitaldb_event(self._episode(), [])
        assert level == "A"
        assert hours == []

    def test_missing_in_ventilation_is_D(self):
        level, hours = classify_vitaldb_event(self._episode(), [Span(5.0, 6.0)])
        assert level == "D"
        assert hours == [5.0]

    def test_missing_in_hour_after_disconnect_is_D(self):
        level, _ = classify_vitaldb_event(self._episode(), [Span(10.5, 11.5)])
        assert level == "D"

    def test_missing_after_label_window_is_B(self):
        level, _ = classify_vitaldb_event(self._episode(), [Span(15.0, 16.0)])
        assert level == "B"

    def test_missing_outside_event_is_A(self):
        level, _ = classify_vitaldb_event(self._episode(), [Span(25.0, 26.0)])
        assert level == "A"


class TestEventRecordFields:
    def test_event_has_missing_fields_and_level(self, tmp_path: Path):
        make_box(tmp_path, "box2", list(range(0, 5)), [0, 1, 4])
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"], probe_fn=_probe_factory({2, 3}))
        idx = build_cohort_index(
            [("box2", seg)], cohort="vitaldb", spec=CLINIC_SPEC, merged=False
        )
        ev = idx["events"][0]
        assert ev["has_missing_files"] is True
        assert ev["level"] == "D"  # horas perdidas dentro de la ventilación
        assert idx["n_missing_files"] == 2
