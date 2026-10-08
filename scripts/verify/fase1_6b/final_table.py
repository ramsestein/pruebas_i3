#!/usr/bin/env python3
"""
scripts/verify/fase1_6b/final_table.py
======================================
Fase 1.6b — **punto 5**: tabla final de las 4 cohortes.

Por cohorte:

- número de eventos;
- éxito / censura a 48 h (y 72 h), mutuamente excluyentes;
- eventos con **≥ 1 fallo** (columna aparte, se conserva aunque haya censura);
- ``vars_ok`` al 50 % y al 80 % (cobertura por variable con D8, LOCF 4 h);
- **error de etiqueta estimado** trasladado de la calibración en MIMIC según la
  frecuencia de anotación (MIMIC es la referencia → 0).

Fuentes:
  MIMIC   ``datasets/mimic3wdb/cases_v*/mimic_cases_index.json`` (+ observations)
  eICU    ``reports/fase1_6b/eicu_events_summary.json``
  Clínic  ``datasets/clinic/cases_v*/clinic_cases_index.json``
  VitalDB ``datasets/vitaldb/cases_v*/vitaldb_cases_index.json``

Salidas (``reports/fase1_6b/``):
  ``tabla_final.json``
  ``tabla_final.md``

Uso:
    python scripts/verify/fase1_6b/final_table.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow.dataset as ds

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.eicu_levels import hourly_coverage  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"

# Variables obligatorias de ``vars_ok`` por cohorte -> conceptos de origen.
MIMIC_COVERAGE_SOURCES = {
    "HR": ("HR",),
    "SpO2": ("SpO2",),
    "MAP": ("MAP_invasive", "MAP_non_invasive"),
    "RR": ("RR_V",),
    "FiO2": ("FiO2",),
    "PEEP": ("PEEP",),
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest(root: Path, cohort: str) -> Path | None:
    cands = sorted(root.glob(f"cases_v*/{cohort}_cases_index.json"))
    return cands[-1] if cands else None


def _labels_stats(events: list[dict], window: str = "48h") -> dict:
    success = censored = 0
    causes: dict[str, int] = {}
    for e in events:
        lab = e["labels"][window]
        if lab["event_type"] == "successful_extubation":
            success += 1
        else:
            censored += 1
            c = lab.get("censor_cause") or "desconocido"
            causes[c] = causes.get(c, 0) + 1
    return {"success": success, "censored": censored, "causes": causes}


def _failure_events(events: list[dict], window: str = "48h") -> int:
    return sum(1 for e in events if e["labels"][window]["n_failed_attempts"] > 0)


def _vars_ok_counts(events: list[dict]) -> tuple[int, int, int]:
    n50 = sum(1 for e in events if e.get("vars_ok_50"))
    n80 = sum(1 for e in events if e.get("vars_ok_80"))
    n_measured = sum(1 for e in events if "vars_ok_50" in e)
    return n50, n80, n_measured


def mimic_coverage(cases_dir: Path, events: list[dict]) -> tuple[int, int, int]:
    """``vars_ok`` 50/80 para MIMIC (cobertura por variable, LOCF 4 h)."""
    dset = ds.dataset(cases_dir / "mimic_observations.parquet", format="parquet")
    concepts = sorted({c for names in MIMIC_COVERAGE_SOURCES.values() for c in names})
    table = dset.to_table(columns=["ICUSTAY_ID", "CONCEPT", "t_unix"],
                          filter=ds.field("CONCEPT").isin(concepts))
    df = table.to_pandas()
    df["min"] = df["t_unix"].astype("float64") / 60.0
    by_stay: dict[int, dict[str, list[tuple[float, float]]]] = {}
    for (stay, concept), g in df.groupby(["ICUSTAY_ID", "CONCEPT"], sort=False):
        by_stay.setdefault(int(stay), {})[str(concept)] = (
            g["min"].to_numpy(dtype=np.float64), np.ones(len(g)))

    n50 = n80 = n_measured = 0
    for e in events:
        stay = int(e["icustay_id"])
        t0_min = float(e["t0_unix"]) / 60.0
        spans = [(t0_min + a["vent_start_h"] * 60.0, t0_min + a["vent_end_h"] * 60.0)
                 for a in e["attempts"]]
        series = by_stay.get(stay, {})
        fracs: dict[str, float] = {}
        for var, sources in MIMIC_COVERAGE_SOURCES.items():
            best = 0.0
            for src in sources:
                if src in series:
                    t, v = series[src]
                    best = max(best, hourly_coverage(t, v, spans))
            fracs[var] = best
        n_measured += 1
        if fracs and all(v > 0.5 for v in fracs.values()):
            n50 += 1
        if fracs and all(v > 0.8 for v in fracs.values()):
            n80 += 1
    return n50, n80, n_measured


def main() -> None:
    try:  # consola de Windows en cp1252: los acentos/≥ rompen print()
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6b"))
    args = p.parse_args()

    config = load_config(args.config)
    cfg = config.get("fase1_6b", {})
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    calib = _load_json(out_dir / "calibracion_mimic.json")
    transfer = {float(t["subsample_h"]): t for t in calib["transferable_error"]}

    table: dict[str, dict] = {}

    # ── MIMIC (referencia) ──────────────────────────────────────────────────
    mimic_dir = sorted((ROOT / cfg.get("mimic_cases_root", "datasets/mimic3wdb"))
                       .glob("cases_v*"))
    mimic_dir = [d for d in mimic_dir if (d / "mimic_cases_index.json").exists()]
    if mimic_dir:
        idx = _load_json(mimic_dir[-1] / "mimic_cases_index.json")
        events = [e for e in idx["events"] if not e.get("excluded")]
        n50, n80, n_meas = mimic_coverage(mimic_dir[-1], events)
        table["mimic"] = {
            "n_events": len(events),
            **_labels_stats(events),
            "failure_events_48h": _failure_events(events),
            "vars_ok_50": n50, "vars_ok_80": n80, "n_measured": n_meas,
            "label_error": {"end_error_median_h": 0.0, "label48_agreement_pct": 100.0,
                            "source": "referencia"},
        }

    # ── eICU ────────────────────────────────────────────────────────────────
    eicu_sum = out_dir / "eicu_events_summary.json"
    if eicu_sum.exists():
        s = _load_json(eicu_sum)
        w = {k: v["events"] for k, v in s["by_annotation_stratum"].items()}
        total = sum(w.values()) or 1
        per = {"le1h": 1.0, "1_2h": 2.0, "gt2h": 4.0}
        end_err = sum(v * transfer[per[k]]["end_error_median_h"] for k, v in w.items()) / total
        agree = sum(v * transfer[per[k]]["label48_agreement_pct"] for k, v in w.items()) / total
        table["eicu"] = {
            "n_events": s["n_events"], "n_hospitals": s["n_hospitals"],
            "success": s["success_48h"], "censored": s["censored_48h"],
            "success_72h": s["success_72h"], "censored_72h": s["censored_72h"],
            "causes": s["censor_causes"],
            "failure_events_48h": s["failure_events_48h"],
            "vars_ok_50": s["vars_ok_50"], "vars_ok_80": s["vars_ok_80"],
            "n_measured": s["n_events"],
            "label_error": {"end_error_median_h": end_err,
                            "label48_agreement_pct": agree,
                            "source": "calibracion MIMIC ponderada por estrato",
                            "strata": w},
        }

    # ── Clínic y VitalDB ────────────────────────────────────────────────────
    for cohort, root in (("clinic", ROOT / "datasets" / "clinic"),
                         ("vitaldb", ROOT / "datasets" / "vitaldb")):
        path = _latest(root, cohort)
        if path is None:
            continue
        idx = _load_json(path)
        events = idx["events"]
        n50, n80, n_meas = _vars_ok_counts(events)
        t = transfer[1.0]          # anotación de onda continua -> estrato de 1 h
        table[cohort] = {
            "n_events": len(events),
            "n_excluded": idx.get("total_excluded_events"),
            "index": str(path.relative_to(ROOT)),
            **_labels_stats(events),
            "failure_events_48h": _failure_events(events),
            "vars_ok_50": n50, "vars_ok_80": n80, "n_measured": n_meas,
            "levels": idx.get("levels"),
            "end_reason": dict(Counter(e["end_reason"] for e in events)),
            "label_error": {"end_error_median_h": t["end_error_median_h"],
                            "label48_agreement_pct": t["label48_agreement_pct"],
                            "source": "calibracion MIMIC, estrato 1 h",
                            "annotation_median_min": 60.0},
        }

    (out_dir / "tabla_final.json").write_text(
        json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# Tabla final de las 4 cohortes (Fase 1.6b, punto 5)", "",
             "| Cohorte | Eventos | Éxito 48 h | Censura 48 h | Fallos (≥1) | "
             "vars_ok 50 % | vars_ok 80 % | Error de etiqueta (fin) | Etiq. 48 h |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, t_ in table.items():
        le = t_.get("label_error", {})
        lines.append(
            f"| {name} | {t_['n_events']} | {t_.get('success', 0)} | "
            f"{t_.get('censored', 0)} | {t_.get('failure_events_48h', 0)} | "
            f"{t_['vars_ok_50']} | {t_['vars_ok_80']} | "
            f"{le.get('end_error_median_h', float('nan')):.2f} h | "
            f"{le.get('label48_agreement_pct', float('nan')):.1f} % |")
    lines += ["", "Causas de censura (48 h):", ""]
    for name, t_ in table.items():
        lines.append(f"- **{name}**: {t_.get('causes', {})}")
    (out_dir / "tabla_final.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
