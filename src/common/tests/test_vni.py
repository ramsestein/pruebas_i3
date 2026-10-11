"""
Tests de ``common/vni.py`` (Fase 1.6e, punto 3): VNI después de la extubación =
extubado, con regla conservadora.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import vitaldb

from src.common.timeutils import to_epoch_utc
from src.common.vital_signals import probe_vital_file
from src.common.vni import (
    LEAK_MIN_FRACTION,
    MIN_FRACTION_MINUTES,
    MIN_GAP_MIN,
    REASON_INVASIVE_IFACE,
    REASON_NO_DISCONNECT,
    REASON_NO_VERIFIABLE,
    REASON_VNI,
    TV_INSP_FRACTION,
    classify_vni,
    interface_signature,
    is_limited_duration,
    leak_signature,
    real_disconnect_before,
    tv_insp_signature,
)


class TestDesconexionReal:
    def test_hueco_suficiente_con_monitor(self):
        assert real_disconnect_before(100.0, 115.0, monitor_present=True) is True

    def test_hueco_insuficiente(self):
        assert real_disconnect_before(100.0, 105.0, monitor_present=True) is False

    def test_sin_monitor_no_es_desconexion_real(self):
        """Sin FC/SpO2 fisiológicas la desconexión podría ser fin de registro."""
        assert real_disconnect_before(100.0, 999.0, monitor_present=False) is False

    def test_umbral_en_el_limite(self):
        assert real_disconnect_before(0.0, MIN_GAP_MIN, monitor_present=True) is True


class TestFirmaDeInterfaz:
    def test_fuga(self):
        # La firma exige la condición de fuga en >= 50 % de los minutos.
        assert leak_signature(0.60) is True
        assert leak_signature(0.25) is False
        assert leak_signature(None) is False

    def test_tv_insp(self):
        assert tv_insp_signature(0.60) is True
        assert tv_insp_signature(0.30) is False

    def test_cobertura_minima_documentada(self):
        assert MIN_FRACTION_MINUTES == 0.50
        assert tv_insp_signature(MIN_FRACTION_MINUTES) is True
        assert tv_insp_signature(MIN_FRACTION_MINUTES - 0.01) is False

    def test_sin_pistas_no_verificable(self):
        sig, verif = interface_signature()
        assert sig is None and verif is False

    def test_fuga_o_tvinsp_verificable(self):
        sig, verif = interface_signature(leak_frac=0.60)
        assert sig is True and verif is True
        sig, verif = interface_signature(tv_insp_frac=0.05)
        assert sig is False and verif is True

    def test_el_modo_manda(self):
        # Con pista de modo, decide el modo aunque la fuga sea alta.
        sig, verif = interface_signature(leak_frac=0.50, mode_is_niv=False)
        assert sig is False and verif is True
        sig, verif = interface_signature(leak_frac=0.0, mode_is_niv=True)
        assert sig is True and verif is True

    def test_umbrales_documentados(self):
        assert MIN_GAP_MIN == 10.0
        assert LEAK_MIN_FRACTION == 0.20
        assert TV_INSP_FRACTION == 0.20


class TestClassifyVni:
    def test_fuga_de_balon_sin_desconexion_no_es_vni(self):
        """Caso exigido: fuga (balón) sin desconexión real -> NO es VNI."""
        d = classify_vni(prev_end_min=100.0, next_start_min=104.0,
                         monitor_present=True, leak_frac=0.60)
        assert d.is_vni is False
        assert d.reason == REASON_NO_DISCONNECT

    def test_desconexion_mas_fuga_es_vni(self):
        """Caso exigido: desconexión real + fuga -> VNI."""
        d = classify_vni(prev_end_min=100.0, next_start_min=120.0,
                         monitor_present=True, leak_frac=0.65)
        assert d.is_vni is True
        assert d.reason == REASON_VNI
        assert d.is_failure is False   # la VNI NUNCA es fallo

    def test_desconexion_sin_pistas_no_verificable(self):
        """Caso exigido: desconexión real sin pistas de fuga -> no verificable."""
        d = classify_vni(prev_end_min=100.0, next_start_min=120.0,
                         monitor_present=True)
        assert d.is_vni is False
        assert d.reason == REASON_NO_VERIFIABLE
        assert d.verifiable is False

    def test_desconexion_mas_interfaz_invasiva(self):
        d = classify_vni(prev_end_min=100.0, next_start_min=120.0,
                         monitor_present=True, leak_frac=0.02)
        assert d.is_vni is False
        assert d.reason == REASON_INVASIVE_IFACE

    def test_modo_niv_con_desconexion(self):
        d = classify_vni(prev_end_min=0.0, next_start_min=30.0,
                         monitor_present=True, mode_is_niv=True)
        assert d.is_vni is True and d.reason == REASON_VNI

    def test_sin_monitor_no_reclasifica(self):
        d = classify_vni(prev_end_min=0.0, next_start_min=600.0,
                         monitor_present=False, leak_frac=0.9)
        assert d.is_vni is False
        assert d.reason == REASON_NO_DISCONNECT

    def test_gap_configurable(self):
        d = classify_vni(prev_end_min=0.0, next_start_min=5.0,
                         monitor_present=True, leak_frac=0.5, min_gap_min=3.0)
        assert d.is_vni is True


class TestDuracionLimitada:
    def test_fraccion(self):
        assert is_limited_duration([2.0, 4.0, 48.0]) == pytest.approx(2 / 3)

    def test_vacio(self):
        assert is_limited_duration([]) == 0.0


# ── Firma de interfaz medida por la SONDA ────────────────────────────────────

BASE = datetime(2025, 3, 1, 8, 0, 0, tzinfo=timezone.utc)


def _write_vent_file(path, *, n_min: int = 60, tv_exp: float | None = 400.0,
                     tv_insp: float | None = None,
                     simv: bool = False) -> float:
    """``.vital`` sintético con las pistas de la firma de interfaz."""
    dt0 = to_epoch_utc(BASE)
    vf = vitaldb.VitalFile()
    vf.dtstart = dt0
    vf.dtend = dt0 + n_min * 60.0
    for name, val in (("Intellivue/TV_EXP", tv_exp),
                      ("Intellivue/TV_INSP", tv_insp)):
        if val is not None:
            vf.add_track(name, [{"dt": dt0 + m * 60.0, "val": float(val)}
                                for m in range(n_min)])
    if simv:
        vf.add_track("Intellivue/SET_SIMV",
                     [{"dt": dt0 + m * 60.0, "val": 1.0} for m in range(n_min)])
    path.parent.mkdir(parents=True, exist_ok=True)
    vf.to_vital(str(path))
    return dt0


class TestFirmaDeInterfazEnLaSonda:
    def test_tv_insp_por_encima_del_umbral(self, tmp_path):
        """(TV_INSP - TV_EXP)/TV_INSP = 1/3 >= 0,2 en todos los minutos."""
        p = tmp_path / "niv.vital"
        _write_vent_file(p, tv_exp=400.0, tv_insp=600.0)
        probe = probe_vital_file(p)
        assert probe.tv_insp_frac == pytest.approx(1.0)

    def test_tv_insp_igual_al_espirado_no_es_firma(self, tmp_path):
        p = tmp_path / "invasiva.vital"
        _write_vent_file(p, tv_exp=400.0, tv_insp=400.0)
        probe = probe_vital_file(p)
        assert probe.tv_insp_frac == pytest.approx(0.0)

    def test_sin_tv_insp_no_hay_firma(self, tmp_path):
        p = tmp_path / "sin_tv_insp.vital"
        _write_vent_file(p, tv_exp=400.0)
        probe = probe_vital_file(p)
        assert probe.tv_insp_frac is None

    def test_modo_simv_es_invasivo(self, tmp_path):
        p = tmp_path / "simv.vital"
        _write_vent_file(p, tv_exp=400.0, simv=True)
        probe = probe_vital_file(p)
        assert probe.mode_is_niv is False

    def test_sin_pista_de_modo_no_se_puede_afirmar(self, tmp_path):
        p = tmp_path / "sin_modo.vital"
        _write_vent_file(p, tv_exp=400.0)
        probe = probe_vital_file(p)
        assert probe.mode_is_niv is None

    def test_serializacion_de_la_firma(self, tmp_path):
        from src.common.vital_signals import probe_from_dict, probe_to_dict

        p = tmp_path / "niv.vital"
        _write_vent_file(p, tv_exp=400.0, tv_insp=600.0, simv=True)
        probe = probe_vital_file(p)
        back = probe_from_dict(p, probe_to_dict(probe))
        assert back.tv_insp_frac == pytest.approx(probe.tv_insp_frac)
        assert back.mode_is_niv is probe.mode_is_niv is False

