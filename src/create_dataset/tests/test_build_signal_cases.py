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


def _recs(dt_unix: float, duration_s: float, step_s: float = 600.0, value: float = 1.0):
    t = np.arange(0.0, duration_s + 1e-9, step_s)
    return [{"dt": dt_unix + float(x), "val": value} for x in t]


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
        vf.add_track("Intellivue/ECG_HR", _recs(dt_unix, duration_s, value=80.0))
        vf.add_track("Intellivue/PLETH_SAT_O2", _recs(dt_unix, duration_s, value=98.0))
    if vent:
        vf.add_track("Intellivue/TV_EXP", _recs(dt_unix, duration_s, value=500.0))
        vf.add_track("Intellivue/VENT_RR", _recs(dt_unix, duration_s, value=14.0))
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


# ── Corrección 1: fin de observación = fin del MONITOR ───────────────────────

class TestEndOfObservation:
    def _write(self, tmp_path: Path, offsets_and_specs):
        for i, (hours, minutes, duration_s, vent) in enumerate(offsets_and_specs):
            dt = BASE + timedelta(hours=hours, minutes=minutes)
            write_hour(
                tmp_path / "box2" / _fname(f"tok{i}", dt), dt,
                monitor=True, vent=vent, duration_s=duration_s,
            )

    def _events(self, tmp_path: Path):
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        segs = [(b, segment_box(b, f)) for b, f in boxes.items()]
        idx = build_cohort_index(segs, cohort="clinic", spec=CLINIC_SPEC, merged=False)
        return idx["events"]

    def test_vent_until_end_of_record_is_censored(self, tmp_path: Path):
        self._write(tmp_path, [(0, 0, 3600, True), (1, 0, 3600, True)])
        ev = self._events(tmp_path)[0]
        assert ev["monitor_tail_h"] == pytest.approx(0.0, abs=1e-6)
        assert ev["end_reason"] == "end_of_record"
        assert ev["labels"]["48h"]["event_type"] == "censored_end_of_record"
        assert ev["labels"]["48h"]["extubation_time_h"] is None

    def test_30min_monitor_after_disconnect_is_censored(self, tmp_path: Path):
        self._write(tmp_path, [(0, 0, 3600, True), (1, 0, 1800, False)])
        ev = self._events(tmp_path)[0]
        assert ev["monitor_tail_h"] == pytest.approx(0.5, abs=1e-6)
        assert ev["end_reason"] == "end_of_record"
        assert ev["labels"]["48h"]["event_type"] == "censored_end_of_record"

    def test_2h_monitor_after_disconnect_is_success(self, tmp_path: Path):
        self._write(tmp_path, [
            (0, 0, 3600, True), (1, 0, 3600, False), (2, 0, 3600, False),
        ])
        ev = self._events(tmp_path)[0]
        assert ev["monitor_tail_h"] == pytest.approx(2.0, abs=1e-6)
        assert ev["end_reason"] == "extubation_observed"
        lab = ev["labels"]["48h"]
        assert lab["event_type"] == "successful_extubation"
        assert lab["extubation_time_h"] == pytest.approx(1.0, abs=1e-3)

    def test_merge_includes_monitor_only_files(self, tmp_path: Path):
        """La fusión incluye los ficheros de monitor sin ventilador (corrección 1)."""
        self._write(tmp_path, [
            (0, 0, 3600, True), (1, 0, 3600, False), (2, 0, 3600, False),
        ])
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        segs = [(b, segment_box(b, f)) for b, f in boxes.items()]
        idx = build_cohort_index(segs, cohort="clinic", spec=CLINIC_SPEC, merged=False)
        assert len(idx["events"][0]["source_files"]) == 3


# ── D5 en Clínic/VitalDB (corrección 3) ──────────────────────────────────────

