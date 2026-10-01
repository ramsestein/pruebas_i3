#!/usr/bin/env python3
"""
Construye eventos de ventilación mecánica a partir de los archivos .vital
de clinic_vitals.

Lógica validada con análisis exploratorio:
1. Agrupa archivos .vital por BOX, ordenados cronológicamente.
2. Detecta archivos donde CO2+TV_EXP están presentes al INICIO y
   AUSENTES al FINAL (extubación intra-archivo).
3. Para cada extubación, busca hacia ATRÁS en el tiempo dentro del
   mismo box hasta encontrar un archivo SIN ventilación → esa es la
   intubación.
4. Fusiona todos los archivos entre intubación y extubación en un
   ÚNICO archivo .vital.
5. Descarta eventos con duración < 2h.
6. Genera índice JSON con metadatos de cada evento.

Salida:
  datasets/clinic_vitals/clinic_full_cases/
    {box}_{intub_time}_to_{extub_time}.vital
  datasets/clinic_vitals/clinic_full_cases_index.json
"""
from __future__ import annotations
import os
import re
import json
import shutil
import argparse
import gzip
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple


# ── constantes ──────────────────────────────────────────────────────────

MIN_EVENT_SECONDS = 7200  # 2 horas mínimas
FILENAME_PATTERN = re.compile(r"([a-z0-9]+)_(\d{6})_(\d{6})\.vital", re.IGNORECASE)


# ── utilidades ──────────────────────────────────────────────────────────

def parse_vital_filename(fname: str) -> Optional[Tuple[str, datetime]]:
    m = FILENAME_PATTERN.match(fname)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(2) + m.group(3), "%y%m%d%H%M%S")
    except ValueError:
        return None
    return m.group(1).lower(), dt


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


def get_box_from_path(path: str, base_dir: str) -> str:
    rel = os.path.relpath(path, base_dir)
    return rel.split(os.sep)[0] if rel != "." else "unknown"


def check_vent_in_text(text: str) -> tuple[bool, bool, bool, bool]:
    """
    Analiza el texto de un .vital y devuelve:
    (co2_ini, tv_exp_ini, co2_fin, tv_exp_fin)
    Compara mitad inicial vs mitad final.
    """
    mid = len(text) // 2
    inicio = text[:mid]
    final = text[mid:]
    return (
        "CO2" in inicio,
        "TV_EXP" in inicio,
        "CO2" in final,
        "TV_EXP" in final,
    )


# ── escaneo ─────────────────────────────────────────────────────────────

def scan_files(base_dir: str) -> Dict[str, List[Tuple[datetime, str, bool, bool, bool, bool]]]:
    """
    Escanea .vital y devuelve por box:
    [(dt, ruta, co2_ini, tv_exp_ini, co2_fin, tv_exp_fin)]
    """
    box_files: Dict[str, List[Tuple[datetime, str, bool, bool, bool, bool]]] = defaultdict(list)
    total = 0
    errors = 0

    for root, _, files in os.walk(base_dir):
        for f in files:
            if not f.lower().endswith(".vital"):
                continue
            parsed = parse_vital_filename(f)
            if not parsed:
                continue
            _, dt = parsed
            path = os.path.join(root, f)
            box = get_box_from_path(path, base_dir)

            try:
                with gzip.open(path, "rt", errors="replace") as gz:
                    text = gz.read(50000)
                co2_ini, tv_exp_ini, co2_fin, tv_exp_fin = check_vent_in_text(text)
            except Exception:
                errors += 1
                co2_ini = tv_exp_ini = co2_fin = tv_exp_fin = False

            box_files[box].append((dt, path, co2_ini, tv_exp_ini, co2_fin, tv_exp_fin))
            total += 1

    for box in box_files:
        box_files[box].sort(key=lambda x: x[0])

    print(f"  -> {total} archivos escaneados, {errors} errores")
    print(f"  -> {len(box_files)} boxes")
    return box_files


# ── detección de eventos ────────────────────────────────────────────────

