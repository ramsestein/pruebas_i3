"""
Genera ventanas de 10 minutos desde los .parquet de mimic_full_cases
+ chartevents, calculando derivadas numericas e indices ventilatorios.

OPTIMIZACION DE MEMORIA:
- Solo carga columnas necesarias del parquet
- Pre-integra chartevents a 1 Hz una sola vez por paciente (merge_asof unico)
- Itera ventanas por slicing de indice (sin mascaras booleanas repetidas)
- Libera memoria explicitamente entre pacientes

Salidas:
  datasets/mimic3wdb/windows_10min_enriched/*.parquet
  datasets/mimic3wdb/windows_enriched_index.json
"""
import os
import re
import json
import gc
import glob
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from scipy.signal import savgol_filter
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
CASES_DIR  = Path("datasets/mimic3wdb/mimic_full_cases")
OUT_DIR    = Path("datasets/mimic3wdb/windows_10min_enriched")
INDEX      = Path("datasets/mimic3wdb/windows_enriched_index.json")
CHART_PATH = Path("datasets/mimic3wdb/mimic_chartevents_vent.parquet")

WINDOW_SEC  = 600
LOOKAHEAD_H = 8
INTERVAL_S  = 5

# ── Columnas deseadas y variantes de nombres ──────────────────────────────────
# Todas las columnas nativas son OPCIONALES. Si no existen, se rellenan con NaN.
NATIVE_COLS = ["Time", "HR", "RESP", "SpO2", "ABP_S", "ABP_D", "ABP_M",
               "PULSE", "CVP", "Temp"]
# Mapeo de variantes de nombres en MIMIC -> nombre canonico
NAME_VARIANTS = {
    "ABP SYS": "ABP_S", "ABP_S": "ABP_S",
    "ABP DIAS": "ABP_D", "ABP_D": "ABP_D",
    "ABP MEAN": "ABP_M", "ABP_M": "ABP_M",
    "ART SYS": "ABP_S", "ART DIAS": "ABP_D", "ART MEAN": "ABP_M",
    "Temp Rect": "Temp",
}

# itemids -> nombre param
ITEMID_MAP = {
    3420: "FiO2", 223835: "FiO2",
    505: "PEEP", 224700: "PEEP",
    681: "TV", 224685: "TV",
    507: "PIP", 224696: "PIP",
    618: "RR_V", 220210: "RR_V",
    682: "MV", 224687: "MV",
}

SAVGOL_WIN = 7

def parse_parquet_filename(fname: str):
    m = re.search(r"mimic_(\d+)_(\d{8})_(\d{6})_to_(\d{8})_(\d{6})\.parquet", fname)
    if not m:
        return None, None, None
    sid = int(m.group(1))
    t0  = datetime.strptime(m.group(2)+m.group(3), "%Y%m%d%H%M%S")
    t1  = datetime.strptime(m.group(4)+m.group(5), "%Y%m%d%H%M%S")
    return sid, t0, t1


def fill_nan(arr):
    s = pd.Series(arr, dtype="float32")
    return s.interpolate(method="linear").ffill().bfill().values.astype(np.float32)


def integrate_chartevents_once(df_case, pat_chart):
    """
    Integra chartevents del paciente al DataFrame del caso en UN solo merge_asof.
    Retorna df_case con columnas FiO2, PEEP, TV, PIP, RR_V, MV anadidas.
    """
    if pat_chart.empty:
        for col in ["FiO2","PEEP","TV","PIP","RR_V","MV"]:
            df_case[col] = np.nan
        return df_case

    df = df_case[["Time"]].copy()
    for param, group in pat_chart.groupby("param"):
        group = group.sort_values("charttime")
        merged = pd.merge_asof(
            df, group,
            left_on="Time", right_on="charttime",
            direction="backward"
        )
        df_case[param] = merged["VALUENUM"].values
    return df_case


