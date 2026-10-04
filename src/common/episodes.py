"""
common/episodes.py
==================
Segmentación de episodios de ventilación mecánica, común a las 4 cohortes.

Este módulo implementa, con tipos de datos agnósticos a la cohorte, las
decisiones fijadas D1 (evento = curso completo de ventilación de un paciente),
D2 (cambio de paciente) y D4 (exclusión de actividad de ventilador sin
paciente).

Vocabulario
-----------
- **Tramo de ventilador** (``Span``): intervalo [start_h, end_h) en horas
  respecto a una referencia común (t0 del fichero/estancia) en el que hay
  actividad de ventilador (en Clínic/VitalDB, presencia de señales de
  ventilador; en eICU/MIMIC, ``ventstartoffset``/``ventendoffset``...).
- **Intento** (``Attempt``): tramo de ventilación continuo. Dos tramos
  separados por un hueco *> disconnect_gap_h* (D1: 2 h) son intentos
  distintos. Un hueco *<= 2 h* es una desconexión y se fusiona.
- **Episodio** (``Episode``): todos los intentos que pertenecen al mismo
  paciente y a la misma estancia. Un episodio puede contener varios intentos
  (extubación y reintubación). Ningún episodio cruza un cambio de paciente
  (D2).

Reglas implementadas
--------------------
D1  Hueco <= 2 h sin ventilador -> desconexión, se fusiona (mismo intento).
    Hueco > 2 h con el paciente presente -> fin de intento; si vuelve a
    ventilarse es un nuevo intento del MISMO evento.
D2  Cambio de paciente cuando el monitor está > 1 h sin ninguna señal
    (HR, SpO2, ECG, PLETH) en Clínic/VitalDB; en eICU/MIMIC por el
    identificador de estancia. Ningún evento cruza un cambio de paciente.
D4  Sin duración mínima como criterio de inclusión. Solo se excluye
    actividad de ventilador sin paciente: ventilador sin HR ni SpO2
    simultáneas durante >= 80 % del tramo ventilado. La exclusión no borra
    el episodio: se marca ``excluded``/``exclusion_reason`` y se reporta.

Los datos concretos de cada cohorte (nombres de pista, itemids, offsets) NO se
conocen aquí: los builders construyen los ``Span`` y las marcas de monitor y
delegan en este módulo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np

# ── Constantes de decisión (D1/D2/D4). Los builders pueden sobrescribirlas ────
DISCONNECT_GAP_H: float = 2.0     # D1: hueco <= 2 h sin ventilador = desconexión
PATIENT_GAP_H: float = 1.0        # D2: hueco de monitor > 1 h = cambio de paciente
NO_PATIENT_FRACTION: float = 0.8  # D4: sin HR ni SpO2 >= 80 % del tramo -> excluido

# Tolerancia numérica para comparaciones de huecos en horas (evita que un
# hueco de exactamente 2.0 h se considere ">" por error de coma flotante).
_EPS: float = 1e-9


# ── Tipos de datos ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Span:
    """Intervalo temporal semiabierto [start_h, end_h) en horas."""

    start_h: float
    end_h: float

    def __post_init__(self) -> None:
        if not np.isfinite(self.start_h) or not np.isfinite(self.end_h):
            raise ValueError(f"Span con extremos no finitos: {self!r}")
        if self.end_h < self.start_h:
            raise ValueError(
                f"Span con end_h < start_h: [{self.start_h}, {self.end_h}]"
            )

    @property
    def duration_h(self) -> float:
        return float(self.end_h - self.start_h)

    def __repr__(self) -> str:  # pragma: no cover - cosmético
        return f"Span({self.start_h:.3f}, {self.end_h:.3f})"


@dataclass(frozen=True)
class Attempt:
    """Tramo de ventilación continuo (un intento de ventilación/extubación)."""

    attempt_idx: int
    start_h: float
    end_h: float

    @property
    def duration_h(self) -> float:
        return float(self.end_h - self.start_h)

    @property
    def span(self) -> Span:
        return Span(self.start_h, self.end_h)


@dataclass
class Episode:
    """Curso completo de ventilación de un paciente dentro de una estancia."""

    attempts: list[Attempt] = field(default_factory=list)
    excluded: bool = False
    exclusion_reason: Optional[str] = None
    # Fracción del tiempo ventilado sin HR ni SpO2 (D4). None si no evaluada.
    no_patient_fraction: Optional[float] = None
    # Frontera de la región de identidad de paciente (D2). El final de la
    # observación es `region_end_h` (fin del MONITOR), no el fin del último
    # intento: puede haber monitor sin ventilador después de la extubación.
    region_start_h: float = float("nan")
    region_end_h: float = float("nan")

    @property
    def n_attempts(self) -> int:
        return len(self.attempts)

    @property
    def start_h(self) -> float:
        return self.attempts[0].start_h

    @property
    def end_h(self) -> float:
        return self.attempts[-1].end_h

    @property
    def duration_h(self) -> float:
        return self.end_h - self.start_h

    @property
    def ventilated_hours(self) -> float:
        return float(sum(a.duration_h for a in self.attempts))


# ── Operaciones básicas sobre intervalos ──────────────────────────────────────

def _as_spans(spans: Iterable[Span | Sequence[float]]) -> list[Span]:
    """Convierte una secuencia heterogénea en lista de ``Span`` positivos."""
    out: list[Span] = []
    for s in spans:
        if isinstance(s, Span):
            span = s
        else:
            start, end = float(s[0]), float(s[1])
            span = Span(start, end)
        if span.duration_h > 0:
            out.append(span)
    return out


def merge_spans(
    spans: Iterable[Span | Sequence[float]],
    max_gap_h: float,
) -> list[Span]:
    """Fusiona intervalos solapados o separados por un hueco <= ``max_gap_h``.

    Devuelve intervalos ordenados y disjuntos. Los intervalos de duración cero
    se descartan. ``max_gap_h`` es inclusivo: un hueco de exactamente
    ``max_gap_h`` se fusiona (D1: "hueco <= 2 h").
    """
    if max_gap_h < 0:
        raise ValueError("max_gap_h debe ser >= 0")

    items = sorted(_as_spans(spans), key=lambda s: (s.start_h, s.end_h))
    merged: list[Span] = []
    for s in items:
        if merged and s.start_h - merged[-1].end_h <= max_gap_h + _EPS:
            last = merged[-1]
            if s.end_h > last.end_h:
                merged[-1] = Span(last.start_h, s.end_h)
        else:
            merged.append(s)
    return merged


def merge_spans_ignoring_missing(
    spans: Iterable[Span | Sequence[float]],
    missing_spans: Iterable[Span | Sequence[float]],
    max_gap_h: float,
) -> list[Span]:
    """Fusiona intervalos ignorando los huecos cubiertos por ``missing_spans``.

    Un fichero **ilegible** es "sin dato", no "sin señal": sus horas no deben
    crear un hueco de ventilador (D1) ni un corte de paciente (D2). El hueco
    efectivo entre dos tramos es ``gap - tiempo_cubierto_por_missing`` y se
    fusionan si ese hueco efectivo es ``<= max_gap_h``.
    """
    merged = merge_spans(spans, 0.0)
    if not merged:
        return []
    missing = merge_spans(missing_spans, 0.0)
    out: list[Span] = [merged[0]]
    for s in merged[1:]:
        gap = s.start_h - out[-1].end_h
        covered = (
            _measure_within(Span(out[-1].end_h, s.start_h), missing)
            if gap > 0 else 0.0
        )
        if gap - covered <= max_gap_h + _EPS:
            out[-1] = Span(out[-1].start_h, max(out[-1].end_h, s.end_h))
        else:
            out.append(s)
    return out


def invert_spans(spans: Iterable[Span], start_h: float, end_h: float) -> list[Span]:
    """Complemento de ``spans`` dentro de [start_h, end_h]."""
    clipped = clip_spans(spans, start_h, end_h)
    gaps: list[Span] = []
    cursor = start_h
    for s in clipped:
        if s.start_h > cursor:
            gaps.append(Span(cursor, s.start_h))
        cursor = max(cursor, s.end_h)
    if cursor < end_h:
        gaps.append(Span(cursor, end_h))
    return gaps


def clip_spans(
    spans: Iterable[Span | Sequence[float]],
    start_h: float,
    end_h: float,
) -> list[Span]:
    """Recorta ``spans`` al intervalo [start_h, end_h] y fusiona solapes."""
    out: list[Span] = []
    for s in _as_spans(spans):
        a = max(s.start_h, start_h)
        b = min(s.end_h, end_h)
        if b > a:
            out.append(Span(a, b))
    return merge_spans(out, 0.0)


def total_duration(spans: Iterable[Span]) -> float:
    """Duración total cubierta por la unión de ``spans`` (sin doble conteo)."""
    return float(sum(s.duration_h for s in merge_spans(spans, 0.0)))


def _measure_within(sample: Span, container: Iterable[Span]) -> float:
    """Duración de ``sample`` cubierta por la unión de ``container``."""
    cover = clip_spans(container, sample.start_h, sample.end_h)
    return total_duration(cover)


# ── Segmentación (D1) ─────────────────────────────────────────────────────────

def segment_attempts(
    vent_spans: Iterable[Span | Sequence[float]],
    disconnect_gap_h: float = DISCONNECT_GAP_H,
) -> list[Attempt]:
    """Segmenta los tramos de ventilador en intentos (D1).

    Fusiona tramos separados por un hueco <= ``disconnect_gap_h`` (desconexión)
    y los separa cuando el hueco es mayor (fin de intento / extubación).
    """
    runs = merge_spans(vent_spans, disconnect_gap_h)
    return [
        Attempt(attempt_idx=i, start_h=r.start_h, end_h=r.end_h)
        for i, r in enumerate(runs)
    ]


def spans_from_points(
    times_h: Iterable[float],
    max_gap_h: float = DISCONNECT_GAP_H,
) -> list[Span]:
    """Construye tramos a partir de INSTANTES con huecos consecutivos <= ``max_gap_h``.

    Es la versión para cohortes donde la presencia se registra como
    observaciones puntuales (p. ej. CHARTEVENTS de MIMIC): una racha de
    observaciones separadas por <= ``max_gap_h`` es un tramo continuo (D1).
    Las rachas de un solo instante (duración 0) se descartan, porque un registro
    aislado no es un curso de ventilación.
    """
    ts = sorted(float(t) for t in times_h if np.isfinite(t))
    spans: list[Span] = []
    start: Optional[float] = None
    prev: Optional[float] = None
    for t in ts:
        if start is None:
            start = prev = t
        elif t - prev <= max_gap_h + _EPS:  # type: ignore[operator]
            prev = t
        else:
            if prev > start:
                spans.append(Span(start, prev))
            start = prev = t
    if start is not None and prev is not None and prev > start:
        spans.append(Span(start, prev))
    return spans


def monitor_change_boundaries(
    monitor_times_h: Iterable[float],
    patient_gap_h: float = PATIENT_GAP_H,
) -> list[float]:
    """Puntos de corte por cambio de paciente a partir de marcas de monitor (D2).

    Dado el conjunto de instantes (horas) en que se observó CUALQUIER señal de
    monitor (HR, SpO2, ECG, PLETH), devuelve el punto medio de cada hueco
    estrictamente mayor que ``patient_gap_h``. Un hueco de exactamente 1 h NO
    supone cambio de paciente (la regla es "> 1 h").
    """
    times = sorted(float(t) for t in monitor_times_h if np.isfinite(t))
    boundaries: list[float] = []
    for a, b in zip(times, times[1:]):
        if b - a > patient_gap_h + _EPS:
            boundaries.append((a + b) / 2.0)
    return boundaries


def monitor_span_boundaries(
    monitor_spans: Iterable[Span | Sequence[float]],
    patient_gap_h: float = PATIENT_GAP_H,
) -> list[float]:
    """Puntos de corte por cambio de paciente a partir de intervalos de presencia.

    A diferencia de ``monitor_change_boundaries`` (marcas de muestra), aquí la
    entrada son intervalos de presencia de monitor ya construidos. Se fusionan
    (unión) y se marca cambio de paciente cuando el hueco entre bloques es
    estrictamente mayor que ``patient_gap_h``.
    """
    blocks = merge_spans(monitor_spans, 0.0)
    boundaries: list[float] = []
    for a, b in zip(blocks, blocks[1:]):
        if b.start_h - a.end_h > patient_gap_h + _EPS:
            boundaries.append((a.end_h + b.start_h) / 2.0)
    return boundaries


def split_regions(
    regions: Iterable[Span],
    cut_points: Iterable[float],
) -> list[Span]:
    """Divide cada región por los puntos de corte que caen en su interior."""
    cuts = sorted(float(c) for c in cut_points)
    out: list[Span] = []
    for r in _as_spans(regions):
        inner = [c for c in cuts if r.start_h < c < r.end_h]
        edges = [r.start_h, *inner, r.end_h]
        for a, b in zip(edges, edges[1:]):
            if b > a:
                out.append(Span(a, b))
    return out


# ── Construcción de episodios (D1 + D2 + D4) ──────────────────────────────────

def _identity_regions(
    vent_spans: Sequence[Span],
    stay_bounds: Optional[Sequence[Sequence[float]]],
    monitor_times_h: Optional[Sequence[float]],
    monitor_spans: Optional[Sequence[Span | Sequence[float]]],
    monitor_gap_h: float,
) -> list[Span]:
    """Regiones de identidad de paciente (ningún episodio las cruza, D2)."""
    if stay_bounds is not None:
        regions = _as_spans(stay_bounds)
    else:
        # La región de identidad abarca ventilador Y monitor: el fin de la
        # observación es el fin del monitor (puede haber monitor sin ventilador
        # tras la extubación; Fase 1 corrección 1).
        extent = list(vent_spans)
        if monitor_spans is not None:
            extent += _as_spans(monitor_spans)
        if not extent:
            return []
        regions = [Span(min(s.start_h for s in extent),
                        max(s.end_h for s in extent))]

    cuts: list[float] = []
    if monitor_times_h is not None:
        cuts.extend(monitor_change_boundaries(monitor_times_h, monitor_gap_h))
    if monitor_spans is not None:
        cuts.extend(monitor_span_boundaries(monitor_spans, monitor_gap_h))
    if cuts:
        regions = split_regions(regions, cuts)
    return regions


def _no_patient_fraction(
    vent_spans: Sequence[Span],
    hr_spans: Sequence[Span],
    spo2_spans: Sequence[Span],
) -> float:
    """Fracción del tiempo ventilado sin HR ni SpO2 simultáneas (D4)."""
    vent_total = total_duration(vent_spans)
    if vent_total <= 0:
        return 0.0
    # Presencia de paciente = unión de HR y SpO2 (basta una de las dos).
    presence = merge_spans([*hr_spans, *spo2_spans], 0.0)
    covered = 0.0
    for v in merge_spans(vent_spans, 0.0):
        covered += _measure_within(v, presence)
    no_patient = max(0.0, vent_total - covered)
    return float(no_patient / vent_total)


def build_episodes(
    vent_spans: Iterable[Span | Sequence[float]],
    *,
    hr_spans: Optional[Iterable[Span | Sequence[float]]] = None,
    spo2_spans: Optional[Iterable[Span | Sequence[float]]] = None,
    monitor_times_h: Optional[Iterable[float]] = None,
    monitor_spans: Optional[Iterable[Span | Sequence[float]]] = None,
    stay_bounds: Optional[Sequence[Sequence[float]]] = None,
    disconnect_gap_h: float = DISCONNECT_GAP_H,
    monitor_gap_h: float = PATIENT_GAP_H,
    no_patient_fraction: float = NO_PATIENT_FRACTION,
) -> list[Episode]:
    """Construye los episodios de ventilación (D1/D2/D4).

    Parámetros
    ----------
    vent_spans:
        Tramos con actividad de ventilador (horas).
    hr_spans, spo2_spans:
        Intervalos de presencia de HR / SpO2 (horas). Si AMBOS son ``None`` no
        se evalúa D4 (la cohorte no aporta monitor). Si se pasan listas vacías
        se evalúa y todo el tramo queda "sin paciente".
    monitor_times_h:
        Instantes de CUALQUIER señal de monitor (HR/SpO2/ECG/PLETH) para
        detectar cambios de paciente (D2). Si es ``None`` no se usa.
    monitor_spans:
        Alternativa a ``monitor_times_h``: intervalos de presencia de monitor
        (horas). Complementario (se combinan ambos si se pasan los dos).
    stay_bounds:
        Fronteras duras de estancia (eICU/MIMIC). Si se pasan, se usan como
        regiones de identidad en lugar de derivarlas de ``monitor_times_h``.
    disconnect_gap_h, monitor_gap_h, no_patient_fraction:
        Umbrales de D1/D2/D4.

    Returns
    -------
    Lista de ``Episode`` ordenados por inicio. Los episodios excluidos por D4
    se conservan marcados (``excluded=True``), nunca se borran.
    """
    raw_vent = list(vent_spans)
    attempts = segment_attempts(raw_vent, disconnect_gap_h)

    regions = _identity_regions(
        raw_vent, stay_bounds, monitor_times_h, monitor_spans, monitor_gap_h
    )

    # Asigna cada intento (recortado a las fronteras de identidad) a su región.
    episodes: list[Episode] = []
    for region in regions:
        assigned: list[Attempt] = []
        for att in attempts:
            a = max(att.start_h, region.start_h)
            b = min(att.end_h, region.end_h)
            if b > a:
                assigned.append(Attempt(0, a, b))
        if not assigned:
            continue
        assigned.sort(key=lambda x: x.start_h)
        assigned = [
            Attempt(attempt_idx=i, start_h=x.start_h, end_h=x.end_h)
            for i, x in enumerate(assigned)
        ]
        episodes.append(Episode(
            attempts=assigned,
            region_start_h=region.start_h,
            region_end_h=region.end_h,
        ))

    episodes.sort(key=lambda e: e.start_h)

    # D4: exclusión por actividad de ventilador sin paciente.
    evaluate_d4 = hr_spans is not None or spo2_spans is not None
    if evaluate_d4:
        hr = _as_spans(hr_spans or [])
        spo2 = _as_spans(spo2_spans or [])
        for ep in episodes:
            frac = _no_patient_fraction(
                [a.span for a in ep.attempts], hr, spo2
            )
            ep.no_patient_fraction = frac
            if frac >= no_patient_fraction - _EPS:
                ep.excluded = True
                ep.exclusion_reason = "ventilator_without_patient"

    return episodes


# ── Puente con la lógica de etiquetado (D3) ───────────────────────────────────

def episode_extubation_events(
    episode: Episode,
) -> list[tuple[float, Optional[float]]]:
    """Devuelve, por intento, ``(hora_extubación, hora_reintubación|None)``.

    La extubación de un intento es su fin; la reintubación es el inicio del
    intento siguiente del mismo episodio (D1). El último intento no tiene
    reintubación conocida.
    """
    events: list[tuple[float, Optional[float]]] = []
    for i, att in enumerate(episode.attempts):
        reintub: Optional[float] = None
        if i + 1 < len(episode.attempts):
            reintub = episode.attempts[i + 1].start_h
        events.append((att.end_h, reintub))
    return events
