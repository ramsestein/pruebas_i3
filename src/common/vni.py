"""
common/vni.py
=============
Fase 1.6e — **punto 3**: VNI con mascarilla después de la extubación = EXTUBADO
(decisión D-b).

Un paciente extubado que queda con **ventilación no invasiva** (mascarilla) no
está intubado: la VNI **no** es ventilación invasiva ni fallo; solo la
**reintubación invasiva** es fallo. Igual que en MIMIC/eICU.

Riesgo principal: clasificar como VNI una ventilación **invasiva con fuga**
(balón) → daría **éxitos falsos**. Por eso la regla es **conservadora**: un tramo
posterior a la última desconexión solo se considera VNI si se cumplen **TODAS**:

(i) va precedido de una **desconexión real del circuito**: ≥ ``MIN_GAP_MIN``
    minutos sin actividad (ni TV ni amplitud de AWP) **con FC/SpO2 fisiológicas**
    (el paciente sigue allí);
(ii) hay **firma de interfaz no invasiva**: con pista de fuga o ``TV_INSP`` →
    fuga ≥ 20 % o ``(TV_INSP − TV_EXP)/TV_INSP ≥ 0,2`` en ≥ 50 % de los minutos;
    con pista de modo/interfaz → manda el modo. **Sin** pistas de fuga ni de modo
    el tramo **NO se reclasifica** (cuenta como ventilación invasiva) y se marca
    ``posible_vni_no_verificable``;
(iii) es intermitente o de duración limitada: se **reporta** la distribución, no
    se excluye por ello.

Este módulo es **puro**: los builders le pasan los minutos/valores ya extraídos.
"""

from __future__ import annotations

from dataclasses import dataclass

# (i) Desconexión real del circuito: minutos sin actividad ni TV ni AWP.
MIN_GAP_MIN: float = 10.0

# (ii) Firma de interfaz no invasiva.
LEAK_MIN_FRACTION: float = 0.20        # fuga >= 20 %
TV_INSP_FRACTION: float = 0.20         # (TV_INSP - TV_EXP)/TV_INSP >= 0,2
MIN_FRACTION_MINUTES: float = 0.50     # en >= 50 % de los minutos del tramo

# Razones de la decisión (vocabulario único).
REASON_VNI = "vni_confirmada"
REASON_NO_DISCONNECT = "sin_desconexion_real"
REASON_NO_VERIFIABLE = "posible_vni_no_verificable"
REASON_INVASIVE_IFACE = "interfaz_invasiva"


@dataclass(frozen=True)
class VniDecision:
    """Resultado de clasificar un tramo posterior como VNI."""
    is_vni: bool
    reason: str
    verifiable: bool

    @property
    def is_failure(self) -> bool:
        """La VNI nunca es fallo: solo la reintubación invasiva lo es."""
        return False


def real_disconnect_before(
    prev_end_min: float,
    next_start_min: float,
    *,
    monitor_present: bool,
    min_gap_min: float = MIN_GAP_MIN,
) -> bool:
    """(i) ¿Hay una desconexión real del circuito antes del tramo?

    Exige ``>= min_gap_min`` minutos sin actividad **y** que el paciente siga
    monitorizado (FC/SpO2 fisiológicas): sin monitor no se puede afirmar que la
    desconexión sea real (podría ser fin de registro).
    """
    if not monitor_present:
        return False
    return (float(next_start_min) - float(prev_end_min)) >= min_gap_min - 1e-9


def leak_signature(leak_frac: float | None,
                   *, threshold: float = MIN_FRACTION_MINUTES) -> bool:
    """Firma de fuga: la condición de fuga se cumple en >= ``threshold`` de los
    minutos del tramo.

    ``leak_frac`` es la **fracción de minutos** del tramo en los que la fuga
    supera ``LEAK_MIN_FRACTION`` (el cálculo por minuto lo hace el llamador). El
    mínimo de cobertura es ``MIN_FRACTION_MINUTES`` (50 %): una fuga puntual no
    es una firma de interfaz.
    """
    return leak_frac is not None and float(leak_frac) >= threshold - 1e-9


def tv_insp_signature(
    tv_insp_frac: float | None, *, threshold: float = MIN_FRACTION_MINUTES
) -> bool:
    """Firma de TV inspirado: ``(TV_INSP − TV_EXP)/TV_INSP >= TV_INSP_FRACTION``
    en >= ``threshold`` de los minutos del tramo.

    ``tv_insp_frac`` es la **fracción de minutos** del tramo que cumplen la
    condición de ``TV_INSP_FRACTION`` (0,2); el cálculo por minuto lo hace el
    llamador (``vital_signals.probe_vital_file_detail``).
    """
    return tv_insp_frac is not None and float(tv_insp_frac) >= threshold - 1e-9


def interface_signature(
    *,
    leak_frac: float | None = None,
    tv_insp_frac: float | None = None,
    mode_is_niv: bool | None = None,
) -> tuple[bool | None, bool]:
    """(ii) ``(es_interfaz_no_invasiva, verificable)``.

    - si hay pista de **modo/interfaz**, manda el modo (verificable);
    - si hay pista de **fuga** o **TV_INSP**, la firma es cualquiera de las dos
      (verificable);
    - si **no** hay ninguna de las dos cosas, ``(None, False)``: no verificable.
    """
    if mode_is_niv is not None:
        return bool(mode_is_niv), True
    if leak_frac is not None or tv_insp_frac is not None:
        return (leak_signature(leak_frac) or tv_insp_signature(tv_insp_frac)), True
    return None, False


def classify_vni(
    *,
    prev_end_min: float,
    next_start_min: float,
    monitor_present: bool,
    leak_frac: float | None = None,
    tv_insp_frac: float | None = None,
    mode_is_niv: bool | None = None,
    min_gap_min: float = MIN_GAP_MIN,
) -> VniDecision:
    """Clasifica un tramo posterior como VNI (regla conservadora completa).

    Solo devuelve ``is_vni=True`` si hay **desconexión real** (i) **y** firma
    **no invasiva verificable** (ii). En cualquier otro caso el tramo cuenta
    como ventilación invasiva (y, si no es verificable, se marca).
    """
    if not real_disconnect_before(prev_end_min, next_start_min,
                                  monitor_present=monitor_present,
                                  min_gap_min=min_gap_min):
        return VniDecision(False, REASON_NO_DISCONNECT, True)
    signature, verifiable = interface_signature(
        leak_frac=leak_frac, tv_insp_frac=tv_insp_frac, mode_is_niv=mode_is_niv)
    if not verifiable:
        return VniDecision(False, REASON_NO_VERIFIABLE, False)
    if signature:
        return VniDecision(True, REASON_VNI, True)
    return VniDecision(False, REASON_INVASIVE_IFACE, True)


def is_limited_duration(
    durations_h: list[float], *, p90_h: float = 24.0,
) -> float:
    """(iii) Fracción de tramos de VNI con duración ``<= p90_h`` (reporte).

    No excluye por duración: solo informa de si la firma observada es
    compatible con VNI (intermitente / limitada).
    """
    if not durations_h:
        return 0.0
    ok = sum(1 for d in durations_h if d <= p90_h)
    return ok / len(durations_h)
