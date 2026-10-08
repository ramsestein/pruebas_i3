#!/usr/bin/env python3
"""
scripts/verify/fase1_6d/final_table.py
======================================
Fase 1.6d — **punto 8**: tabla final de las 4 cohortes.

Por cohorte:

- eventos e incluidos por **D13** (FC y SpO2 con >= 50 % de tiempo útil);
- éxito / censura a 48 h (mutuamente excluyentes);
- eventos con **>= 1 fallo**;
- ``vars_ok`` 50 % / 80 % **sin** y **con** variables derivadas de ondas (estas
  últimas solo si la validación de Bland–Altman del punto 4 las aprueba);
- error de etiqueta;
- perfil de disponibilidad dominante;
- partición propuesta (punto 7).

Lee los artefactos de ``reports/fase1_6d/`` (``derivadas_*.json``,
``particiones.json``) y los índices de cada cohorte. Salida:
``tabla_final.json`` / ``tabla_final.md``.

Uso:
    python scripts/verify/fase1_6d/final_table.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

REPORTS = ROOT / "reports" / "fase1_6d"
WINDOW = "48h"
D13_CORE = ("HR", "SpO2")
D13_MIN = 0.5
PROFILE_VARS = ("HR", "SpO2", "MAP", "RR", "FiO2", "PEEP")

INDEX = {
    "mimic": "datasets/mimic3wdb/cases_v*/mimic_cases_index.json",
    "eicu_b": "datasets/eicu_collaborative/cases_v*/eicu_cases_index.json",
    "clinic": "datasets/clinic/cases_v*/clinic_cases_index.json",
    "vitaldb": "datasets/vitaldb/cases_v*/vitaldb_cases_index.json",
}
# El error de etiqueta de las cohortes de señal no se hereda de MIMIC.
LABEL_ERROR = {
    "mimic": "0.00 h / 100 % (referencia)",
    "eicu_b": "eICU-B: corregida 87.0 % (0.00 h)",
    "clinic": "no aplica (señal continua)",
    "vitaldb": "no aplica (señal continua)",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_index(cohort: str) -> Path | None:
    cands = sorted(ROOT.glob(INDEX[cohort]))
    return cands[-1] if cands else None


def _outcomes(events: list[dict]) -> dict:
    success = censored = failures = 0
    for e in events:
        lab = (e.get("labels") or {}).get(WINDOW) or {}
        if lab.get("event_type") == "successful_extubation":
            success += 1
        else:
            censored += 1
        if int(lab.get("n_failed_attempts") or 0) > 0:
            failures += 1
    return {"success": success, "censored": censored, "failures": failures}


def _d13(events: list[dict]) -> int:
    n = 0
    for e in events:
        cov = e.get("coverage") or {}
        if not cov:
            continue
        if all(float(cov.get(v, 0.0)) > D13_MIN for v in D13_CORE):
            n += 1
    return n


def _vars_ok(events: list[dict], thr: float) -> int:
    n = 0
    for e in events:
        cov = e.get("coverage") or {}
        if not cov:
            continue
        if all(float(v) > thr for v in cov.values()):
            n += 1
    return n


def _profile_dominant(events: list[dict]) -> str:
    c = Counter()
    for e in events:
        cov = e.get("coverage") or {}
        if not cov:
            continue
        have = [v for v in PROFILE_VARS if float(cov.get(v, 0.0)) > D13_MIN]
        c["+".join(have) if have else "(ninguna)"] += 1
    return c.most_common(1)[0][0] if c else "—"


def _derived_accepted(cohort: str) -> dict:
    """Variables derivadas aprobadas por la validación (punto 4)."""
    path = REPORTS / f"derivadas_{cohort}.json"
    if not path.exists():
        return {}
    data = _load(path)
    return {v: bool(r.get("usable")) for v, r in
            (data.get("variables") or {}).items()}


def build_row(cohort: str) -> dict | None:
    path = latest_index(cohort)
    if path is None:
        return None
    events = _load(path)["events"]
    n_events = len(events)
    out = _outcomes(events)
    accepted = _derived_accepted(cohort)
    measured = [e for e in events if e.get("coverage")]
    row = {
        "index": str(path.relative_to(ROOT)),
        "n_events": n_events,
        "d13_included": None,
        "d13_pct": None,
        "success_48h": out["success"],
        "censored_48h": out["censored"],
        "failure_events": out["failures"],
        "vars_ok_50": None,
        "vars_ok_80": None,
        "derived_accepted": accepted,
        "vars_ok_50_con_derivadas": None,
        "vars_ok_80_con_derivadas": None,
        "label_error": LABEL_ERROR[cohort],
        "profile_dominant": "—",
        "n_without_coverage": n_events - len(measured),
        "cov_fuente": "índice",
    }
    if measured:
        d13 = _d13(events)
        row.update({
            "d13_included": d13,
            "d13_pct": round(100.0 * d13 / n_events, 1) if n_events else 0.0,
            "vars_ok_50": _vars_ok(measured, 0.5),
            "vars_ok_80": _vars_ok(measured, 0.8),
            "vars_ok_50_con_derivadas": (_vars_ok(measured, 0.5)
                                         if accepted else None),
            "vars_ok_80_con_derivadas": (_vars_ok(measured, 0.8)
                                         if accepted else None),
            "profile_dominant": _profile_dominant(events),
        })
        return row

    # Fallback: artefacto de cobertura agregado (MIMIC lo tiene en la 1.6c).
    if cohort == "mimic":
        art = ROOT / "reports" / "fase1_6c" / "mimic_coverage.json"
        if art.exists():
            d = _load(art)
            row.update({
                "d13_included": d.get("d13_core_ok_n"),
                "d13_pct": d.get("d13_core_ok_pct"),
                "vars_ok_50": d.get("vars_ok_50_n"),
                "vars_ok_80": d.get("vars_ok_80_n"),
                "profile_dominant": "las 6 variables (88.4 %)",
                "cov_fuente": str(art.relative_to(ROOT)),
            })
    return row


def to_md(rows: dict[str, dict], partitions: dict) -> list[str]:
    lines = ["# Tabla final de las 4 cohortes (Fase 1.6d, punto 8)", "",
             "| Cohorte | Eventos | D13 | Éxito 48 h | Censura 48 h | "
             "Fallos (≥1) | vars_ok 50 % | vars_ok 80 % | vars_ok 50 % + "
             "ondas | Error de etiqueta | Perfil dominante | Partición |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for cohort, r in rows.items():
        part = partitions.get(cohort, {})
        part_s = part.get("criterio", "—")[:70] if part else "—"
        v50 = "—" if r["vars_ok_50"] is None else f"{r['vars_ok_50']}"
        v80 = "—" if r["vars_ok_80"] is None else f"{r['vars_ok_80']}"
        v50d = ("—" if r["vars_ok_50_con_derivadas"] is None
                else f"{r['vars_ok_50_con_derivadas']}")
        d13 = ("—" if r["d13_included"] is None
               else f"{r['d13_included']} ({r['d13_pct']} %)")
        lines.append(
            f"| {cohort} | {r['n_events']} | {d13} | {r['success_48h']} | "
            f"{r['censored_48h']} | {r['failure_events']} | {v50} | {v80} | "
            f"{v50d} | {r['label_error']} | {r['profile_dominant']} | "
            f"{part_s} |")
    lines += ["", "Notas:", "",
              "- Éxito y censura son mutuamente excluyentes; los eventos con ≥ 1 "
              "fallo van aparte (también existen entre los censurados).",
              "- `vars_ok` (6 variables) es indicador de calidad, nunca filtro; "
              "el filtro es D13 (FC y SpO2 > 50 %).",
              "- `vars_ok + ondas` solo aparece si la validación Bland–Altman "
              "del punto 4 aprueba la variable derivada.", ""]
    return lines


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default=str(REPORTS))
    args = p.parse_args()

    rows: dict[str, dict] = {}
    for cohort in INDEX:
        r = build_row(cohort)
        if r is not None:
            rows[cohort] = r

    part_path = Path(args.out_dir) / "particiones.json"
    partitions = _load(part_path) if part_path.exists() else {}

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {c: {**r, "particion": partitions.get(c, {})}
               for c, r in rows.items()}
    (out_dir / "tabla_final.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "tabla_final.md").write_text(
        "\n".join(to_md(rows, partitions)) + "\n", encoding="utf-8")
    print("\n".join(to_md(rows, partitions)))


if __name__ == "__main__":
    main()
