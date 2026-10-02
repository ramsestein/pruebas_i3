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
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from src.common.episodes import Episode, Span, build_episodes, segment_attempts
from src.common.labels import (
    assign_labels_all_windows,
    attempts_from_pairs,
    labels_to_dict,
)
from src.common.paths import config_path, repo_root, resolve_path
from src.common.vital_signals import (
    MERGE_TRACK_NAMES,
    VitalProbe,
    hr_span_from_probe,
    merge_event_files,
    monitor_span_from_probe,
    probe_vital_file,
    spo2_span_from_probe,
    vent_span_from_probe,
)
from src.stage0.io.versioning import compute_config_hash, load_config
from src.common.timeutils import to_epoch_utc

logger = logging.getLogger(__name__)

_SECONDS_PER_HOUR = 3600.0


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

    for sf in files:
        probe = probe_fn(sf.path)
        if probe is None:
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
    return BoxSegmentation(box=box, files=list(files), episodes=kept,
                           excluded_episodes=excluded)


# ── Materialización de eventos (fusión + índice) ─────────────────────────────

def _files_for_episode(files: Sequence[SourceFile], episode: Episode) -> list[SourceFile]:
    """Ficheros de origen que solapan algún intento del episodio."""
    out: list[SourceFile] = []
    for sf in files:
        f_start = sf.dt_unix / _SECONDS_PER_HOUR
        f_end = f_start + 1.0  # los ficheros de origen son horarios
        if any(
            a.start_h < f_end and a.end_h > f_start for a in episode.attempts
        ):
            out.append(sf)
    return out


def _arrived_ventilated(episode: Episode, files: Sequence[SourceFile]) -> bool:
    """D10: el paciente ya estaba intubado al empezar el registro del box."""
    if not files:
        return False
    first_file_start = files[0].dt_unix / _SECONDS_PER_HOUR
    return abs(episode.start_h - first_file_start) < (1.0 / 60.0)


def _end_reason(episode: Episode, files: Sequence[SourceFile]) -> str:
    """Motivo de fin del evento observable con señales (D5 refina la muerte)."""
    if not files:
        return "unknown"
    last_file_end = files[-1].dt_unix / _SECONDS_PER_HOUR + 1.0
    if episode.end_h >= last_file_end - 30.0 / 3600.0:
        return "end_of_record"
    return "extubation_observed"


def build_event_record(
    *,
    cohort: str,
    box: str,
    episode: Episode,
    files: Sequence[SourceFile],
    t0_unix: float,
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
    labels = labels_to_dict(assign_labels_all_windows(
        attempts_from_pairs([
            (a["vent_end_h"], a["reintubation_h"]) for a in attempts
        ]),
        obs_end_h=episode.duration_h,
    ))

    return {
        "event_id": "",  # se rellena en el orquestador
        "cohort": cohort,
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
        "n_attempts": episode.n_attempts,
        "attempts": attempts,
        "end_reason": _end_reason(episode, files),
        "ventilated_hours": round(episode.ventilated_hours, 4),
        "labels": labels,       # Fase 1.6 (D3)
        "trach": None,         # Fase 1.5 (D5)
        "terminal": None,      # Fase 1.5 (D5)
        "file": None,          # se rellena al fusionar
    }


def build_cohort_index(
    results: Sequence[tuple[str, BoxSegmentation]],
    *,
    cohort: str,
    spec: CohortSpec,
    merged: bool,
) -> dict:
    """Construye el índice JSON completo de una cohorte."""
    events: list[dict] = []
    excluded_events: list[dict] = []
    for box, seg in results:
        for i, ep in enumerate(seg.episodes):
            t0_unix = ep.start_h * _SECONDS_PER_HOUR
            rec = build_event_record(
                cohort=cohort, box=box, episode=ep,
                files=_files_for_episode(seg.files, ep), t0_unix=t0_unix,
            )
            rec["event_id"] = f"{cohort}_{box}_event_{i + 1}"
            events.append(rec)
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
            "Sin duración mínima ni límite de 7 días."
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


def run_cohort(
    config: dict,
    cohort: str,
    *,
    do_merge: bool = True,
    limit_boxes: Optional[int] = None,
) -> dict:
    """Ejecuta el pipeline completo de una cohorte con señal."""
    spec = SPECS[cohort]
    raw_dir = config_path(config, "paths", f"{cohort}_raw_dir")
    out_dir, version = output_root(config, cohort)
    index_path = out_dir / f"{cohort}_cases_index.json"

    logger.info("[%s] origen=%s salida=%s", cohort, raw_dir, out_dir)
    boxes = scan_source_files(raw_dir, spec)
    if limit_boxes:
        boxes = dict(list(boxes.items())[:limit_boxes])

    results: list[tuple[str, BoxSegmentation]] = []
    for box, files in boxes.items():
        seg = segment_box(box, files)
        results.append((box, seg))

    if do_merge and out_dir.exists():
        raise FileExistsError(
            f"La versión ya existe y no se sobrescribe: {out_dir}"
        )

    index = build_cohort_index(results, cohort=cohort, spec=spec, merged=do_merge)

    if do_merge:
        out_dir.mkdir(parents=True, exist_ok=True)
        by_id = {r["event_id"]: r for r in index["events"]}
        for box, seg in results:
            for i, ep in enumerate(seg.episodes):
                event_id = f"{cohort}_{box}_event_{i + 1}"
                rec = by_id[event_id]
                files = _files_for_episode(seg.files, ep)
                out_path = out_dir / f"{event_id}.vital"
                _, dt0, dtend = merge_event_files(
                    [f.path for f in files], out_path, MERGE_TRACK_NAMES
                )
                rec["file"] = out_path.name
                rec["tend_unix"] = dtend
                rec["t0_unix"] = dt0

    out_dir.mkdir(parents=True, exist_ok=True)
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
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    result = run_cohort(
        config, args.cohort,
        do_merge=not args.no_merge,
        limit_boxes=args.limit_boxes,
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
