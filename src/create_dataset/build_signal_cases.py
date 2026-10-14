#!/usr/bin/env python3
"""
create_dataset/build_signal_cases.py
====================================
Construye los casos (eventos de ventilación) de las cohortes con señal
(Clínic y VitalDB) a partir de los ficheros ``.vital`` DE ORIGEN.

Diferencias frente a los builders antiguos (``build_clinical_cases.py`` /
``build_vitaldb_cases.py``):

- La ventilación NO se detecta buscando cadenas en los bytes del gzip ni por
  la mera aparición de la pista: se **procesa** cada fichero con el parser de
  ``vitaldb`` y se exige **ACTIVIDAD** de las pistas de ventilador (TV_EXP /
  MV_EXP / amplitud de AWP_WAV con VENT_RR > 0; Fase 1.6e), no la presencia de
  registros (``src/common/vital_signals.py``).
- La segmentación (D1/D2/D4) usa ``src/common/episodes.py``; NO se eliminan
  episodios "que comparten inicio" (ese agrupamiento del builder antiguo
  descartaba eventos legítimos).
- NO hay duración mínima (D4) ni límite de 7 días.
- La fusión de cada evento usa ``vitaldb.VitalFile([ficheros])`` incluyendo
  todas las pistas de ventilador y NIBP, y NO reutiliza salidas previas
  (nada de "resume"): las versiones son inmutables.
- El índice guarda por evento ``t0_unix``, ``t0_source``, ``arrived_ventilated``,
  la lista de intentos, ``end_reason``, las etiquetas y los identificadores de
  origen (D12).

Uso:
    python -m src.create_dataset.build_signal_cases --cohort clinic
    python -m src.create_dataset.build_signal_cases --cohort vitaldb --no-merge
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from src.common.d5_events import (
    d5_censor_for_window,
    terminal_from_signal_loss,
    threshold_simultaneous_shutdown,
)
from src.common.eicu_levels import hourly_coverage
from src.common.end_reasons import end_reason_from_causes
from src.common.episodes import (
    Attempt,
    Episode,
    Span,
    build_episodes,
    clip_spans,
    merge_spans,
    merge_spans_ignoring_missing,
    segment_attempts,
)
from src.common.extubation import ExtubationDecision, resolve_extubation
from src.common.labels import (
    FAILURE_WINDOWS_H,
    assign_label,
    assign_labels_all_windows,
    attempts_from_pairs,
    labels_to_dict,
)
from src.common.signal_death import DeathDecision, Series, detect_signal_death
from src.common.paths import config_path, repo_root, resolve_path
from src.common.raw_mirror import resolve_raw_dir, verify_manifest_sizes
from src.common.vital_signals import (
    COVERAGE_TRACKS,
    GLOBAL_HOLE_MIN_S,
    MERGE_TRACK_NAMES,
    VitalProbe,
    active_vent_span_from_probe,
    coverage_fractions,
    hr_span_from_probe,
    merge_event_files,
    monitor_span_from_probe,
    probe_block_union,
    probe_vital_file,
    probe_vital_file_wide,
    read_coverage_series,
    read_death_series,
    spo2_span_from_probe,
    vent_span_from_probe,
)
from src.common.vni import classify_vni  # noqa: E402
from src.stage0.io.versioning import compute_config_hash, load_config
from src.common.timeutils import to_epoch_utc

logger = logging.getLogger(__name__)

_SECONDS_PER_HOUR = 3600.0

# D3 (corrección 1): un intento solo es una extubación si tras él hay al menos
# 1 h de monitor (HR o SpO2) SIN ventilador. Si no, el evento queda censurado.
MIN_EXTUBATION_MONITOR_TAIL_H: float = 1.0
_EPS: float = 1e-9

# Fase 1.6c (punto 5): de dónde sale la etiqueta en las cohortes con señal
# (los tramos y las desconexiones se derivan de las propias señales).
LABEL_SOURCE = "senal"


# ── Configuración por cohorte ────────────────────────────────────────────────

@dataclass(frozen=True)
class CohortSpec:
    """Reglas de escaneo de los ficheros de origen de una cohorte."""
    name: str
    filename_regex: re.Pattern
    box_mode: str                      # 'top_dir' | 'filename'
    box_group: Optional[str] = None    # grupo del regex si box_mode == 'filename'


CLINIC_SPEC = CohortSpec(
    name="clinic",
    # p.ej. tvpir4b4i_250414_121109.vital  -> token + fecha + hora
    filename_regex=re.compile(
        r"(?P<token>[A-Za-z0-9]+?)_(?P<date>\d{6})_(?P<time>\d{6})\.vital$",
        re.IGNORECASE,
    ),
    box_mode="top_dir",
)

VITALDB_SPEC = CohortSpec(
    name="vitaldb",
    # p.ej. SICU1_01_250101_000010.vital -> box + fecha + hora
    filename_regex=re.compile(
        r"(?P<box>SICU\d+_\d+)_(?P<date>\d{6})_(?P<time>\d{6})\.vital$",
        re.IGNORECASE,
    ),
    box_mode="filename",
    box_group="box",
)

SPECS: dict[str, CohortSpec] = {"clinic": CLINIC_SPEC, "vitaldb": VITALDB_SPEC}

# Fase 1.6e (punto 2c, D-f/D-i): union de cursos partidos por un apagado
# simultaneo y exclusion de fragmentos de registro.
# DECISION 13/10 (opcion a, `reports/fase1_6e/decision_union.md`): la union esta
# DESACTIVADA. El hueco con el que se filtraba no refleja la senal (candidatas
# con "hueco 0,00 h" y ~1 h sin FC/SpO2 justo en el corte) y unir una extubacion
# fallida con su reintubacion borraria la variable del estudio. `enabled: true`
# en `fase1_6e.union` la reactiva SOLO como analisis de sensibilidad.
UNION_ENABLED: bool = False
UNION_GAP_MAX_H: float = 2.0    # (a) hueco maximo del apagado que se une
UNION_RETURN_MIN: float = 15.0  # (c) vuelve ventilado en <= 15 min de monitor
MIN_REGION_H: float = 0.5       # (D-i) region de registro minima (30 min)

# Fase 1.6f (punto 2, D-j): apagon del GRABADOR detectado por MUESTRAS (ninguna
# pista del .vital tiene muestras en el hueco). Un apagon de <= 2 h con
# ventilacion ACTIVA en los 15 min previos y posteriores es la MISMA region: las
# horas del hueco son "blackout" (missing en todas las variables), no
# desconexion, ni extubacion, ni observacion sin ventilador. Un apagon de mas de
# 2 h corta como hasta ahora pero se marca en el indice.
BLACKOUT_MAX_H: float = 2.0
BLACKOUT_VENT_MARGIN_MIN: float = 15.0
GAP_TYPE_BLACKOUT_LONG = "blackout_long"
# Fase 1.6f (punto 3, D-k): caida de la conexion de datos del VENTILADOR (ninguna
# pista del respirador tiene muestras, ni ajustes) con el monitor presente. Una
# caida de <= 2 h es dato ausente (no corta el intento); las de mas de 2 h no
# cambian la regla: se informan por evento.
VENT_FEED_MAX_H: float = 2.0
# Tolerancia de fusion para juntar huecos de ficheros contiguos (1 min).
_HOLE_MERGE_H: float = 1.0 / 60.0
# Interruptores (config ``fase1_6f.blackout``): la sensibilidad «sin D-j/D-k»
# del punto 7 se corre con los dos en ``false``.
BLACKOUT_ENABLED: bool = True
ABSORB_VENT_DROPOUTS: bool = True


# ── Escaneo de ficheros de origen ────────────────────────────────────────────

@dataclass
class SourceFile:
    path: Path
    box: str
    token: str
    dt_unix: float


def _parse_name(path: Path, spec: CohortSpec, top_dir: str) -> Optional[tuple[str, str, float]]:
    m = spec.filename_regex.search(path.name)
    if not m:
        return None
    if spec.box_mode == "filename":
        box = m.group(spec.box_group)  # type: ignore[arg-type]
    else:
        box = top_dir
    token = m.group("token") if "token" in m.groupdict() and m.group("token") else box
    dt = datetime.strptime(m.group("date") + m.group("time"), "%y%m%d%H%M%S")
    dt_unix = to_epoch_utc(dt)
    return box, token, dt_unix


def scan_source_files(
    raw_dir: str | Path,
    spec: CohortSpec,
    *,
    exclude_dirs: Sequence[str] = ("dataset_clinic",),
) -> dict[str, list[SourceFile]]:
    """Agrupa los ficheros ``.vital`` de origen por box, ordenados por tiempo."""
    raw_dir = Path(raw_dir)
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"Directorio de origen no encontrado: {raw_dir}")

    boxes: dict[str, list[SourceFile]] = {}
    if spec.box_mode == "top_dir":
        for top in sorted(p for p in raw_dir.iterdir() if p.is_dir()):
            if top.name in exclude_dirs:
                continue
            for path in top.rglob("*.vital"):
                parsed = _parse_name(path, spec, top.name)
                if parsed is None:
                    continue
                box, token, dt_unix = parsed
                boxes.setdefault(box, []).append(SourceFile(path, box, token, dt_unix))
    else:
        for path in raw_dir.glob("*.vital"):
            parsed = _parse_name(path, spec, raw_dir.name)
            if parsed is None:
                continue
            box, token, dt_unix = parsed
            boxes.setdefault(box, []).append(SourceFile(path, box, token, dt_unix))

    for box in boxes:
        boxes[box], dropped = dedupe_homonyms(boxes[box])
        if dropped:
            logger.warning("[%s] %d ficheros homónimos descartados "
                           "(mismo box en dos carpetas, D-g): %s…",
                           box, len(dropped), ", ".join(dropped[:3]))
    for box in boxes:
        boxes[box].sort(key=lambda f: f.dt_unix)
    return boxes


def dedupe_homonyms(
    files: Sequence[SourceFile],
) -> tuple[list[SourceFile], list[str]]:
    """Quita homónimos **dentro del mismo box** (D-g), de forma determinista.

    `box14` está partido en dos carpetas (`14/` y `14_2/`) con **578 nombres
    repetidos**: son el mismo aparato grabando el mismo contenido (mismos tokens
    y mismo tamaño; el SHA-256 se comprueba en
    `scripts/verify/fase1_6e/box14_dedupe.py`). Al guardar `source_files` solo el
    nombre, la selección cogía **ambas** copias y 30 eventos listaban cada
    fichero dos veces.

    Se conserva la PRIMERA ruta en orden lexicográfico (`14/` antes que `14_2/`).
    Devuelve ``(ficheros, nombres_descartados)``.
    """
    best: dict[str, SourceFile] = {}
    dropped: list[str] = []
    for sf in sorted(files, key=lambda f: str(f.path)):
        if sf.path.name in best:
            dropped.append(sf.path.name)
        else:
            best[sf.path.name] = sf
    return [best[n] for n in sorted(best, key=lambda n: best[n].dt_unix)], dropped


# ── Segmentación de un box ───────────────────────────────────────────────────

@dataclass
class BoxSegmentation:
    """Resultado de segmentar un box en episodios (sin fusionar ficheros)."""
    box: str
    files: list[SourceFile]
    episodes: list[Episode]
    excluded_episodes: list[Episode] = field(default_factory=list)
    # Prueba de cada fichero de origen (para D5: pérdida de constantes).
    probes: dict[str, Optional[VitalProbe]] = field(default_factory=dict)
    # Horas cubiertas por ficheros ILEGIBLES ("sin dato", no "sin señal").
    missing_spans: list[Span] = field(default_factory=list)
    # Fase 1.6e (punto 3): tramos POST-extubación con firma de interfaz no
    # invasiva (VNI confirmada, D-b) y tramos sin firma verificable
    # (``posible_vni_no_verificable``, que NO se reclasifican).
    vni_spans: list[Span] = field(default_factory=list)
    unverifiable_spans: list[Span] = field(default_factory=list)
    # Fase 1.6e (punto 2c, D-f): registro de las uniones de cursos aplicadas.
    unions: list[dict] = field(default_factory=list)
    # Fase 1.6f (punto 2, D-j): apagones del grabador detectados por muestras.
    # Cada uno: ``{"start_h", "end_h", "duration_h", "type", "benign"}`` con
    # ``type`` en ``{"short", "long"}`` y ``benign`` (solo en los cortos) = hay
    # ventilación activa en los 15 min previos y posteriores.
    blackouts: list[dict] = field(default_factory=list)
    # Fase 1.6f (punto 3, D-k): caídas de la conexión de datos del ventilador
    # (ninguna pista del respirador con muestras) con el monitor presente.
    vent_drops: list[dict] = field(default_factory=list)


# Fase 1.6e (punto 3): al separar la ventilación en tramos para poder ver un
# cambio de interfaz basta con unir la contigüidad entre ficheros (minutos), no
# los huecos de desconexión: una desconexión real son >= 10 min.
VNI_SEGMENT_GAP_H: float = 5.0 / 60.0


def _monitor_in_span(
    monitor_spans: Sequence[Span], lo_h: float, hi_h: float,
) -> bool:
    """¿Hay señal de monitor (paciente) en el hueco ``[lo_h, hi_h]``?

    Sin FC/SpO2 fisiológicas la "desconexión" podría ser el fin del registro, no
    una desconexión real del circuito (ver ``vni.real_disconnect_before``).
    """
    if hi_h <= lo_h:
        return False
    return any(s.start_h < hi_h and s.end_h > lo_h for s in monitor_spans)


def _interface_signature(
    span: Span,
    files: Sequence[SourceFile],
    probes: dict[str, Optional[VitalProbe]],
) -> dict:
    """Firma de interfaz de un tramo a partir de los ficheros que lo cubren.

    ``tv_insp_frac`` = mediana de la fracción medida en los ficheros del tramo
    (``None`` si ninguno trae TV_INSP). ``mode_is_niv`` = ``False`` si algún
    fichero declara una modalidad invasiva (``SET_SIMV``); si no, ``None``.
    """
    fracs: list[float] = []
    modes: list[bool] = []
    for sf in files:
        if not _span_overlaps(
                file_span_h(sf, probes.get(str(sf.path))),
                span.start_h, span.end_h):
            continue
        probe = probes.get(str(sf.path))
        if probe is None:
            continue
        if probe.tv_insp_frac is not None:
            fracs.append(float(probe.tv_insp_frac))
        if probe.mode_is_niv is not None:
            modes.append(bool(probe.mode_is_niv))
    out: dict = {}
    if modes:
        # Solo hay modalidad no invasiva si TODOS los ficheros del tramo lo dicen.
        out["mode_is_niv"] = all(modes)
    if fracs:
        out["tv_insp_frac"] = sorted(fracs)[len(fracs) // 2]
    return out


def _split_vni_segments(
    vent_spans: list[Span],
    monitor_spans: Sequence[Span],
    files: Sequence[SourceFile],
    probes: dict[str, Optional[VitalProbe]],
) -> tuple[list[Span], list[Span], list[Span]]:
    """Separa la ventilación INVASIVA de los tramos de VNI post-extubación (D-b).

    Devuelve ``(invasivos, vni, no_verificables)``. Un tramo posterior a otro
    solo se reclasifica como VNI si hay **desconexión real** (>= 10 min sin
    actividad con monitor) y **firma de interfaz no invasiva**; sin firma el
    tramo cuenta como invasivo y se marca como no verificable (regla
    conservadora: la VNI nunca se da por supuesta).
    """
    segments = merge_spans(vent_spans, VNI_SEGMENT_GAP_H)
    invasive: list[Span] = []
    vni: list[Span] = []
    unver: list[Span] = []
    for i, seg in enumerate(segments):
        if i == 0:
            invasive.append(seg)
            continue
        prev_end_h = segments[i - 1].end_h
        sig = _interface_signature(seg, files, probes)
        dec = classify_vni(
            prev_end_min=prev_end_h * 60.0,
            next_start_min=seg.start_h * 60.0,
            monitor_present=_monitor_in_span(monitor_spans, prev_end_h,
                                             seg.start_h),
            **sig,
        )
        if dec.is_vni:
            vni.append(seg)
        elif dec.verifiable:
            invasive.append(seg)
        else:
            invasive.append(seg)
            unver.append(seg)
    return invasive, vni, unver


def _to_hours(span: Optional[Span]) -> Optional[Span]:
    if span is None:
        return None
    return Span(span.start_h / _SECONDS_PER_HOUR, span.end_h / _SECONDS_PER_HOUR)


def _subtract_spans(
    spans: Sequence[Span], holes: Sequence[Span],
) -> list[Span]:
    """``spans`` menos ``holes`` (diferencia de intervalos, en horas)."""
    out: list[Span] = []
    cut = merge_spans(holes, 0.0)
    for s in merge_spans(spans, 0.0):
        pieces: list[Span] = [s]
        for h in cut:
            nxt: list[Span] = []
            for p in pieces:
                if h.end_h <= p.start_h or h.start_h >= p.end_h:
                    nxt.append(p)
                    continue
                if h.start_h > p.start_h:
                    nxt.append(Span(p.start_h, h.start_h))
                if h.end_h < p.end_h:
                    nxt.append(Span(h.end_h, p.end_h))
            pieces = nxt
        out.extend(pieces)
    return out


def _overlaps_any(spans: Sequence[Span], lo_h: float, hi_h: float) -> bool:
    """¿Algún intervalo solapa ``[lo_h, hi_h]``?"""
    return any(s.start_h < hi_h and s.end_h > lo_h for s in spans)


def blackout_spans(
    probes: dict[str, Optional[VitalProbe]],
    *,
    kind: str = "sample",
) -> list[Span]:
    """Huecos sin muestras de un box, en horas (Fase 1.6f, puntos 2 y 3).

    ``kind``: ``"sample"`` (ninguna pista del box), ``"vent"`` (ninguna pista del
    ventilador) o ``"monitor"`` (ninguna pista de monitor). Se calcula como el
    complemento de la UNIÓN de los bloques de muestras de todos los ficheros del
    box, así que un apagón que cae justo en el borde entre dos ficheros también
    sale (ningún fichero lo vería por separado). Los extremos inicial y final no
    son huecos: solo el interior entre dos bloques.
    """
    raw = probe_block_union(probes.values(), kind)
    blocks = merge_spans(
        [Span(s / _SECONDS_PER_HOUR, e / _SECONDS_PER_HOUR) for s, e in raw],
        _HOLE_MERGE_H,
    )
    min_h = GLOBAL_HOLE_MIN_S / _SECONDS_PER_HOUR
    holes: list[Span] = []
    for a, b in zip(blocks, blocks[1:]):
        if b.start_h - a.end_h >= min_h:
            holes.append(Span(a.end_h, b.start_h))
    return holes


def _classify_blackouts(
    holes: Sequence[Span],
    vent_active: Sequence[Span],
    *,
    max_h: float = BLACKOUT_MAX_H,
    margin_min: float = BLACKOUT_VENT_MARGIN_MIN,
) -> list[dict]:
    """Clasifica los apagones (D-j) y devuelve las fichas por hueco."""
    out: list[dict] = []
    margin_h = margin_min / 60.0
    for h in holes:
        dur = h.duration_h
        if dur > max_h + _EPS:
            out.append({
                "start_h": round(h.start_h, 4), "end_h": round(h.end_h, 4),
                "duration_h": round(dur, 4), "type": "long", "benign": False,
            })
            continue
        before = _overlaps_any(vent_active, h.start_h - margin_h, h.start_h)
        after = _overlaps_any(vent_active, h.end_h, h.end_h + margin_h)
        out.append({
            "start_h": round(h.start_h, 4), "end_h": round(h.end_h, 4),
            "duration_h": round(dur, 4), "type": "short",
            "benign": bool(before and after),
        })
    return out


def _classify_vent_drops(
    drops: Sequence[Span],
    *,
    max_h: float = VENT_FEED_MAX_H,
) -> list[dict]:
    """Clasifica las caídas de la conexión del ventilador (D-k)."""
    out: list[dict] = []
    for d in drops:
        dur = d.duration_h
        out.append({
            "start_h": round(d.start_h, 4), "end_h": round(d.end_h, 4),
            "duration_h": round(dur, 4),
            "type": "short" if dur <= max_h + _EPS else "long",
            "benign": bool(dur <= max_h + _EPS),
        })
    return out


def segment_box(
    box: str,
    files: Sequence[SourceFile],
    probe_fn: Callable[[Path], Optional[VitalProbe]] = probe_vital_file_wide,
    *,
    disconnect_gap_h: float = 2.0,
    patient_gap_h: float = 1.0,
    no_patient_fraction: float = 0.8,
    unir_cursos: bool = UNION_ENABLED,
    detect_blackout: bool = BLACKOUT_ENABLED,
    absorb_vent_dropouts: bool = ABSORB_VENT_DROPOUTS,
) -> BoxSegmentation:
    """Segmenta un box en episodios aplicando D1/D2/D4 sobre las señales."""
    vent_spans: list[Span] = []
    monitor_spans: list[Span] = []
    hr_spans: list[Span] = []
    spo2_spans: list[Span] = []
    probes: dict[str, Optional[VitalProbe]] = {}
    missing_spans: list[Span] = []
    unreadable: list[SourceFile] = []

    for sf in files:
        probe = probe_fn(sf.path)
        probes[str(sf.path)] = probe
        if probe is None:
            # Fichero ILEGIBLE: es "sin dato", no "sin señal". Sus horas no
            # deben crear huecos de ventilador (D1) ni cortes de paciente (D2).
            unreadable.append(sf)
            continue
        # Fase 1.6e (punto 2): la ventilación se mide con ACTIVIDAD real
        # (TV_EXP / MV_EXP / amplitud de AWP_WAV con VENT_RR > 0), no con la
        # mera presencia de registros: con el respirador en espera FiO2/PEEP y
        # las ondas siguen registrándose y ocultarían la extubación.
        v = _to_hours(active_vent_span_from_probe(probe))
        m = _to_hours(monitor_span_from_probe(probe))
        h = _to_hours(hr_span_from_probe(probe))
        s = _to_hours(spo2_span_from_probe(probe))
        if v is not None:
            vent_spans.append(v)
        if m is not None:
            monitor_spans.append(m)
        if h is not None:
            hr_spans.append(h)
        if s is not None:
            spo2_spans.append(s)

    # Fase 1.6f (puntos 2 y 3, D-j/D-k): apagones del grabador y caídas de la
    # conexión de datos del ventilador, medidos por MUESTRAS dentro de los
    # ficheros. NO se usan los huecos sin ningún fichero: ese es el caso de la
    # unión de cursos (D-f/opción a, desactivada).
    holes = blackout_spans(probes, kind="sample") if detect_blackout else []
    vent_holes = blackout_spans(probes, kind="vent") if absorb_vent_dropouts else []
    monitor_holes = (
        blackout_spans(probes, kind="monitor")
        if (detect_blackout or absorb_vent_dropouts) else []
    )
    vent_active = merge_spans(
        [sp for sp in (_to_hours(active_vent_span_from_probe(p))
                       for p in probes.values() if p is not None)
         if sp is not None], _HOLE_MERGE_H)
    blackouts = _classify_blackouts(holes, vent_active) if detect_blackout else []
    # D-k: la conexión del ventilador se cayó si NINGUNA pista del respirador
    # tiene muestras y el monitor SÍ está presente. Un respirador en espera deja
    # los ajustes registrados (TV ≈ 0), así que conserva muestras y NO es una
    # caída de la conexión: se distingue por la presencia, no por el valor.
    vent_drops = (_classify_vent_drops(_subtract_spans(vent_holes, monitor_holes))
                  if absorb_vent_dropouts else [])
    benign_blackouts = [Span(b["start_h"], b["end_h"])
                        for b in blackouts if b["benign"]]
    benign_drops = [Span(d["start_h"], d["end_h"])
                    for d in vent_drops if d["benign"]]

    # Ficheros ilegibles (B2 residual): su intervalo REAL se desconoce y la hora
    # del nombre no es fiable en Clínic (grabaciones de 19-48 h cuyo nombre va
    # 1-2 h por detrás del contenido), así que "nombre + 1 h" puede quedarse
    # corto y cortar el episodio en dos. El tramo va del fin del fichero legible
    # anterior al inicio del siguiente: que EXISTA un fichero entre ambos es
    # prueba de que el grabador seguía escribiendo, o sea "sin dato, no sin
    # señal". Los huecos de verdad (sin ningún fichero) no pasan por aquí: solo
    # los crea la ausencia total de ficheros.
    readable = sorted(
        (sf for sf in files if probes.get(str(sf.path)) is not None),
        key=lambda f: file_span_h(f, probes[str(f.path)]).start_h,
    )
    spans_reales = {str(sf.path): file_span_h(sf, probes[str(sf.path)])
                    for sf in readable}
    for sf in unreadable:
        guess = file_span_h(sf)
        prev_end: Optional[float] = None
        next_start: Optional[float] = None
        for other in readable:
            span = spans_reales[str(other.path)]
            if span.start_h <= guess.start_h:
                prev_end = span.end_h if prev_end is None else max(prev_end,
                                                                   span.end_h)
            if span.start_h >= guess.start_h and next_start is None:
                next_start = span.start_h
        lo = guess.start_h if prev_end is None else min(prev_end, guess.start_h)
        hi = guess.end_h if next_start is None else max(next_start, guess.end_h)
        missing_spans.append(Span(lo, hi))

    # Horas "sin dato" de la segmentación. D-j (apagón del grabador) afecta a
    # TODAS las señales: el box no grababa nada, así que el hueco no puede crear
    # ni un fin de ventilación (D1) ni un corte de paciente (D2). D-k (caída de
    # la conexión de datos del ventilador) afecta SOLO a la ventilación: el
    # monitor sigue grabando y la identidad del paciente no está en duda.
    absent_general = [*missing_spans, *benign_blackouts]
    absent_vent = [*absent_general, *benign_drops]

    # Puentear los huecos causados por ficheros ilegibles y por los apagones/
    # caídas benignos (Fase 1.6f) antes de segmentar: un fin de ventilación solo
    # es real si el hueco NO está cubierto por horas "sin dato".
    # Fase 1.6e (punto 3): antes de formar intentos se separan los tramos de VNI
    # post-extubación (D-b): una VNI con mascarilla es estar EXTUBADO, así que su
    # tramo no cuenta como ventilación invasiva ni como fallo, y dejar de
    # eliminarlo es lo que permite detectar la reintubación posterior.
    # Fase 1.6f (D-k): la absorción de caídas de la conexión del ventilador se
    # aplica a la formación de INTENTOS (D1), no a la separación por interfaz
    # (D-b): allí un hueco de datos del respirador con monitor presente es
    # justamente la evidencia de la desconexión que se quiere clasificar.
    vent_span_all = merge_spans_ignoring_missing(
        vent_spans, missing_spans, VNI_SEGMENT_GAP_H
    )
    monitor_span_all = merge_spans_ignoring_missing(
        monitor_spans, absent_general, patient_gap_h
    )
    vent_spans, vni_spans, unver_spans = _split_vni_segments(
        list(vent_span_all), list(monitor_span_all), files, probes
    )
    vent_spans = merge_spans_ignoring_missing(
        vent_spans, absent_vent, disconnect_gap_h
    )
    monitor_spans = monitor_span_all
    hr_spans = merge_spans_ignoring_missing(
        hr_spans, absent_general, patient_gap_h)
    spo2_spans = merge_spans_ignoring_missing(
        spo2_spans, absent_general, patient_gap_h)

    episodes = build_episodes(
        vent_spans,
        hr_spans=hr_spans,
        spo2_spans=spo2_spans,
        monitor_spans=monitor_spans,
        stay_bounds=None,
        disconnect_gap_h=disconnect_gap_h,
        monitor_gap_h=patient_gap_h,
        no_patient_fraction=no_patient_fraction,
    )

    # Fase 1.6e (punto 2c, D-f): la union de cursos partidos por un apagado
    # simultaneo esta DESACTIVADA por decision del 13/10 (opcion a): el hueco
    # con el que se filtraba no refleja la senal y unir una extubacion fallida
    # con su reintubacion borraria la variable del estudio. `join_courses`
    # sigue disponible (`unir_cursos=True`) como analisis de sensibilidad.
    if unir_cursos:
        episodes, uniones = join_courses(episodes, files, probes, monitor_spans)
    else:
        uniones = []
    # D-i: los fragmentos de registro que queden (no absorbidos por la union) se
    # conservan marcados, nunca se borran.
    for ep in episodes:
        if not ep.excluded and _fragmento_de_registro(ep):
            ep.excluded = True
            ep.exclusion_reason = "fragmento_registro"

    kept = [e for e in episodes if not e.excluded]
    excluded = [e for e in episodes if e.excluded]
    return BoxSegmentation(
        box=box, files=list(files), episodes=kept, excluded_episodes=excluded,
        probes=probes, missing_spans=merge_spans(missing_spans, 0.0),
        vni_spans=vni_spans, unverifiable_spans=unver_spans, unions=uniones,
        blackouts=blackouts, vent_drops=vent_drops,
    )


# ── Materialización de eventos (fusión + índice) ─────────────────────────────

def _files_in_span(
    files: Sequence[SourceFile],
    probes: Optional[dict[str, Optional[VitalProbe]]],
    lo_h: float,
    hi_h: float,
) -> list[SourceFile]:
    """Ficheros cuyo intervalo REAL solapa con ``[lo_h, hi_h)`` (B2)."""
    out: list[SourceFile] = []
    for sf in files:
        pr = (probes or {}).get(str(sf.path))
        if _span_overlaps(file_span_h(sf, pr), lo_h, hi_h):
            out.append(sf)
    return out


def _monitor_window(
    monitor_spans: Sequence[Span], region_start_h: float, region_end_h: float,
) -> Optional[tuple[float, float]]:
    """Tramo de MONITOR de una región: (inicio del primer tramo, fin del último).

    Las regiones de identidad (D2) reparten el tiempo sin monitor entre las dos
    regiones vecinas (corte a mitad del hueco), así que el fin de la región NO es
    el fin del monitor. Para medir el apagado simultáneo hace falta el monitor.
    """
    inside = clip_spans(monitor_spans, region_start_h, region_end_h)
    if not inside:
        return None
    return (min(s.start_h for s in inside), max(s.end_h for s in inside))


def join_courses(
    episodes: Sequence[Episode],
    files: Sequence[SourceFile],
    probes: Optional[dict[str, Optional[VitalProbe]]] = None,
    monitor_spans: Sequence[Span] = (),
    *,
    gap_max_h: float = UNION_GAP_MAX_H,
    return_min: float = UNION_RETURN_MIN,
) -> tuple[list[Episode], list[dict]]:
    """D-f: une cursos partidos por un apagado simultáneo, SOLO donde está justificado.

    Se une el evento *i* con el siguiente del mismo box si TODO se cumple:

    - **(a)** el hueco entre el fin del MONITOR de *i* y el inicio del monitor de
      la región siguiente es ``<= gap_max_h`` (2 h);
    - **(b)** en ese hueco **no hay ningún fichero** (el grabador estaba parado:
      es "sin dato", no un fin de observación);
    - **(c)** la región siguiente **empieza ventilada** (actividad en
      ``<= return_min`` min de su primer tramo de monitor).

    La continuidad de ajustes (|dPEEP| <= 3 y |dFiO2| <= 20) **NO** se usa: el
    control negativo del punto 2c.3 dio 59,3 % de falsos positivos con pacientes
    distintos (PEEP y FiO2 se repiten), así que se descarta como criterio.

    El curso unido conserva los dos intentos (con sus tiempos relativos al t0
    común) y marca el hueco en ``unknown_gaps``: horas "fuera del box" en las que
    no se observó ni ventilación ni extubación, así que no cuentan como
    desconexión ni como reintubación.

    Devuelve la lista de episodios (unida) y el registro de las uniones.
    """
    orden = sorted((e for e in episodes), key=lambda e: e.region_start_h)
    if len(orden) < 2:
        return list(episodes), []
    out: list[Episode] = []
    uniones: list[dict] = []
    i = 0
    while i < len(orden):
        cur = orden[i]
        if i + 1 < len(orden):
            nxt = orden[i + 1]
            w_cur = _monitor_window(monitor_spans, cur.region_start_h,
                                    cur.region_end_h)
            w_nxt = _monitor_window(monitor_spans, nxt.region_start_h,
                                    nxt.region_end_h)
            mon_end = w_cur[1] if w_cur else _region_end_h(cur)
            mon_start = w_nxt[0] if w_nxt else float(nxt.region_start_h)
            gap_h = mon_start - mon_end
            en_hueco = _files_in_span(files, probes, mon_end, mon_start)
            vuelve_ventilado = (
                nxt.attempts[0].start_h - mon_start <= return_min / 60.0 + _EPS
            )
            if (not cur.excluded and not nxt.excluded
                    and -_EPS <= gap_h <= gap_max_h + _EPS
                    and not en_hueco and vuelve_ventilado):
                atts = [Attempt(idx, a.start_h, a.end_h)
                        for idx, a in enumerate(list(cur.attempts)
                                               + list(nxt.attempts))]
                fracs = [(cur.no_patient_fraction, cur.ventilated_hours),
                         (nxt.no_patient_fraction, nxt.ventilated_hours)]
                num = sum(f * h for f, h in fracs if f is not None)
                den = sum(h for f, h in fracs if f is not None)
                hueco = ([Span(mon_end, mon_start)] if gap_h > _EPS else [])
                cur = Episode(
                    attempts=atts,
                    region_start_h=cur.region_start_h,
                    region_end_h=nxt.region_end_h,
                    observation_end_h=nxt.observation_end_h,
                    no_patient_fraction=(num / den if den else None),
                    unknown_gaps=list(cur.unknown_gaps) + hueco
                    + list(nxt.unknown_gaps),
                )
                uniones.append({
                    "gap_h": round(gap_h, 4),
                    "monitor_end_h": round(mon_end, 4),
                    "monitor_start_h": round(mon_start, 4),
                    "n_files_en_hueco": 0,
                    "vuelve_ventilado": True,
                })
                i += 2
                out.append(cur)
                continue
        out.append(cur)
        i += 1
    return out, uniones


def _fragmento_de_registro(
    episode: Episode, *, min_region_h: float = MIN_REGION_H,
) -> bool:
    """D-i: marca (no borra) los eventos cuyo REGISTRO es un fragmento.

    Se mide la región de identidad (``region_start_h`` -> fin del monitor), no la
    duración de la ventilación: una ventilación corta dentro de una región larga
    es un evento legítimo (p.ej. intubación cerca del final del box), mientras
    que una región de minutos es un trozo de registro. **No** es una duración
    mínima objetivo: es integridad del registro.
    """
    return (_region_end_h(episode) - float(episode.region_start_h)) < min_region_h


def _region_end_h(episode: Episode) -> float:
    """Fin de la observación = fin del MONITOR de la región de paciente (D2)."""
    if math.isfinite(episode.region_end_h):
        return float(episode.region_end_h)
    return float(episode.end_h)


def observation_end_h(episode: Episode) -> float:
    """Fin de la observación **sin ventilador** con señal fisiológica (Fase 1.6b).

    Solo cuentan FC/SpO2 con valores fisiológicos: si tras la desconexión el box
    sigue grabando ondas planas, no hay observación válida y el fin es el de la
    propia desconexión (→ el evento se censura como ``end_of_record``).

    **Devuelve horas ABSOLUTAS** (epoch/3600) en cualquiera de sus dos ramas
    (``episode.observation_end_h`` y ``_region_end_h``); todo lo que va a las
    etiquetas se pasa a relativo restando ``episode.start_h``.
    """
    if episode.observation_end_h is not None:
        return float(episode.observation_end_h)
    return _region_end_h(episode)


def monitor_tail_h(episode: Episode) -> float:
    """Horas de observación fisiológica sin ventilador tras el último intento."""
    return float(observation_end_h(episode) - episode.attempts[-1].end_h)


def is_confirmed_extubation(episode: Episode) -> bool:
    """D3: la última desconexión es extubación si hay >= 1 h de monitor sin VM."""
    return extubation_decision(episode).is_extubation


def extubation_decision(episode: Episode) -> ExtubationDecision:
    """Regla común de extubación confirmada (Fase 1.5, punto 0).

    En Clínic/VitalDB la observación es el MONITOR de la región de paciente y
    no existe frontera de estancia (``stay_end_h=None``): si no hay la hora de
    monitor sin ventilador, la censura es ``end_of_record`` (o ``death_at_vent``
    si la detección de muerte por señales la confirma, que se aplica aparte).

    **Unidades (B1):** los episodios trabajan en horas ABSOLUTAS (epoch/3600) y
    el registro las pasa a relativas; aquí se pasa a ``resolve_extubation`` ya
    RELATIVO al inicio del episodio, porque devuelve ``censor_time_h`` derivado
    de sus entradas y ese valor va directo a las etiquetas. Antes se le colaba
    el valor absoluto (``censor_time_h`` ≈ 486 000 h en Clínic/VitalDB, mientras
    MIMIC y eICU lo tenían relativo).
    """
    t0_h = float(episode.start_h)
    return resolve_extubation(
        last_vent_end_h=float(episode.attempts[-1].end_h) - t0_h,
        observation_end_h=float(observation_end_h(episode)) - t0_h,
        stay_end_h=None,
    )


def _files_for_episode(
    files: Sequence[SourceFile],
    episode: Episode,
    *,
    probes: dict[str, Optional[VitalProbe]] | None = None,
) -> list[SourceFile]:
    """Ficheros desde t0 hasta el fin del MONITOR de la región (corrección 1).

    No solo los que solapan con ventilación: el monitor posterior a la
    desconexión es necesario para confirmar la extubación y para D5.

    **Selección por solape de intervalos REALES** (B2): usa ``dtstart``/``dtend``
    de la sonda y solo cae a la hora del NOMBRE + 1 h cuando el fichero no tiene
    sonda (ilegible). En Clínic los ficheros no son horarios y su nombre va 1-2 h
    por detrás del contenido, así que la suposición antigua perdía ficheros que
    sí cubren el episodio. Respaldo: solape con cada intento.
    """
    start_h = episode.start_h
    end_h = _region_end_h(episode)
    out: list[SourceFile] = []
    for sf in files:
        pr = (probes or {}).get(str(sf.path))
        if _span_overlaps(file_span_h(sf, pr), start_h, end_h):
            out.append(sf)
    if out:
        return out
    # Respaldo por intentos (mismo criterio de solape real).
    for att in episode.attempts:
        for sf in files:
            pr = (probes or {}).get(str(sf.path))
            if _span_overlaps(file_span_h(sf, pr), att.start_h, att.end_h):
                out.append(sf)
    return out


def file_span_h(
    sf: SourceFile, probe: Optional[VitalProbe] = None,
) -> Span:
    """Intervalo **REAL** de un fichero de origen, en horas.

    Usa la cabecera (``probe.dtstart``/``dtend``) cuando se conoce; si no (fichero
    ilegible o sin sonda) cae a la hora del NOMBRE con 1 h de duración, que es la
    suposición antigua.

    **Por qué (B2):** en Clínic los ficheros de origen no son horarios (hay
    grabaciones de 19-48 h) y la hora del nombre va 1-2 h por detrás del inicio
    real del contenido; suponer 1 h de duración descartaba ficheros que sí cubren
    el episodio (ventanas «vacías» y ventanas de análisis sin datos).
    """
    if probe is not None and float(probe.dtend) > float(probe.dtstart):
        return Span(float(probe.dtstart) / _SECONDS_PER_HOUR,
                    float(probe.dtend) / _SECONDS_PER_HOUR)
    f_start = sf.dt_unix / _SECONDS_PER_HOUR
    return Span(f_start, f_start + 1.0)


def _span_overlaps(span: Span, lo_h: float, hi_h: float) -> bool:
    """¿Solapa ``span`` con ``[lo_h, hi_h]``? (intervalos semiabiertos)"""
    return span.start_h < hi_h and span.end_h > lo_h


def _arrived_ventilated(
    episode: Episode,
    files: Sequence[SourceFile],
    probes: dict[str, Optional[VitalProbe]] | None = None,
) -> bool:
    """D10: el paciente ya estaba intubado al empezar el registro del box."""
    if not files:
        return False
    pr = (probes or {}).get(str(files[0].path))
    first_file_start = file_span_h(files[0], pr).start_h
    return abs(episode.start_h - first_file_start) < (1.0 / 60.0)


def _end_reason(episode: Episode) -> str:
    """Motivo de fin del evento, calculado contra el fin del MONITOR (D3)."""
    if is_confirmed_extubation(episode):
        return "extubation_observed"
    return "end_of_record"


def signal_loss_at_end(
    files: Sequence[SourceFile],
    probes: dict[str, Optional[VitalProbe]],
    episode: Episode,
) -> bool:
    """(QC) el último fichero del evento pierde HR y SpO2.

    Se conserva como indicador de calidad; la censura por muerte la decide
    ``detect_death_for_episode`` (ajuste 2).
    """
    ep_files = _files_for_episode(files, episode, probes=probes)
    if not ep_files:
        return False
    last = probes.get(str(ep_files[-1].path))
    if last is None:
        return False
    return (hr_span_from_probe(last) is None) and (spo2_span_from_probe(last) is None)


DEATH_TAIL_FILES = 4


def detect_death_for_episode(
    files: Sequence[SourceFile],
    episode: Episode,
    *,
    tail_files: int = DEATH_TAIL_FILES,
) -> tuple[DeathDecision, dict[str, list[tuple[float, float]]]]:
    """Detección de muerte por señales en el final del monitor del evento.

    Lee solo los últimos ``tail_files`` ficheros de la región (la ventana de
    detección es de minutos) y aplica ``detect_signal_death``.
    """
    ep_files = _files_for_episode(files, episode)
    if not ep_files:
        return DeathDecision(False, None, "no_files"), {}
    tail = ep_files[-tail_files:]
    raw = read_death_series(
        [f.path for f in tail], t0_unix=episode.start_h * _SECONDS_PER_HOUR
    )
    decision = detect_signal_death(
        Series.of(raw.get("HR", [])),
        spo2=Series.of(raw.get("SpO2", [])),
        map_=Series.of(raw.get("MAP", [])),
        amp_abp=Series.of(raw.get("ABP_amp", [])),
        amp_ppg=Series.of(raw.get("PPG_amp", [])),
    )
    return decision, raw


def simultaneous_shutdown(episode: Episode) -> bool:
    """D5: ventilador y monitor se apagan a la vez (<= 15 min). Se reporta."""
    return threshold_simultaneous_shutdown(
        episode.attempts[-1].end_h, _region_end_h(episode)
    )


def classify_vitaldb_event(
    episode: Episode,
    missing_spans: Sequence[Span],
    *,
    min_tail_h: float = MIN_EXTUBATION_MONITOR_TAIL_H,
) -> tuple[str, list[float]]:
    """Nivel A/B/D de un evento según las horas perdidas (ficheros ilegibles).

    - **A**: sin horas perdidas dentro del evento (etiqueta fiable).
    - **B**: horas perdidas que NO tocan la ventilación ni la hora posterior a
      la última desconexión (la etiqueta no depende de ellas).
    - **D**: horas perdidas dentro de la ventilación o en la hora posterior a la
      última desconexión (la etiqueta SÍ depende de ellas).
    """
    start = episode.start_h
    region_end = _region_end_h(episode)
    last_disconn = episode.attempts[-1].end_h
    label_end = last_disconn + min_tail_h
    missing = merge_spans(missing_spans, 0.0)
    in_event = [m for m in missing if m.end_h > start and m.start_h < region_end]
    if not in_event:
        return "A", []
    in_label = [m for m in in_event if m.end_h > start and m.start_h < label_end]
    hours = [round(m.start_h - start, 4) for m in in_event]
    return ("D" if in_label else "B"), hours


def _pairs_para_etiquetar(episode: Episode) -> list[tuple[float, float | None]]:
    """Intentos para ETIQUETAR (D3), en horas relativas a t0.

    D-f: los intentos separados por un hueco **sin datos** (``unknown_gaps``) se
    fusionan en uno solo, porque en esas horas no se observó ni ventilación ni
    extubación: no son una desconexión (rule 0) ni una reintubación (D13). Los
    intentos del índice se conservan tal cual; esto es solo la vista de cálculo.
    """
    t0 = float(episode.start_h)
    huecos = list(episode.unknown_gaps)

    def _en_hueco(lo: float, hi: float) -> bool:
        return any(g.start_h <= lo + _EPS and g.end_h >= hi - _EPS
                   for g in huecos)

    merged: list[list[float]] = []
    for a in episode.attempts:
        if merged and _en_hueco(merged[-1][1], float(a.start_h)):
            merged[-1][1] = float(a.end_h)
        else:
            merged.append([float(a.start_h), float(a.end_h)])
    return [
        (round(e - t0, 4),
         None if i + 1 >= len(merged) else round(merged[i + 1][0] - t0, 4))
        for i, (_, e) in enumerate(merged)
    ]


def build_event_record(
    *,
    cohort: str,
    box: str,
    episode: Episode,
    files: Sequence[SourceFile],
    t0_unix: float,
    signal_loss: bool = False,
    death: Optional[DeathDecision] = None,
    missing_spans: Sequence[Span] = (),
    vni_spans: Sequence[Span] = (),
    unverifiable_spans: Sequence[Span] = (),
    blackouts: Sequence[dict] = (),
    vent_drops: Sequence[dict] = (),
) -> dict:
    """Ficha de un evento lista para el índice JSON (D12)."""
    attempts = [
        {
            "attempt_idx": a.attempt_idx,
            "vent_start_h": round(a.start_h - episode.start_h, 4),
            "vent_end_h": round(a.end_h - episode.start_h, 4),
            "reintubation_h": (
                round(episode.attempts[i + 1].start_h - episode.start_h, 4)
                if i + 1 < len(episode.attempts) else None
            ),
        }
        for i, a in enumerate(episode.attempts)
    ]

    # Etiquetas D3 (Fase 1.6) a partir de la lista de intentos.
    # El fin de la observación es el fin del MONITOR de la región (corrección 1);
    # si la última desconexión no va seguida de >= 1 h de monitor sin ventilador,
    # NO es una extubación confirmada → el evento queda censurado (end_of_record).
    obs_end_h = observation_end_h(episode) - episode.start_h
    tail_h = monitor_tail_h(episode)
    last_disconnect_h = episode.attempts[-1].end_h - episode.start_h
    ext_dec = extubation_decision(episode)
    confirmed = ext_dec.is_extubation
    # Si la última desconexión NO está confirmada (sin >= 1 h de monitor sin
    # ventilador) no es una extubación: se excluye del etiquetado (así una
    # posible extubación anterior consolidada sigue ganando).
    pairs = _pairs_para_etiquetar(episode)
    label_pairs = pairs if confirmed else pairs[:-1]

    died = bool(death is not None and death.is_death)
    died_ventilated = bool(
        died and death.death_time_h is not None
        and death.death_time_h <= last_disconnect_h + _EPS
    )
    labels: dict[str, dict] = {}
    d5_by_window: dict[str, dict] = {}
    for w in FAILURE_WINDOWS_H:
        if died:
            # La muerte detectada por señales se usa EXACTAMENTE como DEATHTIME.
            dd = d5_censor_for_window(
                failure_window_h=float(w),
                last_disconnect_h=last_disconnect_h,
                death_time_h=death.death_time_h,
                died_ventilated=died_ventilated,
            )
            cause, t_censor = dd.censor_cause, dd.censor_time_h
        elif not confirmed:
            cause, t_censor = ext_dec.censor_cause, ext_dec.censor_time_h
        else:
            cause, t_censor = None, None
        lab = assign_label(
            attempts_from_pairs(label_pairs), obs_end_h=obs_end_h,
            failure_window_h=float(w), censor_cause=cause, censor_time_h=t_censor,
        )
        labels[f"{int(w)}h"] = lab
        d5_by_window[f"{int(w)}h"] = {
            "censor_cause": cause, "censor_time_h": t_censor,
        }
    labels_dict = labels_to_dict(labels)

    # Fase 1.5, punto 2: horas perdidas por ficheros ilegibles ("sin dato").
    level, missing_hours = classify_vitaldb_event(episode, missing_spans)

    # Fase 1.6e (punto 3): VNI post-extubación (D-b). El tramo de VNI que empieza
    # tras el fin de la última ventilación INVASIVA se guarda aparte: el paciente
    # está extubado (no es fallo) y solo una reintubación invasiva posterior lo
    # sería.
    last_invasive_end_h = (episode.attempts[-1].end_h
                           if episode.attempts else None)
    region_end_h = _region_end_h(episode)
    vni_post = [s for s in vni_spans
                if last_invasive_end_h is not None
                and s.end_h > last_invasive_end_h + _EPS
                and episode.start_h <= s.start_h < region_end_h]
    vni_start = vni_end = None
    if vni_post:
        vni_start = round(min(s.start_h for s in vni_post)
                          - episode.start_h, 4)
        vni_end = round(max(s.end_h for s in vni_post)
                        - episode.start_h, 4)

    # Fase 1.6f (puntos 2 y 3): apagones del grabador (D-j) y caídas de la
    # conexión de datos del ventilador (D-k) que tocan la región del evento.
    reg_lo, reg_hi = float(episode.region_start_h), region_end_h
    ev_blackouts = [b for b in blackouts
                    if b["start_h"] < reg_hi and b["end_h"] > reg_lo]
    ev_drops = [d for d in vent_drops
                if d["start_h"] < reg_hi and d["end_h"] > reg_lo]

    return {
        "event_id": "",  # se rellena en el orquestador
        "cohort": cohort,
        "label_source": LABEL_SOURCE,
        "box": box,
        "source_files": [f.path.name for f in files],
        "source_tokens": sorted({f.token for f in files}),
        "t0_unix": t0_unix,
        "t0_source": (
            "already_ventilated_at_record_start" if _arrived_ventilated(episode, files)
            else "vent_start_observed"
        ),
        "arrived_ventilated": _arrived_ventilated(episode, files),
        "duration_seconds": int(round(episode.duration_h * _SECONDS_PER_HOUR)),
        "obs_end_h": round(obs_end_h, 4),
        "monitor_tail_h": round(tail_h, 4),
        "n_attempts": episode.n_attempts,
        "attempts": attempts,
        "vni_post_ext_h_start": vni_start,
        "vni_post_ext_h_end": vni_end,
        "n_vni_segments": len(vni_post),
        "n_vni_unverifiable_segments": sum(
            1 for s in unverifiable_spans
            if episode.start_h <= s.start_h < region_end_h
        ),
        "extubation_rule": ext_dec.reason,
        "end_reason": end_reason_from_causes(
            (["death_at_vent"] if died else []),
            is_extubation=ext_dec.is_extubation,
            fallback=ext_dec.censor_cause or "end_of_record",
        ),
        "d5": d5_by_window,
        "death_signal": {
            "detected": died,
            "time_h": (death.death_time_h if died else None),
            "reason": (death.reason if death is not None else None),
            "detail": (death.detail if death is not None else {}),
            "died_ventilated": died_ventilated,
        },
        "signal_loss_at_end": bool(signal_loss),
        "simultaneous_shutdown": bool(simultaneous_shutdown(episode)),
        # Fase 1.6e (punto 2c, D-f): cursos unidos por un apagado simultáneo.
        # El hueco queda como horas "fuera del box" sin ningún dato.
        "n_unions": len(episode.unknown_gaps),
        "n_label_attempts": len(pairs),
        "fuera_del_box_h": round(
            sum(g.duration_h for g in episode.unknown_gaps), 4
        ),
        "fuera_del_box": [
            [round(g.start_h - episode.start_h, 4),
             round(g.end_h - episode.start_h, 4)]
            for g in episode.unknown_gaps
        ],
        "has_missing_files": bool(missing_hours),
        "missing_hours": missing_hours,
        # Fase 1.6f (punto 2, D-j): horas de apagón del grabador dentro de la
        # región del evento (relative a t0) y su tipo.
        "blackout_gaps": [
            [round(b["start_h"] - episode.start_h, 4),
             round(b["end_h"] - episode.start_h, 4)]
            for b in ev_blackouts
        ],
        "blackout_details": ev_blackouts,
        "n_blackout_h": round(
            sum(b["duration_h"] for b in ev_blackouts), 4),
        "gap_type": (GAP_TYPE_BLACKOUT_LONG
                     if any(b["type"] == "long" for b in ev_blackouts)
                     else None),
        # Fase 1.6f (punto 3, D-k): caídas de la conexión de datos del
        # ventilador (ninguna pista del respirador con muestras, monitor
        # presente). Las de <= 2 h ya no cortan el intento.
        "vent_feed_drops": [
            [round(d["start_h"] - episode.start_h, 4),
             round(d["end_h"] - episode.start_h, 4)]
            for d in ev_drops
        ],
        "vent_feed_drop_details": ev_drops,
        "n_vent_feed_dropout_h": round(
            sum(d["duration_h"] for d in ev_drops), 4),
        "level": level,
        "ventilated_hours": round(episode.ventilated_hours, 4),
        "labels": labels_dict,   # Fase 1.6 (D3) + D5       # Fase 1.6 (D3)
        "trach": None,         # Fase 1.5 (D5)
        "terminal": None,      # Fase 1.5 (D5)
        "file": None,          # se rellena al fusionar
    }


def _downsample_series(
    pairs: Sequence[tuple[float, float]], step_min: float = 1.0
) -> list[list[float]]:
    """Reduce una serie a 1 punto por minuto (para el JSON/PNG de muertes)."""
    out: list[list[float]] = []
    last_t: Optional[float] = None
    for t, v in pairs:
        if last_t is None or (t - last_t) * 60.0 >= step_min - 1e-9:
            out.append([round(float(t), 5), round(float(v), 3)])
            last_t = t
    return out


def _death_record(rec: dict, episode: Episode,
                  raw: dict[str, list[tuple[float, float]]]) -> dict:
    """Ficha de una muerte detectada por señales (para el PNG de revisión)."""
    return {
        "event_id": rec["event_id"],
        "cohort": rec["cohort"],
        "box": rec["box"],
        "death_time_h": rec["death_signal"]["time_h"],
        "reason": rec["death_signal"]["reason"],
        "died_ventilated": rec["death_signal"]["died_ventilated"],
        "d5": rec["d5"],
        "attempts": rec["attempts"],
        "region_end_h": round(_region_end_h(episode) - episode.start_h, 4),
        "series": {k: _downsample_series(v) for k, v in raw.items()},
    }


def build_cohort_index(
    results: Sequence[tuple[str, BoxSegmentation]],
    *,
    cohort: str,
    spec: CohortSpec,
    merged: bool,
    coverage: bool = False,
) -> dict:
    """Construye el índice JSON completo de una cohorte.

    ``coverage=True`` mide, por evento, la fracción de horas ventiladas con
    valor útil (LOCF 4 h) de las variables obligatorias (punto 2).
    """
    events: list[dict] = []
    excluded_events: list[dict] = []
    deaths: list[dict] = []
    n_missing_files = 0
    levels: dict[str, int] = {"A": 0, "B": 0, "D": 0}
    vars_ok_50 = vars_ok_80 = 0
    n_covered = 0
    n_standby_min = 0
    n_vni_segments = 0
    n_unver_segments = 0
    n_unions = 0
    for box, seg in results:
        n_missing_files += sum(1 for p in seg.probes.values() if p is None)
        n_standby_min += sum(int(p.standby_minutes) for p in seg.probes.values()
                             if p is not None)
        n_vni_segments += len(seg.vni_spans)
        n_unver_segments += len(seg.unverifiable_spans)
        n_unions += len(seg.unions)
        for i, ep in enumerate(seg.episodes):
            t0_unix = ep.start_h * _SECONDS_PER_HOUR
            death, raw = detect_death_for_episode(seg.files, ep)
            ep_files = _files_for_episode(seg.files, ep, probes=seg.probes)
            rec = build_event_record(
                cohort=cohort, box=box, episode=ep,
                files=ep_files, t0_unix=t0_unix,
                signal_loss=signal_loss_at_end(seg.files, seg.probes, ep),
                death=death,
                missing_spans=seg.missing_spans,
                vni_spans=seg.vni_spans,
                unverifiable_spans=seg.unverifiable_spans,
                blackouts=seg.blackouts,
                vent_drops=seg.vent_drops,
            )
            rec["event_id"] = f"{cohort}_{box}_event_{i + 1}"
            levels[rec["level"]] = levels.get(rec["level"], 0) + 1
            if coverage:
                series = read_coverage_series(
                    [f.path for f in ep_files], t0_unix
                )
                vent_spans_h = [
                    (a["vent_start_h"], a["vent_end_h"]) for a in rec["attempts"]
                ]
                # OJO: ``coverage_fractions`` convierte horas -> minutos (D8).
                fracs = coverage_fractions(series, vent_spans_h)
                rec["coverage"] = {k: round(v, 4) for k, v in fracs.items()}
                rec["vars_ok_50"] = bool(fracs) and all(
                    v > 0.5 for v in fracs.values()
                )
                rec["vars_ok_80"] = bool(fracs) and all(
                    v > 0.8 for v in fracs.values()
                )
                n_covered += 1
                vars_ok_50 += int(rec["vars_ok_50"])
                vars_ok_80 += int(rec["vars_ok_80"])
            events.append(rec)
            if death.is_death:
                deaths.append(_death_record(rec, ep, raw))
        for i, ep in enumerate(seg.excluded_episodes):
            excluded_events.append({
                "event_id": f"{cohort}_{box}_excluded_{i + 1}",
                "cohort": cohort,
                "box": box,
                "start_h_unix": ep.start_h * _SECONDS_PER_HOUR,
                "duration_seconds": int(round(ep.duration_h * _SECONDS_PER_HOUR)),
                "n_attempts": ep.n_attempts,
                "exclusion_reason": ep.exclusion_reason,
                "no_patient_fraction": round(ep.no_patient_fraction or 0.0, 4),
            })

    return {
        "source": f"{cohort}_source_vital",
        "description": (
            "Eventos de ventilación segmentados con señales (D1/D2/D4). "
            "Sin duración mínima ni límite de 7 días. D5 conectado "
            "(pérdida de constantes tras la desconexión). Fase 1.5: los "
            "ficheros ilegibles son 'sin dato' (no crean huecos) y se "
            "clasifican A/B/D."
        ),
        "method": (
            "Se procesa cada .vital de origen con el parser de vitaldb; la "
            "ventilación exige ACTIVIDAD real de las pistas de ventilador "
            "(TV_EXP/MV_EXP/amplitud de AWP_WAV con VENT_RR>0; Fase 1.6e) "
            "y los tramos posteriores con firma de interfaz NO invasiva se "
            "separan como VNI (D-b: VNI = extubado, no es fallo). "
            "Segmentación: src/common/episodes.py."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "merged": merged,
        "total_boxes": len(results),
        "total_events": len(events),
        "total_excluded_events": len(excluded_events),
        "n_missing_files": n_missing_files,
        "n_vent_standby_minutes": n_standby_min,
        "n_vni_segments": n_vni_segments,
        "n_eventos_con_vni_post_ext": sum(
            1 for e in events if e.get("vni_post_ext_h_start") is not None
        ),
        "n_vni_unverifiable_segments": n_unver_segments,
        "n_unions": n_unions,
        # Fase 1.6f (puntos 2 y 3): apagones del grabador (D-j) y caídas de la
        # conexión de datos del ventilador (D-k).
        "n_blackouts": sum(len(s.blackouts) for _b, s in results),
        "n_blackouts_benign": sum(
            1 for _b, s in results for x in s.blackouts if x["benign"]
        ),
        "n_blackouts_long": sum(
            1 for _b, s in results for x in s.blackouts if x["type"] == "long"
        ),
        "n_blackout_h": round(
            sum(x["duration_h"] for _b, s in results for x in s.blackouts), 4),
        "n_events_with_blackout": sum(1 for e in events if e["blackout_gaps"]),
        "n_events_gap_type_blackout_long": sum(
            1 for e in events if e["gap_type"] == GAP_TYPE_BLACKOUT_LONG),
        "n_vent_feed_drops": sum(len(s.vent_drops) for _b, s in results),
        "n_vent_feed_drops_benign": sum(
            1 for _b, s in results for x in s.vent_drops if x["benign"]
        ),
        "n_vent_feed_dropout_h": round(
            sum(x["duration_h"] for _b, s in results for x in s.vent_drops), 4),
        "n_events_with_vent_feed_drop": sum(
            1 for e in events if e["vent_feed_drops"]),
        "n_fragmentos_registro": sum(
            1 for e in excluded_events
            if e.get("exclusion_reason") == "fragmento_registro"
        ),
        "levels": levels,
        "levels_coverage_measured": n_covered,
        "vars_ok_50": vars_ok_50,
        "vars_ok_80": vars_ok_80,
        "n_signal_loss_at_end": sum(1 for e in events if e["signal_loss_at_end"]),
        "n_simultaneous_shutdown": sum(1 for e in events if e["simultaneous_shutdown"]),
        "n_death_signal": len(deaths),
        "_signal_deaths": deaths,
        "events": events,
        "excluded_events": excluded_events,
    }


# ── Orquestación ─────────────────────────────────────────────────────────────

def output_root(config: dict, cohort: str) -> tuple[Path, str]:
    """Carpeta de salida versionada (nunca dentro del repo de datos antiguos)."""
    version = f"v{config.get('version', '0.0.0')}_{compute_config_hash(config)}"
    key = f"paths.{cohort}_cases_out"
    base = config_path(config, "paths", f"{cohort}_cases_out", required=False)
    if base is None:
        base = repo_root() / "datasets" / cohort / f"cases_{version}"
    return Path(base), version


def _segment_box_task(
    args: tuple[str, list[SourceFile], bool],
) -> tuple[str, BoxSegmentation]:
    """Trabajo por box para el pool de procesos (debe ser de nivel de módulo)."""
    box, files, unir, blackout, drops = args
    return box, segment_box(box, files, unir_cursos=unir,
                            detect_blackout=blackout,
                            absorb_vent_dropouts=drops)


def _segment_box_flags(config: dict) -> tuple[bool, bool]:
    """Interruptores de D-j/D-k desde ``fase1_6f.blackout`` (Fase 1.6f)."""
    node = ((config.get("fase1_6f") or {}).get("blackout") or {})
    return (bool(node.get("enabled", BLACKOUT_ENABLED)),
            bool(node.get("vent_dropout_absorb", ABSORB_VENT_DROPOUTS)))


def filter_boxes(
    boxes: dict[str, list[SourceFile]],
    only: Optional[Sequence[str]] = None,
) -> dict[str, list[SourceFile]]:
    """Restringe el escaneo a una lista de boxes (reconstrucciones resumibles).

    Fase 1.6d (punto 5): el disco puede colgar una lectura durante minutos; con
    ``only`` se puede reconstruir la cohorte **box a box** con un timeout por
    box, de modo que un cuelgue cueste un box y no la reconstrucción completa.
    ``only`` vacío o ``None`` no filtra nada.
    """
    if not only:
        return dict(boxes)
    want = {str(b) for b in only}
    return {b: files for b, files in boxes.items() if b in want}


def merge_partial_indices(parts: Sequence[tuple[str, dict]], *, cohort: str) -> dict:
    """Fusiona índices parciales (uno por box) en un índice de cohorte.

    Fase 1.6d (punto 5): cada box se reconstruye por separado (con timeout) y
    sus eventos se concatenan aquí. Las etiquetas, los intentos y la cobertura
    de cada evento NO se recalculan: se copian tal cual del parcial.
    """
    events: list[dict] = []
    excluded: list[dict] = []
    n_missing = 0
    levels: dict[str, int] = {}
    vars_ok_50 = vars_ok_80 = 0
    n_covered = n_signal_loss = n_shutdown = n_deaths = 0
    n_blackouts = n_blackouts_benign = n_blackouts_long = 0
    n_blackout_h = 0.0
    n_drops = n_drops_benign = 0
    n_drop_h = 0.0
    for _box, idx in parts:
        events += list(idx.get("events", []))
        excluded += list(idx.get("excluded_events", []))
        n_missing += int(idx.get("n_missing_files") or 0)
        for k, v in (idx.get("levels") or {}).items():
            levels[k] = levels.get(k, 0) + int(v)
        vars_ok_50 += int(idx.get("vars_ok_50") or 0)
        vars_ok_80 += int(idx.get("vars_ok_80") or 0)
        n_covered += int(idx.get("levels_coverage_measured") or 0)
        n_signal_loss += int(idx.get("n_signal_loss_at_end") or 0)
        n_shutdown += int(idx.get("n_simultaneous_shutdown") or 0)
        n_deaths += int(idx.get("n_death_signal") or 0)
        n_blackouts += int(idx.get("n_blackouts") or 0)
        n_blackouts_benign += int(idx.get("n_blackouts_benign") or 0)
        n_blackouts_long += int(idx.get("n_blackouts_long") or 0)
        n_blackout_h += float(idx.get("n_blackout_h") or 0.0)
        n_drops += int(idx.get("n_vent_feed_drops") or 0)
        n_drops_benign += int(idx.get("n_vent_feed_drops_benign") or 0)
        n_drop_h += float(idx.get("n_vent_feed_dropout_h") or 0.0)
    events.sort(key=lambda e: e.get("event_id", ""))
    excluded.sort(key=lambda e: e.get("event_id", ""))
    return {
        "source": (parts[0][1].get("source") if parts
                   else f"{cohort}_source_vital"),
        "description": (
            "Índice de cohorte fusionado a partir de parciales por box "
            "(reconstrucción resumible, Fase 1.6d punto 5)."
        ),
        "method": ("build_signal_cases por box con timeout + fusión "
                   "(build_cohort_resumable.py)."),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "merged": False,
        "total_boxes": len(parts),
        "total_events": len(events),
        "total_excluded_events": len(excluded),
        "n_missing_files": n_missing,
        "levels": levels,
        "levels_coverage_measured": n_covered,
        "vars_ok_50": vars_ok_50,
        "vars_ok_80": vars_ok_80,
        "n_signal_loss_at_end": n_signal_loss,
        "n_simultaneous_shutdown": n_shutdown,
        "n_death_signal": n_deaths,
        "n_blackouts": n_blackouts,
        "n_blackouts_benign": n_blackouts_benign,
        "n_blackouts_long": n_blackouts_long,
        "n_blackout_h": round(n_blackout_h, 4),
        "n_events_with_blackout": sum(
            1 for e in events if e.get("blackout_gaps")),
        "n_events_gap_type_blackout_long": sum(
            1 for e in events if e.get("gap_type") == GAP_TYPE_BLACKOUT_LONG),
        "n_vent_feed_drops": n_drops,
        "n_vent_feed_drops_benign": n_drops_benign,
        "n_vent_feed_dropout_h": round(n_drop_h, 4),
        "n_events_with_vent_feed_drop": sum(
            1 for e in events if e.get("vent_feed_drops")),
        "events": events,
        "excluded_events": excluded,
    }


def run_cohort(
    config: dict,
    cohort: str,
    *,
    do_merge: bool = True,
    limit_boxes: Optional[int] = None,
    workers: int = 1,
    coverage: bool = False,
    probe_fn: Optional[Callable[[Path], Optional[VitalProbe]]] = None,
    only_boxes: Optional[Sequence[str]] = None,
    out_dir_override: Optional[str | Path] = None,
) -> dict:
    """Ejecuta el pipeline completo de una cohorte con señal."""
    spec = SPECS[cohort]
    # Fase 1.6f (D-m): si existe el espejo de solo lectura, se lee de ahí.
    raw_dir, is_mirror = resolve_raw_dir(config, cohort)
    if is_mirror:
        info = verify_manifest_sizes(raw_dir)
        if info["n_missing"] or info["n_bad_size"]:
            raise RuntimeError(
                f"El espejo {raw_dir} no cuadra con su manifiesto: "
                f"{info['n_missing']} ficheros que faltan, "
                f"{info['n_bad_size']} con tamaño distinto. "
                f"Reparar con make_mirror.py antes de reconstruir."
            )
        logger.info("[%s] espejo=%s (%d ficheros verificados por tamaño)",
                    cohort, raw_dir, info["n_ok"])
    else:
        logger.warning("[%s] espejo no disponible; se lee el CRUDO %s",
                       cohort, raw_dir)
    out_dir, version = output_root(config, cohort)
    if out_dir_override is not None:
        out_dir = Path(out_dir_override)
    index_path = out_dir / f"{cohort}_cases_index.json"

    logger.info("[%s] origen=%s salida=%s workers=%d", cohort, raw_dir, out_dir, workers)
    boxes = scan_source_files(raw_dir, spec)
    boxes = filter_boxes(boxes, only_boxes)
    if limit_boxes:
        boxes = dict(list(boxes.items())[:limit_boxes])

    results: list[tuple[str, BoxSegmentation]] = []
    # Decision 13/10 (opcion a): la union de cursos esta desactivada por defecto.
    # El config manda: solo se activa con `fase1_6e.union.enabled: true`.
    unir = bool(
        ((config.get("fase1_6e") or {}).get("union") or {})
        .get("enabled", UNION_ENABLED)
    )
    logger.info("[%s] union de cursos: %s", cohort,
                "ACTIVADA" if unir else "desactivada (decision 13/10)")
    blackout_on, drops_on = _segment_box_flags(config)
    logger.info("[%s] D-j apagones: %s | D-k caidas del ventilador: %s",
                cohort, "ON" if blackout_on else "OFF",
                "ON" if drops_on else "OFF")
    if probe_fn is not None:
        # Reutiliza una caché de sondas (escaneo previo): no se re-parsea nada.
        for box, files in boxes.items():
            results.append((box, segment_box(
                box, files, probe_fn=probe_fn, unir_cursos=unir,
                detect_blackout=blackout_on,
                absorb_vent_dropouts=drops_on)))
    elif workers and workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        tareas = [(box, files, unir, blackout_on, drops_on)
                  for box, files in boxes.items()]
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for box, seg in ex.map(_segment_box_task, tareas):
                results.append((box, seg))
    else:
        for box, files in boxes.items():
            results.append((box, segment_box(
                box, files, unir_cursos=unir,
                detect_blackout=blackout_on,
                absorb_vent_dropouts=drops_on)))

    if do_merge and out_dir.exists():
        raise FileExistsError(
            f"La versión ya existe y no se sobrescribe: {out_dir}"
        )

    index = build_cohort_index(
        results, cohort=cohort, spec=spec, merged=do_merge, coverage=coverage
    )

    if do_merge:
        out_dir.mkdir(parents=True, exist_ok=True)
        by_id = {r["event_id"]: r for r in index["events"]}
        for box, seg in results:
            for i, ep in enumerate(seg.episodes):
                event_id = f"{cohort}_{box}_event_{i + 1}"
                rec = by_id[event_id]
                files = _files_for_episode(seg.files, ep, probes=seg.probes)
                out_path = out_dir / f"{event_id}.vital"
                _, dt0, dtend = merge_event_files(
                    [f.path for f in files], out_path, MERGE_TRACK_NAMES
                )
                rec["file"] = out_path.name
                rec["tend_unix"] = dtend
                rec["t0_unix"] = dt0

    out_dir.mkdir(parents=True, exist_ok=True)
    deaths = index.pop("_signal_deaths", [])
    if deaths:
        deaths_path = out_dir / "signal_deaths.json"
        with open(deaths_path, "w", encoding="utf-8") as fh:
            json.dump({"cohort": cohort, "deaths": deaths}, fh,
                      ensure_ascii=False, indent=1)
        logger.info("[%s] %d muertes por senal -> %s", cohort, len(deaths), deaths_path)
    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)

    logger.info(
        "[%s] %d boxes, %d eventos, %d excluidos -> %s",
        cohort, len(results), index["total_events"],
        index["total_excluded_events"], index_path,
    )
    return {"version": version, "output_dir": str(out_dir), "index": index}


def main() -> None:
    p = argparse.ArgumentParser(description="Casos de Clínic/VitalDB desde .vital de origen")
    p.add_argument("--cohort", choices=sorted(SPECS), required=True)
    p.add_argument("--config", default="src/stage0/config/harmonize.yaml")
    p.add_argument("--no-merge", action="store_true", help="Solo segmentar e índice")
    p.add_argument("--limit-boxes", type=int, default=None)
    p.add_argument("--boxes", default=None,
                   help="Lista de boxes separada por comas (reconstrucción "
                        "resumible box a box)")
    p.add_argument("--out-dir", default=None,
                   help="Carpeta de salida alternativa (p. ej. parciales)")
    p.add_argument("--workers", type=int, default=1,
                   help="Procesos en paralelo (un box por tarea)")
    p.add_argument("--coverage", action="store_true",
                   help="Medir cobertura de variables por evento (LOCF 4 h)")
    p.add_argument("--probe-cache", default=None,
                   help="Caché de sondas (JSON del escaneo) para no re-parsear")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    probe_fn = None
    if args.probe_cache:
        from src.common.vital_signals import probe_fn_from_cache
        cache = json.loads(Path(args.probe_cache).read_text(encoding="utf-8"))
        probe_fn = probe_fn_from_cache(cache)
    result = run_cohort(
        config, args.cohort,
        do_merge=not args.no_merge,
        limit_boxes=args.limit_boxes,
        workers=args.workers,
        coverage=args.coverage,
        probe_fn=probe_fn,
        only_boxes=([b.strip() for b in args.boxes.split(",") if b.strip()]
                    if args.boxes else None),
        out_dir_override=args.out_dir,
    )
    print(json.dumps(
        {
            "version": result["version"],
            "output_dir": result["output_dir"],
            "total_events": result["index"]["total_events"],
            "total_excluded_events": result["index"]["total_excluded_events"],
        },
        indent=2, ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
