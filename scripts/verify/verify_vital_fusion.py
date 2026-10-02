"""
scripts/verify/verify_vital_fusion.py
======================================
Fase 0 (a): verificar la fusión de archivos .vital.

`build_clinical_cases.py` y `build_vitaldb_cases.py` concatenan bytes gzip de
varios .vital horarios en un único archivo. Este script (solo lectura) compara,
para cada caso fusionado:

  - duración esperada (nombre de fichero / índice),
  - duración declarada en la cabecera (vf.dtend - vf.dtstart),
  - rango real de timestamps leídos con `vitaldb`.

Un caso se considera truncado si el rango real de datos es sensiblemente menor
que la duración esperada.
"""
from __future__ import annotations

import json
import sys

import vitaldb

from _common import (
    CLINIC_CASES_DIR,
    CLINIC_PATTERN,
    VITALDB_CASES_DIR,
    VITALDB_INDEX,
    VITALDB_PATTERN,
    duration_hours,
    list_vital_files,
    parse_yyMMdd_HHMMSS,
    utc,
    write_json,
)


def analyze_file(path, expected_start_naive, expected_end_naive) -> dict:
    vf = vitaldb.VitalFile(str(path))
    header_duration = (vf.dtend - vf.dtstart) / 3600.0

    t0 = None
    t1 = None
    n_tracks = 0
    n_empty = 0
    n_wave_empty = 0
    for name, first, last, nrecs, srate, fmt, unit in _iter_all(vf):
        n_tracks += 1
        if nrecs == 0:
            n_empty += 1
            if fmt == 6:  # bloque (waveform)
                n_wave_empty += 1
            continue
        if t0 is None or first < t0:
            t0 = first
        if t1 is None or last > t1:
            t1 = last

    actual_duration = (t1 - t0) / 3600.0 if (t0 is not None and t1 is not None) else 0.0
    expected_duration = duration_hours(expected_start_naive, expected_end_naive)

    return {
        "file": path.name,
        "expected_duration_h": round(expected_duration, 4),
        "header_duration_h": round(header_duration, 4),
        "actual_duration_h": round(actual_duration, 4),
        "actual_first_epoch": t0,
        "actual_last_epoch": t1,
        "dgmt_min": vf.dgmt,
        "n_tracks": n_tracks,
        "n_empty_tracks": n_empty,
        "n_wave_tracks_empty": n_wave_empty,
        "truncated": actual_duration < expected_duration * 0.95,
        "ratio_actual_expected": round(actual_duration / expected_duration, 4) if expected_duration > 0 else None,
    }


def _iter_all(vf):
    for name, trk in vf.trks.items():
        if trk.recs:
            yield name, trk.recs[0]["dt"], trk.recs[-1]["dt"], len(trk.recs), trk.srate, trk.fmt, trk.unit
        else:
            yield name, None, None, 0, trk.srate, trk.fmt, trk.unit


def parse_clinic(path):
    m = CLINIC_PATTERN.match(path.name)
    if not m:
        return None
    start = parse_yyMMdd_HHMMSS(m.group(2), m.group(3))
    end = parse_yyMMdd_HHMMSS(m.group(4), m.group(5))
    return start, end


def parse_vitaldb(path):
    m = VITALDB_PATTERN.match(path.name)
    if not m:
        return None
    start = parse_yyMMdd_HHMMSS(m.group(2), m.group(3))
    end = parse_yyMMdd_HHMMSS(m.group(4), m.group(5))
    return start, end


def load_vitaldb_index():
    with open(VITALDB_INDEX, encoding="utf-8") as fh:
        data = json.load(fh)
    index = {}
    for ev in data.get("events", []):
        index[ev["file"]] = ev
    return index


def main():
    report = {"clinic": [], "vitaldb": [], "summary": {}}

    # ── Clínic ────────────────────────────────────────────────────────────────
    for path in list_vital_files(CLINIC_CASES_DIR):
        bounds = parse_clinic(path)
        if bounds is None:
            continue
        start, end = bounds
        row = analyze_file(path, utc(start), utc(end))
        report["clinic"].append(row)

    # ── VitalDB ───────────────────────────────────────────────────────────────
    idx = load_vitaldb_index()
    idx_files = set(idx.keys())
    dir_files = {p.name for p in list_vital_files(VITALDB_CASES_DIR)}
    report["vitaldb_index_consistency"] = {
        "n_files_in_dir": len(dir_files),
        "n_files_in_index": len(idx_files),
        "n_index_files_missing_from_dir": len(idx_files - dir_files),
        "n_dir_files_missing_from_index": len(dir_files - idx_files),
    }

    for path in list_vital_files(VITALDB_CASES_DIR):
        bounds = parse_vitaldb(path)
        if bounds is None:
            continue
        start, end = bounds
        row = analyze_file(path, utc(start), utc(end))
        # contraste con índice (duración declarada en índice)
        ev = idx.get(path.name)
        if ev:
            row["index_duration_h"] = round(ev.get("duration_seconds", 0) / 3600.0, 4)
            row["index_num_files"] = ev.get("num_vital_files_merged")
        report["vitaldb"].append(row)

    for cohort in ("clinic", "vitaldb"):
        rows = report[cohort]
        report["summary"][cohort] = {
            "n_cases": len(rows),
            "n_truncated": sum(1 for r in rows if r["truncated"]),
            "n_not_truncated": sum(1 for r in rows if not r["truncated"]),
            "n_with_empty_wave_tracks": sum(1 for r in rows if r["n_wave_tracks_empty"] > 0),
            "median_ratio_actual_expected": None,
        }
        if rows:
            ratios = sorted(r["ratio_actual_expected"] for r in rows if r["ratio_actual_expected"] is not None)
            if ratios:
                report["summary"][cohort]["median_ratio_actual_expected"] = ratios[len(ratios) // 2]

    write_json("fusion.json", report)
    s = report["summary"]
    print(json.dumps(s, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
