"""
tests/test_signal_death.py
==========================
Tests de la detección de muerte por señales (Fase 1, ajuste 2).

Escenarios exigidos:
  - FC de 75 a 0 en un instante, con el resto normal → NO es muerte;
  - FC descendiendo 60 → 0 en 20 min con SpO2 perdida, 40 min tras la
    desconexión → ``terminal_extubation``;
  - FC a 0 durante 3 min y se recupera → NO es muerte;
  - muerte 3 días después de un éxito consolidado → sigue siendo éxito.
"""

from __future__ import annotations

import pytest

from src.common.d5_events import d5_censor_for_window
from src.common.labels import assign_label, attempts_from_pairs
from src.common.signal_death import Series, detect_signal_death

H = 1.0  # las series de estos tests van en horas


def _s(pairs) -> Series:
    return Series.of(pairs)


class TestAbruptDropIsNotDeath:
    def test_hr_75_to_0_instantly_rest_normal(self):
        """Caída brusca de FC con el resto normal → desconexión, no muerte."""
        hr = _s([(0.0, 75), (0.5, 75), (1.0, 75), (1.02, 0), (1.2, 0), (1.5, 0)])
        spo2 = _s([(0.0, 98), (1.0, 98), (1.2, 98)])       # sin desaturación
        map_ = _s([(0.0, 90), (1.0, 88), (1.2, 88)])       # MAP normal
        amp = _s([(0.0, 60), (1.0, 58), (1.2, 55)])        # pulsatilidad presente
        dec = detect_signal_death(hr, spo2=spo2, map_=map_, amp_abp=amp)
        assert dec.is_death is False
        assert dec.reason == "abrupt_drop_disconnection"


class TestProgressiveDescentIsDeath:
    def test_descent_60_to_0_with_spo2_loss_after_disconnect(self):
        """FC 65→0 en ~22 min con desaturación, 40 min después de la desconexión."""
        hr = _s([(0.0, 65), (0.3, 55), (0.45, 35), (0.6, 15),
                 (0.667, 0), (0.7, 0), (0.9, 0)])
        spo2 = _s([(0.2, 97), (0.5, 88), (0.7, 80)])       # desaturación progresiva
        dec = detect_signal_death(hr, spo2=spo2)
        assert dec.is_death is True
        assert dec.death_time_h == pytest.approx(0.667, abs=1e-6)

        # Se usa EXACTAMENTE como DEATHTIME: 40 min después de la desconexión.
        disc = dec.death_time_h - 40.0 / 60.0
        c5 = d5_censor_for_window(
            failure_window_h=48.0, last_disconnect_h=disc,
            death_time_h=dec.death_time_h, died_ventilated=False,
        )
        assert c5.censor_cause == "terminal_extubation"
        assert c5.censor_time_h == pytest.approx(disc)

    def test_pulsatility_loss_counts_as_deterioration(self):
        hr = _s([(0.0, 80), (0.3, 78), (0.6, 75), (0.68, 0), (0.7, 0), (0.9, 0)])
        amp = _s([(0.0, 60), (0.5, 55), (0.68, 0.2), (0.8, 0.1)])
        dec = detect_signal_death(hr, amp_ppg=amp)
        assert dec.is_death is True
        assert dec.reason == "pulsatility_loss"


class TestRecoveryIsNotDeath:
    def test_hr_zero_3min_then_recovers(self):
        hr = _s([(0.0, 75), (0.05, 0), (0.1, 0), (0.15, 0), (0.2, 70), (0.3, 70)])
        dec = detect_signal_death(hr)
        assert dec.is_death is False
        assert dec.reason == "hr_not_zero_at_end"

    def test_negative_control_zero_run_too_short(self):
        """FC 0 durante 3 min hasta el final (sin recuperar) pero < 10 min."""
        hr = _s([(0.0, 75), (0.8, 70), (0.9, 60), (0.95, 0), (1.0, 0)])
        dec = detect_signal_death(hr)
        assert dec.is_death is False
        assert dec.reason == "zero_run_too_short"


class TestOrderingRule:
    def test_death_3_days_after_consolidated_success_keeps_success(self):
        """Muerte 3 días (72 h) después de un éxito consolidado → sigue éxito."""
        attempts = [(72.0, None)]            # éxito a las 72 h
        death_h = 72.0 + 72.0                # 3 días después
        dec = d5_censor_for_window(
            failure_window_h=48.0, last_disconnect_h=72.0,
            death_time_h=death_h, died_ventilated=False,
        )
        # 72 h > 48 h → no censura por ventana; y a 72 h tampoco.
        assert dec.censor_cause is None
        lab = assign_label(
            attempts_from_pairs(attempts), obs_end_h=death_h,
            failure_window_h=48.0,
            censor_cause=dec.censor_cause, censor_time_h=dec.censor_time_h,
        )
        assert lab.event_type == "successful_extubation"
        assert lab.extubation_time_h == pytest.approx(72.0)
