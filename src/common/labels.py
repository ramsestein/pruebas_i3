"""
common/labels.py
================
Etiquetado del estimando (D3) a partir de los intentos de ventilación.

Reglas (D3)
-----------
- **Ventana de observación** = la estancia en UCI/box (igual en las 4 cohortes).
- **Fallo**: reintubación dentro de 48/72 h en la misma estancia. En MIMIC, las
  reintubaciones tras un reingreso se calculan SOLO como sensibilidad (no aquí).
- **Éxito**: extubación sin reintubación dentro de la ventana. El alta de UCI
  antes de que acabe la ventana, sin reintubación, cuenta como éxito.
- **Censura**: no hay extubación exitosa antes de que termine la observación
  (muerte ventilado, traslado ventilado, fin de datos). ``extubation_time_h``
  = NaN y ``event_type`` = ``censored_*`` con la causa.
- **Sensibilidad**: no se pierden los fallos previos a una censura
  (``n_failed_attempts`` los cuenta aunque el desenlace sea censura).
- ``is_at_risk``: hasta el evento (éxito) o la censura o el fin de observación.

La clasificación por intentos la hace la lógica de ``classify_attempts``
(``src/stage0/labeling/survival.py``); aquí se traduce a etiquetas por evento.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

FAILURE_WINDOWS_H: tuple[float, ...] = (48.0, 72.0)

EPS = 1e-9


@dataclass(frozen=True)
class AttemptOutcome:
    """Un intento: extubación y (si la hay) reintubación, en horas desde t0."""
    attempt_idx: int
    extubation_h: float
    reintubation_h: Optional[float] = None


@dataclass
class LabelResult:
    """Etiqueta de un evento para una ventana de fallo dada."""
    event_type: str                       # successful_extubation | censored_*
    failure_window_h: float
    extubation_time_h: Optional[float]    # None si censurado
    censor_cause: Optional[str]
    censor_time_h: Optional[float]
    n_failed_attempts: int
    first_attempt_h: Optional[float]
    is_at_risk_until_h: float


def evaluate_attempts(
    attempts: Sequence[AttemptOutcome],
    failure_window_h: float,
) -> tuple[Optional[int], int]:
    """Devuelve (índice del primer intento exitoso | None, nº de fallos previos).

    Un intento es exitoso si no tiene reintubación o si la reintubación ocurre
    ESTRICTAMENTE después de la ventana (``> failure_window_h``).
    """
    for i, att in enumerate(attempts):
        if att.reintubation_h is None:
            return i, sum(
                1 for a in attempts[:i] if _is_failure(a, failure_window_h)
            )
        gap = att.reintubation_h - att.extubation_h
        if gap > failure_window_h + EPS:
            # Reintubación fuera de la ventana → este intento ya fue exitoso.
            return i, sum(
                1 for a in attempts[:i] if _is_failure(a, failure_window_h)
            )
    return None, sum(1 for a in attempts if _is_failure(a, failure_window_h))


def _is_failure(att: AttemptOutcome, failure_window_h: float) -> bool:
    return (
        att.reintubation_h is not None
        and (att.reintubation_h - att.extubation_h) <= failure_window_h + EPS
    )


def classify_attempt(
    att: AttemptOutcome,
    failure_window_h: float,
    *,
    extubation_confirmed: bool,
) -> str:
    """Clasifica UN intento para una ventana: 'failure' | 'success' | 'unknown'.

    Lógica ÚNICA compartida por `src/stage0/labeling/survival.py` (corrección 4):
    - fallo si hay reintubación dentro de la ventana;
    - éxito si la reintubación llega después de la ventana o si la extubación
      está confirmada;
    - en otro caso, desconocido.
    """
    if _is_failure(att, failure_window_h):
        return "failure"
    if att.reintubation_h is not None or extubation_confirmed:
        return "success"
    return "unknown"


def count_failures(attempts: Sequence[AttemptOutcome], failure_window_h: float) -> int:
    """Nº total de intentos fallidos (se conserva aunque haya censura)."""
    return sum(1 for a in attempts if _is_failure(a, failure_window_h))


def assign_label(
    attempts: Sequence[AttemptOutcome],
    obs_end_h: float,
    failure_window_h: float,
    *,
    censor_cause: Optional[str] = None,
    censor_time_h: Optional[float] = None,
) -> LabelResult:
    """Etiqueta un evento según D3 (y censura explícita de D5 si se indica).

    **Orden temporal (D5):** una censura solo se aplica si ocurre ANTES (o en el
    mismo instante) de la primera extubación exitosa. Si la traqueostomía o la
    muerte llegan después de un éxito ya consolidado, la etiqueta sigue siendo
    éxito.
    """
    first_attempt = attempts[0].extubation_h if attempts else None
    n_failed = count_failures(attempts, failure_window_h)
    idx, n_failed_before = evaluate_attempts(attempts, failure_window_h)

    if censor_cause is not None:
        t_censor = (
            float(censor_time_h) if censor_time_h is not None
            else _last_event_h(attempts, obs_end_h)
        )
        apply_censor = (
            idx is None
            or t_censor <= attempts[idx].extubation_h + EPS
        )
        if apply_censor:
            return LabelResult(
                event_type=f"censored_{censor_cause}",
                failure_window_h=failure_window_h,
                extubation_time_h=None,
                censor_cause=censor_cause,
                censor_time_h=t_censor,
                n_failed_attempts=n_failed,
                first_attempt_h=first_attempt,
                is_at_risk_until_h=t_censor,
            )
        # Hay un éxito consolidado antes de la censura → se mantiene el éxito.

    if idx is None:
        # Sin extubación exitosa dentro de la observación → censura por fin de datos.
        # Si el paciente falleció ventilado, el builder lo marca con censor_cause.
        return LabelResult(
            event_type="censored_no_extubation",
            failure_window_h=failure_window_h,
            extubation_time_h=None,
            censor_cause="no_successful_extubation",
            censor_time_h=float(obs_end_h),
            n_failed_attempts=n_failed,
            first_attempt_h=first_attempt,
            is_at_risk_until_h=float(obs_end_h),
        )

    extub_h = attempts[idx].extubation_h
    return LabelResult(
        event_type="successful_extubation",
        failure_window_h=failure_window_h,
        extubation_time_h=float(extub_h),
        censor_cause=None,
        censor_time_h=None,
        n_failed_attempts=n_failed_before,
        first_attempt_h=first_attempt,
        is_at_risk_until_h=float(extub_h),
    )


def _last_event_h(attempts: Sequence[AttemptOutcome], obs_end_h: float) -> float:
    if not attempts:
        return obs_end_h
    last = attempts[-1]
    return last.extubation_h


def assign_labels_all_windows(
    attempts: Sequence[AttemptOutcome],
    obs_end_h: float,
    failure_windows_h: Sequence[float] = FAILURE_WINDOWS_H,
    *,
    censor_cause: Optional[str] = None,
    censor_time_h: Optional[float] = None,
) -> dict[str, LabelResult]:
    """Etiquetas para todas las ventanas (clave ``'48h'``, ``'72h'``)."""
    return {
        f"{int(w)}h": assign_label(
            attempts, obs_end_h, float(w),
            censor_cause=censor_cause, censor_time_h=censor_time_h,
        )
        for w in failure_windows_h
    }


def labels_to_dict(labels: dict[str, LabelResult]) -> dict:
    """Serializa las etiquetas a un dict apto para el índice JSON."""
    out: dict = {}
    for key, lab in labels.items():
        out[key] = {
            "event_type": lab.event_type,
            "extubation_time_h": lab.extubation_time_h,
            "censor_cause": lab.censor_cause,
            "censor_time_h": lab.censor_time_h,
            "n_failed_attempts": lab.n_failed_attempts,
            "first_attempt_h": lab.first_attempt_h,
            "is_at_risk_until_h": lab.is_at_risk_until_h,
        }
    return out


def attempts_from_pairs(
    pairs: Sequence[tuple[float, Optional[float]]],
) -> list[AttemptOutcome]:
    """Convierte ``[(extub_h, reintub_h|None), ...]`` en ``AttemptOutcome``."""
    return [
        AttemptOutcome(attempt_idx=i, extubation_h=float(e),
                       reintubation_h=(None if r is None else float(r)))
        for i, (e, r) in enumerate(pairs)
    ]
