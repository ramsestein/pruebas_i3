"""
common/transfer_validation.py
=============================
Fase 1.6d — **punto 2**: lógica pura para validar la censura
``transfer_ventilated`` de eICU con el **destino del alta**.

La regla 0 censura como ``transfer_ventilated`` cuando la observación termina con
la estancia sin la hora de monitor sin ventilador. Pero si el paciente se va a
**casa**, a **planta** o a **hospicio**, es implausible que siguiera ventilado
de forma invasiva: la censura sería un artefacto.

Este módulo NO aplica la reclasificación (es una **propuesta** de la Fase 1.6d);
solo decide, de forma determinista, si el destino es incompatible y si el evento
cumpliría la regla propuesta.
"""

from __future__ import annotations

from typing import Optional

# Destinos en los que no se puede continuar la ventilación invasiva.
INCOMPATIBLE_DESTINATIONS: frozenset[str] = frozenset({
    "home", "home with home health", "home w/ home health", "home with hospice",
    "floor", "floor bed", "assisted living", "other", "hospice",
    "psychiatric hospital", "residential facility",
})

# Tiempo mínimo (h) entre el último ajuste invasivo y el alta para considerar
# que el paciente ya no estaba ventilado al irse.
MIN_GAP_LAST_ADJ_H: float = 1.0


def is_incompatible_destination(destination: Optional[str]) -> bool:
    """¿El destino es incompatible con seguir ventilado de forma invasiva?"""
    if destination is None:
        return False
    return str(destination).strip().lower() in INCOMPATIBLE_DESTINATIONS


def propose_transfer_reclassification(
    cause: Optional[str],
    destination: Optional[str],
    gap_last_adj_h: Optional[float],
    *,
    min_gap_h: float = MIN_GAP_LAST_ADJ_H,
) -> Optional[str]:
    """Causa propuesta si la censura ``transfer_ventilated`` es un artefacto.

    Devuelve ``"end_of_record"`` cuando:

    - la causa es ``transfer_ventilated``;
    - el destino es incompatible con seguir ventilado;
    - el último ajuste invasivo es ``>= min_gap_h`` anterior al alta.

    En cualquier otro caso devuelve ``None`` (no se reclasifica).
    """
    if cause != "transfer_ventilated":
        return None
    if not is_incompatible_destination(destination):
        return None
    if gap_last_adj_h is None or gap_last_adj_h < min_gap_h:
        return None
    return "end_of_record"
