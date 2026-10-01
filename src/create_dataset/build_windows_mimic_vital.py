"""
Genera ventanas de 10 minutos desde los archivos .parquet de mimic_full_cases.
Mantiene resolucion maxima (1 Hz) y empareja chartevents a cada timestamp.

Label=1 si el paciente sigue intubado 8h despues del inicio de la ventana.

Salidas:
  datasets/mimic3wdb/windows_10min/*.parquet   (~600 filas por ventana)
  datasets/mimic3wdb/windows_index.json
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
MIMIC_DIR = Path("datasets/mimic3wdb")
CASES_DIR = MIMIC_DIR / "mimic_full_cases"
WIN_DIR   = MIMIC_DIR / "windows_10min"
WIN_INDEX = MIMIC_DIR / "windows_index.json"

# ── Parameters ────────────────────────────────────────────────────────────────
WINDOW_SEC  = 600    # 10 min
LOOKAHEAD_H = 8
INTERVAL_S  = 1      # 1 Hz (maxima resolucion)

# Chartevents: nombre corto -> set de itemids
CHART_ITEMS = {
    "FiO2": {3420, 223835},
    "PEEP": {505,  224700},
    "TV":   {681,  224685},
    "PIP":  {507,  224696},
    "RR_V": {618,  220210},
    "MV":   {682,  224687},
}


def parse_mimic_filename(fname: str):
    """Parse mimic_{sid}_{start}_to_{end}.parquet -> sid, start_dt, end_dt"""
    base = fname.replace(".parquet", "")
    parts = base.split("_to_")
    if len(parts) != 2:
        return None
    left = parts[0]
    right = parts[1]
    left_parts = left.split("_")
    if len(left_parts) < 4:
        return None
    sid = left_parts[1]
    start_str = left_parts[2] + left_parts[3]
    end_str = right.replace("_", "")
    try:
        start_dt = datetime.strptime(start_str, "%Y%m%d%H%M%S")
        end_dt = datetime.strptime(end_str, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return int(sid), start_dt, end_dt


def enrich_with_chartevents(win_df: pd.DataFrame, pat_chart: pd.DataFrame) -> pd.DataFrame:
    """
    Para cada timestamp de win_df, busca el ultimo chartevent de cada parametro
    antes o exacto de ese timestamp (forward-fill).
    Usa merge_asof para eficiencia.
    """
    if pat_chart.empty:
        return win_df

    win = win_df.copy().sort_values("Time").reset_index(drop=True)
    win_time = win[["Time"]].copy()

    for cname, iids in CHART_ITEMS.items():
        sub = pat_chart[pat_chart["itemid"].isin(iids)][["charttime", "valuenum"]].copy()
        if sub.empty:
            win[cname] = np.nan
            continue
        sub = sub.sort_values("charttime")
        # merge_asof: para cada Time en win_time, busca el charttime mas cercano <= Time
        merged = pd.merge_asof(
            win_time, sub,
            left_on="Time", right_on="charttime",
            direction="backward"
        )
        win[cname] = merged["valuenum"].values

    return win


def main():
    WIN_DIR.mkdir(parents=True, exist_ok=True)

    # Cargar chartevents
    chart_path = MIMIC_DIR / "chartevents_vent.parquet"
    if chart_path.exists():
        chart_df = pd.read_parquet(chart_path)
        chart_df["charttime"] = pd.to_datetime(chart_df["charttime"])
        has_chart = True
        print(f"Chartevents cargados: {len(chart_df):,} filas")
    else:
        chart_df  = pd.DataFrame()
        has_chart = False
        print("Sin chartevents — parametros ventilatorios seran NaN")

    files = sorted([f.name for f in CASES_DIR.glob("*.parquet")])
    print(f"Total eventos: {len(files)}")

    all_windows = []
    total_w     = 0
    w_intubado  = 0
    w_extubado  = 0

    for fname in files:
        parsed = parse_mimic_filename(fname)
        if not parsed:
            print(f"  ERROR: no se pudo parsear {fname}")
            continue
        sid, vent_start, vent_end = parsed
        event_duration = (vent_end - vent_start).total_seconds()

        print(f"\n{fname}")
        print(f"  Duracion: {event_duration/3600:.1f}h ({vent_start} -> {vent_end})")

        # Cargar .parquet (ya esta a 1 Hz)
        df = pd.read_parquet(CASES_DIR / fname)
        df["Time"] = pd.to_datetime(df["Time"])

        if df.empty:
            print("  -> Sin datos")
            continue

        t_start = df["Time"].iloc[0]
        t_end   = df["Time"].iloc[-1]
        print(f"  datos: {t_start} -> {t_end}  ({len(df):,} filas)")

        # Hacer vent_end compatible con timezone
        if hasattr(t_start, 'tz') and t_start.tz is not None:
            ev_end_aware = datetime(vent_end.year, vent_end.month, vent_end.day,
                                    vent_end.hour, vent_end.minute, vent_end.second,
                                    tzinfo=t_start.tz)
        else:
            ev_end_aware = vent_end

        # Chartevents de este paciente (pre-filtrados)
        if has_chart and not chart_df.empty:
            pat_chart = chart_df[chart_df["subject_id"] == sid].copy()
            pat_chart = pat_chart.sort_values("charttime")
        else:
            pat_chart = pd.DataFrame()

        window_start = t_start
        event_windows = 0

        while window_start + timedelta(seconds=WINDOW_SEC) <= t_end:
            win_end = window_start + timedelta(seconds=WINDOW_SEC)

            mask   = (df["Time"] >= window_start) & (df["Time"] < win_end)
            win_df = df[mask].copy().reset_index(drop=True)

            if win_df.empty:
                window_start += timedelta(seconds=WINDOW_SEC)
                continue

            # Label
            check_time = window_start + timedelta(hours=LOOKAHEAD_H)
            label      = 1 if check_time < ev_end_aware else 0

            if label == 1:
                w_intubado += 1
            else:
                w_extubado += 1

            # Enriquecer con chartevents a maxima resolucion
            if not pat_chart.empty:
                win_df = enrich_with_chartevents(win_df, pat_chart)

            # Guardar ventana
            win_name = f"mimic_{sid}_{window_start.strftime('%Y%m%d_%H%M%S')}.parquet"
            win_df.to_parquet(WIN_DIR / win_name, index=False)

            all_windows.append({
                "window_file":  win_name,
                "subject_id":   sid,
                "window_start": window_start.isoformat(),
                "window_end":   win_end.isoformat(),
                "vent_end":     vent_end.isoformat(),
                "label":        label,
                "label_desc":   "intubado" if label == 1 else "extubado",
                "num_samples":  len(win_df),
                "signals":      [c for c in win_df.columns],
            })
            event_windows += 1
            total_w       += 1
            window_start += timedelta(seconds=WINDOW_SEC)

        print(f"  -> {event_windows} ventanas generadas")

    # Guardar indice
    index_data = {
        "source":                "mimic3wdb-matched",
        "description":           (
            f"Ventanas de {WINDOW_SEC//60}min de señales vitales MIMIC-III a 1 Hz. "
            f"Label=1 si el paciente sigue intubado {LOOKAHEAD_H}h despues, "
            f"Label=0 si ya fue extubado."
        ),
        "window_seconds":        WINDOW_SEC,
        "lookahead_hours":       LOOKAHEAD_H,
        "sampling_interval_sec": INTERVAL_S,
        "total_windows":         total_w,
        "windows_intubado":      w_intubado,
        "windows_extubado":      w_extubado,
        "has_chartevents":       has_chart,
        "generated_at":          datetime.now().isoformat(),
        "windows":               all_windows,
    }

    with open(WIN_INDEX, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"RESUMEN")
    print(f"  Ventanas totales:    {total_w:,}")
    print(f"  Intubado (label=1):  {w_intubado:,}")
    print(f"  Extubado (label=0):  {w_extubado:,}")
    if total_w > 0:
        print(f"  Proporcion: "
              f"{w_intubado/total_w*100:.1f}% / {w_extubado/total_w*100:.1f}%")
    print(f"  Ventanas en:  {WIN_DIR}")
    print(f"  Indice en:    {WIN_INDEX}")


if __name__ == "__main__":
    main()
