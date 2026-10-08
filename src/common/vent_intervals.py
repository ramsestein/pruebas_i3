"""
common/vent_intervals.py
========================
Fase 1.6b — **algoritmo único** de reconstrucción de intervalos de ventilación
invasiva a partir de **anotaciones** (ajustes del respirador), calibrado en
MIMIC y aplicado después a eICU.

Regla (una sola para todas las cohortes):

- una anotación invasiva **abre o mantiene** el episodio;
- un hueco entre anotaciones consecutivas **> ``gap_h``** lo **cierra**;
- el inicio del episodio es la **primera** anotación de la racha y el fin la
  **última** (no se extrapola hacia fuera: no se inventan horas sin anotación).

El hueco es inclusivo: un hueco de exactamente ``gap_h`` mantiene el episodio
(misma convención que ``episodes.merge_spans``, D1).
"""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np

from src.common.episodes import Span

_EPS = 1e-9

# Valores de ``gap_h`` que se calibran en MIMIC (punto 1 de la Fase 1.6b).
GAP_CANDIDATES_H: tuple[float, ...] = (2.0, 4.0, 6.0, 8.0)

# Submuestreos con los que se emula la frecuencia de anotación de eICU.
ANNOTATION_STRATA_H: tuple[float, ...] = (1.0, 2.0, 4.0)


def _clean_times(times_h: Iterable[float]) -> list[float]:
    return sorted(float(t) for t in times_h if t is not None and np.isfinite(t))


def intervals_from_annotations(
    times_h: Iterable[float],
    gap_h: float,
    *,
    keep_singletons: bool = True,
) -> list[Span]:
    """Intervalos de ventilación a partir de las anotaciones invasivas.

    ``keep_singletons=True`` conserva las rachas de una sola anotación como
    intervalos de duración 0 (una anotación aislada **es** evidencia de un
    ajuste, aunque no permita medir duración); ``False`` las descarta.
    """
    if gap_h < 0:
        raise ValueError("gap_h debe ser >= 0")
    ts = _clean_times(times_h)
    if not ts:
        return []

    out: list[Span] = []
    start = prev = ts[0]
    for t in ts[1:]:
        if t - prev > gap_h + _EPS:
            if prev > start or keep_singletons:
                out.append(Span(start, prev))
            start = t
        prev = t
    if prev > start or keep_singletons:
        out.append(Span(start, prev))
    return out


def subsample_times(times_h: Iterable[float], min_interval_h: float) -> list[float]:
    """Submuestrea anotaciones conservando las separadas por ≥ ``min_interval_h``.

    Sirve para emular cohortes con anotación más espaciada (p. ej. eICU, con
    medianas de 1–4 h) sobre los datos de MIMIC, que se anotan cada hora.
    ``min_interval_h <= 0`` no cambia nada.
    """
    ts = _clean_times(times_h)
    if min_interval_h <= 0:
        return ts
    kept: list[float] = []
    for t in ts:
        if not kept or t - kept[-1] >= min_interval_h - _EPS:
            kept.append(t)
    return kept


def annotation_interval_h(times_h: Sequence[float]) -> float | None:
    """Intervalo mediano entre anotaciones consecutivas (h), o ``None``."""
    ts = _clean_times(times_h)
    if len(ts) < 2:
        return None
    diffs = np.diff(np.asarray(ts, dtype=float))
    diffs = diffs[diffs > _EPS]
    if diffs.size == 0:
        return None
    return float(np.median(diffs))


# ── Etiqueta de 3 clases y corrección por estrato (Fase 1.6c, punto 3) ───────

LABEL3_SUCCESS = "exito"
LABEL3_SUCCESS_AFTER_FAILURE = "exito_con_fallo_previo"
LABEL3_CENSORED = "censura"
LABEL3_CLASSES: tuple[str, ...] = (LABEL3_SUCCESS, LABEL3_SUCCESS_AFTER_FAILURE,
                                   LABEL3_CENSORED)


def label3(event_type: str | None, n_failed_attempts: int = 0) -> str:
    """Clase de 3 niveles de la etiqueta a 48 h.

    - ``exito``: extubación exitosa sin fallos previos;
    - ``exito_con_fallo_previo``: extubación exitosa tras ≥ 1 fallo;
    - ``censura``: sin extubación exitosa (cualquier causa).
    """
    if event_type == "successful_extubation":
        return (LABEL3_SUCCESS_AFTER_FAILURE if int(n_failed_attempts or 0) > 0
                else LABEL3_SUCCESS)
    return LABEL3_CENSORED


def shift_interval_ends(
    spans: Sequence[Span],
    shift_h: float,
    *,
    min_duration_h: float = 0.0,
) -> list[Span]:
    """Suma ``shift_h`` al FIN de cada intervalo (corrección sistemática).

    El desplazamiento del fin reconstruido es sistemático (los ajustes dejan de
    anotarse antes de la desconexión real), así que se corrige sumando la
    mediana medida en MIMIC. ``min_duration_h`` impide que un desplazamiento
    negativo invierta o colapse el intervalo. El inicio NO se toca.
    """
    out: list[Span] = []
    for sp in spans:
        end = sp.end_h + float(shift_h)
        if end < sp.start_h + min_duration_h:
            end = sp.start_h + min_duration_h
        out.append(Span(sp.start_h, end))
    return out


def confusion_matrix(
    pred: Sequence[str],
    ref: Sequence[str],
    classes: Sequence[str] = LABEL3_CLASSES,
) -> dict[str, dict[str, int]]:
    """Matriz de confusión (filas = ``pred``, columnas = ``ref``)."""
    idx = {c: i for i, c in enumerate(classes)}
    m = np.zeros((len(classes), len(classes)), dtype=int)
    for p, r in zip(pred, ref):
        if p in idx and r in idx:
            m[idx[p], idx[r]] += 1
    return {pc: {rc: int(m[i, j]) for j, rc in enumerate(classes)}
            for i, pc in enumerate(classes)}


def correction_verdict(
    before_pct: dict[float, float],
    after_pct: dict[float, float],
    *,
    min_gain_pct: float = 0.0,
) -> dict:
    """¿Mejora la concordancia de la etiqueta en TODOS los estratos?

    Criterio de aplicación de la corrección por estrato: se aplica solo si la
    concordancia a 48 h mejora (``> min_gain_pct``) en **todos** los estratos.
    """
    strata = sorted(set(before_pct) & set(after_pct))
    gains = {s: float(after_pct[s]) - float(before_pct[s]) for s in strata}
    improves_all = bool(strata) and all(g > min_gain_pct for g in gains.values())
    return {"approved": improves_all, "gains_pct": gains,
            "mean_gain_pct": (float(np.mean(list(gains.values()))) if gains else None)}
