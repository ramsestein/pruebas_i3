"""
scripts/verify/verify_waves.py
===============================
Fase 0 (b): ondas (waveforms).

Para ~10 casos por cohorte (clinic, vitaldb) se comprueba:
  - qué contiene `trk.recs` en las pistas de onda (escalar vs bloque, o vacío),
  - el `srate` declarado,
  - el `fs_native` que calcularía el adaptador,
  - si la rama de ondas de stage0 produce algo (get_waveforms).

Salida JSON: reports/fase0/waves.json
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import vitaldb

from src.stage0.adapters import get_adapter
from src.stage0.io.versioning import load_config

from _common import (
    CLINIC_CASES_DIR,
    VITALDB_CASES_DIR,
    list_vital_files,
    write_json,
)

N_SAMPLES = 10

WAVE_TRACKS = {
    "clinic": ["Intellivue/ECG_II", "Intellivue/PLETH", "Intellivue/ART", "Intellivue/ECG_III", "Intellivue/CO2"],
    "vitaldb": ["Intellivue/ECG_II", "Intellivue/PLETH", "Intellivue/ABP", "Intellivue/ECG_II_WAV"],
}


def raw_wave_info(path, cohort: str) -> dict:
    vf = vitaldb.VitalFile(str(path))
    tracks = {}
    for name in WAVE_TRACKS[cohort]:
        trk = vf.trks.get(name)
        if trk is None:
            tracks[name] = {"present": False}
            continue
        first_val = None
        if trk.recs:
            v = trk.recs[0]["val"]
            if isinstance(v, (list, np.ndarray)):
                first_val = {"type": type(v).__name__, "len": len(v)}
            else:
                first_val = {"type": type(v).__name__, "len": "scalar"}
        tracks[name] = {
            "present": True,
            "srate": trk.srate,
            "fmt": trk.fmt,
            "n_recs": len(trk.recs),
            "first_val": first_val,
        }
    return tracks


def adapter_waves(cohort: str, config: dict, n: int) -> list:
    adapter = get_adapter(cohort, config)
    pids = adapter.list_patients()[:n]
    out = []
    for pid in pids:
        try:
            wf = adapter.get_waveforms(pid)
        except Exception as e:  # noqa: BLE001
            out.append({"patient_id": pid, "error": str(e)})
            continue
        row = {"patient_id": pid}
        for sig in ("ECG", "PPG", "ABP"):
            rec = wf[sig]
            row[sig] = {
                "available": bool(rec.available),
                "n_samples": int(rec.n_samples),
                "fs_native": float(rec.fs_native),
            }
        out.append(row)
    return out


def main():
    config = load_config(str(PROJECT_ROOT / "src/stage0/config/harmonize.yaml"))
    report = {"raw": {}, "adapter": {}, "summary": {}}

    for cohort, cases_dir in (("clinic", CLINIC_CASES_DIR), ("vitaldb", VITALDB_CASES_DIR)):
        files = list_vital_files(cases_dir)[:N_SAMPLES]
        report["raw"][cohort] = {
            f.name: raw_wave_info(f, cohort) for f in files
        }
        report["adapter"][cohort] = adapter_waves(cohort, config, N_SAMPLES)
        # resumen
        n_avail = 0
        n_total = 0
        any_samples = 0
        n_errors = 0
        for row in report["adapter"][cohort]:
            if "error" in row:
                n_errors += 1
                continue
            for sig in ("ECG", "PPG", "ABP"):
                n_total += 1
                if row[sig]["available"]:
                    n_avail += 1
                any_samples += row[sig]["n_samples"]
        report["summary"][cohort] = {
            "n_patients": len(report["adapter"][cohort]),
            "n_patient_errors": n_errors,
            "n_waveforms_available": n_avail,
            "n_waveforms_expected": n_total,
            "total_waveform_samples": any_samples,
        }

    # MIMIC / eICU (resumen rápido, sin ondas por diseño)
    for cohort in ("mimic", "eicu"):
        adapter = get_adapter(cohort, config)
        pids = adapter.list_patients()[:N_SAMPLES]
        n_avail = 0
        for pid in pids:
            wf = adapter.get_waveforms(pid)
            n_avail += sum(1 for r in wf.values() if r.available)
        report["summary"][cohort] = {
            "n_patients": len(pids),
            "n_waveforms_available": n_avail,
            "n_waveforms_expected": len(pids) * 3,
        }

    write_json("waves.json", report)
    print(report["summary"])


if __name__ == "__main__":
    main()
