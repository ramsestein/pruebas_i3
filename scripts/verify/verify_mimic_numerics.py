"""
scripts/verify/verify_mimic_numerics.py
========================================
Fase 0 (c): numéricas MIMIC a 1 Hz.

Comprueba qué lee `MimicAdapter.get_numerics` de los .vital enriquecidos y qué
descarta o falla. En concreto:
  - nombres de track reales del .vital vs channel_map de la config,
  - cuántas muestras hay en los tracks "MIMIC/*" (1 Hz) vs "Derived/*" (1 Hz),
  - resultado de get_numerics (columnas con datos vs vacías).
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import vitaldb

from src.stage0.adapters import get_adapter
from src.stage0.io.versioning import load_config

from _common import MIMIC_CASES_DIR, MIMIC_CLINICAL_DIR, list_vital_files, write_json

N_SAMPLES = 5

# columnas canónicas y su mapeo en config para MIMIC
CANONICAL_MAP = {
    "HR": "MIMIC/HR", "SBP": "MIMIC/ABP_S", "DBP": "MIMIC/ABP_D",
    "MAP": "MIMIC/ABP_M", "SpO2": "MIMIC/SpO2", "RR": "MIMIC/RESP",
    "FiO2": "MIMIC/FiO2", "PEEP": "MIMIC/PEEP", "TV": "MIMIC/TV",
    "MV": "MIMIC/MV", "PIP": "MIMIC/PIP",
}


def main():
    config = load_config(str(PROJECT_ROOT / "src/stage0/config/harmonize.yaml"))
    report = {"clinical_dir_exists": MIMIC_CLINICAL_DIR.exists(),
              "track_inventory": {}, "adapter_numerics": {}, "summary": {}}

    files = list_vital_files(MIMIC_CASES_DIR)[:N_SAMPLES]
    for f in files:
        vf = vitaldb.VitalFile(str(f))
        inv = {}
        for name, trk in vf.trks.items():
            inv[name] = {"srate": trk.srate, "n_recs": len(trk.recs), "fmt": trk.fmt, "unit": trk.unit}
        report["track_inventory"][f.name] = inv

    adapter = get_adapter("mimic", config)
    pids = adapter.list_patients()[:N_SAMPLES]
    for pid in pids:
        nr = adapter.get_numerics(pid)
        report["adapter_numerics"][pid] = {
            "n_timestamps": int(len(nr.timestamps_rel)),
            "non_null_counts": {c: int(nr.data[c].notna().sum()) for c in nr.data.columns},
        }

    # resumen agregado: por columna canónica, cuántos pacientes con datos
    per_col = {c: 0 for c in CANONICAL_MAP}
    for pid, d in report["adapter_numerics"].items():
        for c in CANONICAL_MAP:
            if d["non_null_counts"].get(c, 0) > 0:
                per_col[c] += 1
    report["summary"] = {
        "n_patients_sampled": len(pids),
        "patients_with_data_per_column": per_col,
    }

    write_json("mimic_numerics.json", report)
    print(report["summary"])


if __name__ == "__main__":
    main()
