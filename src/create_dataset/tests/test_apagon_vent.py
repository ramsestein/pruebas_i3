"""
tests/test_apagon_vent.py
=========================
Fase 1.6f, puntos 2 y 3:

- **D-j** (apagón del grabador): un hueco se mide por MUESTRAS, no por «hay o no
  hay fichero». Es un apagón si en él NINGUNA pista de ningún dispositivo tiene
  muestras. Si dura ``<= 2 h`` y hay ventilación ACTIVA en los 15 min previos y
  posteriores, es la MISMA región (horas "blackout"); si dura más, corta pero se
  marca (``gap_type="blackout_long"``).
- **D-k** (caída de la conexión de datos del ventilador): ninguna pista del
  respirador (ni los ajustes) con muestras y el monitor presente. Una caída de
  ``<= 2 h`` no corta el intento; las de más de 2 h se informan sin cambiar la
  regla. Un respirador en ESPERA sí deja ajustes (TV ≈ 0), así que conserva
  muestras y NO es una caída de la conexión.
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


def _recs(unix: float, spans, step_s: float = 60.0, value: float = 1.0):
    out = []
    for a, b in spans:
        for x in np.arange(a, b + 1e-9, step_s):
            out.append({"dt": unix + float(x), "val": value})
    return out


def write_file(
    path: Path, dt: datetime, *, duration_h: float,
    monitor: bool = True, vent: bool = True, other: bool = False,
    hole: tuple[float, float] | None = None,
    tv: float = 500.0, hr: float = 80.0, spo2: float = 97.0,
    vent_spans_h: list[tuple[float, float]] | None = None,
) -> Path:
    """Escribe un .vital.

    ``hole`` = ``(inicio_h, fin_h)`` sin NINGÚN registro (apagón); ``other``
    añade una pista que no es de monitor ni de ventilador (NIBP) con las mismas
    horas (el grabador seguía escribiendo); ``vent_spans_h`` limita los registros
    del ventilador a esos tramos (caída de la conexión de datos).
    """
    unix = to_epoch_utc(dt)
    dur_s = duration_h * 3600.0
    vf = vitaldb.VitalFile()
    vf.dtstart = unix
    vf.dtend = unix + dur_s
    spans = ([(0.0, dur_s)] if hole is None
             else [(0.0, hole[0] * 3600.0), (hole[1] * 3600.0, dur_s)])
    vent_spans = ([(a * 3600.0, b * 3600.0) for a, b in vent_spans_h]
                  if vent_spans_h is not None else spans)
    if monitor:
        vf.add_track("Intellivue/ECG_HR", _recs(unix, spans, value=hr))
        vf.add_track("Intellivue/PLETH_SAT_O2", _recs(unix, spans, value=spo2))
    if vent:
        vf.add_track("Intellivue/TV_EXP", _recs(unix, vent_spans, value=tv))
        vf.add_track("Intellivue/VENT_RR", _recs(unix, vent_spans, value=14.0))
    if other:
        vf.add_track("Intellivue/NIBP_SYS", _recs(unix, spans, value=120.0))
    path.parent.mkdir(parents=True, exist_ok=True)
    vf.to_vital(str(path))
    return path


def _name(hours: float) -> str:
    dt = BASE + timedelta(hours=hours)
    return f"tok1_{dt.strftime('%y%m%d_%H%M%S')}.vital"


def _seg(tmp_path: Path, box: str = "box2", *, blackout: bool = True,
         drops: bool = True):
    boxes = scan_source_files(tmp_path, CLINIC_SPEC)
    return segment_box(box, boxes[box], detect_blackout=blackout,
                       absorb_vent_dropouts=drops)


def _index(seg) -> dict:
    return build_cohort_index([("box2", seg)], cohort="clinic",
                              spec=CLINIC_SPEC, merged=False)


class TestD_jApagonDelGrabador:
    def test_apagon_de_1h_dentro_de_un_fichero_una_sola_region(self, tmp_path: Path):
        """Un hueco de 1 h sin NINGUNA muestra dentro de un fichero no corta."""
        write_file(tmp_path / "box2" / _name(0.0), BASE, duration_h=6.0,
                   hole=(2.0, 3.0))
        seg = _seg(tmp_path)
        assert len(seg.episodes) == 1
        assert len(seg.blackouts) == 1
        assert seg.blackouts[0]["benign"] is True
        assert 0.9 <= seg.blackouts[0]["duration_h"] <= 1.1

    def test_apagon_de_1_5h_entre_ficheros_una_sola_region(self, tmp_path: Path):
        """Control: sin D-j el hueco son dos regiones; con D-j, una."""
        write_file(tmp_path / "box2" / _name(0.0), BASE, duration_h=2.0)
        write_file(tmp_path / "box2" / _name(3.5),
                   BASE + timedelta(hours=3.5), duration_h=2.5)
        con = _seg(tmp_path)
        assert len(con.episodes) == 1
        assert con.episodes[0].n_attempts == 1
        assert len(con.blackouts) == 1 and con.blackouts[0]["benign"]
        sin = _seg(tmp_path, blackout=False)
        assert len(sin.episodes) == 2
        assert sin.blackouts == []

    def test_box_grabando_sin_paciente_1h_corta(self, tmp_path: Path):
        """Hay MUESTRAS en el hueco (NIBP): el box grababa → salida → corte."""
        write_file(tmp_path / "box2" / _name(0.0), BASE, duration_h=2.0)
        write_file(tmp_path / "box2" / _name(2.0),
                   BASE + timedelta(hours=2.0), duration_h=2.0,
                   monitor=False, vent=False, other=True)
        write_file(tmp_path / "box2" / _name(4.0),
                   BASE + timedelta(hours=4.0), duration_h=3.0)
        seg = _seg(tmp_path)
        assert len(seg.episodes) == 2
        assert seg.blackouts == []

    def test_apagon_de_3h_corta_y_se_marca(self, tmp_path: Path):
        write_file(tmp_path / "box2" / _name(0.0), BASE, duration_h=2.0)
        write_file(tmp_path / "box2" / _name(5.0),
                   BASE + timedelta(hours=5.0), duration_h=3.0)
        seg = _seg(tmp_path)
        assert len(seg.episodes) == 2
        assert len(seg.blackouts) == 1
        assert seg.blackouts[0]["type"] == "long"
        assert seg.blackouts[0]["benign"] is False
        idx = _index(seg)
        assert idx["n_blackouts_long"] == 1
        # Los dos lados del apagón largo quedan marcados.
        assert idx["n_events_gap_type_blackout_long"] == 2
        for ev in idx["events"]:
            assert ev["gap_type"] == "blackout_long"
            assert ev["n_blackout_h"] > 2.0

    def test_apagon_sin_ventilacion_a_un_lado_corta(self, tmp_path: Path):
        """2 h sin muestras y sin ventilación activa después → corte."""
        write_file(tmp_path / "box2" / _name(0.0), BASE, duration_h=2.0)
        write_file(tmp_path / "box2" / _name(4.0),
                   BASE + timedelta(hours=4.0), duration_h=2.5,
                   vent_spans_h=[(1.5, 2.5)])
        seg = _seg(tmp_path)
        assert len(seg.blackouts) == 1
        assert seg.blackouts[0]["benign"] is False
        assert len(seg.episodes) == 2


class TestD_kCaidaDelVentilador:
    def test_caida_de_1_5h_con_monitor_un_solo_intento(self, tmp_path: Path):
        """Sin NINGUNA muestra del ventilador durante 1,5 h y monitor presente.

        El monitor está presente porque hay un fichero (sin pistas de
        ventilador) que lo cubre: por eso no hay corte de paciente y la caída es
        "dato ausente".
        """
        write_file(tmp_path / "box2" / _name(0.0), BASE, duration_h=2.0)
        write_file(tmp_path / "box2" / _name(2.0),
                   BASE + timedelta(hours=2.0), duration_h=1.5,
                   monitor=True, vent=False)
        write_file(tmp_path / "box2" / _name(3.5),
                   BASE + timedelta(hours=3.5), duration_h=2.5)
        seg = _seg(tmp_path)
        assert len(seg.episodes) == 1
        assert seg.episodes[0].n_attempts == 1
        assert len(seg.vent_drops) == 1
        assert seg.vent_drops[0]["benign"] is True

    def test_espera_con_tv_cero_3h_es_desconexion(self, tmp_path: Path):
        """TV ≈ 0 son AJUSTES registrados: no es una caída de la conexión."""
        write_file(tmp_path / "box2" / _name(0.0), BASE, duration_h=2.0)
        write_file(tmp_path / "box2" / _name(2.0),
                   BASE + timedelta(hours=2.0), duration_h=3.0, tv=0.0)
        write_file(tmp_path / "box2" / _name(5.0),
                   BASE + timedelta(hours=5.0), duration_h=3.0)
        seg = _seg(tmp_path)
        assert seg.vent_drops == []
        assert len(seg.episodes) == 1
        assert seg.episodes[0].n_attempts == 2

    def test_caida_de_3h_se_informa_sin_absorber(self, tmp_path: Path):
        write_file(tmp_path / "box2" / _name(0.0), BASE, duration_h=2.5)
        write_file(tmp_path / "box2" / _name(2.5),
                   BASE + timedelta(hours=2.5), duration_h=3.0,
                   monitor=True, vent=False)
        write_file(tmp_path / "box2" / _name(5.5),
                   BASE + timedelta(hours=5.5), duration_h=2.5)
        seg = _seg(tmp_path)
        assert len(seg.vent_drops) == 1
        assert seg.vent_drops[0]["type"] == "long"
        assert seg.vent_drops[0]["benign"] is False
        # Regla actual (D1): el hueco pasa de 2 h → dos intentos.
        assert len(seg.episodes) == 1
        assert seg.episodes[0].n_attempts == 2
        idx = _index(seg)
        assert idx["n_vent_feed_drops"] == 1
        assert idx["n_events_with_vent_feed_drop"] >= 1
        assert idx["n_vent_feed_dropout_h"] > 2.0

    def test_sin_dk_no_se_detecta_la_caida(self, tmp_path: Path):
        """Control: con D-k desactivado no hay caída que absorber."""
        write_file(tmp_path / "box2" / _name(0.0), BASE, duration_h=2.0)
        write_file(tmp_path / "box2" / _name(2.0),
                   BASE + timedelta(hours=2.0), duration_h=1.5,
                   monitor=True, vent=False)
        write_file(tmp_path / "box2" / _name(3.5),
                   BASE + timedelta(hours=3.5), duration_h=2.5)
        seg = _seg(tmp_path, drops=False)
        assert seg.vent_drops == []
        assert len(seg.episodes) == 1
        assert seg.episodes[0].n_attempts == 1
