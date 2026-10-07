#!/usr/bin/env python3
"""
scripts/verify/fase1_6c/fix_16b_label_error.py
==============================================
Fase 1.6c — **punto 4** (corrección del artefacto de la fase anterior).

La tabla final de la Fase 1.6b trasladaba a Clínic y VitalDB el error de
etiqueta calibrado en MIMIC (estrato de 1 h). Eso es incorrecto: en esas dos
cohortes la etiqueta no sale de anotaciones de enfermería con frecuencia
variable, sino de la **señal continua** (onda AWP/FLOW). El error de
temporalidad de anotación no aplica.

Este script reescribe ``label_error`` de Clínic y VitalDB en
``reports/fase1_6b/tabla_final.{json,md}`` con el valor

    ``no aplica (señal continua)``

y deja constancia de la corrección (``label_error_correccion``). El resto de
la tabla no se toca.

Uso:
    python scripts/verify/fase1_6c/fix_16b_label_error.py
    python scripts/verify/fase1_6c/fix_16b_label_error.py --check   # no escribe
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

COHORTS = ("clinic", "vitaldb")
NO_APLICA = "no aplica (señal continua)"
NOTA = ("Etiqueta derivada de la señal continua (onda AWP/FLOW): no existe el "
        "error de temporalidad de las anotaciones de enfermería de MIMIC/eICU.")


def _fmt_h(v) -> str:
    return "—" if v is None else f"{v:.2f} h"


def _fmt_pct(v) -> str:
    return "—" if v is None else f"{v:.1f} %"


def _markdown(table: dict[str, dict]) -> list[str]:
    lines = ["# Tabla final de las 4 cohortes (Fase 1.6b, punto 5)", "",
             "> Corrección de la Fase 1.6c (punto 4): el error de etiqueta de "
             "Clínic y VitalDB es **no aplica (señal continua)**; la 1.6b les "
             "trasladaba el error de anotación calibrado en MIMIC, que no "
             "corresponde a una etiqueta derivada de la onda.", "",
             "| Cohorte | Eventos | Éxito 48 h | Censura 48 h | Fallos (≥1) | "
             "vars_ok 50 % | vars_ok 80 % | Error de etiqueta (fin) | Etiq. 48 h |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, t in table.items():
        le = t.get("label_error", {})
        err = (le.get("source") if le.get("end_error_median_h") is None
               else _fmt_h(le.get("end_error_median_h")))
        lines.append(
            f"| {name} | {t.get('n_events', 0)} | {t.get('success', 0)} | "
            f"{t.get('censored', 0)} | {t.get('failure_events_48h', 0)} | "
            f"{t.get('vars_ok_50', 0)} | {t.get('vars_ok_80', 0)} | {err} | "
            f"{_fmt_pct(le.get('label48_agreement_pct'))} |")
    lines += ["", "Causas de censura (48 h):", ""]
    for name, t in table.items():
        lines.append(f"- **{name}**: {t.get('causes', {})}")
    return lines


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6b"))
    p.add_argument("--check", action="store_true",
                   help="solo informa; no reescribe los artefactos")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    json_path = out_dir / "tabla_final.json"
    md_path = out_dir / "tabla_final.md"
    if not json_path.exists():
        print(f"[error] no existe {json_path}")
        return

    table = json.loads(json_path.read_text(encoding="utf-8"))
    changed = False
    for cohort in COHORTS:
        t = table.get(cohort)
        if not t:
            print(f"[aviso] {cohort} no está en la tabla")
            continue
        before = t.get("label_error", {})
        if before.get("source") == NO_APLICA:
            print(f"[{cohort}] ya corregido")
            continue
        t["label_error"] = {
            "end_error_median_h": None,
            "label48_agreement_pct": None,
            "source": NO_APLICA,
            "nota": NOTA,
        }
        t["label_error_correccion"] = {
            "fase": "1.6c punto 4",
            "antes": before,
        }
        changed = True
        print(f"[{cohort}] {before.get('source')} -> {NO_APLICA}")

    if args.check:
        print("\n(--check: no se ha escrito nada)")
        return
    if not changed:
        print("nada que cambiar")
        return

    json_path.write_text(json.dumps(table, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    md_path.write_text("\n".join(_markdown(table)) + "\n", encoding="utf-8")
    print(f"\nescritos {json_path.name} y {md_path.name}")


if __name__ == "__main__":
    main()
