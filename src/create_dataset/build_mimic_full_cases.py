"""
Crea archivos .vital de casos completos de ventilacion mecanica desde MIMIC-III WFDB.

Entrada:
  datasets/mimic3wdb/candidate_list.csv
  datasets/mimic3wdb/raw/   <- archivos WFDB numerics descargados

Salida:
  datasets/mimic3wdb/mimic_full_cases/
    mimic_{subject_id}_{vent_start}_to_{vent_end}.vital
  datasets/mimic3wdb/mimic_full_cases_index.json

Cada archivo .vital contiene las senales vitales continuas del periodo de ventilacion
mecanica del paciente, en formato compatible con vitaldb (igual que clinic_full_cases).
"""
import json
import numpy as np
import pandas as pd
import wfdb
import vitaldb
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
MIMIC_DIR   = Path("datasets/mimic3wdb")
RAW_DIR     = MIMIC_DIR / "raw"
OUT_DIR     = MIMIC_DIR / "mimic_full_cases"
INDEX_PATH  = MIMIC_DIR / "mimic_full_cases_index.json"

# ── Signal mapping ─────────────────────────────────────────────────────────────
# MIMIC numerics column names -> output track names (without device prefix)
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

# Invert mapping: source column -> canonical name
COL_TO_CANON = {}
for canon, candidates in NUMERICS_MAP.items():
    for c in candidates:
        COL_TO_CANON[c] = canon


def read_numerics(local_record_path: str) -> pd.DataFrame:
    """Lee archivo numerics WFDB y devuelve DataFrame con columna 'Time' (datetime)."""
    try:
        rec = wfdb.rdrecord(local_record_path)
    except Exception as e:
        print(f"    ERROR rdrecord({local_record_path}): {e}")
        return pd.DataFrame()

    if rec is None or rec.p_signal is None:
        return pd.DataFrame()

    fs   = rec.fs if rec.fs and rec.fs > 0 else 1.0
    dt_s = 1.0 / fs
    n    = rec.sig_len

    # Base datetime
    start_dt = None
    if hasattr(rec, "base_datetime") and rec.base_datetime:
        start_dt = rec.base_datetime
    elif hasattr(rec, "base_time") and rec.base_time:
        from datetime import date as _date
        bd = rec.base_date if hasattr(rec, "base_date") and rec.base_date else _date(2000, 1, 1)
        start_dt = datetime.combine(bd, rec.base_time)

    if start_dt is None:
        print("    WARN: sin fecha base")
        return pd.DataFrame()

    if hasattr(start_dt, "tzinfo") and start_dt.tzinfo is not None:
        start_dt = start_dt.replace(tzinfo=None)

    freq_ns = int(dt_s * 1e9)
    times = pd.date_range(start=start_dt, periods=n, freq=pd.tseries.offsets.Nano(freq_ns))

    df = pd.DataFrame(rec.p_signal, columns=rec.sig_name)
    df.insert(0, "Time", times)

    # Sentinel values -> NaN
    for col in rec.sig_name:
        df.loc[df[col] < -32000, col] = np.nan

    return df


