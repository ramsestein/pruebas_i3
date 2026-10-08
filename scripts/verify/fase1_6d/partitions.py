#!/usr/bin/env python3
"""
scripts/verify/fase1_6d/partitions.py
=====================================
Fase 1.6d — **punto 7**: **propuesta** de particiones (sin aplicarlas) para las 4
cohortes, con sus eventos y desenlaces por bloque.

- **MIMIC**: test = 15 % de los pacientes por ``subject_id`` (semilla fija); 5
  pliegues por paciente (ningún paciente cae en train y test a la vez).
- **eICU-B**: test = 8 hospitales fijos (propuesta de la 1.6c); 5 pliegues por
  **hospital** del resto.
- **Clínic**: test = último 40 % del tiempo; el train se parte en **2 bloques
  temporales con ventana creciente** (bloque 1 → validar con bloque 2).
- **VitalDB**: los 96 eventos son **test** (sin ajuste fino ni validación
  cruzada; sólo test externo).

Salida: ``reports/fase1_6d/particiones.json`` / ``.md``.

Uso:
    python scripts/verify/fase1_6d/partitions.py
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
WINDOW = "48h"
INDEX_GLOBS = {
    "mimic": "datasets/mimic3wdb/cases_v*/mimic_cases_index.json",
    "eicu": "datasets/eicu_collaborative/cases_v*/eicu_cases_index.json",
    "clinic": "datasets/clinic/cases_v*/clinic_cases_index.json",
    "vitaldb": "datasets/vitaldb/cases_v*/vitaldb_cases_index.json",
}


def latest_index(cohort: str) -> Path | None:
    cands = sorted(ROOT.glob(INDEX_GLOBS[cohort]))
    return cands[-1] if cands else None


def _outcomes(events: list[dict]) -> dict:
    """Resumen de desenlaces de un conjunto de eventos."""
    success = censored = failures = 0
    for e in events:
        lab = (e.get("labels") or {}).get(WINDOW) or {}
        if lab.get("event_type") == "successful_extubation":
            success += 1
        else:
            censored += 1
        if int(lab.get("n_failed_attempts") or 0) > 0:
            failures += 1
    return {"n": len(events), "success": success, "censored": censored,
            "failures": failures}


def load(cohort: str) -> list[dict]:
    path = latest_index(cohort)
    if path is None:
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("events", [])


def mimic_partition(events: list[dict], cfg: dict, seed: int) -> dict:
    frac = float(cfg.get("mimic_test_fraction", 0.15))
    folds = int(cfg.get("mimic_folds", 5))
    patients = sorted({int(e["subject_id"]) for e in events
                       if e.get("subject_id") is not None})
    rng = random.Random(seed)
    shuffled = patients[:]
    rng.shuffle(shuffled)
    n_test = int(round(frac * len(patients)))
    test_pat = set(shuffled[:n_test])
    train_pat = [p for p in shuffled if p not in test_pat]
    test_ev = [e for e in events if int(e.get("subject_id", -1)) in test_pat]
    train_ev = [e for e in events if int(e.get("subject_id", -1)) not in test_pat]
    fold_assign = {p: i % folds for i, p in enumerate(train_pat)}
    fold_out = []
    for k in range(folds):
        ev = [e for e in train_ev
              if fold_assign.get(int(e.get("subject_id", -1))) == k]
        fold_out.append(_outcomes(ev))
    return {
        "criterio": f"test {frac:.0%} por subject_id + {folds} pliegues por paciente",
        "n_pacientes": len(patients),
        "test_pacientes": len(test_pat),
        "test": _outcomes(test_ev),
        "train": _outcomes(train_ev),
        "folds_train": fold_out,
    }


def eicu_partition(events: list[dict], cfg: dict) -> dict:
    test_h = set(int(x) for x in cfg.get("eicu_test_hospitals", []))
    folds = int(cfg.get("eicu_folds", 5))
    test_ev = [e for e in events if int(e.get("hospital_id", -1)) in test_h]
    rest = [e for e in events if int(e.get("hospital_id", -1)) not in test_h]
    train_h = sorted({int(e["hospital_id"]) for e in rest})
    fold_assign = {h: i % folds for i, h in enumerate(train_h)}
    fold_out = []
    for k in range(folds):
        ev = [e for e in rest if fold_assign.get(int(e["hospital_id"])) == k]
        fold_out.append(_outcomes(ev))
    return {
        "criterio": f"test = {len(test_h)} hospitales (fijos) + {folds} pliegues "
                    "por hospital del resto",
        "test_hospitals": sorted(test_h),
        "train_hospitals": len(train_h),
        "test": _outcomes(test_ev),
        "train": _outcomes(rest),
        "folds_train": fold_out,
    }


def clinic_partition(events: list[dict], cfg: dict) -> dict:
    frac = float(cfg.get("clinic_test_fraction", 0.40))
    n_blocks = int(cfg.get("clinic_train_blocks", 2))
    ordered = sorted(events, key=lambda e: float(e.get("t0_unix") or 0.0))
    n = len(ordered)
    n_train = int(round((1.0 - frac) * n))
    train = ordered[:n_train]
    test = ordered[n_train:]
    # Ventana creciente: bloques contiguos del train por tiempo.
    blocks = []
    for i in range(n_blocks):
        lo = int(round(i * len(train) / n_blocks))
        hi = int(round((i + 1) * len(train) / n_blocks))
        blocks.append(_outcomes(train[lo:hi]))
    return {
        "criterio": f"test = último {frac:.0%} del tiempo; train en {n_blocks} "
                    "bloques temporales (ventana creciente: entrenar con el "
                    "bloque 1, validar con el 2)",
        "test": _outcomes(test),
        "train": _outcomes(train),
        "bloques_train": blocks,
        "fecha_corte": (
            None if not test else
            __import__("datetime").datetime.utcfromtimestamp(
                float(test[0].get("t0_unix") or 0.0)).isoformat()),
    }


def vitaldb_partition(events: list[dict]) -> dict:
    return {
        "criterio": "los 96 eventos = test (test externo; sin ajuste fino ni "
                    "validación cruzada)",
        "test": _outcomes(events),
        "train": {"n": 0, "success": 0, "censored": 0, "failures": 0},
    }


def _md(rep: dict) -> list[str]:
    lines = ["# Particiones propuestas (Fase 1.6d, punto 7 — NO aplicadas)", ""]
    for cohort, r in rep.items():
        lines += [f"## {cohort}", "",
                  f"- Criterio: {r['criterio']}",
                  f"- Test: {r['test']}",
                  f"- Train: {r['train']}"]
        if "folds_train" in r:
            lines.append("- Pliegues de train: " +
                         ", ".join(f"n={f['n']} (éx {f['success']}/cens "
                                   f"{f['censored']})" for f in r["folds_train"]))
        if "bloques_train" in r:
            lines.append("- Bloques de train: " +
                         ", ".join(f"n={b['n']} (éx {b['success']}/cens "
                                   f"{b['censored']}, fallos {b['failures']})"
                                   for b in r["bloques_train"]))
        if "n_pacientes" in r:
            lines.append(f"- Pacientes: {r['n_pacientes']} "
                         f"(test {r['test_pacientes']})")
        if "test_hospitals" in r:
            lines.append(f"- Hospitales: test {r['test_hospitals']}, "
                         f"train {r['train_hospitals']}")
        lines.append("")
    return lines


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6d"))
    args = p.parse_args()

    config = load_config(args.config)
    cfg = config.get("fase1_6d", {}).get("partitions", {})
    seed = int(config.get("random_seed", 42))

    rep: dict[str, dict] = {}
    mimic = load("mimic")
    if mimic:
        rep["mimic"] = mimic_partition(mimic, cfg, seed)
    eicu = load("eicu")
    if eicu:
        rep["eicu_b"] = eicu_partition(eicu, cfg)
    clinic = load("clinic")
    if clinic:
        rep["clinic"] = clinic_partition(clinic, cfg)
    vitaldb = load("vitaldb")
    if vitaldb:
        rep["vitaldb"] = vitaldb_partition(vitaldb)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "particiones.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "particiones.md").write_text("\n".join(_md(rep)) + "\n",
                                            encoding="utf-8")
    print("\n".join(_md(rep)))


if __name__ == "__main__":
    main()
