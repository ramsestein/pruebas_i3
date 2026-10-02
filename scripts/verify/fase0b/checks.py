"""
scripts/verify/fase0b/checks.py
================================
Funciones puras y testeables para la verificación de Fase 0b.

Reglas de honestidad (obligatorias):
  * Ninguna comprobación compara un valor con la fuente de la que se derivó.
  * No hay umbrales distintos por cohorte para forzar el verde.
  * "Disponible" nunca significa "al menos un valor no vacío": se mide la
    fracción de horas con al menos una muestra dentro del tramo del evento.
  * Cada comprobación es determinista y devuelve un dict con `passed`.

Estas funciones no leen ficheros: reciben los datos ya extraídos, de modo que
se pueden probar con fixtures sintéticos (incluidos fixtures rotos que deben
dar ROJO).
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence


# ── utilidades de distribución ────────────────────────────────────────────────

def percentile(sorted_values: Sequence[float], p: float) -> float | None:
    """Percentil lineal (interpolación nearest-rank) sobre una lista ordenada."""
    if not sorted_values:
        return None
    a = list(sorted_values)
    k = max(0, min(len(a) - 1, int(round((len(a) - 1) * p))))
    return float(a[k])


def summarize(sorted_values: Sequence[float]) -> dict:
    """Resumen {n, min, p5, median, p95, max} de una lista ordenada."""
    a = list(sorted_values)
    return {
        "n": len(a),
        "min": percentile(a, 0.0),
        "p5": percentile(a, 0.05),
        "median": percentile(a, 0.5),
        "p95": percentile(a, 0.95),
        "max": percentile(a, 1.0),
    }


# ── P1. completitud real por caso ─────────────────────────────────────────────

def check_fused_vs_source(
    fused_hours: float,
    source_span_hours: float,
    tolerance: float = 0.01,
) -> dict:
    """
    Comprueba que la duración fusionada coincide con el tramo cubierto por los
    ficheros de origen (max dtend - min dtstart) dentro de `tolerance` (1 %).

    Nota de honestidad: la referencia es la cabecera de los ficheros de origen
    (fuente independiente de la cabecera del fichero fusionado).
    """
    if source_span_hours is None or source_span_hours <= 0:
        return {
            "passed": False,
            "reason": "sin ficheros de origen o tramo de origen no positivo",
            "fused_hours": fused_hours,
            "source_span_hours": source_span_hours,
            "rel_error": None,
        }
    rel_error = abs(fused_hours - source_span_hours) / source_span_hours
    return {
        "passed": rel_error <= tolerance,
        "fused_hours": fused_hours,
        "source_span_hours": source_span_hours,
        "rel_error": rel_error,
        "tolerance": tolerance,
    }


def check_omitted_sources(
    source_names: Iterable[str],
    merged_names: Iterable[str],
) -> dict:
    """
    Detecta ficheros de origen presentes en el tramo del evento pero no
    incluidos en la fusión. Devuelve ROJO si se omitió alguno.
    """
    src = set(source_names)
    merged = set(merged_names)
    omitted = sorted(src - merged)
    return {
        "passed": not omitted,
        "n_source": len(src),
        "n_merged": len(merged),
        "omitted": omitted,
    }


def channel_hour_coverage(
    rel_hours: Sequence[float],
    t0_hours: float,
    tend_hours: float,
    bin_hours: float = 1.0,
) -> float:
    """
    Fracción de horas (bins de `bin_hours`) con al menos una muestra dentro de
    [t0_hours, tend_hours]. Sustituye al antiguo "disponible = un valor no vacío".
    """
    if tend_hours <= t0_hours or bin_hours <= 0:
        return 0.0
    n_bins = max(1, int(math.ceil((tend_hours - t0_hours) / bin_hours)))
    covered: set[int] = set()
    for h in rel_hours:
        if h < t0_hours or h > tend_hours:
            continue
        b = int((h - t0_hours) / bin_hours)
        if 0 <= b < n_bins:
            covered.add(b)
    return len(covered) / n_bins


def check_channel_coverage(
    coverage_by_channel: dict[str, float],
    zero_is_red: bool = True,
) -> dict:
    """
    Comprueba la cobertura horaria por canal. Un canal con cobertura 0 se
    reporta en ROJO (ausente en la práctica). No se usa umbral "mágico".
    """
    red = {c: round(v, 4) for c, v in coverage_by_channel.items()
           if (v == 0.0 if zero_is_red else False)}
    return {
        "passed": not red,
        "coverage": {c: round(v, 4) for c, v in coverage_by_channel.items()},
        "red_channels": red,
    }


def check_expected_channels(
    present_channels: Iterable[str],
    expected_channels: Sequence[str],
) -> dict:
    """
    La misma lista de canales esperados para todas las cohortes. ROJO si falta
    alguno. `present_channels` debe ser el conjunto de canales con cobertura > 0
    (nunca "el canal aparece como pista aunque esté vacío").
    """
    present = set(present_channels)
    expected = list(expected_channels)
    missing = [c for c in expected if c not in present]
    return {
        "passed": not missing,
        "expected": expected,
        "missing": missing,
        "present": sorted(present),
    }


# ── P2. plausibilidad: segmentación por señales (D1/D2) ──────────────────────

def _merge_intervals(intervals: Sequence[tuple[float, float]], gap_hours: float) -> list[tuple[float, float]]:
    """Fusiona intervalos separados por un hueco <= gap_hours."""
    merged: list[tuple[float, float]] = []
    for s, e in sorted(intervals):
        if not merged:
            merged.append([s, e])
            continue
        if s - merged[-1][1] <= gap_hours:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _union_intervals(
    a: Sequence[tuple[float, float]],
    b: Sequence[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Unión de dos conjuntos de intervalos (para saber si hay monitor en un hueco)."""
    all_iv = sorted(list(a) + list(b))
    out: list[tuple[float, float]] = []
    for s, e in all_iv:
        if not out:
            out.append([s, e])
            continue
        if s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def segment_events_and_attempts(
    vent_intervals: Sequence[tuple[float, float]],
    monitor_intervals: Sequence[tuple[float, float]],
    vent_merge_gap_hours: float = 2.0,
    monitor_cut_gap_hours: float = 1.0,
) -> dict:
    """
    Segmentación D1+D2 a partir de señales.

      D1  hueco de ventilador <= vent_merge_gap_hours  → mismo intento.
          hueco de ventilador >  vent_merge_gap_hours con monitor presente →
          nuevo intento (reintubación) dentro del mismo evento.
      D2  hueco de monitor > monitor_cut_gap_hours sin HR/SpO2/ECG/PLETH →
          cambio de paciente: fin de evento; ningún evento lo cruza.

    Devuelve:
      events: lista de eventos; cada uno con `attempts` = lista de (start, end)
              de ventilación de cada intento.
      n_vent_nonpatient_excluded: intervalos de ventilador sin monitor
              simultáneo >= 80 % del tramo (pulmón de test / standby).
    """
    vent = _merge_intervals(vent_intervals, gap_hours=vent_merge_gap_hours)
    mon = _merge_intervals(monitor_intervals, gap_hours=0.0)

    # Puntos de corte de paciente (D2): punto medio de cada hueco de monitor
    # mayor que monitor_cut_gap_hours. Ningún evento cruza uno de estos cortes.
    cuts: list[float] = []
    for i in range(len(mon) - 1):
        gap_start, gap_end = mon[i][1], mon[i + 1][0]
        if gap_end - gap_start > monitor_cut_gap_hours:
            cuts.append((gap_start + gap_end) / 2.0)

    events_by_region: dict[int, dict] = {}
    nonpatient = 0
    for s, e in vent:
        if _overlap_fraction(s, e, mon) < 0.20:
            # ventilador sin monitor simultáneo >= 80 % del tramo → no-paciente
            nonpatient += 1
            continue
        for ps, pe in _split_by_cuts(s, e, cuts):
            region = _region_index(ps, cuts)
            ev = events_by_region.setdefault(
                region, {"start": ps, "end": pe, "attempts": []}
            )
            ev["attempts"].append([ps, pe])
            ev["start"] = min(ev["start"], ps)
            ev["end"] = max(ev["end"], pe)

    events = [events_by_region[k] for k in sorted(events_by_region)]
    return {
        "events": events,
        "n_attempts_total": sum(len(ev["attempts"]) for ev in events),
        "n_vent_nonpatient_excluded": nonpatient,
    }


