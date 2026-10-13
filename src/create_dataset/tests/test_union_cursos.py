"""
tests/test_union_cursos.py
==========================
Fase 1.6e, punto 2c (D-f/D-i): union de cursos partidos por un apagado
simultaneo de monitor y ventilador, y exclusion de fragmentos de registro.

La regla aplicada es la ESTRECHA, porque el control negativo de 2c.3 descarto
el criterio de continuidad de ajustes (59,3 % de falsos positivos):

- (a) hueco entre el fin del monitor y la region siguiente <= 2 h,
- (b) en el hueco NO hay ningun fichero (el grabador estaba parado),
- (c) la region siguiente empieza ventilada en <= 15 min.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import vitaldb

from src.common.timeutils import to_epoch_utc
from src.create_dataset.build_signal_cases import (
    CLINIC_SPEC,
    build_cohort_index,
    scan_source_files,
    segment_box,
)

BASE = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
MONITOR_TRACKS = ("Intellivue/ECG_HR", "Intellivue/PLETH_SAT_O2")
VENT_TRACKS = ("Intellivue/TV_EXP", "Intellivue/VENT_RR")


def _recs(dt_unix: float, duration_s: float, step_s: float = 60.0,
          value: float = 1.0):
    t = np.arange(0.0, duration_s + 1e-9, step_s)
    return [{"dt": dt_unix + float(x), "val": value} for x in t]


def write_file(
    path: Path, dt: datetime, *, duration_s: float,
    monitor: bool = True, vent: bool = False, other: bool = False,
) -> Path:
    """Escribe un .vital con monitor y/o ventilador (actividad real)."""
    unix = to_epoch_utc(dt)
    vf = vitaldb.VitalFile()
    vf.dtstart = unix
    vf.dtend = unix + duration_s
    if monitor:
        for tr in MONITOR_TRACKS:
            vf.add_track(tr, _recs(unix, duration_s, value=80.0))
    if vent:
        vf.add_track("Intellivue/TV_EXP", _recs(unix, duration_s, value=500.0))
        vf.add_track("Intellivue/VENT_RR", _recs(unix, duration_s, value=14.0))
    if other:
        # Pista que NO es de monitor ni de ventilador: el fichero existe (el
        # grabador escribia) pero no aporta senal de paciente util.
        vf.add_track("Intellivue/NIBP_SYS", _recs(unix, duration_s, value=120.0))
    path.parent.mkdir(parents=True, exist_ok=True)
    vf.to_vital(str(path))
    return path


def _name(hours: float) -> str:
    dt = BASE + timedelta(hours=hours)
    return f"tok1_{dt.strftime('%y%m%d_%H%M%S')}.vital"


def _seg(tmp_path: Path, box: str = "box2", *, unir: bool = True):
    boxes = scan_source_files(tmp_path, CLINIC_SPEC)
    return segment_box(box, boxes[box], unir_cursos=unir)


class TestUnionDeCursos:
    def test_por_defecto_no_une_nunca(self, tmp_path: Path):
        """Decision 13/10 (opcion a): `unir_cursos` esta OFF por defecto.

        Aunque se cumplan TODAS las condiciones de la regla estricta, sin pedir
        la union explicitamente no se une nada: el cohorte de analisis tiene 0
        uniones.
        """
        self._box(tmp_path, gap_h=1.5)
        seg = _seg(tmp_path, unir=False)
        assert seg.unions == []
        assert len(seg.episodes) == 2

    def _box(self, tmp_path: Path, *, gap_h: float = 1.5,
             fichero_en_hueco: bool = False) -> None:
        # Region 1: 0-2 h ventilada.
        write_file(tmp_path / "box2" / _name(0.0), BASE, duration_s=7200.0,
                   monitor=True, vent=True)
        if fichero_en_hueco:
            # Fichero dentro del hueco: existe grabacion (no es "sin dato").
            write_file(tmp_path / "box2" / _name(2.5),
                       BASE + timedelta(hours=2.5), duration_s=1800.0,
                       monitor=False, vent=False, other=True)
        # Region 2: empieza ventilada (gap_h horas despues) y dura 2,5 h.
        start2 = 2.0 + gap_h
        write_file(tmp_path / "box2" / _name(start2),
                   BASE + timedelta(hours=start2), duration_s=9000.0,
                   monitor=True, vent=True)
        # Cola de monitor sin ventilador: 2 h (confirma la extubacion).
        write_file(tmp_path / "box2" / _name(start2 + 2.5),
                   BASE + timedelta(hours=start2 + 2.5), duration_s=7200.0,
                   monitor=True, vent=False)

    def test_une_si_hueco_sin_ficheros_y_vuelve_ventilado(self, tmp_path: Path):
        self._box(tmp_path, gap_h=1.5)
        seg = _seg(tmp_path)
        assert len(seg.episodes) == 1
        ep = seg.episodes[0]
        assert ep.n_attempts == 2
        assert len(ep.unknown_gaps) == 1
        assert ep.unknown_gaps[0].duration_h == 2.0 + 1.5 - 2.0
        assert len(seg.unions) == 1

    def test_el_hueco_no_cuenta_como_desconexion(self, tmp_path: Path):
        """D-f: en el hueco no se observo ni extubacion ni reintubacion."""
        self._box(tmp_path, gap_h=1.5)
        seg = _seg(tmp_path)
        idx = build_cohort_index([("box2", seg)], cohort="clinic",
                                 spec=CLINIC_SPEC, merged=False)
        ev = idx["events"][0]
        assert ev["n_unions"] == 1
        assert ev["n_label_attempts"] == 1          # vista de etiquetado
        assert ev["n_attempts"] == 2                # intentos conservados
        assert ev["fuera_del_box_h"] == 1.5
        assert ev["fuera_del_box"] == [[2.0, 3.5]]
        lab = ev["labels"]["48h"]
        assert lab["event_type"] == "successful_extubation"
        assert lab["n_failed_attempts"] == 0

    def test_no_une_si_hay_fichero_en_el_hueco(self, tmp_path: Path):
        self._box(tmp_path, gap_h=1.5, fichero_en_hueco=True)
        seg = _seg(tmp_path)
        assert len(seg.episodes) == 2
        assert seg.unions == []

    def test_no_une_si_el_hueco_pasa_de_2h(self, tmp_path: Path):
        self._box(tmp_path, gap_h=3.5)
        seg = _seg(tmp_path)
        assert len(seg.episodes) == 2
        assert seg.unions == []

    def test_no_une_si_no_vuelve_ventilado(self, tmp_path: Path):
        """La region 2 empieza con monitor, pero la ventilacion llega 3 h después."""
        write_file(tmp_path / "box2" / _name(0.0), BASE, duration_s=7200.0,
                   monitor=True, vent=True)
        write_file(tmp_path / "box2" / _name(3.5),
                   BASE + timedelta(hours=3.5), duration_s=10800.0,
                   monitor=True, vent=False)
        write_file(tmp_path / "box2" / _name(6.5),
                   BASE + timedelta(hours=6.5), duration_s=5400.0,
                   monitor=True, vent=True)
        seg = _seg(tmp_path)
        assert len(seg.episodes) == 2
        assert seg.unions == []


class TestFragmentoDeRegistro:
    def test_region_corta_se_excluye_marcada(self, tmp_path: Path):
        """D-i: una region de 20 min es un fragmento; se marca, no se borra."""
        write_file(tmp_path / "box2" / _name(0.0), BASE, duration_s=1200.0,
                   monitor=True, vent=True)
        seg = _seg(tmp_path)
        assert seg.episodes == []
        assert len(seg.excluded_episodes) == 1
        assert seg.excluded_episodes[0].exclusion_reason == "fragmento_registro"
        idx = build_cohort_index([("box2", seg)], cohort="clinic",
                                 spec=CLINIC_SPEC, merged=False)
        assert idx["total_events"] == 0
        assert idx["n_fragmentos_registro"] == 1
        assert idx["excluded_events"][0]["exclusion_reason"] == \
            "fragmento_registro"

    def test_ventilacion_corta_en_region_larga_no_es_fragmento(self, tmp_path: Path):
        """20 min de ventilacion en 8 h de region: evento legitimo."""
        write_file(tmp_path / "box2" / _name(0.0), BASE, duration_s=1200.0,
                   monitor=True, vent=True)
        write_file(tmp_path / "box2" / _name(1.0),
                   BASE + timedelta(hours=1), duration_s=25200.0,
                   monitor=True, vent=False)
        seg = _seg(tmp_path)
        assert len(seg.episodes) == 1
        assert seg.excluded_episodes == []
