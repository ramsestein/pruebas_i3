#!/usr/bin/env python3
"""
[Sustituido en Fase 1.2 por `src/create_dataset/build_signal_cases.py`]
Este builder detectaba la ventilación buscando nombres de pista en los bytes
del gzip, recortaba el merge a 7 días y reutilizaba salidas previas ("resume").
Se conserva solo como referencia histórica.

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

import vitaldb

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


# ── fusión de archivos ─────────────────────────────────────────────────

# Tracks que se conservan en el .vital fusionado (los que usa la armonización).
MERGE_TRACK_NAMES = [
    # Waveforms
    "Intellivue/ECG_II",
    "Intellivue/PLETH",
    "Intellivue/ABP",
    # Numéricos (constantes vitales + ventilador)
    "Intellivue/ECG_HR",
    "Intellivue/ABP_SYS",
    "Intellivue/ABP_DIA",
    "Intellivue/ABP_MEAN",
    "Intellivue/PLETH_SAT_O2",
    "Intellivue/RR",
    "Intellivue/TV_EXP",
    "Intellivue/MV_EXP",
]


def read_vital_dt(path: str):
    """(dtstart, dtend) desde la cabecera sin descomprimir el resto del fichero."""
    import struct as _s
    try:
        with open(path, "rb") as fh:
            gz = gzip.GzipFile(fileobj=fh)
            if gz.read(4) != b"VITA":
                return None
            gz.read(4)
            hb = gz.read(2)
            if len(hb) < 2:
                return None
            hl = _s.unpack("<H", hb)[0]
            if hl < 26:
                return None  # formato antiguo: dtstart/dtend no están en cabecera
            h = gz.read(hl)
            if len(h) < 26:
                return None
            return _s.unpack("<d", h[10:18])[0], _s.unpack("<d", h[18:26])[0]
    except Exception:
        return None


def merge_vital_files(event: dict, output_dir: str) -> tuple[str, float, float] | tuple[None, None, None]:
    """
    Fusiona los .vital del evento en un único archivo válido, fichero a fichero
    y tolerante a errores de lectura (los ficheros ilegibles se omiten).
    Si el fichero de salida ya existe, se reutiliza (resume).
    Devuelve (ruta_salida, t0_unix, tend_unix) o (None, None, None) si falla.
    """
    start_str = event["start_time"].strftime("%y%m%d_%H%M%S")
    end_str = event["end_time"].strftime("%y%m%d_%H%M%S")
    box = event["box"]
    fname = f"{box}_{start_str}_to_{end_str}.vital"
    out_path = os.path.join(output_dir, fname)

    os.makedirs(output_dir, exist_ok=True)

    # Resume: si ya existe un fichero válido, reutilizarlo
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        d = read_vital_dt(out_path)
        if d is not None:
            return out_path, d[0], d[1]

    merged = None
    skipped = 0
    for p in event["files"]:
        try:
            vf = vitaldb.VitalFile(p, track_names=MERGE_TRACK_NAMES)
        except Exception as e:  # noqa: BLE001
            print(f"  (skip {os.path.basename(p)}: {e})")
            skipped += 1
            continue
        if vf is None or not getattr(vf, "trks", None):
            skipped += 1
            continue
        if merged is None:
            merged = vf
            continue
        if abs(merged.dtstart - vf.dtstart) > 7 * 24 * 3600:
            skipped += 1
            continue
        merged.dtstart = min(merged.dtstart, vf.dtstart)
        merged.dtend = max(merged.dtend, vf.dtend)
        for dname, dev in vf.devs.items():
            if dname not in merged.devs:
                merged.devs[dname] = dev
        for dtname, trk in vf.trks.items():
            if dtname in merged.trks:
                merged.trks[dtname].recs.extend(trk.recs)
            else:
                merged.trks[dtname] = trk
        for dtname in vf.order:
            if dtname not in merged.order:
                merged.order.append(dtname)

    if merged is None or not merged.trks:
        print(f"  ERROR: sin datos legibles para {fname}")
        return None, None, None
    if skipped:
        print(f"  (aviso: {skipped} ficheros omitidos en {fname})")

    try:
        merged.to_vital(out_path)
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR escribiendo {fname}: {e}")
        return None, None, None

    return out_path, float(merged.dtstart), float(merged.dtend)


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
            res = merge_vital_files(ev, args.output_dir)
            if res is None or res[0] is None:
                print(f"    [X] omitido: {box} {ev['start_time']} -> {ev['end_time']}")
                continue
            merged_path, t0_unix, tend_unix = res
            merged_size = os.path.getsize(merged_path)

            event_info = {
                "event_id": f"{box}_event_{idx+1}",
                "box": box,
                "file": os.path.basename(merged_path),
                "file_size_bytes": merged_size,
                "start_time": ev["start_time"].isoformat(),
                "end_time": ev["end_time"].isoformat(),
                "t0_unix": t0_unix,
                "tend_unix": tend_unix,
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