class TestSignalD5:
    def _write_hour(self, tmp_path: Path, hours: int, minutes: int,
                    duration_s: float, vent: bool, monitor: bool) -> None:
        dt = BASE + timedelta(hours=hours, minutes=minutes)
        write_hour(
            tmp_path / "box2" / _fname(f"t{hours}_{minutes}", dt), dt,
            monitor=monitor, vent=vent, duration_s=duration_s,
        )

    def _events(self, tmp_path: Path):
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        segs = [(b, segment_box(b, f)) for b, f in boxes.items()]
        return build_cohort_index(
            segs, cohort="clinic", spec=CLINIC_SPEC, merged=False
        )["events"]

    def test_monitor_loss_without_hr_zero_is_not_death(self, tmp_path: Path):
        """Perder el monitor no es una muerte: sin FC 0 sostenida → end_of_record."""
        self._write_hour(tmp_path, 0, 0, 3600, vent=True, monitor=True)
        self._write_hour(tmp_path, 1, 0, 3600, vent=True, monitor=False)
        ev = self._events(tmp_path)[0]
        assert ev["death_signal"]["detected"] is False
        assert ev["signal_loss_at_end"] is True
        assert ev["labels"]["48h"]["event_type"] == "censored_end_of_record"

    def test_negative_control_monitor_present_no_censor(self, tmp_path: Path):
        self._write_hour(tmp_path, 0, 0, 3600, vent=True, monitor=True)
        self._write_hour(tmp_path, 1, 0, 3600, vent=False, monitor=True)
        self._write_hour(tmp_path, 2, 0, 3600, vent=False, monitor=True)
        ev = self._events(tmp_path)[0]
        assert ev["signal_loss_at_end"] is False
        assert ev["labels"]["48h"]["event_type"] == "successful_extubation"

    def test_simultaneous_shutdown_reported(self, tmp_path: Path):
        """Ventilador y monitor se apagan a la vez → se reporta (D5)."""
        self._write_hour(tmp_path, 0, 0, 3600, vent=True, monitor=True)
        ev = self._events(tmp_path)[0]
        assert ev["simultaneous_shutdown"] is True

    def test_negative_control_shutdown_not_simultaneous(self, tmp_path: Path):
        self._write_hour(tmp_path, 0, 0, 3600, vent=True, monitor=True)
        self._write_hour(tmp_path, 1, 0, 3600, vent=False, monitor=True)
        ev = self._events(tmp_path)[0]
        assert ev["simultaneous_shutdown"] is False

    def test_signal_death_is_detected_and_censors_at_disconnect(self, tmp_path: Path):
        """FC 65→0 con desaturación, 16 min tras la desconexión → terminal."""
        dt = BASE
        dt_unix = to_epoch_utc(dt)
        vf = vitaldb.VitalFile()
        vf.dtstart = dt_unix
        vf.dtend = dt_unix + 3600
        hr = [(0.0, 65), (0.2, 55), (0.3, 40), (0.45, 25), (0.6, 12),
              (0.667, 0), (0.7, 0), (0.8, 0), (0.9, 0), (0.99, 0)]
        spo2 = [(0.0, 97), (0.3, 93), (0.5, 86), (0.7, 70), (0.9, 65)]
        vf.add_track("Intellivue/ECG_HR",
                     [{"dt": dt_unix + t * 3600, "val": v} for t, v in hr], srate=0)
        vf.add_track("Intellivue/PLETH_SAT_O2",
                     [{"dt": dt_unix + t * 3600, "val": v} for t, v in spo2], srate=0)
        # Ventilación solo hasta 0,4 h (desconexión) y sin recuperar monitor.
        vf.add_track("Intellivue/TV_EXP",
                     [{"dt": dt_unix + t * 3600, "val": 500.0}
                      for t in (0.0, 0.2, 0.4)], srate=0)
        path = tmp_path / "box2" / _fname("tok", dt)
        path.parent.mkdir(parents=True, exist_ok=True)
        vf.to_vital(str(path))

        ev = self._events(tmp_path)[0]
        assert ev["death_signal"]["detected"] is True
        assert ev["death_signal"]["died_ventilated"] is False
        # Fase 1.6b (punto 4): vocabulario unico -> death_at_vent.
        assert ev["end_reason"] == "death_at_vent"
        assert ev["d5"]["48h"]["censor_cause"] == "terminal_extubation"
        assert ev["labels"]["48h"]["event_type"] == "censored_terminal_extubation"


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
                    "end_reason", "labels", "source_files", "source_tokens",
                    "label_source", "box"):
            assert key in ev
        assert ev["arrived_ventilated"] is True
        assert ev["t0_source"] == "already_ventilated_at_record_start"
        # Fase 1.6c (punto 5): origen de la etiqueta en las cohortes con señal.
        assert ev["label_source"] == "senal"

    def test_evento_sin_ficheros_en_la_region_usa_los_del_intento(self, tmp_path: Path):
        """Fase 1.6c (punto 4): ningún evento debe quedarse sin ``source_files``.

        Se fuerza una región de monitor posterior a los ficheros (episodio
        recortado) y se comprueba que el respaldo por intento rellena la lista.
        """
        from src.create_dataset.build_signal_cases import _files_for_episode
        from src.common.episodes import Attempt, Episode

        make_box(tmp_path / "raw", "box2", [0, 1], [0])
        boxes = scan_source_files(tmp_path / "raw", CLINIC_SPEC)
        files = list(boxes.values())[0]
        # Región de monitor 100 h después de los ficheros: no solapa ninguno.
        ep = Episode(attempts=[Attempt(0, files[0].dt_unix / 3600.0,
                                       files[0].dt_unix / 3600.0 + 1.0)],
                     region_start_h=files[0].dt_unix / 3600.0,
                     region_end_h=files[0].dt_unix / 3600.0 + 100.0)
        out = _files_for_episode(files, ep)
        assert out, "el respaldo debe devolver los ficheros del intento"

    def test_respaldo_por_hora_de_cabecera(self, tmp_path: Path):
        """Hay ficheros de VitalDB cuyo nombre no coincide con su contenido.

        El episodio nace de la **cabecera** (``dtstart``), así que si ni la
        región ni los intentos solapan por nombre-hora hay que mirar la
        cabecera del sondeo: sin esto el evento se quedaba sin
        ``source_files`` (4 de 96 en VitalDB, Fase 1.6c punto 4).
        """
        from src.create_dataset.build_signal_cases import _files_for_episode
        from src.common.episodes import Attempt, Episode

        raw = tmp_path / "raw" / "box2"
        # Fichero con el nombre en la hora 0 y el contenido 100 h después.
        real = BASE + timedelta(hours=100)
        write_hour(raw / _fname("tok1", BASE), real, monitor=True, vent=True)
        boxes = scan_source_files(tmp_path / "raw", CLINIC_SPEC)
        files = list(boxes.values())[0]
        seg = segment_box("box2", files)
        start_h = to_epoch_utc(real) / 3600.0
        ep = Episode(attempts=[Attempt(0, start_h, start_h + 1.0)],
                     region_start_h=start_h, region_end_h=start_h + 1.0)
        # Por nombre-hora no solapa nada...
        assert _files_for_episode(files, ep) == []
        # ... pero la CABECERA del sondeo sí: se devuelve por esa vía.
        out = _files_for_episode(files, ep, probes=seg.probes)
        assert out, "el respaldo por cabecera debe encontrar los ficheros"
        assert out[0].path.name == files[0].path.name

    def test_negativo_sin_probes_no_inventa_ficheros(self, tmp_path: Path):
        from src.create_dataset.build_signal_cases import _files_for_episode
        from src.common.episodes import Attempt, Episode

        make_box(tmp_path / "raw", "box2", [0, 1], [0])
        boxes = scan_source_files(tmp_path / "raw", CLINIC_SPEC)
        files = list(boxes.values())[0]
        ep = Episode(attempts=[Attempt(0, files[0].dt_unix / 3600.0 + 100.0,
                                       files[0].dt_unix / 3600.0 + 101.0)],
                     region_start_h=files[0].dt_unix / 3600.0 + 100.0,
                     region_end_h=files[0].dt_unix / 3600.0 + 101.0)
        assert _files_for_episode(files, ep, probes=None) == []

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
