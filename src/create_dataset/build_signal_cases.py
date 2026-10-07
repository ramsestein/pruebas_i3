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
  ``vitaldb`` y se exige presencia real de pistas de ventilador
  (``src/common/vital_signals.py``).
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
from src.common.episodes import (
    Episode,
    Span,
    build_episodes,
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
from src.common.vital_signals import (
    COVERAGE_TRACKS,
    MERGE_TRACK_NAMES,
    VitalProbe,
    coverage_fractions,
    hr_span_from_probe,
    merge_event_files,
    monitor_span_from_probe,
    probe_vital_file,
    read_coverage_series,
    read_death_series,
    spo2_span_from_probe,
    vent_span_from_probe,
)
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
        boxes[box].sort(key=lambda f: f.dt_unix)
    return boxes


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


def _to_hours(span: Optional[Span]) -> Optional[Span]:
    if span is None:
        return None
    return Span(span.start_h / _SECONDS_PER_HOUR, span.end_h / _SECONDS_PER_HOUR)


def segment_box(
    box: str,
    files: Sequence[SourceFile],
    probe_fn: Callable[[Path], Optional[VitalProbe]] = probe_vital_file,
    *,
    disconnect_gap_h: float = 2.0,
    patient_gap_h: float = 1.0,
    no_patient_fraction: float = 0.8,
) -> BoxSegmentation:
    """Segmenta un box en episodios aplicando D1/D2/D4 sobre las señales."""
    vent_spans: list[Span] = []
    monitor_spans: list[Span] = []
    hr_spans: list[Span] = []
    spo2_spans: list[Span] = []
    probes: dict[str, Optional[VitalProbe]] = {}
    missing_spans: list[Span] = []

    for sf in files:
        probe = probe_fn(sf.path)
        probes[str(sf.path)] = probe
        if probe is None:
            # Fichero ILEGIBLE: es "sin dato", no "sin señal". Sus horas no
            # deben crear huecos de ventilador (D1) ni cortes de paciente (D2).
            f_start = sf.dt_unix / _SECONDS_PER_HOUR
            missing_spans.append(Span(f_start, f_start + 1.0))
            continue
        v = _to_hours(vent_span_from_probe(probe))
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

    # Puentear los huecos causados por ficheros ilegibles antes de segmentar:
    # un fin de ventilación solo es real si el hueco NO está cubierto por
    # ficheros "sin dato".
    vent_spans = merge_spans_ignoring_missing(
        vent_spans, missing_spans, disconnect_gap_h
    )
    monitor_spans = merge_spans_ignoring_missing(
        monitor_spans, missing_spans, patient_gap_h
    )
    hr_spans = merge_spans_ignoring_missing(hr_spans, missing_spans, patient_gap_h)
    spo2_spans = merge_spans_ignoring_missing(spo2_spans, missing_spans, patient_gap_h)

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
    kept = [e for e in episodes if not e.excluded]
    excluded = [e for e in episodes if e.excluded]
    return BoxSegmentation(
        box=box, files=list(files), episodes=kept, excluded_episodes=excluded,
        probes=probes, missing_spans=merge_spans(missing_spans, 0.0),
    )


# ── Materialización de eventos (fusión + índice) ─────────────────────────────

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
    """
    return resolve_extubation(
        last_vent_end_h=float(episode.attempts[-1].end_h),
        observation_end_h=float(observation_end_h(episode)),
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

    Respaldos (Fase 1.6c, punto 4), en este orden:

    1. los que solapan algún intento;
    2. los que solapan el episodio según la **hora de cabecera** del sondeo
       (``dtstart``/``dtend``): hay ficheros de VitalDB cuyo nombre no coincide
       con la fecha real del contenido y su episodio nace de la cabecera, no del
       nombre. Sin este respaldo el evento quedaba sin ``source_files`` y no se
       podía ni auditar ni medir su cobertura.
    """
    start_h = episode.start_h
    end_h = _region_end_h(episode)
    out: list[SourceFile] = []
    for sf in files:
        f_start = sf.dt_unix / _SECONDS_PER_HOUR
        f_end = f_start + 1.0  # los ficheros de origen son horarios
        if f_start < end_h and f_end > start_h:
            out.append(sf)
    if out:
        return out
    for att in episode.attempts:
        for sf in files:
            f_start = sf.dt_unix / _SECONDS_PER_HOUR
            if f_start < att.end_h and f_start + 1.0 > att.start_h:
                out.append(sf)
    if out:
        return out
    if probes:
        for sf in files:
            pr = probes.get(str(sf.path))
            if pr is None:
                continue
            if (pr.dtstart / _SECONDS_PER_HOUR < end_h
                    and pr.dtend / _SECONDS_PER_HOUR > start_h):
                out.append(sf)
    return out


def _arrived_ventilated(episode: Episode, files: Sequence[SourceFile]) -> bool:
    """D10: el paciente ya estaba intubado al empezar el registro del box."""
    if not files:
        return False
    first_file_start = files[0].dt_unix / _SECONDS_PER_HOUR
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
    pairs = [(a["vent_end_h"], a["reintubation_h"]) for a in attempts]
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
        "extubation_rule": ext_dec.reason,
        "end_reason": (
            # Vocabulario ÚNICO compartido por las 4 cohortes (Fase 1.6b,
            # punto 4): la pérdida de constantes al final NO es un motivo de
            # fin por sí misma. Si no hay muerte por señal y la observación
            # fisiológica sin ventilador no alcanza 1 h, el evento es
            # ``end_of_record``. ``signal_loss_at_end`` se conserva como QC.
            "death_at_vent" if died
            else ("extubation_observed" if ext_dec.is_extubation else "end_of_record")
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
        "has_missing_files": bool(missing_hours),
        "missing_hours": missing_hours,
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
    for box, seg in results:
        n_missing_files += sum(1 for p in seg.probes.values() if p is None)
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
            "ventilación exige presencia real de pistas de ventilador. "
            "Segmentación: src/common/episodes.py."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "merged": merged,
        "total_boxes": len(results),
        "total_events": len(events),
        "total_excluded_events": len(excluded_events),
        "n_missing_files": n_missing_files,
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


def _segment_box_task(args: tuple[str, list[SourceFile]]) -> tuple[str, BoxSegmentation]:
    """Trabajo por box para el pool de procesos (debe ser de nivel de módulo)."""
    box, files = args
    return box, segment_box(box, files)


def run_cohort(
    config: dict,
    cohort: str,
    *,
    do_merge: bool = True,
    limit_boxes: Optional[int] = None,
    workers: int = 1,
    coverage: bool = False,
    probe_fn: Optional[Callable[[Path], Optional[VitalProbe]]] = None,
) -> dict:
    """Ejecuta el pipeline completo de una cohorte con señal."""
    spec = SPECS[cohort]
    raw_dir = config_path(config, "paths", f"{cohort}_raw_dir")
    out_dir, version = output_root(config, cohort)
    index_path = out_dir / f"{cohort}_cases_index.json"

    logger.info("[%s] origen=%s salida=%s workers=%d", cohort, raw_dir, out_dir, workers)
    boxes = scan_source_files(raw_dir, spec)
    if limit_boxes:
        boxes = dict(list(boxes.items())[:limit_boxes])

    results: list[tuple[str, BoxSegmentation]] = []
    if probe_fn is not None:
        # Reutiliza una caché de sondas (escaneo previo): no se re-parsea nada.
        for box, files in boxes.items():
            results.append((box, segment_box(box, files, probe_fn=probe_fn)))
    elif workers and workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for box, seg in ex.map(_segment_box_task, list(boxes.items())):
                results.append((box, seg))
    else:
        for box, files in boxes.items():
            results.append((box, segment_box(box, files)))

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
