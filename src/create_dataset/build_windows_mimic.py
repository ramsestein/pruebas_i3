"""
Paso 3: Construye ventanas de 10 min desde los archivos WFDB numerics de MIMIC-III.

Label=1 si el paciente sigue intubado 8h despues del inicio de la ventana.

Combina señales continuas (numerics WFDB, 1 Hz) con parametros ventilatorios
de chartevents (forward-fill del ultimo valor conocido antes de la ventana).

Salidas:
  datasets/mimic3wdb/windows_10min/*.parquet
  datasets/mimic3wdb/windows_index.json
"""
import json
import numpy as np
import pandas as pd
import wfdb
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
MIMIC_DIR = Path("datasets/mimic3wdb")
RAW_DIR   = MIMIC_DIR / "raw"
WIN_DIR   = MIMIC_DIR / "windows_10min"
WIN_INDEX = MIMIC_DIR / "windows_index.json"

# ── Parameters ────────────────────────────────────────────────────────────────
WINDOW_SEC  = 600    # 10 min
LOOKAHEAD_H = 8
INTERVAL_S  = 5      # resamplear a 5 s (igual que clinic_vitals)

# ── Signal mapping: MIMIC numerics column names ───────────────────────────────
# Candidatos por orden de preferencia para cada señal
NUMERICS_MAP = {
    "HR":    ["HR"],
    "SpO2":  ["SpO2", "%SpO2", "SaO2"],
    "RESP":  ["RESP", "RR"],
    "ABP_M": ["ART Mean", "ABP Mean", "Art Mean", "ABP-M"],
    "ABP_S": ["ART Sys",  "ABP Sys",  "Art Sys"],
    "ABP_D": ["ART Dias", "ABP Dias", "Art Dias"],
    "NBP_M": ["NBP Mean", "NBP-M"],
    "PULSE": ["PULSE", "Pulse"],
}

# Chartevents: nombre corto -> set de itemids (CareVue + Metavision)
CHART_ITEMS = {
    "FiO2": {3420, 223835},
    "PEEP": {505,  224700},
    "TV":   {681,  224685},
    "PIP":  {507,  224696},
    "RR_V": {618,  220210},
    "MV":   {682,  224687},
}


# ── WFDB reader ───────────────────────────────────────────────────────────────
def read_numerics(local_record_path: str) -> pd.DataFrame:
    """Lee archivo numerics WFDB local y devuelve DataFrame con columna 'Time'."""
    try:
        rec = wfdb.rdrecord(local_record_path)
    except Exception as e:
        print(f"    ERROR rdrecord({local_record_path}): {e}")
        return pd.DataFrame()

    if rec is None or rec.p_signal is None:
        return pd.DataFrame()

    # Timestamps
    fs     = rec.fs if rec.fs and rec.fs > 0 else 1.0
    dt_s   = 1.0 / fs
    n      = rec.sig_len

    # Base datetime
    start_dt = None
    if hasattr(rec, "base_datetime") and rec.base_datetime:
        start_dt = rec.base_datetime
    elif hasattr(rec, "base_time") and rec.base_time:
        from datetime import date as _date
        bd = rec.base_date if hasattr(rec, "base_date") and rec.base_date else _date(2000, 1, 1)
        start_dt = datetime.combine(bd, rec.base_time)

    if start_dt is None:
        print("    WARN: sin fecha base — no se puede temporizar el registro")
        return pd.DataFrame()

    # Strip timezone to keep all datetimes naive (matches clinical CSV timestamps)
    if hasattr(start_dt, "tzinfo") and start_dt.tzinfo is not None:
        start_dt = start_dt.replace(tzinfo=None)

    # Vectorized timestamp generation (much faster than list comprehension)
    freq_ns = int(dt_s * 1e9)   # nanoseconds per sample
    times   = pd.date_range(start=start_dt, periods=n, freq=pd.tseries.offsets.Nano(freq_ns))

    df = pd.DataFrame(rec.p_signal, columns=rec.sig_name)
    df.insert(0, "Time", times)

    # Reemplazar valores centinela (muy negativos) con NaN
    for col in rec.sig_name:
        df.loc[df[col] < -32000, col] = np.nan

    return df


