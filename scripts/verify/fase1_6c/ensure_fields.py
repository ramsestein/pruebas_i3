#!/usr/bin/env python3
"""
scripts/verify/fase1_6c/ensure_fields.py
========================================
Fase 1.6c — **punto 5**: comprueba (y opcionalmente completa) los campos que
necesitará la Fase 2 en los índices de las 4 cohortes.

Campos requeridos por evento:

- ``censor_cause`` y ``censor_time_h`` **por ventana** (48 h y 72 h): el modelo
  necesita la hora del evento competidor (muerte / traqueostomía);
- ``label_source``: ``explicita`` (MIMIC: procedimiento documentado),
  ``anotaciones`` (eICU) o ``senal`` (Clínic/VitalDB);
- identificadores de agrupación: MIMIC ``subject_id``; eICU ``uniquepid`` y
  ``hospital_id``; Clínic y VitalDB ``box`` (caja/cama);
- ``annotation_stratum`` (solo eICU).

``--apply`` añade los campos que falten de forma determinista (solo
``label_source`` y ``uniquepid``); nunca borra ni reescribe otros datos.

Salida: ``reports/fase1_6c/campos_fase2.json``

Uso:
    python scripts/verify/fase1_6c/ensure_fields.py            # solo comprueba
    python scripts/verify/fase1_6c/ensure_fields.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"

# cohorte -> (globs de índice, campo label_source, claves de agrupación)
SPEC: dict[str, dict] = {
    "mimic": {"globs": ["datasets/mimic3wdb/cases_v*/mimic_cases_index.json"],
              "label_source": "explicita", "group_keys": ["subject_id"]},
    "eicu": {"globs": ["datasets/eicu_collaborative/cases_v*/eicu_cases_index.json"],
             "label_source": "anotaciones",
             "group_keys": ["uniquepid", "hospital_id"]},
    "clinic": {"globs": ["datasets/clinic/cases_v*/clinic_cases_index.json"],
               "label_source": "senal", "group_keys": ["box"]},
    "vitaldb": {"globs": ["datasets/vitaldb/cases_v*/vitaldb_cases_index.json"],
                "label_source": "senal", "group_keys": ["box"]},
}
WINDOWS = ("48h", "72h")


def latest(globs: list[str]) -> Path | None:
    cands: list[Path] = []
    for g in globs:
        cands.extend(ROOT.glob(g))
    return sorted(cands)[-1] if cands else None


def check_index(path: Path, spec: dict, uniquepid: dict[int, str]) -> dict:
    idx = json.loads(path.read_text(encoding="utf-8"))
    events = idx["events"]
    missing: dict[str, int] = {}
    n = len(events)

    def miss(field: str) -> None:
        missing[field] = missing.get(field, 0) + 1

    for e in events:
        if not e.get("label_source"):
            miss("label_source")
        if "annotation_stratum" not in e and spec["label_source"] == "anotaciones":
            miss("annotation_stratum")
        for key in spec["group_keys"]:
            # El campo debe estar EN el evento: la Fase 2 no debe depender de
            # volver a leer patient.csv (aunque aquí se use como respaldo).
            if not e.get(key):
                miss(key)
        for w in WINDOWS:
            lab = (e.get("labels") or {}).get(w)
            if not lab or "censor_cause" not in lab or "censor_time_h" not in lab:
                miss(f"labels.{w}.censor")
    return {"index": str(path.relative_to(ROOT)), "n_events": n,
            "missing_counts": missing,
            "complete": not missing}


def apply_fixes(path: Path, spec: dict, uniquepid: dict[int, str]) -> int:
    idx = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for e in idx["events"]:
        if not e.get("label_source"):
            e["label_source"] = spec["label_source"]
            changed += 1
        if spec["label_source"] == "anotaciones" and not e.get("uniquepid"):
            up = uniquepid.get(int(e.get("patientunitstayid", -1)))
            if up:
                e["uniquepid"] = up
                changed += 1
    if changed:
        path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--apply", action="store_true",
                   help="Añade label_source/uniquepid donde falten")
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6c"))
    args = p.parse_args()

    config = load_config(args.config)
    eicu_dir = config_path(config, "paths", "eicu_dir")

    uniquepid: dict[int, str] = {}
    pat = eicu_dir / "patient.csv.gz"
    if pat.exists():
        import pandas as pd

        df = pd.read_csv(pat, usecols=["patientunitstayid", "uniquepid"])
        uniquepid = {int(a): str(b) for a, b in
                     zip(df.patientunitstayid, df.uniquepid)}

    report: dict[str, dict] = {}
    for cohort, spec in SPEC.items():
        path = latest(spec["globs"])
        if path is None:
            report[cohort] = {"index": None, "error": "índice no encontrado"}
            continue
        before = check_index(path, spec, uniquepid)
        if args.apply and before["missing_counts"]:
            n = apply_fixes(path, spec, uniquepid)
            after = check_index(path, spec, uniquepid)
            before["applied"] = n
            before["after"] = after
        report[cohort] = before

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "campos_fase2.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = all((v.get("after") or v).get("complete") for v in report.values())
    print(f"\nRESULTADO: {'todos los índices completos' if ok else 'FALTAN CAMPOS'}")


if __name__ == "__main__":
    main()
