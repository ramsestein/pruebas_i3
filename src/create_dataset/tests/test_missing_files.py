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


class TestHuecoDelIlegibleUsaLosVecinosReales:
    """B2 residual: el tramo de un fichero ilegible NO es "nombre + 1 h".

    En Clínic la hora del nombre va 1-2 h por detrás del contenido y las
    grabaciones duran 19-48 h, así que una ventana de 1 h puede quedarse corta
    y cortar el episodio en dos. El tramo debe ir del fin del fichero legible
    anterior al inicio del siguiente, con el tope de la duración real máxima
    observada en el box.
    """

    def _box(self, tmp_path: Path, *, c_start_h: float = 10.0) -> None:
        # A: contenido 00:00-06:00 (6 h) con ventilación.
        write_hour(tmp_path / "box2" / _fname("tok1", BASE), BASE,
                   monitor=True, vent=True, duration_s=6 * 3600.0)
        # B: fichero ILEGIBLE (se simula con probe_fn) cuyo NOMBRE (01:00) va
        # por detrás del contenido, como en Clínic.
        write_hour(tmp_path / "box2" / _fname("tok1", BASE + timedelta(hours=1)),
                   BASE + timedelta(hours=1), duration_s=3600.0)
        # C: contenido desde c_start_h, 6 h, con ventilación.
        dt_c = BASE + timedelta(hours=c_start_h)
        write_hour(tmp_path / "box2" / _fname("tok1", dt_c), dt_c,
                   monitor=True, vent=True, duration_s=6 * 3600.0)

    def test_hueco_de_4h_queda_cubierto_y_no_corta(self, tmp_path: Path):
        """Hueco real de 4 h con un fichero ilegible dentro: se cubre."""
        self._box(tmp_path, c_start_h=10.0)
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"], probe_fn=_probe_factory({1}))
        base = to_epoch_utc(BASE) / 3600.0
        spans = [(s.start_h - base, s.end_h - base) for s in seg.missing_spans]
        assert any(s <= 6.0 + 1e-6 and e >= 10.0 - 1e-6 for s, e in spans), spans
        assert len(seg.episodes) == 1
        assert seg.episodes[0].n_attempts == 1

    def test_control_negativo_sin_ilegible_son_dos_eventos(self, tmp_path: Path):
        self._box(tmp_path, c_start_h=10.0)
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"])
        assert len(seg.episodes) == 2

    def test_sin_fichero_ilegible_no_se_puentia_el_hueco_largo(
        self, tmp_path: Path
    ):
        """Sin ficheros en el hueco de 24 h, no hay nada que puentear (D-f)."""
        self._box(tmp_path, c_start_h=30.0)
        # El fichero de las 01:00 se BORRA: no hay constancia de grabación.
        (tmp_path / "box2" / _fname("tok1", BASE + timedelta(hours=1))).unlink()
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"])
        assert seg.missing_spans == []
        assert len(seg.episodes) == 2


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
