#!/usr/bin/env python3
"""
Construye eventos de ventilación mecánica a partir de archivos .vital
en vitaldb_sicu (archivos por hora y por box).

Lógica:
1. Agrupa archivos .vital por BOX, ordenados cronológicamente.
2. Detecta ventilación por presencia de tracks de ventilador:
   FLOW_WAV, AWP_WAV, TV_EXP, MV_EXP, VENT_RR, PEEP_CMH2O, PIP_CMH2O.
3. Unifica archivos consecutivos del mismo box donde hay ventilación.
4. Fusiona todos los archivos de cada evento en un único .vital.
5. Descarta eventos con duración < 2h.
6. Genera índice JSON con metadatos.

Salida:
  datasets/vitaldb_sicu/vitaldb_full_cases/
    {box}_{start}_to_{end}.vital
  datasets/vitaldb_sicu/vitaldb_full_cases_index.json
"""
from __future__ import annotations
import os
import re
import json
import shutil
import gzip
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# ── constantes ──────────────────────────────────────────────────────────
MIN_EVENT_SECONDS = 7200  # 2 horas mínimas
FILENAME_PATTERN = re.compile(
    r"(SICU\d+_\d+)_(\d{6})_(\d{6})\.vital",
    re.IGNORECASE,
)

VENT_TRACKS = {
    "Intellivue/FLOW_WAV",
    "Intellivue/AWP_WAV",
    "Intellivue/TV_EXP",
    "Intellivue/MV_EXP",
    "Intellivue/VENT_RR",
    "Intellivue/PEEP_CMH2O",
    "Intellivue/PIP_CMH2O",
}


def parse_vital_filename(fname: str) -> Optional[Tuple[str, datetime]]:
    m = FILENAME_PATTERN.match(fname)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(2) + m.group(3), "%y%m%d%H%M%S")
    except ValueError:
        return None
    return m.group(1).upper(), dt