def find_events(
    files: List[Tuple[datetime, str, bool, bool, bool, bool]],
    box: str,
    min_seconds: int = MIN_EVENT_SECONDS,
) -> List[dict]:
    """
    Detecta eventos de ventilación.

    Lógica (validada con _trace_events.py):
    1. Busca archivos que EXTuBAN: vent_ini=True y vent_fin=False.
    2. Desde esa extubación, busca hacia ATRÁS hasta encontrar un
       archivo SIN vent_ini → esa es la intubación.
    3. Si no encuentra archivo sin vent, la intubación es el primer
       archivo del box.
    4. Fusiona todos los archivos entre intubación y extubación.
    5. Agrupa eventos con misma intubación y se queda con el más largo.
    6. Descarta eventos < min_seconds.
    """
    raw_events = []

    for i, (dt, path, co2_i, tv_i, co2_f, tv_f) in enumerate(files):
        vent_ini = co2_i and tv_i
        vent_fin = co2_f and tv_f

        if not vent_ini or vent_fin:
            # No extuba (o no tiene vent)
            continue

        # EXTUBA AQUÍ: vent_ini=True, vent_fin=False
        ext_time = dt
        ext_file = path

        # Buscar intubación hacia atrás
        j = i - 1
        intub_time = None
        intub_file = None
        while j >= 0:
            prev_co2_i, prev_tv_i = files[j][2], files[j][3]
            if not (prev_co2_i and prev_tv_i):
                # Archivo sin vent -> el siguiente (j+1) es la intubación
                intub_time = files[j+1][0]
                intub_file = files[j+1][1]
                break
            j -= 1

        if intub_time is None:
            intub_time = files[0][0]
            intub_file = files[0][1]

        duration = (ext_time - intub_time).total_seconds()
        if duration < min_seconds:
            continue

        # Recolectar archivos entre intubación y extubación
        seg_files = []
        for ff in files:
            if ff[0] >= intub_time and ff[0] <= ext_time:
                seg_files.append(ff[1])

        raw_events.append({
            "box": box,
            "start_time": intub_time,
            "start_file": intub_file,
            "end_time": ext_time,
            "end_file": ext_file,
            "files": seg_files,
            "duration_seconds": duration,
            "duration_str": format_timedelta(duration),
            "num_files": len(seg_files),
        })

    # Agrupar por misma intubación y quedarse con el más largo
    from collections import defaultdict
    grupos = defaultdict(list)
    for ev in raw_events:
        key = (box, ev["start_time"])
        grupos[key].append(ev)

    events = []
    for key, evs in grupos.items():
        mejor = max(evs, key=lambda x: x["duration_seconds"])
        events.append(mejor)

    events.sort(key=lambda x: x["start_time"])
    return events


# ── fusión de archivos ─────────────────────────────────────────────────

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


# ── main ───────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Construir eventos de ventilación desde clinic_vitals"
    )
    p.add_argument("--input-dir", default="datasets/clinic_vitals")
    p.add_argument("--output-dir", default="datasets/clinic_vitals/clinic_full_cases")
    p.add_argument("--index", default="datasets/clinic_vitals/clinic_full_cases_index.json")
    p.add_argument("--min-duration", type=int, default=MIN_EVENT_SECONDS)
    p.add_argument("--max-boxes", type=int, default=None)
    args = p.parse_args()

    if not os.path.isdir(args.input_dir):
        print(f"Directorio no encontrado: {args.input_dir}")
        return

    print("Escaneando archivos .vital...")
    box_files = scan_files(args.input_dir)

    sorted_boxes = sorted(box_files.items(), key=lambda x: len(x[1]), reverse=True)
    if args.max_boxes:
        sorted_boxes = sorted_boxes[: args.max_boxes]

    all_events = []
    total_boxes_with_events = 0

    for box, files in sorted_boxes:
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

            has_induction = ev["start_file"] != files[0][1] or ev["start_time"] != files[0][0]
            has_emergence = True

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
                "has_induction": has_induction,
                "has_emergence": has_emergence,
                "notes": [],
            }

            if has_induction:
                event_info["notes"].append(
                    "Inducción detectada: se encontró un archivo sin "
                    "ventilación justo antes del inicio"
                )
            else:
                event_info["notes"].append(
                    "Inducción no capturada: el evento comienza con el "
                    "primer archivo del box (posible intubación previa)"
                )

            event_info["notes"].append(
                "Educción detectada: el archivo final pasa de tener "
                "CO2+TV_EXP a no tenerlos (extubación intra-archivo)"
            )

            all_events.append(event_info)

            print(
                f"    [{idx+1}] {ev['start_time'].strftime('%y%m%d_%H%M%S')} -> "
                f"{ev['end_time'].strftime('%y%m%d_%H%M%S')} "
                f"({ev['duration_str']}, {ev['num_files']} archivos, "
                f"{merged_size // 1024} KB)"
            )

    # Guardar índice
    index_data = {
        "source": "clinic_vitals",
        "description": (
            "Eventos de ventilación mecánica. Cada evento comienza con "
            "la intubación (primer archivo con CO2+TV_EXP tras uno sin) "
            "y termina con la extubación (archivo donde CO2+TV_EXP "
            "desaparecen intra-archivo)."
        ),
        "method": (
            "Para cada archivo con extubación intra-archivo, se busca "
            "hacia atrás en el tiempo dentro del mismo box hasta "
            "encontrar un archivo sin ventilación (intubación)."
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
