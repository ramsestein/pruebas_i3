"""
tests/test_obs_end.py
=====================
Fase 1.6f, punto 1: de dónde sale ``obs_end_h``.

Debe salir de la FISIOLOGÍA del monitor (FC/SpO2 con valores fisiológicos) y
nunca recortarse a ``vent_end_h``. Las únicas veces que coinciden son las que NO
tienen ninguna observación fisiológica tras el fin de la ventilación (el evento
queda censurado como ``end_of_record``), y el fallback es un SUELO, no un tope:
grabar ondas planas no alarga la observación.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import vitaldb

from src.common.episodes import Span, build_episodes
from src.common.timeutils import to_epoch_utc
from src.create_dataset.build_signal_cases import (
    CLINIC_SPEC,
    build_cohort_index,
    extubation_decision,
    observation_end_h,
    scan_source_files,
    segment_box,
)

BASE = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


class TestObservationEndDeEpisodios:
    def _ep(self, *, vent, hr, spo2, monitor):
        eps = build_episodes(
            [Span(*v) for v in vent],
            hr_spans=[Span(*s) for s in hr],
            spo2_spans=[Span(*s) for s in spo2],
            monitor_spans=[Span(*s) for s in monitor],
            stay_bounds=None,
        )
        assert len(eps) == 1
        return eps[0]

    def test_cola_de_monitor_extiende_la_observacion(self):
        ep = self._ep(vent=[(0, 10)], hr=[(0, 12)], spo2=[(0, 12)],
                      monitor=[(0, 12)])
        assert observation_end_h(ep) == 12.0
        assert observation_end_h(ep) > ep.end_h          # nunca se recorta

    def test_sin_cola_fisiologica_la_observacion_acaba_en_el_ventilador(self):
        """Caso censurado: no hay FC/SpO2 tras el fin de la ventilación."""
        ep = self._ep(vent=[(0, 10)], hr=[(0, 6)], spo2=[(0, 6)],
                      monitor=[(0, 10)])
        assert observation_end_h(ep) == 10.0 == ep.end_h
        assert not extubation_decision(ep).is_extubation

    def test_ondas_sin_fisiologia_no_alargan_la_observacion(self):
        """El box sigue grabando ondas, pero sin FC/SpO2 no hay observación."""
        ep = self._ep(vent=[(0, 8)], hr=[(0, 8)], spo2=[(0, 8)],
                      monitor=[(0, 14)])
        assert observation_end_h(ep) == 8.0 == ep.end_h

    def test_cola_corta_censura_pero_no_recorta(self):
        ep = self._ep(vent=[(0, 10)], hr=[(0, 10.5)], spo2=[(0, 10.5)],
                      monitor=[(0, 10.5)])
        assert observation_end_h(ep) == 10.5 > ep.end_h
        assert not extubation_decision(ep).is_extubation

    def test_obs_end_nunca_es_menor_que_vent_end(self):
        for tail in (0.0, 0.5, 1.0, 3.0):
            ep = self._ep(vent=[(0, 10)], hr=[(0, 10 + tail)],
                          spo2=[(0, 10 + tail)], monitor=[(0, 10 + tail)])
            assert observation_end_h(ep) >= ep.end_h


def _recs(unix: float, spans, step_s: float = 60.0, value: float = 1.0):
    out = []
    for a, b in spans:
        for x in np.arange(a, b + 1e-9, step_s):
            out.append({"dt": unix + float(x), "val": value})
    return out


def _write(path: Path, dt: datetime, *, duration_h: float,
           monitor_spans_h, vent_spans_h) -> Path:
    unix = to_epoch_utc(dt)
    vf = vitaldb.VitalFile()
    vf.dtstart = unix
    vf.dtend = unix + duration_h * 3600.0
    mon = [(a * 3600.0, b * 3600.0) for a, b in monitor_spans_h]
    ven = [(a * 3600.0, b * 3600.0) for a, b in vent_spans_h]
    if mon:
        vf.add_track("Intellivue/ECG_HR", _recs(unix, mon, value=80.0))
        vf.add_track("Intellivue/PLETH_SAT_O2", _recs(unix, mon, value=97.0))
    if ven:
        vf.add_track("Intellivue/TV_EXP", _recs(unix, ven, value=500.0))
        vf.add_track("Intellivue/VENT_RR", _recs(unix, ven, value=14.0))
    path.parent.mkdir(parents=True, exist_ok=True)
    vf.to_vital(str(path))
    return path


class TestObsEndEnElIndice:
    def test_indice_guarda_la_cola_de_monitor(self, tmp_path: Path):
        """FC/SpO2 hasta 4 h; ventilación hasta 2 h → obs_end_h = 4 h."""
        _write(tmp_path / "box2" / "tok1_250101_000000.vital", BASE,
               duration_h=4.0, monitor_spans_h=[(0.0, 4.0)],
               vent_spans_h=[(0.0, 2.0)])
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"])
        idx = build_cohort_index([("box2", seg)], cohort="clinic",
                                 spec=CLINIC_SPEC, merged=False)
        ev = idx["events"][0]
        assert ev["obs_end_h"] > ev["attempts"][-1]["vent_end_h"]
        assert abs(ev["monitor_tail_h"] - 2.0) < 0.05

    def test_indice_sin_cola_coincide_con_vent_end(self, tmp_path: Path):
        """FC/SpO2 y ventilación acaban juntas → coincide y queda censurado."""
        _write(tmp_path / "box2" / "tok1_250101_000000.vital", BASE,
               duration_h=4.0, monitor_spans_h=[(0.0, 2.0)],
               vent_spans_h=[(0.0, 2.0)])
        boxes = scan_source_files(tmp_path, CLINIC_SPEC)
        seg = segment_box("box2", boxes["box2"])
        idx = build_cohort_index([("box2", seg)], cohort="clinic",
                                 spec=CLINIC_SPEC, merged=False)
        ev = idx["events"][0]
        assert abs(ev["obs_end_h"] - ev["attempts"][-1]["vent_end_h"]) < 1e-3
        assert ev["monitor_tail_h"] == 0.0
        assert ev["end_reason"] == "end_of_record"
