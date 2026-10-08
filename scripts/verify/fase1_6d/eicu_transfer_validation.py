#!/usr/bin/env python3
"""
scripts/verify/fase1_6d/eicu_transfer_validation.py
===================================================
Fase 1.6d — **punto 2**: validación de ``transfer_ventilated`` en la cohorte
eICU-B.

Para cada evento censurado por ``transfer_ventilated`` (con y sin la corrección
del fin) se cruza la censura con:

- ``patient.unitDischargeLocation`` (destino del alta);
- el **plan de cuidados** (``carePlanGeneral`` / ``carePlanEOL``: medidas de
  confort / limitación del esfuerzo);
- los **ajustes invasivos de las últimas horas** (último ajuste frente al alta).

Salida (``reports/fase1_6d/``):
  ``eicu_transfer.json`` / ``.md`` con:

- tabla **destino × causa de censura** (48 h), con y sin corrección del fin;
- cuantificación de los ``transfer_ventilated`` con destino **incompatible con
  seguir ventilado** (planta, casa, hospicio, otro...);
- una **regla propuesta** (que NO se aplica) que usa el destino como evidencia
  adicional.

Uso:
    python scripts/verify/fase1_6d/eicu_transfer_validation.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.common.transfer_validation import (  # noqa: E402
    INCOMPATIBLE_DESTINATIONS,
    propose_transfer_reclassification,
)
from src.create_dataset.build_eicu_index import load_respcharting  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
WINDOW = "48h"

PATIENT_COLS = ["patientunitstayid", "hospitalid", "unitdischargeoffset",
                "unitdischargestatus", "unitdischargelocation", "uniquepid"]


def read_patients(eicu_dir: Path) -> pd.DataFrame:
    """``patient`` con el destino del alta (columnas presentes)."""
    header = pd.read_csv(eicu_dir / "patient.csv.gz", nrows=0).columns.tolist()
    cols = [c for c in PATIENT_COLS if c in header]
    return pd.read_csv(eicu_dir / "patient.csv.gz", usecols=cols,
                       low_memory=False)


def read_careplan(eicu_dir: Path) -> dict[int, list[str]]:
    """Medidas de confort / limitación del esfuerzo por estancia (si existen)."""
    out: dict[int, list[str]] = defaultdict(list)
    for fname, text_cols in (
        ("carePlanGeneral.csv.gz", ["cplgroup", "cplitemvalue"]),
        ("carePlanGoal.csv.gz", ["cplgoalcategory", "cplgoalvalue"]),
    ):
        path = eicu_dir / fname
        if not path.exists():
            continue
        try:
            header = pd.read_csv(path, nrows=0).columns.tolist()
        except Exception:  # noqa: BLE001
            continue
        cols = [c for c in (["patientunitstayid"] + text_cols) if c in header]
        if "patientunitstayid" not in cols:
            continue
        df = pd.read_csv(path, usecols=cols, low_memory=False)
        for r in df.itertuples(index=False):
            vals = [str(getattr(r, c)).lower() for c in cols
                    if c != "patientunitstayid" and pd.notna(getattr(r, c))]
            text = " ".join(vals)
            if any(k in text for k in ("comfort", "dnr", "dni", "no cpr",
                                       "withdraw", "limit", "palliat",
                                       "terminal", "hospice")):
                out[int(r.patientunitstayid)].append(text[:120])
    # La mera existencia de una fila en carePlanEOL = discusión de fin de vida.
    eol = eicu_dir / "carePlanEOL.csv.gz"
    if eol.exists():
        try:
            df = pd.read_csv(eol, usecols=["patientunitstayid"],
                             low_memory=False)
            for pid in df["patientunitstayid"].unique():
                out[int(pid)].append("carePlanEOL (discusión fin de vida)")
        except Exception:  # noqa: BLE001
            pass
    return out


def _cause(ev: dict, corrected: bool) -> str | None:
    if corrected:
        lab = (ev.get("labels") or {}).get(WINDOW) or {}
    else:
        lab = ((ev.get("labels_sin_correccion") or {})
               .get(WINDOW) or (ev.get("labels") or {}).get(WINDOW) or {})
    return lab.get("censor_cause")


def _is_success(ev: dict, corrected: bool) -> bool:
    lab = ((ev.get("labels") if corrected
            else (ev.get("labels_sin_correccion") or ev.get("labels"))) or {}
           ).get(WINDOW) or {}
    return lab.get("event_type") == "successful_extubation"


def latest_eicu_b_index() -> Path | None:
    cands = sorted((ROOT / "datasets" / "eicu_collaborative").glob(
        "cases_v*/eicu_cases_index.json"))
    return cands[-1] if cands else None


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--index", default=None)
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6d"))
    args = p.parse_args()

    config = load_config(args.config)
    idx_path = Path(args.index) if args.index else latest_eicu_b_index()
    if idx_path is None:
        raise SystemExit("no hay índice de eICU")
    index = json.loads(idx_path.read_text(encoding="utf-8"))
    events = index["events"]
    eicu_dir = Path(config_path(config, "paths", "eicu_dir"))

    pids = {int(e["patientunitstayid"]) for e in events}
    patients = read_patients(eicu_dir)
    patients = patients[patients["patientunitstayid"].isin(pids)]
    dest_by_pid = dict(zip(patients["patientunitstayid"],
                           patients.get("unitdischargelocation",
                                        pd.Series(dtype=str))))
    status_by_pid = dict(zip(patients["patientunitstayid"],
                             patients.get("unitdischargestatus",
                                          pd.Series(dtype=str))))
    discharge_by_pid = dict(zip(patients["patientunitstayid"],
                                patients.get("unitdischargeoffset",
                                             pd.Series(dtype=float))))
    adjustments, _ = load_respcharting(eicu_dir, pids)
    careplan = read_careplan(eicu_dir)

    tables: dict[str, dict[str, Counter]] = {}
    incompatible: list[dict] = []
    propuesta_events: list[str] = []
    comfort = defaultdict(int)
    n_transfer = defaultdict(int)
    for corrected in (True, False):
        key = "con_correccion" if corrected else "sin_correccion"
        tables[key] = defaultdict(Counter)
        for e in events:
            c = _cause(e, corrected)
            if _is_success(e, corrected):
                continue
            if c is None:
                continue
            pid = int(e["patientunitstayid"])
            dest = str(dest_by_pid.get(pid) or "(vacío)").strip() or "(vacío)"
            tables[key][dest][c] += 1
            if corrected and c == "transfer_ventilated":
                n_transfer["n"] += 1
                last_adj = max(adjustments.get(pid, [0.0]))
                discharge = float(discharge_by_pid.get(pid, last_adj))
                gap_h = (discharge - last_adj) / 60.0
                dn = dest.lower()
                incompat = dn in INCOMPATIBLE_DESTINATIONS
                prop_cause = propose_transfer_reclassification(
                    c, dest, gap_h)
                if prop_cause is not None:
                    propuesta_events.append(e["event_id"])
                if incompat:
                    incompatible.append({
                        "event_id": e["event_id"], "destination": dest,
                        "gap_last_adj_h": round(gap_h, 2),
                        "unitdischargestatus": str(status_by_pid.get(pid)),
                        "careplan": careplan.get(pid, [])[:2],
                    })
                if careplan.get(pid):
                    comfort["n"] += 1

    prop = {
        "regla_propuesta": (
            "Si la causa de censura a 48 h es `transfer_ventilated` y "
            "`unitDischargeLocation` es incompatible con seguir ventilado "
            "(casa/planta/hospicio/otro) y el último ajuste invasivo es >= 1 h "
            "anterior al alta, la estancia NO estaba ventilada al alta: se "
            "reclasifica como `end_of_record` (sin extubación observada)."
        ),
        "aplicada": False,
        "destinos_incompatibles": sorted(INCOMPATIBLE_DESTINATIONS),
    }

    report = {
        "index": str(idx_path.relative_to(ROOT)),
        "n_events": len(events),
        "episodios_transfer_ventilated_corregido": n_transfer["n"],
        "con_evidencia_de_confort": comfort["n"],
        "tabla_destino_x_causa": {
            k: {d: dict(c) for d, c in v.items()} for k, v in tables.items()},
        "transfer_ventilated_destino_incompatible": {
            "n": len(incompatible),
            "pct_del_total_transfer": (
                round(100.0 * len(incompatible) / n_transfer["n"], 2)
                if n_transfer["n"] else 0.0),
            "ejemplos": incompatible[:20],
        },
        "regla_propuesta_reclasificaria": {
            "n": len(propuesta_events),
            "ejemplos": propuesta_events[:20],
        },
        "propuesta": prop,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "eicu_transfer.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# eICU-B: validación de `transfer_ventilated` (Fase 1.6d, punto 2)",
             "", f"- Índice: `{report['index']}`",
             f"- Eventos: {report['n_events']}",
             f"- Censuras `transfer_ventilated` (corregido): "
             f"{report['episodios_transfer_ventilated_corregido']}",
             f"- Con evidencia de confort/limitación: "
             f"{report['con_evidencia_de_confort']}",
             f"- `transfer_ventilated` con destino **incompatible**: "
             f"{len(incompatible)} "
             f"({report['transfer_ventilated_destino_incompatible']['pct_del_total_transfer']} %)",
             f"- La **regla propuesta** reclasificaría "
             f"{report['regla_propuesta_reclasificaria']['n']} eventos "
             f"(`transfer_ventilated` → `end_of_record`)",
             ""]
    for key, label in (("con_correccion", "Con corrección del fin"),
                       ("sin_correccion", "Sin corrección del fin")):
        lines += [f"## Destino × causa de censura — {label}", "",
                  "| Destino | " + " | ".join(
                      sorted({c for d in tables[key].values() for c in d}))
                  + " |", "|---|" + "---|" * len(
                      {c for d in tables[key].values() for c in d})]
        causes = sorted({c for d in tables[key].values() for c in d})
        for dest in sorted(tables[key], key=lambda d: -sum(
                tables[key][d].values())):
            row = " | ".join(str(tables[key][dest].get(c, 0)) for c in causes)
            lines.append(f"| {dest} | {row} |")
        lines.append("")
    lines += ["## Regla propuesta (NO aplicada)", "",
              f"> {prop['regla_propuesta']}", "",
              f"Destinos considerados incompatibles: "
              f"{', '.join(prop['destinos_incompatibles'])}", ""]
    (out_dir / "eicu_transfer.md").write_text("\n".join(lines) + "\n",
                                              encoding="utf-8")
    print("\n".join(lines[:30]))


if __name__ == "__main__":
    main()
