#!/usr/bin/env python3
"""
scripts/verify/fase1_6a/report_extract.py
=========================================
Fase 1.6a — Extrae las tablas del informe (`resumen.md`) desde
`eicu_evidence.json` / `eicu_hospitales.csv` y genera los histogramas.

Salida:
  reports/fase1_6a/figs/estancias_por_hospital.png
  reports/fase1_6a/figs/intervalo_mediano_por_hospital.png
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "reports" / "fase1_6a"


def main() -> None:
    ev = json.loads((OUT / "eicu_evidence.json").read_text(encoding="utf-8"))
    d = pd.read_csv(OUT / "eicu_hospitales.csv")

    print("### Concordancia entre fuentes\n")
    print("| Par | both | only_a | only_b | Jaccard | kappa | kappa (union) |")
    print("|---|---|---|---|---|---|---|")
    for k, v in ev["concordance"].items():
        print(f"| {k} | {v['both']} | {v['only_a']} | {v['only_b']} | "
              f"{v['jaccard']:.3f} | {v['kappa']:.3f} | {v['kappa_union']:.3f} |")

    print("\n### Escenarios\n")
    print("| Escenario | Hospitales | Estancias | min | P25 | mediana | P75 | máx | peso mayor |")
    print("|---|---|---|---|---|---|---|---|---|")
    for name, s in ev["scenarios"].items():
        print(f"| {name} | {s['hospitals']} | {s['vent_stays']} | {s['min']} | "
              f"{s['p25']:.0f} | {s['median']:.0f} | {s['p75']:.0f} | {s['max']} | "
              f"{100*s['largest_weight']:.1f}% |")

    print("\n### Top 30 hospitales por estancias ventiladas\n")
    cols = ["hospital_id", "region", "teaching", "beds_category", "vent_stays",
            "rc_invasive_stays", "pct_apache_vent_with_invasive", "pct_vent_with_airway",
            "pct_ventstart_supported", "pct_last_adj_lt1h", "inter_adj_median_min",
            "vars_ok50_n", "coverage_n"]
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for _, r in d.sort_values("vent_stays", ascending=False).head(30).iterrows():
        vals = []
        for c in cols:
            v = r[c]
            vals.append(f"{v:.1f}" if isinstance(v, float) else str(v))
        print("| " + " | ".join(vals) + " |")

    # Histogramas
    figs = OUT / "figs"
    figs.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(7, 3.5))
    plt.hist(d["vent_stays"].dropna(), bins=40, color="#4C72B0")
    plt.xlabel("estancias ventiladas por hospital")
    plt.ylabel("nº hospitales")
    plt.title("Estancias con evidencia de ventilación invasiva por hospital")
    plt.tight_layout()
    plt.savefig(figs / "estancias_por_hospital.png", dpi=120)
    plt.close()

    med = d["inter_adj_median_min"].dropna()
    plt.figure(figsize=(7, 3.5))
    plt.hist(med, bins=40, color="#55A868")
    plt.xlabel("intervalo mediano entre ajustes invasivos (min)")
    plt.ylabel("nº hospitales")
    plt.title("Intervalo mediano de anotación de ajustes invasivos por hospital")
    plt.tight_layout()
    plt.savefig(figs / "intervalo_mediano_por_hospital.png", dpi=120)
    plt.close()
    print(f"\n[figs] -> {figs}")


if __name__ == "__main__":
    main()
