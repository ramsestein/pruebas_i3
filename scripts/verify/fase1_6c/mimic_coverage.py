#!/usr/bin/env python3
"""
scripts/verify/fase1_6c/mimic_coverage.py
=========================================
Fase 1.6c — **punto 1**: diagnóstico y corrección de la cobertura de variables
de MIMIC (D8) y de la extracción de itemids.

Qué comprueba:

1. **Cobertura POR VARIABLE** en los eventos de MIMIC: fracción de eventos con
   > 50 % de horas útiles (arrastre de 4 h para constantes y 12 h para ajustes,
   en MINUTOS) y **variable limitante**.
2. **Itemids**: que todos los del catálogo (``mimic_itemids.py``) estén en el
   parquet de observaciones, y que su etiqueta coincida con ``D_ITEMS``.
3. **D13**: inclusión por núcleo mínimo (FC y SpO2 ≥ 50 %), que es el criterio
   real de admisión; ``vars_ok`` (6 variables) pasa a ser calidad.

Salidas (``reports/fase1_6c/``):
  ``mimic_coverage.json``
  ``mimic_itemids_check.json``

Uso:
    python scripts/verify/fase1_6c/mimic_coverage.py [--limit-stays N]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pyarrow.dataset as ds
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.eicu_levels import hourly_coverage  # noqa: E402
from src.create_dataset.mimic_itemids import MIMIC_CHART_ITEMIDS  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"

# Variables a diagnosticar -> conceptos de origen (varios = se toma el mejor).
VARIABLES: dict[str, tuple[str, ...]] = {
    "HR": ("HR",),
    "SpO2": ("SpO2",),
    "MAP_inv": ("MAP_invasive",),
    "MAP_nibp": ("MAP_non_invasive",),
    "MAP": ("MAP_invasive", "MAP_non_invasive"),
    "RR": ("RR_V",),
    "FiO2": ("FiO2",),
    "PEEP": ("PEEP",),
    "TV": ("TV_set", "TV_observed"),
    "PIP": ("PIP",),
}

# LOCF por tipo de variable (min): constantes 2 h, ajustes 12 h, D8.
LOCF_CONST_H = 2.0
LOCF_SETTING_H = 12.0
MANDATORY_6 = ("HR", "SpO2", "MAP", "RR", "FiO2", "PEEP")
D13_CORE = ("HR", "SpO2")

SETTING_VARS = ("RR", "FiO2", "PEEP", "TV", "PIP")


def latest_mimic_dir(root: Path) -> Path:
    cands = [d for d in sorted(root.glob("cases_v*"))
             if (d / "mimic_cases_index.json").exists()]
    if not cands:
        raise FileNotFoundError(f"sin cases_v*/mimic_cases_index.json en {root}")
    return cands[-1]


def load_series(parquet: Path, concepts: list[str]) -> dict[str, dict[int, np.ndarray]]:
    """``{concepto: {icustay_id: tiempos_en_minutos}}`` (sin valores: presencia)."""
    dset = ds.dataset(parquet, format="parquet")
    out: dict[str, dict[int, np.ndarray]] = {}
    for c in concepts:
        table = dset.to_table(columns=["ICUSTAY_ID", "t_unix"],
                              filter=ds.field("CONCEPT") == c)
        df = table.to_pandas()
        if df.empty:
            out[c] = {}
            continue
        df["min"] = df["t_unix"].astype("float64") / 60.0
        grouped = df.groupby("ICUSTAY_ID", sort=False)["min"].apply(
            lambda s: np.sort(s.to_numpy(dtype=np.float64)))
        out[c] = {int(k): v for k, v in grouped.items()}
    return out


def itemids_check(parquet: Path, clinical_dir: Path) -> dict:
    """Todos los itemids del catálogo presentes en el parquet + etiquetas D_ITEMS."""
    dset = ds.dataset(parquet, format="parquet")
    present = set(int(x) for x in dset.to_table(columns=["ITEMID"])["ITEMID"].to_pandas().unique())
    catalog = {iid: (concept, label)
               for concept, pairs in MIMIC_CHART_ITEMIDS.items()
               for iid, label in pairs}

    labels_ditems: dict[int, str] = {}
    dpath = Path(clinical_dir).parent / "D_ITEMS.csv.gz"
    if not dpath.exists():
        dpath = Path("D:/data/mimiciii/D_ITEMS.csv.gz")
    if dpath.exists():
        di = pd.read_csv(dpath, compression="gzip", usecols=["ITEMID", "LABEL", "LINKSTO"])
        labels_ditems = {int(r.ITEMID): str(r.LABEL) for r in di.itertuples()}

    rows = []
    for iid, (concept, label) in sorted(catalog.items()):
        rows.append({
            "itemid": iid, "concept": concept, "label_catalogo": label,
            "en_parquet": iid in present,
            "label_ditems": labels_ditems.get(iid),
            "label_coincide": (labels_ditems.get(iid) is None
                               or labels_ditems[iid].strip().lower() == label.strip().lower()),
        })
    return {
        "n_catalogo": len(catalog),
        "n_en_parquet": sum(1 for r in rows if r["en_parquet"]),
        "faltantes": [r["itemid"] for r in rows if not r["en_parquet"]],
        "etiquetas_distintas": [r for r in rows if r["label_ditems"] and not r["label_coincide"]],
        "detalle": rows,
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--limit-stays", type=int, default=0)
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6c"))
    args = p.parse_args()

    config = load_config(args.config)
    root = ROOT / config.get("fase1_6b", {}).get("mimic_cases_root", "datasets/mimic3wdb")
    cases = latest_mimic_dir(root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    idx = json.loads((cases / "mimic_cases_index.json").read_text(encoding="utf-8"))
    events = [e for e in idx["events"] if not e.get("excluded")]
    if args.limit_stays:
        events = events[: args.limit_stays]
    print(f"[mimic] {len(events)} eventos de {cases.name}", flush=True)

    concepts = sorted({c for names in VARIABLES.values() for c in names})
    series = load_series(cases / "mimic_observations.parquet", concepts)
    print("[mimic] series cargadas: "
          + ", ".join(f"{c}={len(v)}" for c, v in series.items()), flush=True)

    per_var: dict[str, list[float]] = defaultdict(list)
    limiting: Counter = Counter()
    below50: Counter = Counter()
    d13_ok = 0
    mandatory_ok = 0
    mandatory_ok80 = 0
    n_missing_events = 0
    per_event: list[dict] = []
    rows: list[dict] = []

    for e in events:
        stay = int(e["icustay_id"])
        t0_min = float(e["t0_unix"]) / 60.0
        spans = [(t0_min + a["vent_start_h"] * 60.0, t0_min + a["vent_end_h"] * 60.0)
                 for a in e["attempts"]]
        fracs: dict[str, float] = {}
        any_series = False
        for var, sources in VARIABLES.items():
            age = LOCF_SETTING_H if var in SETTING_VARS else LOCF_CONST_H
            best = 0.0
            for src in sources:
                times = series.get(src, {}).get(stay)
                if times is None or times.size == 0:
                    continue
                any_series = True
                best = max(best, hourly_coverage(times, np.ones(times.size), spans,
                                                 max_age_h=age))
            fracs[var] = best
        if not any_series:
            n_missing_events += 1
        for var, f in fracs.items():
            per_var[var].append(f)
        mand = {v: fracs[v] for v in MANDATORY_6}
        lim = min(mand, key=lambda k: mand[k])
        limiting[lim] += 1
        for var in MANDATORY_6:
            if fracs[var] <= 0.5:
                below50[var] += 1
        if all(fracs[v] > 0.5 for v in MANDATORY_6):
            mandatory_ok += 1
        if all(fracs[v] > 0.8 for v in MANDATORY_6):
            mandatory_ok80 += 1
        core_ok = all(fracs[v] > 0.5 for v in D13_CORE)
        d13_ok += int(core_ok)
        rows.append({"event_id": e["event_id"], "icustay_id": stay,
                     "subject_id": e.get("subject_id"),
                     "core_ok": core_ok,
                     **{k: round(v, 4) for k, v in fracs.items()}})
        if len(per_event) < 200:
            per_event.append({"event_id": e["event_id"], "core_ok": core_ok,
                              **{k: round(v, 3) for k, v in fracs.items()}})

    n = len(events)
    summary = {
        "cases_dir": str(cases.relative_to(ROOT)),
        "n_events": n,
        "n_events_sin_ninguna_serie": n_missing_events,
        "pct_mayor_50": {v: round(100.0 * np.mean([f > 0.5 for f in vals]), 1)
                         for v, vals in per_var.items()},
        "mediana_cobertura": {v: round(float(np.median(vals)), 3)
                              for v, vals in per_var.items()},
        "pct_mayor_80": {v: round(100.0 * np.mean([f > 0.8 for f in vals]), 1)
                         for v, vals in per_var.items()},
        "variable_limitante": dict(limiting.most_common()),
        # ``variable_limitante`` es el mínimo de las 6 variables (con coberturas
        # altas suele ser un empate sin significado). Lo que de verdad bloquea
        # ``vars_ok`` es cuántos eventos caen por debajo del 50 % en cada
        # variable: ``variables_bajo_50``.
        "variables_bajo_50": dict(below50.most_common()),
        "vars_ok_50_pct": round(100.0 * mandatory_ok / n, 1) if n else 0.0,
        "vars_ok_50_n": mandatory_ok,
        "vars_ok_80_pct": round(100.0 * mandatory_ok80 / n, 1) if n else 0.0,
        "vars_ok_80_n": mandatory_ok80,
        "d13_core_ok_pct": round(100.0 * d13_ok / n, 1) if n else 0.0,
        "d13_core_ok_n": d13_ok,
        "locf": {"constantes_h": LOCF_CONST_H, "ajustes_h": LOCF_SETTING_H},
        "unidades": "coverage en MINUTOS (t_unix/60 y spans*60)",
        "ejemplos": per_event,
    }
    (out_dir / "mimic_coverage.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    # Tabla por evento (perfiles de disponibilidad, punto 7)
    cols = ["event_id", "icustay_id", "subject_id", "core_ok", *VARIABLES.keys()]
    pd.DataFrame(rows, columns=cols).to_csv(
        out_dir / "mimic_coverage_events.csv", index=False)
    print(json.dumps({k: v for k, v in summary.items() if k != "ejemplos"},
                     ensure_ascii=False, indent=2))

    check = itemids_check(cases / "mimic_observations.parquet",
                          config.get("paths", {}).get("mimic_clinical_dir", "datasets/mimic3wdb/clinical"))
    (out_dir / "mimic_itemids_check.json").write_text(
        json.dumps(check, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in check.items() if k != "detalle"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
