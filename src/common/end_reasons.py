"""
common/end_reasons.py
=====================
Fase 1.6d — **punto 6**: vocabulario **único** de ``end_reason`` para las cuatro
cohortes (MIMIC, eICU-B, Clínic y VitalDB).

Antes de esta fase cada builder usaba nombres distintos para el mismo motivo
(``death`` / ``death_at_vent``, ``trach`` / ``tracheostomy``,
``excluded_trach_preexisting``...) y la unión de los cuatro índices no era un
vocabulario común. Este módulo fija:

- los **seis** valores canónicos (``END_REASONS``);
- el mapeo de las causas internas de D5 / regla 0 a esos valores;
- ``end_reason_from_causes``: orden de prioridad único
  **muerte ventilado → extubación terminal → traqueostomía →
  extubación observada → causa de la regla 0**.

Orden de prioridad (idéntico para todas las cohortes)
-----------------------------------------------------
1. ``death_at_vent`` — el paciente fallece estando ventilado.
2. ``terminal_extubation`` — el paciente fallece dentro de la ventana de fallo
   tras la última desconexión (extubación terminal / limitación del esfuerzo).
3. ``tracheostomy`` — traqueostomía documentada (con o sin hora).
4. ``extubation_observed`` — extubación confirmada (regla 0, ≥ 1 h sin VM).
5. ``transfer_ventilated`` — la observación termina con la estancia, aún
   ventilado.
6. ``end_of_record`` — se acaban los datos sin extubación ni alta.

``excluded_trach_preexisting`` **no** es un ``end_reason``: es un
``exclusion_reason``. En MIMIC la estancia se marca con ``excluded``/
``exclusion_reason`` y su ``end_reason`` pasa a ``tracheostomy``.
"""

from __future__ import annotations

from typing import Iterable, Optional

EXTUBATION_OBSERVED: str = "extubation_observed"
TRANSFER_VENTILATED: str = "transfer_ventilated"
DEATH_AT_VENT: str = "death_at_vent"
TERMINAL_EXTUBATION: str = "terminal_extubation"
TRACHEOSTOMY: str = "tracheostomy"
END_OF_RECORD: str = "end_of_record"

# Vocabulario canónico compartido por las 4 cohortes.
END_REASONS: tuple[str, ...] = (
    EXTUBATION_OBSERVED,
    TRANSFER_VENTILATED,
    DEATH_AT_VENT,
    TERMINAL_EXTUBATION,
    TRACHEOSTOMY,
    END_OF_RECORD,
)

# Causas internas (D5 / regla 0) -> valor canónico.
_RAW_TO_CANONICAL: dict[str, str] = {
    "extubation_observed": EXTUBATION_OBSERVED,
    "transfer_ventilated": TRANSFER_VENTILATED,
    "death_at_vent": DEATH_AT_VENT,
    "terminal_extubation": TERMINAL_EXTUBATION,
    "trach": TRACHEOSTOMY,
    "trach_time_unknown": TRACHEOSTOMY,
    "tracheostomy": TRACHEOSTOMY,
    "trach_preexisting": TRACHEOSTOMY,
    "end_of_record": END_OF_RECORD,
    # Alias heredados que se normalizan al valor canónico.
    "death": DEATH_AT_VENT,
    "no_successful_extubation": END_OF_RECORD,
}

# Prioridad de las causas D5 (de mayor a menor).
_PRIORITY: tuple[str, ...] = (
    DEATH_AT_VENT, TERMINAL_EXTUBATION, TRACHEOSTOMY,
)


def canonical_end_reason(raw: Optional[str]) -> Optional[str]:
    """Normaliza una causa interna al vocabulario canónico.

    Devuelve el propio valor si ya es canónico; ``None`` si ``raw`` es ``None``.
    Un valor desconocido se devuelve **tal cual** para que un test/informe pueda
    detectarlo como fuera de vocabulario.
    """
    if raw is None:
        return None
    return _RAW_TO_CANONICAL.get(str(raw), str(raw))


def is_canonical(reason: Optional[str]) -> bool:
    return reason in END_REASONS


def end_reason_from_causes(
    causes: Iterable[Optional[str]],
    *,
    is_extubation: bool,
    fallback: Optional[str] = None,
) -> str:
    """Motivo de fin canónico a partir de las causas D5 y de la regla 0.

    ``causes``: causas de censura de las ventanas D5 (p. ej. ``trach``,
    ``terminal_extubation``, ``death_at_vent``). ``is_extubation``: la regla 0
    confirmó la extubación. ``fallback``: causa de la regla 0 cuando NO hay
    extubación (``ext.censor_cause``).
    """
    canon = {canonical_end_reason(c) for c in causes if c}
    for reason in _PRIORITY:
        if reason in canon:
            return reason
    if is_extubation:
        return EXTUBATION_OBSERVED
    return canonical_end_reason(fallback) or END_OF_RECORD