def _overlap_fraction(s: float, e: float, intervals: Sequence[tuple[float, float]]) -> float:
    if e <= s:
        return 0.0
    total = e - s
    covered = 0.0
    for a, b in intervals:
        lo, hi = max(s, a), min(e, b)
        if hi > lo:
            covered += hi - lo
    return covered / total


def _region_index(t: float, cuts: Sequence[float]) -> int:
    idx = 0
    for c in cuts:
        if t >= c:
            idx += 1
        else:
            break
    return idx


def _split_by_cuts(
    s: float,
    e: float,
    cuts: Sequence[float],
) -> list[tuple[float, float]]:
    """Divide [s, e] en las partes que no cruzan ningún punto de corte."""
    if e <= s:
        return []
    boundaries = sorted(set([s, e] + [c for c in cuts if s < c < e]))
    pieces = []
    for i in range(len(boundaries) - 1):
        a, b = boundaries[i], boundaries[i + 1]
        if b > a:
            pieces.append((a, b))
    return pieces


# ── P2. eventos que empiezan/terminan en el límite de un fichero de origen ────

def check_file_boundary_events(
    event_starts: Sequence[float],
    event_ends: Sequence[float],
    source_starts: Sequence[float],
    source_ends: Sequence[float],
    tolerance_hours: float = 0.05,
) -> dict:
    """
    Detecta eventos cuyo inicio/fin coincide (dentro de `tolerance_hours`) con
    el límite de un fichero de origen. No es un juicio clínico: solo lo reporta.
    """
    sset = set(round(x / tolerance_hours) for x in source_starts)
    eset = set(round(x / tolerance_hours) for x in source_ends)
    n_start = sum(1 for x in event_starts if round(x / tolerance_hours) in sset)
    n_end = sum(1 for x in event_ends if round(x / tolerance_hours) in eset)
    return {
        "passed": True,  # informativo: siempre pasa; se reporta el recuento
        "n_events": len(event_starts),
        "n_start_at_source_boundary": n_start,
        "n_end_at_source_boundary": n_end,
    }


# ── P8. sesgo de selección ────────────────────────────────────────────────────

def count_below_threshold(sorted_durations_hours: Sequence[float], threshold_hours: float) -> int:
    """Cuenta eventos con duración < threshold (se perderían con ese mínimo)."""
    return sum(1 for d in sorted_durations_hours if d < threshold_hours)
