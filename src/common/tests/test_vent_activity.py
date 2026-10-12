"""
Tests de la **ventilación ACTIVA** (Fase 1.6e, punto 2).

La presencia de pistas del ventilador no basta: con el respirador en espera (o
con alto flujo) FiO2/PEEP y las ondas siguen registrándose. Un minuto es activo
si TV_EXP >= 100 mL, o MV_EXP >= 1,0 L/min, o amplitud de AWP_WAV (p95-p5) >=
5 cmH2O **con VENT_RR > 0**.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest
import vitaldb

from src.common.vital_signals import (
    ACTIVITY_AWP_AMP_MIN_CMH2O,
    ACTIVITY_AWP_RR_MIN_RPM,
    ACTIVITY_MV_EXP_MIN_LPM,
    ACTIVITY_TV_EXP_MIN_ML,
    active_vent_minutes,
    active_vent_span_from_probe,
    count_standby_minutes,
    minute_amplitude,
    minute_max,
    probe_vital_file,
    spans_from_active_minutes,
    to_physical,
)
from src.common.timeutils import to_epoch_utc

BASE = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)


# ── Funciones puras ──────────────────────────────────────────────────────────

class TestMinuteHelpers:
    def test_minute_max(self):
        t = [0.0, 30.0, 61.0, 90.0]
        v = [10.0, 40.0, 5.0, 7.0]
        out = minute_max(t, v)
        assert out[0] == 40.0 and out[1] == 7.0

    def test_minute_amplitude(self):
        # Minuto 0: rampa 0..7 -> p95-p5 ~ 6.3; minuto 1: plano -> 0.
        t = list(np.arange(0, 60, 1.0)) + list(np.arange(60, 120, 1.0))
        v = list(np.linspace(0, 7, 60)) + [3.0] * 60
        amp = minute_amplitude(t, v)
        assert amp[0] > 5.0
        assert amp[1] == pytest.approx(0.0, abs=1e-9)

    def test_amplitud_serie_corta(self):
        assert minute_amplitude([0.0, 1.0], [1.0, 2.0]) == {}


class TestActiveVentMinutes:
    def test_tv_exp_activo(self):
        assert active_vent_minutes(tv_exp={0: 500.0}) == {0}

    def test_tv_exp_bajo_no_activo(self):
        assert active_vent_minutes(tv_exp={0: 0.0, 1: 99.9}) == set()

    def test_mv_exp_activo(self):
        assert active_vent_minutes(mv_exp={2: 4.0}) == {2}

    def test_mv_exp_bajo_no_activo(self):
        assert active_vent_minutes(mv_exp={2: 0.5}) == set()

    def test_awp_amplitud_con_rr_activo(self):
        assert active_vent_minutes(awp_amp={3: 9.0}, vent_rr={3: 12.0}) == {3}

    def test_awp_amplitud_sin_rr_no_activo(self):
        """Amplitud de AWP sin respiraciones declaradas no es ventilación."""
        assert active_vent_minutes(awp_amp={3: 9.0}, vent_rr={3: 0.0}) == set()
        assert active_vent_minutes(awp_amp={3: 9.0}) == set()

    def test_awp_amplitud_baja_no_activo(self):
        """Alto flujo: ondas presentes pero sin excursión de presión."""
        assert active_vent_minutes(awp_amp={3: 0.2}, vent_rr={3: 20.0}) == set()

    def test_fio2_peep_solos_no_activan(self):
        """FiO2/PEEP no entran en la regla: pasar solo TV/MV/AWP/RR."""
        assert active_vent_minutes(tv_exp={}, mv_exp={}, awp_amp={},
                                   vent_rr={}) == set()

    def test_union_de_criterios(self):
        got = active_vent_minutes(tv_exp={0: 500.0}, mv_exp={1: 4.0},
                                  awp_amp={2: 9.0}, vent_rr={2: 12.0})
        assert got == {0, 1, 2}

    def test_umbrales_documentados(self):
        assert ACTIVITY_TV_EXP_MIN_ML == 100.0
        assert ACTIVITY_MV_EXP_MIN_LPM == 1.0
        assert ACTIVITY_AWP_AMP_MIN_CMH2O == 5.0


class TestSpans:
    def test_hueco_corto_se_fusiona(self):
        # Minutos 0-9 y 30-39: hueco de 21 min <= 120 -> un solo tramo.
        spans = spans_from_active_minutes([0, 1, 2, 30, 31])
        assert spans == [(0.0, 32.0)]

    def test_hueco_largo_cierra(self):
        # Hueco de 300 min > 120 -> dos tramos.
        spans = spans_from_active_minutes([0, 1, 300, 301])
        assert spans == [(0.0, 2.0), (300.0, 302.0)]

    def test_un_solo_minuto(self):
        assert spans_from_active_minutes([5]) == [(5.0, 6.0)]

    def test_vacio(self):
        assert spans_from_active_minutes([]) == []

    def test_standby(self):
        assert count_standby_minutes([0, 1, 2, 3], [0, 3]) == 2


# ── Sondas sintéticas ────────────────────────────────────────────────────────

def _write_file(path, *, tv_exp=None, mv_exp=None, vent_rr=0.0,
                fio2=40.0, peep=5.0, n_min=60):
    """.vital sintético de ``n_min`` minutos con los ajustes y TV dados."""
    dt0 = to_epoch_utc(BASE)
    vf = vitaldb.VitalFile()
    vf.dtstart = dt0
    vf.dtend = dt0 + n_min * 60.0
    for name, val in (("Intellivue/FIO2", fio2),
                      ("Intellivue/PEEP_CMH2O", peep),
                      ("Intellivue/VENT_RR", vent_rr)):
        if val is not None:
            vf.add_track(name, [{"dt": dt0 + m * 60.0, "val": float(val)}
                                for m in range(n_min)])
    if tv_exp is not None:
        recs = []
        for m in range(n_min):
            v = tv_exp(m) if callable(tv_exp) else tv_exp
            recs.append({"dt": dt0 + m * 60.0, "val": float(v)})
        vf.add_track("Intellivue/TV_EXP", recs)
    if mv_exp is not None:
        recs = []
        for m in range(n_min):
            v = mv_exp(m) if callable(mv_exp) else mv_exp
            recs.append({"dt": dt0 + m * 60.0, "val": float(v)})
        vf.add_track("Intellivue/MV_EXP", recs)
    path.parent.mkdir(parents=True, exist_ok=True)
    vf.to_vital(str(path))
    return dt0


class TestProbeActividad:
    def test_ventilador_en_espera_no_es_ventilacion(self, tmp_path):
        """FiO2/PEEP registrados + TV_EXP = 0 -> no hay ventilación activa."""
        p = tmp_path / "espera.vital"
        _write_file(p, tv_exp=0.0, vent_rr=0.0)
        probe = probe_vital_file(p)
        assert probe is not None
        assert probe.active_span is None
        assert probe.standby_minutes > 0   # pistas presentes pero inactivas

    def test_alto_flujo_sin_amplitud_no_es_ventilacion(self, tmp_path):
        """MV_EXP y VENT_RR presentes pero TV por debajo del umbral."""
        p = tmp_path / "flujo.vital"
        _write_file(p, tv_exp=10.0, mv_exp=0.2, vent_rr=20.0)
        probe = probe_vital_file(p)
        assert probe.active_span is None

    def test_ventilacion_normal_si_es_activa(self, tmp_path):
        p = tmp_path / "normal.vital"
        dt0 = _write_file(p, tv_exp=500.0, vent_rr=14.0)
        probe = probe_vital_file(p)
        assert probe.active_span is not None
        assert probe.active_span[0] == dt0
        # El fin es la ÚLTIMA MUESTRA observada de actividad (minuto 59), no el
        # fin del minuto completo: alargarlo 1 min llegaba a invertir la
        # etiqueta D3 en el umbral exacto de 1 h de cola.
        assert probe.active_span[1] == dt0 + 59 * 60.0

    def test_span_en_segundos_epoch_no_en_horas(self, tmp_path):
        """``active_vent_span_from_probe`` debe dar SEGUNDOS epoch.

        Regresión: devolvía horas y el llamador (``_to_hours``) volvía a
        dividir por 3600, dejando un tramo de segundos y rompiendo la
        segmentación (26 tests). ``vent_span_from_probe`` usa segundos.
        """
        p = tmp_path / "normal.vital"
        dt0 = _write_file(p, tv_exp=500.0, vent_rr=14.0)
        span = active_vent_span_from_probe(probe_vital_file(p))
        assert span is not None
        assert span.start_h == dt0
        assert span.end_h == dt0 + 59 * 60.0

    def test_caso_mixto_el_fin_es_el_correcto(self, tmp_path):
        """TV activo los primeros 30 min y 0 después -> el fin es a los 30 min."""
        p = tmp_path / "mixto.vital"
        dt0 = _write_file(p, tv_exp=lambda m: 500.0 if m < 30 else 0.0,
                          vent_rr=14.0)
        probe = probe_vital_file(p)
        assert probe.active_span is not None
        assert probe.active_span[0] == dt0
        # El último minuto activo es el 29 -> fin = inicio del minuto 30.
        assert probe.active_span[1] == dt0 + 30 * 60.0

    def test_mv_sostenido_activo(self, tmp_path):
        p = tmp_path / "mv.vital"
        _write_file(p, tv_exp=0.0, mv_exp=5.0, vent_rr=12.0)
        probe = probe_vital_file(p)
        assert probe.active_span is not None


class TestUnidadesDeLaOndaDePresion:
    """La amplitud de ``AWP_WAV`` se compara con un umbral en cmH2O.

    Regresión (Fase 1.6e, punto 2): los valores de los registros son CRUDOS y
    ``Intellivue/AWP_WAV`` tiene ``gain=0,015 offset=−10`` (cmH2O). Comparar la
    amplitud cruda con ``5`` (el umbral en cmH2O) equivalía a un umbral de
    0,075 cmH2O y dejaba pasar el **23,3 %** de los minutos en espera.
    """

    def _onda(self, exc_raw: float) -> tuple[np.ndarray, np.ndarray]:
        """Onda cuadrada de un minuto con excursión (p95−p5) de ``exc_raw``."""
        t = np.arange(0.0, 60.0, 0.1)
        raw = np.where((t % 4.0) < 2.0, exc_raw / 2.0, -exc_raw / 2.0)
        return t, raw

    def test_amplitud_cruda_se_convierte_a_cmh2o(self):
        t, raw = self._onda(400.0)                       # 400 crudo = 6 cmH2O
        amp = minute_amplitude(t, to_physical(raw, gain=0.015, offset=-10.0))
        assert amp and max(amp.values()) == pytest.approx(6.0, abs=0.01)
        assert active_vent_minutes(awp_amp=amp, vent_rr={0: 14.0}) == {0}

    def test_amplitud_por_debajo_del_umbral_no_es_actividad(self):
        t, raw = self._onda(200.0)                       # 200 crudo = 3 cmH2O
        amp = minute_amplitude(t, to_physical(raw, gain=0.015, offset=-10.0))
        assert amp and max(amp.values()) == pytest.approx(3.0, abs=0.01)
        assert active_vent_minutes(awp_amp=amp, vent_rr={0: 14.0}) == set()

    def test_sin_vent_rr_el_criterio_de_onda_no_activa(self):
        t, raw = self._onda(400.0)
        amp = minute_amplitude(t, to_physical(raw, gain=0.015, offset=-10.0))
        assert active_vent_minutes(awp_amp=amp, vent_rr={}) == set()

    def test_las_pistas_numericas_no_se_escalan(self):
        vals = np.array([324.0, 500.0, 0.0])
        assert list(to_physical(vals, gain=1.0, offset=0.0)) == [324.0, 500.0, 0.0]


class TestCriterioAwpConFrDerivada:
    """D-h: sin pista `VENT_RR`, la amplitud de AWP cuenta si la FR derivada ≥ 4 rpm.

    Cubre los casos en los que el ventilador no declara `VENT_RR` (p. ej.
    `clinic_box14_event_40` o `vitaldb_SICU1_07_event_1`, con excursiones de AWP
    reales que no activaban el criterio).
    """

    def test_amplitud_alta_sin_vent_rr_con_fr_derivada_es_actividad(self):
        assert active_vent_minutes(awp_amp={5: 600.0}, vent_rr={},
                                   awp_rr_derived={5: 12.0}) == {5}

    def test_fr_derivada_baja_no_es_actividad(self):
        assert active_vent_minutes(awp_amp={5: 600.0}, vent_rr={},
                                   awp_rr_derived={5: 2.0}) == set()

    def test_sin_fr_derivada_no_es_actividad(self):
        assert active_vent_minutes(awp_amp={5: 600.0}, vent_rr={}) == set()

    def test_si_hay_vent_rr_manda_vent_rr(self):
        """Con pista `VENT_RR` presente, la FR derivada no la sustituye."""
        assert active_vent_minutes(awp_amp={5: 600.0}, vent_rr={5: 0.0},
                                   awp_rr_derived={5: 12.0}) == set()
        assert active_vent_minutes(awp_amp={5: 600.0}, vent_rr={5: 14.0},
                                   awp_rr_derived={}) == {5}

    def test_el_umbral_de_amplitud_sigue_mandando(self):
        # Amplitud por debajo de 5 cmH2O: la FR derivada no la salva.
        assert active_vent_minutes(awp_amp={5: 3.0}, vent_rr={},
                                   awp_rr_derived={5: 20.0}) == set()

    def test_umbral_de_fr_documentado(self):
        assert ACTIVITY_AWP_RR_MIN_RPM == 4.0
        assert active_vent_minutes(awp_amp={1: 600.0}, vent_rr={},
                                   awp_rr_derived={1: ACTIVITY_AWP_RR_MIN_RPM}) == {1}
        assert active_vent_minutes(awp_amp={1: 600.0}, vent_rr={},
                                   awp_rr_derived={1: 3.9}) == set()
