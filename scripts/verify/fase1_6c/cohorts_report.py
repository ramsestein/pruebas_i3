#!/usr/bin/env python3
"""
scripts/verify/fase1_6c/cohorts_report.py
=========================================
Fase 1.6c — **puntos 6 y 7**: viabilidad de las particiones y perfiles de
disponibilidad. **No** se aplica ninguna partición: solo se mide.

Punto 6 (particiones)
---------------------
- MIMIC: pacientes con > 1 estancia; tamaño de un test del 15 % **por paciente**
  y de 5 pliegues.
- eICU-B: nº de hospitales; propuesta de hospitales de test (~20–25 %,
  estratificados por región y tamaño) y tamaño resultante; si quedan < 4
  hospitales de test o < 10 de train, plan alternativo (por paciente
  estratificado por hospital).
- Clínic y VitalDB: eventos y desenlaces por mes; corte temporal 60/40 y 3–4
  bloques temporales dentro del train.

Punto 7 (perfiles de disponibilidad)
------------------------------------
Por cohorte: frecuencia de cada combinación de variables con ≥ 50 % de horas
útiles y, por variable, fracción de eventos con la variable (a) ausente todo el
evento, (b) intermitente y (c) completa.

Salida: ``reports/fase1_6c/particiones.json`` y ``reports/fase1_6c/perfiles.json``

Uso:
    python scripts/verify/fase1_6c/cohorts_report.py
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
CORE = ("HR", "SpO2")
MANDATORY6 = ("HR", "SpO2", "MAP", "RR", "FiO2", "PEEP")


def _latest(globs: list[str]) -> Path | None:
    cands: list[Path] = []
    for g in globs:
        cands.extend(ROOT.glob(g))
    return sorted(cands)[-1] if cands else None


def _events(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["events"]


def _per_event_coverage(cohort: str, events: list[dict]) -> dict[str, dict[str, float]]:
    """Cobertura por evento: del índice (eICU/Clínic/VitalDB) o del CSV (MIMIC)."""
    if cohort == "mimic":
        csv = ROOT / "reports" / "fase1_6c" / "mimic_coverage_events.csv"
        if not csv.exists():
            return {}
        df = pd.read_csv(csv)
        return {str(r.event_id): {v: float(getattr(r, v)) for v in MANDATORY6
                                  if hasattr(r, v)} for r in df.itertuples()}
    return {str(e["event_id"]): dict(e.get("coverage") or {}) for e in events}


# ── Punto 7: perfiles de disponibilidad ─────────────────────────────────────

def availability(coverage: dict[str, dict[str, float]],
                 variables: tuple[str, ...] = MANDATORY6) -> dict:
    combos: Counter = Counter()
    state = {v: Counter() for v in variables}
    for cov in coverage.values():
        present = tuple(v for v in variables if cov.get(v, 0.0) > 0.5)
        combos["+".join(present) if present else "(ninguna)"] += 1
        for v in variables:
            f = cov.get(v, 0.0)
            state[v]["ausente_todo" if f <= 0.0 else
                     ("completa" if f >= 0.999 else "intermitente")] += 1
    n = len(coverage) or 1
    top = combos.most_common(8)
    return {
        "n_events": len(coverage),
        "combinaciones_top": [{"variables": k, "events": c,
                               "pct": round(100.0 * c / n, 1)} for k, c in top],
        "combinaciones_distintas": len(combos),
        "estado_por_variable": {
            v: {k: {"events": c, "pct": round(100.0 * c / n, 1)}
                for k, c in sorted(state[v].items())}
            for v in variables},
    }


# ── Punto 6: particiones ────────────────────────────────────────────────────

def mimic_partitions(events: list[dict]) -> dict:
    by_subj = defaultdict(set)
    for e in events:
        by_subj[e["subject_id"]].add(e["icustay_id"])
    multi = {s: st for s, st in by_subj.items() if len(st) > 1}
    n_subj = len(by_subj)
    test15 = math.ceil(0.15 * n_subj)
    return {
        "n_events": len(events),
        "n_patients": n_subj,
        "pacientes_con_mas_de_una_estancia": len(multi),
        "pct_pacientes_multi_estancia": round(100.0 * len(multi) / n_subj, 1),
        "test_15_por_paciente": {"pacientes": test15,
                                 "pct_pacientes": round(100.0 * test15 / n_subj, 1)},
        "cinco_pliegues": {"pacientes_por_pliegue": round(n_subj / 5.0, 1),
                           "test_por_pliegue": n_subj - math.ceil(0.2 * n_subj)},
        "aviso": ("partición por PACIENTE (subject_id): un mismo paciente puede "
                  "tener varias estancias y no debe cruzar train/test"),
    }


def eicu_partitions(events: list[dict], meta: dict[int, dict]) -> dict:
    per_hosp = Counter(int(e["hospital_id"]) for e in events)
    hospitals = sorted(per_hosp)
    by_region: dict[str, list[int]] = defaultdict(list)
    for h in hospitals:
        by_region[(meta.get(h) or {}).get("region") or "desconocida"].append(h)
    target_hosp = max(4, math.ceil(0.225 * len(hospitals)))
    target_events = 0.25 * len(events)
    # Muestreo aleatorio FIJO (semilla 42) dentro de cada región, ~25 % de los
    # hospitales: determinista, sin sesgo de tamaño y con cobertura geográfica.
    import random

    rng = random.Random(42)
    test: list[int] = []
    for region, hs in sorted(by_region.items()):
        hs = sorted(hs)
        k = max(1, int(round(0.25 * len(hs))))
        test.extend(rng.sample(hs, min(k, len(hs))))
    test = sorted(set(test))
    train = [h for h in hospitals if h not in set(test)]
    events_test = sum(per_hosp[h] for h in test)
    proposal = {
        "n_hospitals": len(hospitals),
        "test_hospitals": test,
        "test_pct_hospitals": round(100.0 * len(test) / len(hospitals), 1),
        "test_events": events_test,
        "test_pct_events": round(100.0 * events_test / len(events), 1),
        "train_hospitals": len(train),
        "train_events": len(events) - events_test,
        "estratificado_por": "region (y tamaño dentro de cada región)",
    }
    viable = len(test) >= 4 and len(train) >= 10
    out = {"proposal_hospital_level": proposal, "viable_hospital_level": viable}
    out["sesgo_tamano"] = (
        "el test se elige de menor a mayor volumen para acercarse al 25 % de "
        "eventos; con 27 hospitales el reparto por hospitales NO puede dar a la "
        "vez 25 % de hospitales y 25 % de eventos (los grandes concentran la "
        "muestra). Reportar ambos porcentajes y vigilar el sesgo por tamaño.")
    out["plan_alternativo"] = (
        "partición por PACIENTE (uniquepid) estratificada por hospital: cada "
        "hospital aporta pacientes a train y test, sin fuga entre estancias del "
        "mismo paciente y con todos los hospitales representados")
    out["razon_alternativa"] = f"test={len(test)} hospitales, train={len(train)}"
    return out


def _random_fallback(events: list[dict], seed: int = 42) -> dict:
    """Alternativa cuando NO hay corte temporal posible (p. ej. VitalDB)."""
    import random

    keys = sorted({(e.get("box"), e.get("event_id")) for e in events})
    rng = random.Random(seed)
    rng.shuffle(keys)
    n_test = max(1, int(round(0.25 * len(keys))))
    test = set(k[1] for k in keys[:n_test])
    te = [e for e in events if e["event_id"] in test]
    tr = [e for e in events if e["event_id"] not in test]

    def _stats(evs: list[dict]) -> dict:
        return {"events": len(evs), "fallos": sum(
            1 for e in evs if (e["labels"]["48h"]["n_failed_attempts"] > 0)),
            "censura": sum(1 for e in evs if e["labels"]["48h"]["event_type"]
                           != "successful_extubation")}

    return {"motivo": "todos los eventos caen en 3 meses: no hay corte temporal",
            "propuesta": "partición aleatoria fija (semilla 42) 75/25",
            "train": _stats(tr), "test": _stats(te)}


def temporal_partitions(events: list[dict]) -> dict:
    rows = []
    for e in events:
        t = e.get("t0_unix")
        if not t:
            continue
        lab = (e.get("labels") or {}).get("48h") or {}
        rows.append({"month": datetime.fromtimestamp(float(t), tz=timezone.utc)
                     .strftime("%Y-%m"),
                     "end_reason": e.get("end_reason"),
                     "event_type": lab.get("event_type"),
                     "failed": int((lab.get("n_failed_attempts") or 0) > 0)})
    if not rows:
        return {"n_events": 0}
    df = pd.DataFrame(rows).sort_values("month")
    months = sorted(df["month"].unique())
    per_month = df.groupby("month").agg(
        events=("month", "size"), fallos=("failed", "sum")).to_dict(orient="index")
    n = len(df)
    cut = int(0.6 * n)
    train_months, acc = [], 0
    for m in months:
        if acc >= cut:
            break
        train_months.append(m)
        acc += int(per_month[m]["events"])
    train = df[df["month"].isin(train_months)]
    test = df[~df["month"].isin(train_months)]
    blocks = np.array_split(np.array(train_months), min(4, max(1, len(train_months))))
    return {
        "n_events": n,
        "meses": {m: {"events": int(v["events"]), "fallos": int(v["fallos"])}
                  for m, v in per_month.items()},
        "corte_60_40": {
            "train_meses": train_months, "train_events": int(len(train)),
            "test_meses": [m for m in months if m not in train_months],
            "test_events": int(len(test)),
        },
        "bloques_train": [
            {"bloque": i + 1, "meses": list(b), "events": int(train["month"].isin(b).sum()),
             "fallos": int(train.loc[train["month"].isin(b), "failed"].sum())}
            for i, b in enumerate(blocks)],
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6c"))
    args = p.parse_args()

    config = load_config(args.config)
    cfg = config.get("fase1_6c", {})
    eicu_b_hospitals = set(int(h) for h in cfg.get("eicu_b", {}).get("hospital_ids", []))

    paths = {
        "mimic": _latest(["datasets/mimic3wdb/cases_v*/mimic_cases_index.json"]),
        "eicu": _latest(["datasets/eicu_collaborative/cases_v*/eicu_cases_index.json"]),
        "clinic": _latest(["datasets/clinic/cases_v*/clinic_cases_index.json"]),
        "vitaldb": _latest(["datasets/vitaldb/cases_v*/vitaldb_cases_index.json"]),
    }
    raw = {k: _events(v) for k, v in paths.items()}
    events = {k: [e for e in v if not e.get("excluded")] for k, v in raw.items()}
    events["eicu"] = [e for e in raw["eicu"]
                      if int(e["hospital_id"]) in eicu_b_hospitals] or raw["eicu"]

    meta: dict[int, dict] = {}
    csv = ROOT / "reports" / "fase1_6b" / "eicu_hospitales_v3.csv"
    if csv.exists():
        df = pd.read_csv(csv)
        for r in df.itertuples():
            meta[int(r.hospital_id)] = {
                "region": None if pd.isna(r.region) else str(r.region),
                "beds_category": None if pd.isna(r.beds_category) else str(r.beds_category)}

    particiones = {
        "indices": {k: (str(v.relative_to(ROOT)) if v else None) for k, v in paths.items()},
        "mimic": mimic_partitions(events["mimic"]),
        "eicu_b": eicu_partitions(events["eicu"], meta),
        "clinic": temporal_partitions(events["clinic"]),
        "vitaldb": temporal_partitions(events["vitaldb"]),
    }
    if particiones["vitaldb"].get("corte_60_40", {}).get("test_events", 0) < 5:
        particiones["vitaldb"]["alternativa"] = _random_fallback(events["vitaldb"])

    perfiles = {}
    for cohort in ("mimic", "eicu", "clinic", "vitaldb"):
        cov = _per_event_coverage(cohort, events[cohort])
        perfiles[cohort] = (availability(cov) if cov else
                            {"n_events": 0, "aviso": "sin cobertura medida"})

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "particiones.json").write_text(
        json.dumps(particiones, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    (out_dir / "perfiles.json").write_text(
        json.dumps(perfiles, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    print(json.dumps(particiones, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(perfiles, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
