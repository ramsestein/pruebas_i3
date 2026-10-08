#!/usr/bin/env python3
"""
scripts/verify/verify_completeness.py
=====================================
Tests de completitud de datos (Fase 0).

Comprueba invariantes de completitud para las cohortes Clínic, VitalDB SICU
y MIMIC-III. Cada comprobación devuelve (pasó|falló) y el script termina
con exit code 1 si alguna falla (ROJO) o 0 si todo está verde.

Salida: reports/fase0/completeness.json

Comprobaciones:
  C1  índice Clínic: biyección disco <-> índice
  C2  índice Clínic: t0_unix/tend_unix presentes y coherentes
  C3  t0 Clínic: adapter vs dtstart del .vital
  V1  índice VitalDB: biyección disco <-> índice
  V2  índice VitalDB: t0_unix/tend_unix presentes y coherentes
  V3  t0 VitalDB: adapter vs dtstart del .vital
  M1  tablas clínicas MIMIC-III presentes
  M2  etiquetas MIMIC computables (eventos clínicos + verificación de muerte)
  G1  salidas harmonized (survival/landmarks/availability)
  G2  disponibilidad de canales por cohorte (channel_availability.parquet)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

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

CONFIG_PATH = PROJECT_ROOT / "src" / "stage0" / "config" / "harmonize.yaml"
HARMONIZED_DIR = PROJECT_ROOT / "datasets" / "harmonized"
CLINIC_INDEX = PROJECT_ROOT / "datasets" / "clinic_vitals" / "clinic_full_cases_index.json"

# ── canales por cohorte (canonical -> track) tomados de la config ─────────────

WAVE_CANONICALS = ["ECG", "PPG", "ABP"]
VITALS = ["HR", "SBP", "DBP", "MAP", "SpO2", "RR"]
VENT = ["FiO2", "PEEP", "TV", "MV", "PIP"]
ALL_NUMERIC = VITALS + VENT

CORE_VITALS_MIN_PCT = 95.0
VENT_MIN_PCT = 90.0
T0_TOLERANCE_S = 300.0


def _read_vital_header(path: Path) -> dict | None:
    """
    Lee solo la cabecera de un .vital sin descomprimir el resto del fichero.
    Devuelve {'dtstart','dtend','dgmt'} o None si no se puede leer.
    Formato VITA: 4B 'VITA' | 4B versión | 2B headerlen | header
    (header: dgmt@0 int16, dtstart@10 double, dtend@18 double).
    """
    import gzip as _gzip
    import struct as _struct
    try:
        with open(path, "rb") as fh:
            gz = _gzip.GzipFile(fileobj=fh)
            if gz.read(4) != b"VITA":
                return None
            gz.read(4)  # versión
            hlen_b = gz.read(2)
            if len(hlen_b) < 2:
                return None
            headerlen = _struct.unpack("<H", hlen_b)[0]
            header = gz.read(headerlen)
            if len(header) < 26:
                return None
            dgmt = _struct.unpack("<h", header[0:2])[0]
            dtstart = _struct.unpack("<d", header[10:18])[0]
            dtend = _struct.unpack("<d", header[18:26])[0]
        return {"dtstart": dtstart, "dtend": dtend, "dgmt": dgmt}
    except Exception:
        return None


def _check_index_bijection(cases_dir: Path, index_path: Path) -> dict:
    """Devuelve dict con 'passed' y detalles de biyección disco<->índice."""
    if not index_path.exists():
        return {"passed": False, "reason": "índice no existe", "index": str(index_path)}

    try:
        with open(index_path, encoding="utf-8") as fh:
            idx = json.load(fh)
    except Exception as e:  # noqa: BLE001
        return {"passed": False, "reason": f"índice ilegible: {e}"}

    events = idx.get("events", [])
    index_files = {ev.get("file") for ev in events}
    disk_files = {p.name for p in list_vital_files(cases_dir)} if cases_dir.exists() else set()

    missing_on_disk = sorted(index_files - disk_files)
    missing_in_index = sorted(disk_files - index_files)

    passed = not missing_on_disk and not missing_in_index and len(events) == len(disk_files)
    return {
        "passed": passed,
        "n_events_index": len(events),
        "n_files_disk": len(disk_files),
        "missing_on_disk": missing_on_disk,
        "missing_in_index": missing_in_index,
    }


def _check_t0(cohort: str, config: dict) -> dict:
    """Compara t0 del adaptador con dtstart del .vital (lectura rápida de cabecera)."""
    try:
        adapter = get_adapter(cohort, config)
        pids = adapter.list_patients()
    except Exception as e:  # noqa: BLE001
        return {"passed": False, "reason": f"adapter init: {e}", "n": 0}

    cases_dir = {
        "clinic": CLINIC_CASES_DIR,
        "vitaldb": VITALDB_CASES_DIR,
        "mimic": MIMIC_CASES_DIR,
    }[cohort]

    offsets = []
    errors = []
    for pid in pids:
        try:
            t0 = float(adapter.get_clinical_events(pid).t0_unix)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{pid}: {e}")
            continue
        path = None
        if cohort == "clinic":
            with open(CLINIC_INDEX, encoding="utf-8") as fh:
                idx = json.load(fh)
            for e in idx.get("events", []):
                if e.get("event_id") == pid:
                    path = cases_dir / e["file"]
                    break
        elif cohort == "mimic":
            m = list(cases_dir.glob(f"{pid}_*.vital"))
            path = m[0] if m else None
        elif cohort == "vitaldb":
            with open(VITALDB_INDEX, encoding="utf-8") as fh:
                idx = json.load(fh)
            for e in idx.get("events", []):
                if e.get("event_id") == pid:
                    path = cases_dir / e["file"]
                    break
        if path is None or not path.exists():
            errors.append(f"{pid}: fichero no localizado")
            continue
        hdr = _read_vital_header(path)
        if hdr is None:
            errors.append(f"{pid}: cabecera ilegible")
            continue
        offsets.append(abs(t0 - hdr["dtstart"]))

    n = len(offsets)
    max_off = max(offsets) if offsets else None
    passed = n > 0 and max_off is not None and max_off <= T0_TOLERANCE_S and not errors
    return {
        "passed": passed,
        "n": n,
        "max_abs_offset_s": round(max_off, 1) if max_off is not None else None,
        "tolerance_s": T0_TOLERANCE_S,
        "errors": errors[:10],
    }


def _check_index_t0_fields(index_path: Path) -> dict:
    """Comprueba que cada evento del índice tiene t0_unix/tend_unix coherentes."""
    if not index_path.exists():
        return {"passed": False, "reason": "índice no existe"}
    try:
        with open(index_path, encoding="utf-8") as fh:
            idx = json.load(fh)
    except Exception as e:  # noqa: BLE001
        return {"passed": False, "reason": f"índice ilegible: {e}"}
    events = idx.get("events", [])
    bad = []
    for ev in events:
        t0 = ev.get("t0_unix")
        tend = ev.get("tend_unix")
        if t0 is None or tend is None or not (tend > t0):
            bad.append(ev.get("event_id") or ev.get("file"))
    return {
        "passed": bool(events) and not bad,
        "n_events": len(events),
        "bad_events": bad[:10],
    }


def _check_mimic_labels(config: dict) -> dict:
    """Etiquetas MIMIC computables: tablas clínicas presentes + eventos por paciente."""
    m_files = ["ADMISSIONS.csv.gz", "PATIENTS.csv.gz", "ICUSTAYS.csv.gz", "PROCEDUREEVENTS_MV.csv.gz"]
    m_missing = [f for f in m_files if not (MIMIC_CLINICAL_DIR / f).exists()]
    if m_missing:
        return {"passed": False, "reason": f"faltan tablas clínicas: {m_missing}"}
    try:
        adapter = get_adapter("mimic", config)
        pids = adapter.list_patients()
        events = []
        errors = []
        for pid in pids:
            try:
                events.append(adapter.get_clinical_events(pid))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{pid}: {e}")
        n_censored = sum(1 for e in events if e.censored_no_extubation)
        return {
            "passed": bool(events) and not errors,
            "n_patients": len(events),
            "n_censored": n_censored,
            "errors": errors[:10],
        }
    except Exception as e:  # noqa: BLE001
        return {"passed": False, "reason": f"adapter: {e}"}


def _check_harmonized() -> dict:
    if not HARMONIZED_DIR.exists():
        return {"passed": False, "reason": "datasets/harmonized no existe"}

    versions = [p for p in HARMONIZED_DIR.iterdir() if p.is_dir()]
    if not versions:
        return {"passed": False, "reason": "sin versiones harmonized"}

    latest = max(versions, key=lambda p: p.stat().st_mtime)
    required = [
        "survival_48h.parquet",
        "survival_72h.parquet",
        "extubation_attempts.parquet",
        "landmarks_index.parquet",
        "channel_availability.parquet",
    ]
    missing = [f for f in required if not (latest / f).exists() or (latest / f).stat().st_size == 0]

    cohorts_present = []
    if not missing:
        import pandas as pd
        surv = pd.read_parquet(latest / "survival_48h.parquet")
        cohorts_present = sorted(surv["cohort"].unique().tolist()) if "cohort" in surv.columns else []
        missing_cohorts = [c for c in ("clinic", "vitaldb", "mimic") if c not in cohorts_present]
    else:
        missing_cohorts = []

    passed = not missing and not missing_cohorts
    return {
        "passed": passed,
        "version": latest.name,
        "missing_files": missing,
        "cohorts_in_survival_48h": cohorts_present,
        "missing_cohorts": missing_cohorts,
    }


def _check_availability() -> dict:
    """Disponibilidad de canales por cohorte desde channel_availability.parquet."""
    import pandas as pd
    if not HARMONIZED_DIR.exists():
        return {"passed": False, "reason": "datasets/harmonized no existe"}
    versions = [p for p in HARMONIZED_DIR.iterdir() if p.is_dir()]
    if not versions:
        return {"passed": False, "reason": "sin versiones harmonized"}
    latest = max(versions, key=lambda p: p.stat().st_mtime)
    path = latest / "channel_availability.parquet"
    if not path.exists() or path.stat().st_size == 0:
        return {"passed": False, "version": latest.name,
                "reason": "channel_availability.parquet ausente/vacío"}

    df = pd.read_parquet(path)

    # Requisitos por cohorte y tipo de canal, con umbrales diferenciados:
    #  - waveform: 90% (no todos los pacientes tienen línea arterial invasiva)
    #  - vitals:   95%
    #  - vent:     90%
    required = {
        "clinic": {
            "wave": ["ecg_waveform", "ppg_waveform", "abp_waveform"],
            "vitals": VITALS,
            "vent": [],
        },
        "vitaldb": {
            "wave": ["ecg_waveform", "ppg_waveform", "abp_waveform"],
            "vitals": VITALS,
            "vent": [],
        },
        # MIMIC-III: sin waveforms ECG/PPG por diseño y sin fuente de vitals
        # (HR/SBP/SpO2) en esta fase; se exigen los parámetros ventilatorios
        # que sí puebla el enriquecimiento.
        "mimic": {
            "wave": [],
            "vitals": [],
            "vent": VENT,
        },
    }
    thresholds = {
        "wave": 90.0,
        "vitals": CORE_VITALS_MIN_PCT,
        "vent": VENT_MIN_PCT,
    }

    low = {}
    report = {}
    for cohort, groups in required.items():
        sub = df[df["cohort"].astype(str) == cohort]
        if sub.empty:
            low[cohort] = "sin filas"
            continue
        per = {}
        for group_name, cols in groups.items():
            thr = thresholds[group_name]
            for c in cols:
                if c in df.columns:
                    pct = round(100.0 * sub[c].astype(bool).mean(), 1)
                    per[c] = pct
                    if pct < thr:
                        low.setdefault(cohort, {})[c] = pct
                else:
                    low.setdefault(cohort, {})[c] = None
        report[cohort] = per

    return {
        "passed": not low,
        "version": latest.name,
        "thresholds": thresholds,
        "availability_pct": report,
        "low_availability": low,
    }


# ── comprobaciones ────────────────────────────────────────────────────────────

def run_checks(config: dict) -> dict:
    checks = {}

    checks["C1_clinic_index"] = _check_index_bijection(CLINIC_CASES_DIR, CLINIC_INDEX)
    checks["C2_clinic_t0_fields"] = _check_index_t0_fields(CLINIC_INDEX)
    checks["C3_clinic_t0_vs_file"] = _check_t0("clinic", config)

    checks["V1_vitaldb_index"] = _check_index_bijection(VITALDB_CASES_DIR, VITALDB_INDEX)
    checks["V2_vitaldb_t0_fields"] = _check_index_t0_fields(VITALDB_INDEX)
    checks["V3_vitaldb_t0_vs_file"] = _check_t0("vitaldb", config)

    m_files = ["ADMISSIONS.csv.gz", "PATIENTS.csv.gz", "ICUSTAYS.csv.gz", "PROCEDUREEVENTS_MV.csv.gz"]
    m_missing = [f for f in m_files if not (MIMIC_CLINICAL_DIR / f).exists()]
    checks["M1_mimic_clinical_tables"] = {
        "passed": MIMIC_CLINICAL_DIR.exists() and not m_missing,
        "clinical_dir": str(MIMIC_CLINICAL_DIR),
        "missing_files": m_missing,
    }
    checks["M2_mimic_labels"] = _check_mimic_labels(config)

    checks["G1_harmonized_outputs"] = _check_harmonized()
    checks["G2_channel_availability"] = _check_availability()

    checks["summary"] = {
        "all_green": all(v.get("passed", False) for k, v in checks.items() if k != "summary"),
        "n_checks": len([k for k in checks if k != "summary"]),
        "n_failed": len([k for k, v in checks.items() if k != "summary" and not v.get("passed", True)]),
    }
    return checks


def main() -> int:
    config = load_config(str(CONFIG_PATH))
    checks = run_checks(config)
    write_json("completeness.json", checks)

    # imprimir tabla resumida
    print("\n" + "=" * 70)
    print("COMPLETITUD — resumen")
    print("=" * 70)
    for k, v in checks.items():
        if k == "summary":
            continue
        status = "VERDE" if v.get("passed") else "ROJO"
        print(f"  [{status}] {k}  {_brief(v)}")
    print("-" * 70)
    s = checks["summary"]
    print(f"  Total: {s['n_checks']} | Falladas: {s['n_failed']} | {'TODO VERDE' if s['all_green'] else 'HAY ROJOS'}")
    print("=" * 70)

    return 0 if checks["summary"]["all_green"] else 1


def _brief(v: dict) -> str:
    if not isinstance(v, dict):
        return str(v)
    if v.get("reason"):
        return f"— {v['reason']}"
    if v.get("missing_files"):
        return f"— faltan: {v['missing_files']}"
    if v.get("missing_on_disk"):
        return f"— en índice sin fichero: {v['missing_on_disk'][:5]}"
    if v.get("missing_in_index"):
        return f"— ficheros fuera de índice: {v['missing_in_index'][:5]}"
    if v.get("low_availability"):
        return f"— disponibilidad bajo umbral: {v['low_availability']}"
    if v.get("bad_events"):
        return f"— eventos sin t0/tend: {v['bad_events'][:5]}"
    if v.get("missing_cohorts"):
        return f"— cohortes ausentes: {v['missing_cohorts']}"
    if "max_abs_offset_s" in v:
        return f"— max|offset|={v['max_abs_offset_s']}s (n={v.get('n')})"
    if "n_patients" in v:
        return f"— {v['n_patients']} pacientes, censurados={v.get('n_censored')}"
    return ""


if __name__ == "__main__":
    sys.exit(main())
