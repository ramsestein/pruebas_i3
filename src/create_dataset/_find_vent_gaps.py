"""
Analiza gaps de ventilación en todos los casos de vital_full_cases.
Para cada caso, identifica los periodos donde ETCO2=0 o NaN (no ventilación).
El último gap (extubación definitiva) suele ser el más largo.
"""
import os, json, sys, signal, threading
import pandas as pd
import vitaldb

vital_dir = "datasets/vitaldb/vital_full_cases"
files = sorted([f for f in os.listdir(vital_dir) if f.endswith(".vital")])
print(f"Total .vital files: {len(files)}")

results = []
errors = []

def process_file(fname):
    """Process a single file, returns (result_dict or None, error_dict or None)"""
    try:
        vf = vitaldb.VitalFile(os.path.join(vital_dir, fname))
    except Exception as e:
        return None, {"file": fname, "error": f"VitalFile: {e}"}

    try:
        tracks = vf.get_track_names()
    except Exception as e:
        return None, {"file": fname, "error": f"get_track_names: {e}"}

    etco2_tracks = [t for t in tracks if "ETCO2" in t]
    if not etco2_tracks:
        return None, None

    try:
        df = vf.to_pandas(etco2_tracks[:1], 5)
    except Exception as e:
        return None, {"file": fname, "error": f"to_pandas: {e}"}

    if df.empty:
        return None, None

    etco2 = df[etco2_tracks[0]]

    no_vent = (etco2.isna()) | (etco2 == 0)
    vent_on = no_vent == False

    stops = (vent_on.shift(1) == True) & (vent_on == False)
    starts = (vent_on.shift(1) == False) & (vent_on == True)

    stop_idx = stops[stops].index.tolist()
    start_idx = starts[starts].index.tolist()

    if not stop_idx:
        return None, None

    if no_vent.iloc[-1]:
        stop_idx.append(len(df) - 1)

    gaps = []
    for i in range(min(len(stop_idx), len(start_idx))):
        gs = stop_idx[i]
        ge = start_idx[i]
        gap_min = (ge - gs) * 5 / 60
        if gap_min > 0.5:
            gaps.append({
                "start_row": int(gs),
                "end_row": int(ge),
                "duration_min": round(gap_min, 1)
            })

    if not gaps:
        return None, None

    gaps.sort(key=lambda x: x["duration_min"], reverse=True)
    longest = gaps[0]

    case_id = fname.replace(".vital", "")
    return {
        "case_id": case_id,
        "file": fname,
        "num_gaps": len(gaps),
        "longest_gap_min": longest["duration_min"],
        "longest_gap_start_row": longest["start_row"],
        "longest_gap_end_row": longest["end_row"],
        "all_gaps": gaps
    }, None

for idx, fname in enumerate(files):
    if idx % 10 == 0:
        print(f"Progress: {idx}/{len(files)}")
    
    result, error = process_file(fname)
    if error:
        errors.append(error)
    if result:
        results.append(result)

# Ordenar por duración del gap más largo
results.sort(key=lambda x: x["longest_gap_min"], reverse=True)

print(f"Total cases with gaps >30s: {len(results)}")
print()
print(f"{'Case':>8} {'Gaps':>5} {'Longest(min)':>13} {'Details'}")
print("-" * 60)
for r in results:
    gap_details = ", ".join([str(g["duration_min"]) + "min" for g in r["all_gaps"]])
    print(f"{r['case_id']:>8} {r['num_gaps']:>5} {r['longest_gap_min']:>13.1f}  {gap_details}")

# Guardar resultados
out_path = "datasets/vitaldb/vent_gaps_analysis.json"
with open(out_path, "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print(f"\nResultados guardados en: {out_path}")
