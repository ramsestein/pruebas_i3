"""
Genera ventanas de 10 minutos a partir de los eventos de ventilación
y las categoriza según el estado del paciente 8 horas después.

Clases:
  - 1 (intubado):   la ventana termina >= 8h antes de la extubación
  - 0 (extubado):   la ventana está a < 8h del final (o después de extubación)

Cada ventana se guarda como archivo Parquet con las señales originales.
"""
import os
import json
import argparse
import numpy as np
import pandas as pd
import vitaldb
from datetime import datetime, timedelta

WINDOW_SEC = 600  # 10 minutos
LOOKAHEAD_HOURS = 8
LOOKAHEAD_SEC = LOOKAHEAD_HOURS * 3600

# Señales esenciales a extraer
ESSENTIAL_TRACKS = [
    "Intellivue/CO2",
    "Intellivue/TV_EXP",
    "Intellivue/HR",
    "Intellivue/ART_MEAN",
    "Intellivue/PLETH_SAT_O2",
    "Intellivue/TV_INSP",
    "Intellivue/MV_EXP",
    "Intellivue/VENT_RR",
    "Intellivue/FIO2",
]


def parse_event_filename(fname: str):
    """Parse {box}_{start}_to_{end}.vital -> box, start_dt, end_dt"""
    base = fname.replace(".vital", "")
    parts = base.split("_to_")
    if len(parts) != 2:
        return None
    left = parts[0]
    right = parts[1]
    left_parts = left.split("_")
    if len(left_parts) < 3:
        return None
    box = "_".join(left_parts[:-2])
    start_str = left_parts[-2] + left_parts[-1]
    end_str = right.replace("_", "")
    try:
        start_dt = datetime.strptime(start_str, "%y%m%d%H%M%S")
        end_dt = datetime.strptime(end_str, "%y%m%d%H%M%S")
    except ValueError:
        return None
    return box, start_dt, end_dt


def main():
    p = argparse.ArgumentParser(description="Generar ventanas de 10min desde eventos clinic")
    p.add_argument("--input-dir", default="datasets/clinic_vitals/clinic_full_cases")
    p.add_argument("--output-dir", default="datasets/clinic_vitals/windows_10min")
    p.add_argument("--index", default="datasets/clinic_vitals/windows_index.json")
    p.add_argument("--window-sec", type=int, default=WINDOW_SEC)
    p.add_argument("--lookahead-hours", type=int, default=LOOKAHEAD_HOURS)
    p.add_argument("--interval", type=int, default=5, help="Intervalo de muestreo en segundos")
    args = p.parse_args()

    lookahead_sec = args.lookahead_hours * 3600
    os.makedirs(args.output_dir, exist_ok=True)

    # Listar archivos .vital
    files = sorted([
        f for f in os.listdir(args.input_dir)
        if f.endswith(".vital") and f != "clinic_full_cases_index.json"
    ])
    print(f"Total eventos: {len(files)}")

    all_windows = []
    total_windows = 0
    windows_intubado = 0
    windows_extubado = 0
    feature_names = None

    for fname in files:
        parsed = parse_event_filename(fname)
        if not parsed:
            print(f"  ERROR: no se pudo parsear {fname}")
            continue
        box, event_start, event_end = parsed
        event_duration = (event_end - event_start).total_seconds()

        print(f"\n{fname}")
        print(f"  Duración: {event_duration/3600:.1f}h ({event_start} -> {event_end})")

        # Cargar archivo .vital
        vf = vitaldb.VitalFile(os.path.join(args.input_dir, fname))
        tracks = vf.get_track_names()

        # Seleccionar tracks: los esenciales que existan + todos los numéricos
        selected = []
        for et in ESSENTIAL_TRACKS:
            if et in tracks:
                selected.append(et)

        # Añadir tracks numéricos adicionales (evitar duplicados)
        for t in tracks:
            if t not in selected and not t.startswith("Intellivue/ST_"):
                selected.append(t)

        if not selected:
            print("  -> Sin tracks seleccionables")
            continue

        # Cargar datos
        df = vf.to_pandas(selected, args.interval, return_datetime=True)
        if df.empty:
            print("  -> Sin datos")
            continue

        time_col = "Time"
        if time_col not in df.columns:
            print("  -> No tiene columna Time")
            continue

        t_start = df[time_col].iloc[0]
        t_end = df[time_col].iloc[-1]

        # Hacer event_end compatible con timezone de los datos
        if hasattr(t_start, 'tz') and t_start.tz is not None:
            ev_end_aware = datetime(event_end.year, event_end.month, event_end.day,
                                    event_end.hour, event_end.minute, event_end.second,
                                    tzinfo=t_start.tz)
        else:
            ev_end_aware = event_end

        window_start = t_start
        event_windows = 0

        while window_start + timedelta(seconds=args.window_sec) <= t_end:
            window_end = window_start + timedelta(seconds=args.window_sec)

            mask = (df[time_col] >= window_start) & (df[time_col] < window_end)
            win_df = df[mask].copy()

            if win_df.empty:
                window_start += timedelta(seconds=args.window_sec)
                continue

            # Categorizar
            check_time = window_start + timedelta(seconds=lookahead_sec)
            label = 1 if check_time <= ev_end_aware else 0

            if label == 1:
                windows_intubado += 1
            else:
                windows_extubado += 1

            # Nombre del archivo
            win_name = fname.replace(".vital", f"_{window_start.strftime('%y%m%d_%H%M%S')}.parquet")
            win_path = os.path.join(args.output_dir, win_name)

            # Guardar como Parquet (sin columna Time para ahorrar espacio)
            win_df.to_parquet(win_path, index=False)

            # Metadata
            win_info = {
                "window_file": win_name,
                "source_event": fname,
                "box": box,
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "event_start": event_start.isoformat(),
                "event_end": event_end.isoformat(),
                "label": label,
                "label_desc": "intubado" if label == 1 else "extubado",
                "check_time": check_time.isoformat(),
                "num_samples": len(win_df),
                "num_signals": len(selected),
                "signals": selected,
            }
            all_windows.append(win_info)
            event_windows += 1
            total_windows += 1

            window_start += timedelta(seconds=args.window_sec)

        print(f"  -> {event_windows} ventanas generadas")

    # Guardar índice
    index_data = {
        "source": "clinic_vitals",
        "description": f"Ventanas de {args.window_sec//60}min de señales vitales. "
                       f"Label=1 si el paciente sigue intubado {args.lookahead_hours}h después, "
                       f"Label=0 si ya fue extubado.",
        "window_seconds": args.window_sec,
        "lookahead_hours": args.lookahead_hours,
        "sampling_interval_sec": args.interval,
        "total_windows": total_windows,
        "windows_intubado": windows_intubado,
        "windows_extubado": windows_extubado,
        "generated_at": datetime.now().isoformat(),
        "windows": all_windows,
    }

    with open(args.index, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"RESUMEN")
    print(f"  Ventanas totales: {total_windows}")
    print(f"  Intubado (label=1): {windows_intubado}")
    print(f"  Extubado (label=0): {windows_extubado}")
    print(f"  Proporción: {windows_intubado/total_windows*100:.1f}% / {windows_extubado/total_windows*100:.1f}%")
    print(f"  Ventanas en: {args.output_dir}")
    print(f"  Índice en: {args.index}")


if __name__ == "__main__":
    main()