def write_vital_from_df(df: pd.DataFrame, out_path: Path, device_prefix: str = "MIMIC"):
    """
    Escribe un DataFrame (con columna 'Time' datetime) como archivo .vital
    usando add_track directamente (sin CSV intermedio) para ahorrar memoria.
    """
    if df.empty:
        return False

    numeric_cols = [c for c in df.columns if c != "Time" and pd.api.types.is_numeric_dtype(df[c])]
    if not numeric_cols:
        return False

    # Convert Time to unix timestamp seconds (int)
    time_sec = df["Time"].apply(
        lambda dt: int(dt.replace(tzinfo=timezone.utc).timestamp()) if dt.tzinfo is None else int(dt.timestamp())
    ).values

    vf = vitaldb.VitalFile()

    for col in numeric_cols:
        # Sanitize: vitaldb uses '/' as device/track separator
        safe_col = col.replace("/", "_")
        track_name = f"{device_prefix}/{safe_col}" if device_prefix else safe_col

        vals = df[col].astype(np.float32).values
        # drop NaN
        mask = ~np.isnan(vals)
        if not mask.any():
            continue

        t_sec = time_sec[mask]
        v_arr = vals[mask]
        n = len(v_arr)

        # Fast O(n) chunking using direct numpy slicing (not O(n²) boolean masks)
        CHUNK_SAMPLES = 60  # 60 samples = 60 seconds at 1 Hz
        recs = []
        for start in range(0, n, CHUNK_SAMPLES):
            end = start + CHUNK_SAMPLES
            if end > n:
                end = n
            recs.append({"dt": int(t_sec[start]), "val": v_arr[start:end]})

        if not recs:
            continue

        vf.add_track(track_name, recs, srate=1)

    if not vf.get_track_names():
        return False

    try:
        vf.save_vital(str(out_path))
        return True
    except Exception as e:
        print(f"    ERROR saving .vital: {e}")
        return False


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    candidates = pd.read_csv(
        MIMIC_DIR / "candidate_list.csv",
        parse_dates=["vent_start", "vent_end", "intime", "outtime"],
        dtype_backend="numpy_nullable",
    )

    all_events = []
    total_cases = 0
    skipped = 0

    for _, row in candidates.iterrows():
        sid        = int(row["subject_id"])
        vent_start = pd.to_datetime(row["vent_start"]).replace(tzinfo=None)
        vent_end   = pd.to_datetime(row["vent_end"]).replace(tzinfo=None)

        try:
            all_recs_raw = str(row.get("numerics_records_all", row.get("numerics_record", "")))
            all_recs = [r.strip() for r in all_recs_raw.split("|") if r.strip()]
            if not all_recs:
                continue

            print(f"\n[sid={sid}]  {len(all_recs)} registros numerics")

            # Read and concatenate all numerics records
            dfs = []
            for rec_name in all_recs:
                parts      = rec_name.split("/")
                rec_base   = parts[-1]
                rel_subdir = "/".join(parts[:-1])
                local_path = str(RAW_DIR / rel_subdir / rec_base)

                if not Path(local_path + ".hea").exists():
                    print(f"    SKIP (no .hea): {rec_name}")
                    continue

                seg_df = read_numerics(local_path)
                if not seg_df.empty:
                    dfs.append(seg_df)
                else:
                    print(f"    SKIP (empty): {rec_name}")

            if not dfs:
                print("  -> Sin datos disponibles")
                skipped += 1
                continue

            df = pd.concat(dfs, ignore_index=True).sort_values("Time").reset_index(drop=True)
            df = df.drop_duplicates(subset="Time").reset_index(drop=True)

            # Rename columns to canonical names
            rename_map = {}
            for col in df.columns:
                if col == "Time":
                    continue
                canon = COL_TO_CANON.get(col, col)
                if canon != col:
                    rename_map[col] = canon
            if rename_map:
                df = df.rename(columns=rename_map)

            # Restrict to MV period
            t_start = df["Time"].iloc[0]
            t_end   = df["Time"].iloc[-1]
            win_floor = max(t_start, vent_start)
            win_ceil  = min(t_end, vent_end)

            if win_floor >= win_ceil:
                print(f"  -> Sin overlap datos/MV (datos:{t_start}--{t_end}  MV:{vent_start}--{vent_end})")
                skipped += 1
                continue

            df_case = df[(df["Time"] >= win_floor) & (df["Time"] <= win_ceil)].copy().reset_index(drop=True)
            # Deduplicate columns (keep first) – parquet cannot have duplicate column names
            if df_case.columns.duplicated().any():
                df_case = df_case.loc[:, ~df_case.columns.duplicated()]
            if df_case.empty:
                print("  -> Sin datos tras filtrar MV")
                skipped += 1
                continue

            dur_h = (df_case["Time"].iloc[-1] - df_case["Time"].iloc[0]).total_seconds() / 3600
            print(f"  datos: {df_case['Time'].iloc[0]} -> {df_case['Time'].iloc[-1]}  ({dur_h:.1f}h)")
            print(f"  señales: {[c for c in df_case.columns if c != 'Time']}")
            print(f"  filas: {len(df_case):,}")

            # Write .vital
            start_str = vent_start.strftime("%Y%m%d_%H%M%S")
            end_str   = vent_end.strftime("%Y%m%d_%H%M%S")
            fname     = f"mimic_{sid}_{start_str}_to_{end_str}.vital"
            out_path  = OUT_DIR / fname

            try:
                ok = write_vital_from_df(df_case, out_path, device_prefix="MIMIC")
            except Exception as e:
                print(f"  -> ERROR escribiendo {fname}: {e}")
                ok = False
            if not ok:
                print(f"  -> ERROR escribiendo {fname}")
                skipped += 1
                continue

            # Also save as parquet for fast downstream windowing
            parquet_path = out_path.with_suffix(".parquet")
            try:
                df_case.to_parquet(parquet_path, index=False)
            except Exception as e2:
                print(f"  -> WARN: no se pudo guardar parquet: {e2}")

        except Exception as e:
            print(f"  -> EXCEPCION en sid={sid}: {e}")
            skipped += 1
            continue

        file_size = out_path.stat().st_size
        print(f"  -> Guardado: {fname} ({file_size // 1024} KB)")

        # Verify
        try:
            vf = vitaldb.VitalFile(str(out_path))
            verify_tracks = vf.get_track_names()
        except Exception as e:
            print(f"  -> WARN: no se pudo verificar: {e}")
            verify_tracks = []

        all_events.append({
            "event_id": f"mimic_{sid}",
            "subject_id": sid,
            "file": fname,
            "file_size_bytes": file_size,
            "start_time": vent_start.isoformat(),
            "end_time": vent_end.isoformat(),
            "duration_seconds": int((vent_end - vent_start).total_seconds()),
            "duration_hours": round((vent_end - vent_start).total_seconds() / 3600, 2),
            "data_start": df_case["Time"].iloc[0].isoformat(),
            "data_end": df_case["Time"].iloc[-1].isoformat(),
            "signals": [c for c in df_case.columns if c != "Time"],
            "vital_tracks": verify_tracks,
            "num_samples": len(df_case),
        })
        total_cases += 1

    # Save index
    index_data = {
        "source": "mimic3wdb",
        "description": (
            "Casos completos de ventilacion mecanica desde MIMIC-III Waveform Database. "
            "Cada archivo .vital contiene senales vitales continuas del periodo de VM."
        ),
        "generated_at": datetime.now().isoformat(),
        "total_candidates": len(candidates),
        "total_cases": total_cases,
        "skipped": skipped,
        "events": all_events,
    }

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"RESUMEN")
    print(f"  Casos generados: {total_cases}")
    print(f"  Skipped: {skipped}")
    print(f"  Archivos en: {OUT_DIR}")
    print(f"  Indice en: {INDEX_PATH}")


if __name__ == "__main__":
    main()
