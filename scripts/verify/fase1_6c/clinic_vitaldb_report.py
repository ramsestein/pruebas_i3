#!/usr/bin/env python3
"""
scripts/verify/fase1_6c/clinic_vitaldb_report.py
================================================
Fase 1.6c — **punto 4**: informe de las cohortes de señal (Clínic y VitalDB)
tras la reconstrucción completa con el índice v0.4.0.

Por cohorte:

- eventos y eventos excluidos, versión del índice;
- éxito / censura a 48 h y 72 h, mutuamente excluyentes, con causas de censura;
- eventos con **≥ 1 fallo** (columna aparte);
- **D13**: eventos con FC y SpO2 por encima del 50 % de horas útiles
  (incluye ``coverage``); es el criterio de inclusión de la Fase 1.6c;
- ``vars_ok`` al 50 % y al 80 % como **indicador de calidad** (6 variables), y
  cobertura por variable (mediana, p10 y fracción de eventos > 50 %);
- vocabulario de ``end_reason`` (debe ser único y conocido);
- eventos sin ``source_files`` (debe ser 0: un evento sin ficheros no es
  trazable).

Clínic y VitalDB **no** heredan error de etiqueta de MIMIC: su etiqueta sale de
la señal continua (onda AWP/FLOW), no de anotaciones de enfermería con
frecuencia variable. El error de etiqueta es ``no aplica (señal continua)``.

Salidas (``reports/fase1_6c/``):
  ``clinic_vitaldb.json``
  ``clinic_vitaldb.md``

Uso:
    python scripts/verify/fase1_6c/clinic_vitaldb_report.py
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

D13_CORE = ("HR", "SpO2")
D13_MIN_COVERAGE = 0.5
LABEL_ERROR = "no aplica (señal continua)"
# Vocabulario canónico de ``end_reason`` (Fase 1.6d: único en las 4 cohortes).
from src.common.end_reasons import END_REASONS as _END_REASONS  # noqa: E402

END_REASON_VOCAB = set(_END_REASONS)
WINDOWS = ("48h", "72h")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_index(cohort: str, root: Path | None = None) -> Path | None:
    base = root or (ROOT / "datasets" / cohort)
    cands = sorted(base.glob(f"cases_v*/{cohort}_cases_index.json"))
    return cands[-1] if cands else None


def _labels_stats(events: list[dict], window: str) -> dict:
    success = censored = 0
    causes: Counter[str] = Counter()
    for e in events:
        lab = (e.get("labels") or {}).get(window)
        if not lab:
            continue
        if lab.get("event_type") == "successful_extubation":
            success += 1
        else:
            censored += 1
            causes[lab.get("censor_cause") or "desconocido"] += 1
    return {"success": success, "censored": censored,
            "causes": dict(causes.most_common())}


def _failure_events(events: list[dict], window: str = "48h") -> int:
    return sum(1 for e in events
               if ((e.get("labels") or {}).get(window) or {}).get(
                   "n_failed_attempts", 0) > 0)


def _coverage_stats(events: list[dict]) -> dict:
    """Cobertura por variable + D13 sobre los eventos con cobertura medida."""
    measured = [e for e in events if isinstance(e.get("coverage"), dict)
                and e["coverage"]]
    per_var: dict[str, dict] = {}
    if measured:
        variables = sorted({v for e in measured for v in e["coverage"]})
        for var in variables:
            vals = np.array([float(e["coverage"][var]) for e in measured
                             if var in e["coverage"]], dtype=np.float64)
            per_var[var] = {
                "n": int(vals.size),
                "median": float(np.median(vals)),
                "p10": float(np.percentile(vals, 10)),
                "frac_events_gt_50pct": float((vals > D13_MIN_COVERAGE).mean()),
            }
    d13 = sum(1 for e in measured
              if all(float(e["coverage"].get(v, 0.0)) > D13_MIN_COVERAGE
                     for v in D13_CORE))
    return {"n_measured": len(measured), "n_d13": d13, "per_variable": per_var}


def _vars_ok_from_events(events: list[dict]) -> tuple[int, int]:
    n50 = sum(1 for e in events if e.get("vars_ok_50"))
    n80 = sum(1 for e in events if e.get("vars_ok_80"))
    return n50, n80


def report_cohort(cohort: str) -> dict | None:
    path = latest_index(cohort)
    if path is None:
        return None
    idx = _load_json(path)
    events = idx["events"]
    coverage = _coverage_stats(events)
    n50, n80 = _vars_ok_from_events(events)
    end_reasons = Counter(e.get("end_reason") for e in events)
    unknown = sorted(set(end_reasons) - END_REASON_VOCAB)
    no_files = [e["event_id"] for e in events if not e.get("source_files")]

    return {
        "index": str(path.relative_to(ROOT)),
        "n_events": len(events),
        "n_excluded": idx.get("total_excluded_events"),
        "levels": idx.get("levels"),
        "labels48h": _labels_stats(events, "48h"),
        "labels72h": _labels_stats(events, "72h"),
        "failure_events_48h": _failure_events(events, "48h"),
        "vars_ok_50": n50,
        "vars_ok_80": n80,
        "vars_ok_50_summary": idx.get("vars_ok_50"),
        "vars_ok_80_summary": idx.get("vars_ok_80"),
        "d13": {"core": list(D13_CORE), "min_coverage": D13_MIN_COVERAGE,
                "n_included": coverage["n_d13"],
                "pct_included": (round(100.0 * coverage["n_d13"] / len(events), 2)
                                 if events else None),
                "n_coverage_measured": coverage["n_measured"]},
        "coverage_per_variable": coverage["per_variable"],
        "end_reason": dict(end_reasons.most_common()),
        "end_reason_unknown": unknown,
        "n_events_without_source_files": len(no_files),
        "events_without_source_files": no_files[:20],
        "label_source": idx.get("label_source"),
        "label_error": {"value": None, "source": LABEL_ERROR},
    }


def _fmt_pct(x: float | None) -> str:
    return "—" if x is None else f"{100.0 * x:.1f} %"


def to_markdown(table: dict[str, dict]) -> list[str]:
    lines = ["# Clínic y VitalDB tras la reconstrucción (Fase 1.6c, punto 4)", "",
             "| Cohorte | Eventos | Exc. | Éxito 48 h | Censura 48 h | "
             "Fallos (≥1) | D13 (FC+SpO2 > 50 %) | vars_ok 50 % | vars_ok 80 % |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, t in table.items():
        d = t["d13"]
        lines.append(
            f"| {name} | {t['n_events']} | {t['n_excluded']} | "
            f"{t['labels48h']['success']} | {t['labels48h']['censored']} | "
            f"{t['failure_events_48h']} | {d['n_included']} "
            f"({d['pct_included']} %) | {t['vars_ok_50']} | {t['vars_ok_80']} |")
    lines += ["", "## Detalle por cohorte", ""]
    for name, t in table.items():
        lines += [f"### {name}", "",
                  f"- Índice: `{t['index']}`",
                  f"- Excluidos: {t['n_excluded']} · niveles: {t['levels']}",
                  f"- Censura 48 h por causa: {t['labels48h']['causes']}",
                  f"- Censura 72 h por causa: {t['labels72h']['causes']}",
                  f"- `end_reason`: {t['end_reason']} "
                  f"(desconocidos: {t['end_reason_unknown'] or 'ninguno'})",
                  f"- Eventos sin `source_files`: "
                  f"{t['n_events_without_source_files']}",
                  f"- Error de etiqueta: {t['label_error']['source']}",
                  f"- Variables obligatorias (señal): "
                  f"{', '.join(t['coverage_per_variable']) or '—'}", ""]
        if t["coverage_per_variable"]:
            lines += ["| Variable | n | Mediana | p10 | Eventos > 50 % |",
                      "|---|---|---|---|---|"]
            for var, s in sorted(t["coverage_per_variable"].items()):
                lines.append(f"| {var} | {s['n']} | {s['median']:.3f} | "
                             f"{s['p10']:.3f} | {_fmt_pct(s['frac_events_gt_50pct'])} |")
            lines.append("")
    return lines


def main() -> None:
    try:  # consola de Windows en cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--cohorts", nargs="+", default=["clinic", "vitaldb"])
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6c"))
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    table: dict[str, dict] = {}
    for cohort in args.cohorts:
        t = report_cohort(cohort)
        if t is None:
            print(f"[aviso] sin índice para {cohort}")
            continue
        table[cohort] = t

    (out_dir / "clinic_vitaldb.json").write_text(
        json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = to_markdown(table)
    (out_dir / "clinic_vitaldb.md").write_text("\n".join(lines) + "\n",
                                               encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
