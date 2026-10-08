"""
scripts/verify/verify_timezone.py
==================================
Fase 0 (d): zona horaria.

Para ~5 pacientes por cohorte muestra:
  - t0 según el adaptador (timestamp de la cabecera/nombre de fichero tratado
    como datetime naive → .timestamp()),
  - primer timestamp real de la señal (epoch),
  - offset en horas entre ambos,
  - el offset de GMT declarado en la cabecera .vital (`vf.dgmt`).

Para MIMIC se comprueba además si es posible verificar los tiempos de muerte
(requiere `datasets/mimic3wdb/clinical/ADMISSIONS.csv.gz`).
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
import vitaldb

from src.stage0.adapters import get_adapter
from src.stage0.io.versioning import load_config

from _common import (
    CLINIC_CASES_DIR,
    MIMIC_CASES_DIR,
    MIMIC_CLINICAL_DIR,
    VITALDB_CASES_DIR,
    VITALDB_INDEX,
    list_vital_files,
    write_json,
)

N_SAMPLES = 5


def first_sample_epoch(path) -> float:
    vf = vitaldb.VitalFile(str(path))
    mn = None
    for name, trk in vf.trks.items():
        if trk.recs:
            d = trk.recs[0]["dt"]
            if mn is None or d < mn:
                mn = d
    return mn, vf.dgmt


def locate_case_file(cohort: str, pid: str, cases_dir: Path):
    """Devuelve la ruta al .vital de un paciente según la cohorte."""
    if cohort == "clinic":
        return cases_dir / f"{pid}.vital"
    if cohort == "mimic":
        matches = list(cases_dir.glob(f"{pid}_*.vital"))
        return matches[0] if matches else None
    if cohort == "vitaldb":
        import json
        with open(VITALDB_INDEX, encoding="utf-8") as fh:
            idx = json.load(fh)
        for ev in idx.get("events", []):
            if ev.get("event_id") == pid:
                return cases_dir / ev["file"]
        return None
    return None


def check_cohort(cohort: str, cases_dir: Path, config: dict) -> list:
    adapter = get_adapter(cohort, config)
    pids = adapter.list_patients()[:N_SAMPLES]
    rows = []
    for pid in pids:
        try:
            events = adapter.get_clinical_events(pid)
            t0 = float(events.t0_unix)
            path = locate_case_file(cohort, pid, cases_dir)
            if path is None or not path.exists():
                rows.append({"patient_id": pid, "error": f"archivo no localizado para {pid}"})
                continue
            first_epoch, dgmt = first_sample_epoch(path)
            rows.append({
                "patient_id": pid,
                "file": path.name,
                "t0_unix_adapter": t0,
                "first_sample_epoch": first_epoch,
                "offset_hours": round((first_epoch - t0) / 3600.0, 4) if first_epoch is not None else None,
                "vital_dgmt_min": dgmt,
                "record_end_hours": round(events.record_end_hours, 3),
            })
        except Exception as e:  # noqa: BLE001
            rows.append({"patient_id": pid, "error": str(e)})
    return rows


def main():
    config = load_config(str(PROJECT_ROOT / "src/stage0/config/harmonize.yaml"))
    report = {}
    report["clinic"] = check_cohort("clinic", CLINIC_CASES_DIR, config)
    report["vitaldb"] = check_cohort("vitaldb", VITALDB_CASES_DIR, config)
    report["mimic"] = check_cohort("mimic", MIMIC_CASES_DIR, config)

    report["mimic_death_check"] = {
        "clinical_dir_exists": MIMIC_CLINICAL_DIR.exists(),
        "admissions_exists": (MIMIC_CLINICAL_DIR / "ADMISSIONS.csv.gz").exists(),
        "procedureevents_exists": (MIMIC_CLINICAL_DIR / "PROCEDUREEVENTS_MV.csv.gz").exists(),
        "icustays_exists": (MIMIC_CLINICAL_DIR / "ICUSTAYS.csv.gz").exists(),
        "note": "Si clinical_dir no existe, los tiempos de muerte de MIMIC NO se pueden verificar.",
    }

    # resumen de offsets por cohorte
    summary = {}
    for cohort, rows in report.items():
        if cohort == "mimic_death_check" or not isinstance(rows, list):
            continue
        offs = [r["offset_hours"] for r in rows if r.get("offset_hours") is not None]
        summary[cohort] = {
            "n_patients": len(rows),
            "offset_hours_sample": offs,
            "median_offset_hours": round(float(np.median(offs)), 3) if offs else None,
        }
    report["summary"] = summary

    write_json("timezone.json", report)
    print(summary)
    print(report["mimic_death_check"])


if __name__ == "__main__":
    main()