def compute_numeric_derived(arr_dict):
    """
    Calcula derivadas e indices sobre diccionario de arrays (no DataFrame).
    Retorna diccionario {nombre_columna: array} con las nuevas columnas.
    """
    n = len(arr_dict["Time"]) if "Time" in arr_dict else 0
    if n < 7:
        return {}

    dt = INTERVAL_S
    out = {}
    numeric_cols = ["HR","RESP","SpO2","ABP_S","ABP_D","ABP_M",
                    "PULSE","CVP","Temp","FiO2","PEEP","TV","PIP","RR_V","MV"]

    for col in numeric_cols:
        if col not in arr_dict:
            continue
        vals = arr_dict[col]
        valid = np.sum(np.isfinite(vals))
        if valid < 5:
            continue
        filled = fill_nan(vals)

        d1 = np.gradient(filled, dt).astype(np.float32)
        d2 = np.gradient(d1, dt).astype(np.float32)
        try:
            filt = savgol_filter(filled, window_length=SAVGOL_WIN, polyorder=3).astype(np.float32)
            residual = (filled - filt).astype(np.float32)
        except Exception:
            filt = filled.copy()
            residual = np.zeros_like(filled)
        auc_cum = (np.cumsum(np.abs(filled)) / np.arange(1, n+1)).astype(np.float32)

        out[f"{col}_d1"]       = d1
        out[f"{col}_d2"]       = d2
        out[f"{col}_filtered"] = filt
        out[f"{col}_residual"] = residual
        out[f"{col}_auc_cum"]  = auc_cum

    # Indices ventilatorios
    if all(c in arr_dict for c in ("PIP","PEEP")):
        pip  = fill_nan(arr_dict["PIP"])
        peep = fill_nan(arr_dict["PEEP"])
        out["driving_pressure"] = (pip - peep).astype(np.float32)

        with np.errstate(divide="ignore", invalid="ignore"):
            out["peep_pip_ratio"] = np.where(pip > 0, peep / pip, np.nan).astype(np.float32)

    if all(c in arr_dict for c in ("TV","PIP","PEEP")):
        tv   = fill_nan(arr_dict["TV"])
        pip  = fill_nan(arr_dict["PIP"])
        peep = fill_nan(arr_dict["PEEP"])
        with np.errstate(divide="ignore", invalid="ignore"):
            cdyn = np.where((pip - peep) > 0, tv / (pip - peep), np.nan)
        out["compliance_dyn"] = cdyn.astype(np.float32)

    if all(c in arr_dict for c in ("RR_V","TV")):
        rr = fill_nan(arr_dict["RR_V"])
        tv = fill_nan(arr_dict["TV"])
        with np.errstate(divide="ignore", invalid="ignore"):
            rsbi = np.where(tv > 0, rr / (tv / 1000.0), np.nan)
        out["rsbi"] = rsbi.astype(np.float32)

    if all(c in arr_dict for c in ("RR_V","TV","PIP","PEEP")):
        rr   = fill_nan(arr_dict["RR_V"])
        tv   = fill_nan(arr_dict["TV"])
        pip  = fill_nan(arr_dict["PIP"])
        peep = fill_nan(arr_dict["PEEP"])
        tv_l = tv / 1000.0
        dp   = pip - peep
        with np.errstate(divide="ignore", invalid="ignore"):
            mp = (0.098 * rr * tv_l * (pip - dp / 2.0))
            mp = np.where(np.isfinite(mp), mp, np.nan)
        out["mechanical_power"] = mp.astype(np.float32)

    for arr_s, name in [("PIP", "delta_pip"), ("PEEP", "delta_peep"), ("TV", "delta_tv")]:
        if arr_s in arr_dict:
            arr = arr_dict[arr_s]
            filled = fill_nan(arr)
            out[name] = np.diff(filled, prepend=filled[0]).astype(np.float32)

    # Tendencias (rolling mediana)
    roll_win = max(3, int(60 / INTERVAL_S))
    for src, dst in [
        ("TV", "trend_tv"), ("MV", "trend_mv"), ("PIP", "trend_pip"),
        ("PEEP", "trend_peep"), ("RR_V", "trend_rr"), ("FiO2", "trend_fio2"),
    ]:
        if src in arr_dict:
            s = pd.Series(arr_dict[src])
            trend = s.rolling(roll_win, min_periods=1, center=True).median().values.astype(np.float32)
            out[dst] = trend

    return out


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Cargar chartevents una sola vez
    if CHART_PATH.exists():
        chart_df = pd.read_parquet(CHART_PATH)
        chart_df["charttime"] = pd.to_datetime(chart_df["CHARTTIME"])
        chart_df["param"] = chart_df["ITEMID"].map(ITEMID_MAP)
        chart_df = chart_df.dropna(subset=["param", "VALUENUM"])
        has_chart = True
        print(f"Chartevents cargados: {len(chart_df):,} filas")
    else:
        chart_df = pd.DataFrame()
        has_chart = False
        print("Sin chartevents")

    files = sorted(CASES_DIR.glob("*.parquet"))
    print(f"Total eventos: {len(files)}")

    all_windows = []
    total_w = w_intubado = w_extubado = 0

    for i, fpath in enumerate(files, 1):
        fname = fpath.name
        sid, vent_start, vent_end = parse_parquet_filename(fname)
        if sid is None:
            continue

        dur_h = (vent_end - vent_start).total_seconds() / 3600
        print(f"\n[{i}/{len(files)}] {fname}  ({dur_h:.1f}h)")

        # --- Cargar todo el parquet y normalizar nombres ---
        try:
            df = pd.read_parquet(fpath)
        except Exception as e:
            print(f"  -> ERROR cargando parquet: {e}")
            continue

        if "Time" not in df.columns:
            print("  -> ERROR: columna Time no encontrada")
            continue

        df["Time"] = pd.to_datetime(df["Time"])

        # Renombrar variantes a nombres canonicos
        rename_map = {}
        for v, k in NAME_VARIANTS.items():
            if v in df.columns and k not in df.columns:
                rename_map[v] = k
        df = df.rename(columns=rename_map)
        df = df.loc[:, ~df.columns.duplicated(keep='first')]

        # Asegurar que todas las columnas canonicas existan (NaN si faltan)
        for col in NATIVE_COLS:
            if col not in df.columns:
                df[col] = np.nan

        print(f"  datos: {df['Time'].iloc[0]} -> {df['Time'].iloc[-1]}  ({len(df):,} filas)")
        print(f"  columnas presentes: {sum(1 for c in NATIVE_COLS if df[c].notna().any())}/{len(NATIVE_COLS)}")

        # --- Integrar chartevents una sola vez ---
        if has_chart:
            pat_chart = chart_df[chart_df["SUBJECT_ID"] == sid][["charttime","param","VALUENUM"]].copy()
            print(f"  chartevents: {len(pat_chart):,} filas")
        else:
            pat_chart = pd.DataFrame()

        df = integrate_chartevents_once(df, pat_chart)
        del pat_chart  # liberar

        # --- Resamplear todo a 5s de una vez ---
        df = df.set_index("Time").resample(f"{INTERVAL_S}s").mean().reset_index()
        print(f"  resampled: {len(df):,} filas a {INTERVAL_S}s")

        # --- Generar ventanas por slicing de indice ---
        n_total = len(df)
        win_rows = WINDOW_SEC // INTERVAL_S  # 120
        event_windows = 0

        for start_idx in range(0, n_total - win_rows + 1, win_rows):
            end_idx = start_idx + win_rows
            win_df = df.iloc[start_idx:end_idx].copy()
            if len(win_df) < win_rows * 0.8:
                continue

            ws = win_df["Time"].iloc[0]
            we = win_df["Time"].iloc[-1] + timedelta(seconds=INTERVAL_S)

            # Label
            check_time = ws + timedelta(hours=LOOKAHEAD_H)
            label = 1 if check_time < vent_end else 0
            if label == 1:
                w_intubado += 1
            else:
                w_extubado += 1

            # --- Derivadas (sobre arrays, no DataFrame grande) ---
            arr_dict = {col: win_df[col].values.astype(np.float32) for col in win_df.columns}
            try:
                derived = compute_numeric_derived(arr_dict)
                for dname, darr in derived.items():
                    win_df[dname] = darr
            except Exception as e:
                print(f"    WARN derivadas: {e}")

            # Guardar
            win_name = f"mimic_{sid}_{ws.strftime('%Y%m%d_%H%M%S')}.parquet"
            win_df.to_parquet(OUT_DIR / win_name, index=False)

            all_windows.append({
                "window_file": win_name,
                "subject_id": sid,
                "window_start": ws.isoformat(),
                "window_end": we.isoformat(),
                "vent_end": vent_end.isoformat(),
                "label": label,
                "label_desc": "intubado" if label == 1 else "extubado",
                "num_samples": len(win_df),
                "signals": [c for c in win_df.columns if c != "Time"],
            })
            event_windows += 1
            total_w += 1

        print(f"  -> {event_windows} ventanas")

        # --- Liberar memoria explicitamente ---
        del df, win_df
        gc.collect()

    # Guardar indice
    index_data = {
        "source": "mimic3wdb-enriched",
        "description": (
            f"Ventanas de {WINDOW_SEC//60}min a {INTERVAL_S}s. "
            f"Label=1 si intubado {LOOKAHEAD_H}h despues. "
            f"Incluye derivadas e indices ventilatorios."
        ),
        "window_seconds": WINDOW_SEC,
        "lookahead_hours": LOOKAHEAD_H,
        "sampling_interval_sec": INTERVAL_S,
        "total_windows": total_w,
        "windows_intubado": w_intubado,
        "windows_extubado": w_extubado,
        "generated_at": datetime.now().isoformat(),
        "windows": all_windows,
    }
    with open(INDEX, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"RESUMEN")
    print(f"  Ventanas totales:    {total_w:,}")
    print(f"  Intubado (label=1):  {w_intubado:,}")
    print(f"  Extubado (label=0):  {w_extubado:,}")
    if total_w > 0:
        print(f"  Proporcion: {w_intubado/total_w*100:.1f}% / {w_extubado/total_w*100:.1f}%")
    print(f"  Ventanas en:  {OUT_DIR}")
    print(f"  Indice en:    {INDEX}")


if __name__ == "__main__":
    main()