def format_timedelta(seconds: float) -> str:
    td = timedelta(seconds=seconds)
    days = td.days
    hours, rem = divmod(td.seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def has_ventilation(path: str) -> bool:
    """
    Lee los primeros bytes gzip del .vital y busca nombres de tracks
    de ventilación (formato raw: 'FLOW_WAV', 'AWP_WAV', etc).
    """
    VENT_NAMES = {
        b"FLOW_WAV", b"AWP_WAV", b"TV_EXP", b"MV_EXP",
        b"VENT_RR", b"PEEP_CMH2O", b"PIP_CMH2O",
    }
    try:
        with gzip.open(path, "rb") as gz:
            chunk = gz.read(50000)
    except Exception:
        return False
    return any(name in chunk for name in VENT_NAMES)


def scan_files(base_dir: str) -> Dict[str, List[Tuple[datetime, str, bool]]]:
    """
    Escanea .vital y devuelve por box:
    [(dt, ruta, has_vent)]
    """
    box_files: Dict[str, List[Tuple[datetime, str, bool]]] = defaultdict(list)
    total = 0
    errors = 0

    for f in os.listdir(base_dir):
        if not f.lower().endswith(".vital"):
            continue
        parsed = parse_vital_filename(f)
        if not parsed:
            continue
        box, dt = parsed
        path = os.path.join(base_dir, f)

        try:
            vent = has_ventilation(path)
        except Exception:
            errors += 1
            vent = False

        box_files[box].append((dt, path, vent))
        total += 1

    for box in box_files:
        box_files[box].sort(key=lambda x: x[0])

    print(f"  -> {total} archivos escaneados, {errors} errores")
    print(f"  -> {len(box_files)} boxes")
    return box_files


def find_events(
    files: List[Tuple[datetime, str, bool]],
    box: str,
    min_seconds: int = MIN_EVENT_SECONDS,
) -> List[dict]:
    """
    Detecta eventos de ventilación como segmentos consecutivos donde
    hay ventilación (has_vent=True), delimitados por archivos sin ventilación.
    """
    events = []
    current_start = None
    current_files = []

    for dt, path, vent in files:
        if vent:
            if current_start is None:
                current_start = dt
            current_files.append(path)
        else:
            # Fin de ventilación (o no había)
            if current_start is not None and len(current_files) > 0:
                duration = (dt - current_start).total_seconds()
                if duration >= min_seconds:
                    events.append({
                        "box": box,
                        "start_time": current_start,
                        "end_time": dt,
                        "files": current_files.copy(),
                        "duration_seconds": duration,
                        "duration_str": format_timedelta(duration),
                        "num_files": len(current_files),
                    })
                current_start = None
                current_files = []

    # Si termina con ventilación activa
    if current_start is not None and len(current_files) > 0:
        last_dt = files[-1][0] + timedelta(hours=1)  # aprox
        duration = (last_dt - current_start).total_seconds()
        if duration >= min_seconds:
            events.append({
                "box": box,
                "start_time": current_start,
                "end_time": last_dt,
                "files": current_files.copy(),
                "duration_seconds": duration,
                "duration_str": format_timedelta(duration),
                "num_files": len(current_files),
            })

    return events


def merge_vital_files(event: dict, output_dir: str) -> str:
    """Fusiona todos los .vital del evento en un único archivo."""
    start_str = event["start_time"].strftime("%y%m%d_%H%M%S")
    end_str = event["end_time"].strftime("%y%m%d_%H%M%S")
    box = event["box"]
    fname = f"{box}_{start_str}_to_{end_str}.vital"
    out_path = os.path.join(output_dir, fname)

    os.makedirs(output_dir, exist_ok=True)

    with open(out_path, "wb") as out:
        for src_path in event["files"]:
            with open(src_path, "rb") as src:
                shutil.copyfileobj(src, out)

    return out_path


def main():
    import argparse
    p = argparse.ArgumentParser(
        description="Construir eventos de ventilación desde vitaldb_sicu"
    )
    p.add_argument("--input-dir", default="datasets/vitaldb_sicu")
    p.add_argument("--output-dir", default="datasets/vitaldb_sicu/vitaldb_full_cases")
    p.add_argument("--index", default="datasets/vitaldb_sicu/vitaldb_full_cases_index.json")
    p.add_argument("--min-duration", type=int, default=MIN_EVENT_SECONDS)
    args = p.parse_args()

    if not os.path.isdir(args.input_dir):
        print(f"Directorio no encontrado: {args.input_dir}")
        return

    print("Escaneando archivos .vital...")
    box_files = scan_files(args.input_dir)

    all_events = []
    total_boxes_with_events = 0

    for box in sorted(box_files.keys()):
        files = box_files[box]
        print(f"\nBox: {box} ({len(files)} archivos)")
        events = find_events(files, box, args.min_duration)

        if not events:
            print("  -> Sin eventos >= 2h")
            continue

        total_boxes_with_events += 1
        print(f"  -> {len(events)} evento(s)")

        for idx, ev in enumerate(events):
            merged_path = merge_vital_files(ev, args.output_dir)
            merged_size = os.path.getsize(merged_path)

            event_info = {
                "event_id": f"{box}_event_{idx+1}",
                "box": box,
                "file": os.path.basename(merged_path),
                "file_size_bytes": merged_size,
                "start_time": ev["start_time"].isoformat(),
                "end_time": ev["end_time"].isoformat(),
                "duration_seconds": ev["duration_seconds"],
                "duration_str": ev["duration_str"],
                "num_vital_files_merged": ev["num_files"],
                "has_induction": True,
                "has_emergence": True,
                "notes": [
                    "Evento detectado por cambio en tracks de ventilación (FLOW_WAV/AWP_WAV/TV_EXP/etc)"
                ],
            }
            all_events.append(event_info)

            print(
                f"    [{idx+1}] {ev['start_time'].strftime('%y%m%d_%H%M%S')} -> "
                f"{ev['end_time'].strftime('%y%m%d_%H%M%S')} "
                f"({ev['duration_str']}, {ev['num_files']} archivos, "
                f"{merged_size // 1024} KB)"
            )

    # Guardar índice
    index_data = {
        "source": "vitaldb_sicu",
        "description": (
            "Eventos de ventilación mecánica detectados desde archivos "
            "por hora de SICU. Un evento es un segmento consecutivo donde "
            "hay tracks de ventilación (FLOW_WAV, AWP_WAV, TV_EXP, etc)."
        ),
        "method": (
            "Para cada box, se unifican archivos consecutivos con tracks de "
            "ventilación. Los eventos terminan cuando aparece un archivo sin "
            "estos tracks."
        ),
        "generated_at": datetime.now().isoformat(),
        "total_boxes": len(box_files),
        "total_boxes_with_events": total_boxes_with_events,
        "total_events": len(all_events),
        "min_duration_seconds": args.min_duration,
        "events": all_events,
    }

    os.makedirs(os.path.dirname(args.index), exist_ok=True)
    with open(args.index, "w", encoding="utf-8") as fh:
        json.dump(index_data, fh, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"RESUMEN FINAL")
    print(f"  Boxes totales: {index_data['total_boxes']}")
    print(f"  Boxes con eventos: {total_boxes_with_events}")
    print(f"  Eventos generados: {len(all_events)}")
    print(f"  Duración mínima: {format_timedelta(args.min_duration)}")
    print(f"  Archivos en: {args.output_dir}")
    print(f"  Índice en: {args.index}")


if __name__ == "__main__":
    main()
