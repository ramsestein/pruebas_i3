"""
scripts/verify/_common.py
=========================
Utilidades compartidas por los scripts de verificación (Fase 0).

Solo lectura: ninguno de estos scripts escribe en `datasets/`.
Las salidas van a `reports/fase0/`.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Raíz del proyecto (para poder importar src/ y localizar datasets/)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

REPORT_DIR = PROJECT_ROOT / "reports" / "fase0"

CLINIC_CASES_DIR = PROJECT_ROOT / "datasets" / "clinic_vitals" / "clinic_full_cases"
VITALDB_CASES_DIR = PROJECT_ROOT / "datasets" / "vitaldb_sicu" / "vitaldb_full_cases"
VITALDB_INDEX = PROJECT_ROOT / "datasets" / "vitaldb_sicu" / "vitaldb_full_cases_index.json"
MIMIC_CASES_DIR = PROJECT_ROOT / "datasets" / "mimic3wdb" / "mimic_full_cases_enriched"
MIMIC_CLINICAL_DIR = PROJECT_ROOT / "datasets" / "mimic3wdb" / "clinical"

CLINIC_PATTERN = re.compile(
    r"(box\d+)_(\d{6})_(\d{6})_to_(\d{6})_(\d{6})\.vital", re.IGNORECASE
)
VITALDB_PATTERN = re.compile(
    r"(SICU\d+_\d+)_(\d{6})_(\d{6})_to_(\d{6})_(\d{6})\.vital", re.IGNORECASE
)
MIMIC_PATTERN = re.compile(
    r"mimic_(\d+)_(\d{8})_(\d{6})_to_(\d{8})_(\d{6})\.vital", re.IGNORECASE
)


def parse_yyMMdd_HHMMSS(date_part: str, time_part: str) -> datetime:
    return datetime.strptime(date_part + time_part, "%y%m%d%H%M%S")


def parse_YYYYMMDD_HHMMSS(date_part: str, time_part: str) -> datetime:
    return datetime.strptime(date_part + time_part, "%Y%m%d%H%M%S")


def write_json(name: str, payload) -> Path:
    """Escribe un resumen JSON en reports/fase0/ y devuelve la ruta."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / name
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    print(f"[verificacion] escrito {path}")
    return path


def duration_hours(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600.0


def utc(dt_naive: datetime) -> datetime:
    return dt_naive.replace(tzinfo=timezone.utc)


def list_vital_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.vital"))


def iter_track_records(vf):
    """Genera (name, first_dt, last_dt, n_recs, srate, fmt, unit) por track."""
    for name, trk in vf.trks.items():
        if trk.recs:
            yield name, trk.recs[0]["dt"], trk.recs[-1]["dt"], len(trk.recs), trk.srate, trk.fmt, trk.unit
        else:
            yield name, None, None, 0, trk.srate, trk.fmt, trk.unit
