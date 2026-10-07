#!/usr/bin/env python3
"""
scripts/verify/fase1_6c/eicu_b_report.py
========================================
Fase 1.6c — **punto 2**: cohorte eICU estrategia B.

D14: hospitales de la cohorte e2 (regla de permeabilidad sin densidad) con
**intervalo mediano de anotación ≤ 2 h**. La lista está en
``harmonize.yaml → fase1_6c.eicu_b.hospital_ids`` (27 hospitales).

Reporta: nº de hospitales, eventos, éxito/censura a 48 y 72 h **por causa**,
eventos con ≥ 1 fallo, inclusión **D13** (FC y SpO2 ≥ 50 % de horas útiles) y
``vars_ok`` 50/80 %, distribución de eventos por hospital / región / tamaño, y
comprueba que cada evento guarda ``patientunitstayid``, ``uniquepid`` y
``hospital_id``.

Salida: ``reports/fase1_6c/eicu_b.json``

Uso:
    python scripts/verify/fase1_6c/eicu_b_report.py [--index PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
CORE = ("HR", "SpO2")
MONTH_H = {"le1h": 1.0, "1_2h": 2.0, "gt2h": 4.0}


def latest_index(root: Path) -> Path | None:
    cands = sorted(root.glob("cases_v*/eicu_cases_index.json"))
    return cands[-1] if cands else None


def _by_cause(events: list[dict], window: str) -> dict:
    """Éxito y censura (excluyentes) y causas de la censura."""
    success = 0
    causes: Counter = Counter()
    for e in events:
        lab = e["labels"][window]
        if lab["event_type"] == "successful_extubation":
            success += 1
        else:
            causes[lab.get("censor_cause") or "desconocido"] += 1
    return {"success": success, "censored": sum(causes.values()),
            "causes": dict(causes)}


def _label_versions(events: list[dict], calib: dict | None) -> dict:
    """Las dos versiones del error de etiqueta: sin y con corrección del fin.

    La corrección del desplazamiento por estrato solo se aplica si mejora la
    concordancia en MIMIC en **ambos** estratos (1 h y 2 h); si no, la version
    aplicada es la de **sin corrección** y la corregida queda solo simulada.
    """
    if not calib:
        return {}
    transfer = {float(t["subsample_h"]): t
                for t in calib.get("transferable_error", [])}
    weights = Counter(e.get("annotation_stratum") for e in events)
    corr = calib.get("end_shift_correction") or {}
    verdict = corr.get("verdict_strata_1_2h") or {}
    after = {float(s["subsample_h"]): s for s in corr.get("metrics_corrected", [])}

    def _weighted(table, getter):
        num = den = 0.0
        for stratum, n in weights.items():
            h = MONTH_H.get(stratum)
            if h is None or h not in table:
                continue
            value = getter(table[h])
            if value is None:
                continue
            num += n * float(value)
            den += n
        return round(num / den, 2) if den else None

    antes = {
        "end_error_median_h": _weighted(transfer, lambda s: s["end_error_median_h"]),
        "label48_agreement_pct": _weighted(transfer, lambda s: s["label48_agreement_pct"]),
    }
    despues = {
        "end_error_median_h": _weighted(after, lambda s: s["end_error"]["median"]),
        "label48_agreement_pct": _weighted(after, lambda s: s["label48_agreement"]),
    }
    return {
        "estratos": dict(weights),
        "desplazamiento_por_estrato_h": corr.get("shifts_h"),
        "sin_correccion": antes,
        "con_correccion": despues,
        "veredicto_mimic": verdict,
        "aplicada": bool(verdict.get("approved")),
        "nota": ("La corrección (sumar al fin reconstruido la mediana con signo del "
                 "error de fin de su estrato) se aplica a eICU solo si mejora la "
                 "concordancia de la etiqueta a 48 h en MIMIC en los estratos de 1 h "
                 "y 2 h. Si el veredicto es negativo, la versión vigente es la de sin "
                 "corrección y la corregida queda como simulación."),
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--index", default=None)
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6c"))
    args = p.parse_args()

    config = load_config(args.config)
    cfg = config.get("fase1_6c", {}).get("eicu_b", {})
    hospitals = set(int(h) for h in cfg.get("hospital_ids", []))
    max_med = float(cfg.get("max_annotation_median_min", 120))

    path = Path(args.index) if args.index else latest_index(
        ROOT / "datasets" / "eicu_collaborative")
    if path is None:
        raise SystemExit("no hay índice de eICU")
    idx = json.loads(path.read_text(encoding="utf-8"))
    events = [e for e in idx["events"] if int(e["hospital_id"]) in hospitals]

    calib_path = ROOT / "reports" / "fase1_6c" / "calibracion_mimic.json"
    calib = (json.loads(calib_path.read_text(encoding="utf-8"))
             if calib_path.exists() else None)

    # Metadatos de hospital (región, tamaño, docencia)
    hosp_meta: dict[int, dict] = {}
    csv = ROOT / "reports" / "fase1_6b" / "eicu_hospitales_v3.csv"
    if csv.exists():
        import pandas as pd

        df = pd.read_csv(csv)
        for r in df.itertuples():
            hosp_meta[int(r.hospital_id)] = {
                "region": None if pd.isna(r.region) else str(r.region),
                "teaching": None if pd.isna(r.teaching) else str(r.teaching),
                "beds_category": None if pd.isna(r.beds_category) else str(r.beds_category),
                "inter_adj_median_min": (None if pd.isna(r.inter_adj_median_min)
                                         else float(r.inter_adj_median_min)),
            }

    per_hosp = Counter(int(e["hospital_id"]) for e in events)
    n_core = sum(1 for e in events
                 if all((e.get("coverage") or {}).get(v, 0.0) > 0.5 for v in CORE))
    n50 = sum(1 for e in events if e.get("vars_ok_50"))
    n80 = sum(1 for e in events if e.get("vars_ok_80"))
    n_fail = sum(1 for e in events if e["labels"]["48h"]["n_failed_attempts"] > 0)

    per_var: dict[str, list[float]] = {}
    for e in events:
        for k, v in (e.get("coverage") or {}).items():
            per_var.setdefault(k, []).append(float(v))

    missing_ids = {
        "sin_patientunitstayid": sum(1 for e in events if not e.get("patientunitstayid")),
        "sin_uniquepid": sum(1 for e in events if not e.get("uniquepid")),
        "sin_hospital_id": sum(1 for e in events if not e.get("hospital_id")),
    }

    out = {
        "index": str(path.relative_to(ROOT)),
        "regla": f"cohorte e2 con anotación mediana <= {max_med:.0f} min",
        "gap_h": idx.get("gap_h"),
        "n_hospitals": len(per_hosp),
        "n_events": len(events),
        "success_48h": _by_cause(events, "48h"),
        "success_72h": _by_cause(events, "72h"),
        "failure_events_48h": n_fail,
        "d13_incluidos": n_core,
        "d13_pct": round(100.0 * n_core / len(events), 1) if events else 0.0,
        "vars_ok_50": n50, "vars_ok_80": n80,
        "vars_ok_50_pct": round(100.0 * n50 / len(events), 1) if events else 0.0,
        "cobertura_mediana_por_variable": {
            k: round(float(np.median(v)), 3) for k, v in sorted(per_var.items())},
        "identificadores": missing_ids,
        "por_hospital": [
            {"hospital_id": h, "events": n, **hosp_meta.get(h, {})}
            for h, n in sorted(per_hosp.items(), key=lambda x: -x[1])],
        "por_region": dict(Counter(
            (hosp_meta.get(h) or {}).get("region") or "desconocida" for h in per_hosp)),
        "por_tamano": dict(Counter(
            (hosp_meta.get(h) or {}).get("beds_category") or "desconocido" for h in per_hosp)),
        "por_docencia": dict(Counter(
            (hosp_meta.get(h) or {}).get("teaching") or "desconocido" for h in per_hosp)),
        "eventos_por_hospital": {
            "min": int(min(per_hosp.values())), "median": float(np.median(list(per_hosp.values()))),
            "max": int(max(per_hosp.values())),
            "mayor_peso_pct": round(100.0 * max(per_hosp.values()) / len(events), 1),
        },
        "por_estrato_de_anotacion": dict(Counter(
            e.get("annotation_stratum") for e in events)),
        "error_de_etiqueta": _label_versions(events, calib),
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "eicu_b.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items()
                      if k not in ("por_hospital",)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