# ── Chartevents helpers ───────────────────────────────────────────────────────
def get_chart_value_before(pat_chart: pd.DataFrame, param: str, cutoff: datetime) -> float:
    """
    Obtiene el ultimo valor de 'param' en chartevents antes de cutoff.
    Forward-fill: el ultimo valor conocido se aplica a toda la ventana.
    """
    iids = CHART_ITEMS.get(param, set())
    sub  = pat_chart[pat_chart["itemid"].isin(iids)]
    sub  = sub[sub["charttime"] < cutoff]
    if sub.empty:
        return np.nan
    last_val = sub.sort_values("charttime").iloc[-1]["valuenum"]
    return float(last_val) if pd.notna(last_val) else np.nan


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    WIN_DIR.mkdir(parents=True, exist_ok=True)

    candidates = pd.read_csv(
        MIMIC_DIR / "candidate_list.csv",
        parse_dates=["vent_start", "vent_end", "intime", "outtime"],
        dtype_backend="numpy_nullable",
    )

    # Chartevents
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

    all_windows = []
    total_w     = 0
    w_intubado  = 0
    w_extubado  = 0

    for _, row in candidates.iterrows():
        sid        = int(row["subject_id"])
        vent_start = pd.to_datetime(row["vent_start"]).replace(tzinfo=None)
        vent_end   = pd.to_datetime(row["vent_end"]).replace(tzinfo=None)

        # Use ALL numerics records for this patient
        all_recs_raw = str(row.get("numerics_records_all", row.get("numerics_record", "")))
        all_recs = [r.strip() for r in all_recs_raw.split("|") if r.strip()]
        if not all_recs:
            continue

        print(f"\n[sid={sid}]  {len(all_recs)} registros numerics")

        # Concatenate all available numerics segments into one DataFrame
        dfs = []
        for rec_name in all_recs:
            parts      = rec_name.split("/")
            rec_base   = parts[-1]
            rel_subdir = "/".join(parts[:-1])
            local_path = str(RAW_DIR / rel_subdir / rec_base)

            if not Path(local_path + ".hea").exists():
                continue

            seg_df = read_numerics(local_path)
            if not seg_df.empty:
                dfs.append(seg_df)

        if not dfs:
            print("  -> Sin datos")
            continue

        df = pd.concat(dfs, ignore_index=True).sort_values("Time").reset_index(drop=True)
        df = df.drop_duplicates(subset="Time").reset_index(drop=True)

        # Use first record name for labeling
        rec_name = all_recs[0]

        if df.empty or "Time" not in df.columns:
            print("  -> Sin datos")
            continue

        # Resamplear a INTERVAL_S segundos
        df = (
            df.set_index("Time")
              .resample(f"{INTERVAL_S}s")
              .mean()
              .reset_index()
        )

        t_start = df["Time"].iloc[0]
        t_end   = df["Time"].iloc[-1]
        dur_h   = (t_end - t_start).total_seconds() / 3600
        print(f"  datos: {t_start} -> {t_end}  ({dur_h:.1f}h)")
        print(f"  MV:    {vent_start} -> {vent_end}  ({(vent_end-vent_start).total_seconds()/3600:.1f}h)")
        print(f"  señales: {[c for c in df.columns if c != 'Time'][:8]}")

        # Restrict to MV period only
        win_floor = max(t_start, vent_start)
        win_ceil  = min(t_end,   vent_end)

        if win_floor >= win_ceil:
            print(f"  -> Sin overlap datos/MV (datos:{t_start:%Y-%m-%d}—{t_end:%Y-%m-%d}  "
                  f"MV:{vent_start:%Y-%m-%d}—{vent_end:%Y-%m-%d})")
            continue

        # Chartevents para este paciente
        if has_chart and not chart_df.empty:
            pat_chart = chart_df[chart_df["subject_id"] == sid].copy()
            pat_chart = pat_chart.sort_values("charttime")
        else:
            pat_chart = pd.DataFrame()

        win_start   = win_floor
        win_count   = 0

        while win_start + timedelta(seconds=WINDOW_SEC) <= win_ceil:
            win_end = win_start + timedelta(seconds=WINDOW_SEC)

            mask   = (df["Time"] >= win_start) & (df["Time"] < win_end)
            win_df = df[mask].copy()

            if win_df.empty:
                win_start += timedelta(seconds=WINDOW_SEC)
                continue

            # Label
            check_time = win_start + timedelta(hours=LOOKAHEAD_H)
            label      = 1 if check_time < vent_end else 0

            if label == 1:
                w_intubado += 1
            else:
                w_extubado += 1

            # Enriquecer con chartevents (forward-fill al inicio de la ventana)
            if not pat_chart.empty:
                for cname in CHART_ITEMS:
                    win_df[cname] = get_chart_value_before(pat_chart, cname, win_start)

            # Guardar ventana
            win_name = f"mimic_{sid}_{win_start.strftime('%Y%m%d_%H%M%S')}.parquet"
            win_df.to_parquet(WIN_DIR / win_name, index=False)

            all_windows.append({
                "window_file":  win_name,
                "subject_id":   sid,
                "window_start": win_start.isoformat(),
                "window_end":   win_end.isoformat(),
                "vent_end":     vent_end.isoformat(),
                "label":        label,
                "label_desc":   "intubado" if label == 1 else "extubado",
                "num_samples":  len(win_df),
                "signals":      [c for c in win_df.columns],
            })
            win_count += 1
            total_w   += 1
            win_start += timedelta(seconds=WINDOW_SEC)

        print(f"  -> {win_count} ventanas generadas")

    # Guardar indice
    index_data = {
        "source":                "mimic3wdb-matched",
        "description":           (
            f"Ventanas de {WINDOW_SEC//60}min de señales vitales MIMIC-III. "
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
