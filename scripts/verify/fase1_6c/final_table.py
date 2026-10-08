#!/usr/bin/env python3
"""
scripts/verify/fase1_6c/final_table.py
======================================
Fase 1.6c — **punto 8**: tabla final de las 4 cohortes de cierre.

Por cohorte:

- eventos totales del índice y **eventos incluidos por D13** (núcleo mínimo:
  FC y SpO2 con ≥ 50 % de horas útiles);
- éxito / censura a 48 h (mutuamente excluyentes) y causas de censura;
- eventos con **≥ 1 fallo** (columna aparte: se conserva aunque el evento esté
  censurado);
- ``vars_ok`` al 50 % y al 80 % como **indicador de calidad** (6 variables),
  nunca como filtro;
- **error de etiqueta esperado** (MIMIC es la referencia; eICU lo hereda por
  estrato de anotación, con y sin corrección del desplazamiento del fin;
  Clínic y VitalDB no aplican porque la etiqueta sale de la señal continua);
- **perfil de disponibilidad dominante** (qué variables están de verdad).

Fuentes:
  ``reports/fase1_6c/calibracion_mimic.json``   (punto 3)
  ``reports/fase1_6c/mimic_coverage.json``      (punto 1)
  ``reports/fase1_6c/eicu_b.json``              (punto 2)
  ``reports/fase1_6c/clinic_vitaldb.json``      (punto 4)
  ``reports/fase1_6c/perfiles.json``            (punto 7)
  índices ``cases_v*/<cohort>_cases_index.json``

Salidas (``reports/fase1_6c/``):
  ``tabla_final.json``
  ``tabla_final.md``

Uso:
    python scripts/verify/fase1_6c/final_table.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

COHORTS = ("mimic", "eicu", "clinic", "vitaldb")
INDEX_ROOTS = {
    "mimic": ROOT / "datasets" / "mimic3wdb",
    "eicu": ROOT / "datasets" / "eicu_collaborative",
    "clinic": ROOT / "datasets" / "clinic",
    "vitaldb": ROOT / "datasets" / "vitaldb",
}
STRATUM_HOURS = {"le1h": 1.0, "1_2h": 2.0, "gt2h": 4.0}
NO_APLICA = "no aplica (señal continua)"


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def latest_index(cohort: str) -> Path | None:
    cands = sorted(INDEX_ROOTS[cohort].glob(f"cases_v*/{cohort}_cases_index.json"))
    return cands[-1] if cands else None


def _events(cohort: str) -> tuple[list[dict], str | None]:
    path = latest_index(cohort)
    if path is None:
        return [], None
    idx = _load(path) or {}
    events = [e for e in idx.get("events", []) if not e.get("excluded")]
    return events, str(path.relative_to(ROOT))


def _labels_stats(events: list[dict], window: str = "48h") -> dict:
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


def _strata_weights(events: list[dict]) -> dict[str, int]:
    return dict(Counter(e.get("annotation_stratum") for e in events
                        if e.get("annotation_stratum")))


def _transfer(calib: dict | None, weights: dict[str, int]) -> dict | None:
    """Error de etiqueta para eICU ponderado por estrato de anotación."""
    if not calib:
        return None
    table = {float(t["subsample_h"]): t for t in calib.get("transferable_error", [])}
    total = sum(weights.values())
    if not total:
        return None
    err = agree = 0.0
    used: dict[str, float] = {}
    for stratum, n in weights.items():
        h = STRATUM_HOURS.get(stratum)
        if h is None or h not in table:
            continue
        err += n * table[h]["end_error_median_h"]
        agree += n * table[h]["label48_agreement_pct"]
        used[stratum] = h
    n_used = sum(n for s, n in weights.items() if s in used)
    if not n_used:
        return None
    return {"end_error_median_h": err / n_used,
            "label48_agreement_pct": agree / n_used,
            "source": "calibración MIMIC ponderada por estrato de anotación",
            "strata": weights}


def _dominant_profile(perfiles: dict | None, cohort: str) -> dict | None:
    if not perfiles:
        return None
    blk = perfiles.get(cohort)
    if not isinstance(blk, dict):
        return None
    combos = blk.get("combinaciones_top") or []
    if not combos:
        return None
    top = max(combos, key=lambda c: c.get("pct", 0.0))
    return {"profile": top.get("variables"), "n": top.get("events"),
            "pct": top.get("pct")}


def build_row(cohort: str, artifacts: dict[str, dict | None]) -> dict:
    events, index = _events(cohort)
    row: dict = {"cohort": cohort, "index": index, "n_events": len(events)}
    if not events:
        row["pendiente"] = "sin índice"
        return row
    # Fase 1.6c: los índices de esta fase salen con la versión de la config.
    if index and "cases_v0.4.0" not in index:
        row["index_obsoleto"] = True
        row["pendiente"] = "reconstrucción de la fase pendiente (índice antiguo)"

    row["labels48h"] = _labels_stats(events)
    row["labels72h"] = _labels_stats(events, "72h")
    row["failure_events_48h"] = _failure_events(events)
    row["end_reason"] = dict(Counter(e.get("end_reason") for e in events).most_common())
    row["profile_dominante"] = _dominant_profile(artifacts.get("perfiles"), cohort)

    # D13 y vars_ok -------------------------------------------------------
    def _pct(count, total):
        return (round(100.0 * count / total, 1) if count is not None and total
                else None)

    cov = artifacts.get("mimic_coverage") if cohort == "mimic" else None
    eicu_b = artifacts.get("eicu_b") if cohort == "eicu" else None
    sig = (artifacts.get("clinic_vitaldb") or {}).get(cohort)
    if cov:
        row["d13"] = {"core": ["HR", "SpO2"], "n_included": cov["d13_core_ok_n"],
                     "pct_included": cov["d13_core_ok_pct"],
                     "fuente": "reports/fase1_6c/mimic_coverage.json"}
        row["vars_ok_50"] = cov["vars_ok_50_n"]
        row["vars_ok_80"] = cov.get("vars_ok_80_n")
        row["vars_ok_50_pct"] = cov["vars_ok_50_pct"]
        row["vars_ok_80_pct"] = cov.get("vars_ok_80_pct")
    elif eicu_b:
        row["d13"] = {"core": ["HR", "SpO2"], "n_included": eicu_b["d13_incluidos"],
                      "pct_included": eicu_b["d13_pct"],
                      "fuente": "reports/fase1_6c/eicu_b.json"}
        row["vars_ok_50"], row["vars_ok_50_pct"] = eicu_b["vars_ok_50"], eicu_b["vars_ok_50_pct"]
        row["vars_ok_80"] = eicu_b["vars_ok_80"]
        row["vars_ok_80_pct"] = _pct(eicu_b["vars_ok_80"], eicu_b["n_events"])
    elif sig:
        d13 = sig["d13"]
        row["d13"] = {"core": d13["core"], "n_included": d13["n_included"],
                      "pct_included": d13["pct_included"],
                      "fuente": "reports/fase1_6c/clinic_vitaldb.json"}
        row["vars_ok_50"], row["vars_ok_80"] = sig["vars_ok_50"], sig["vars_ok_80"]
        row["vars_ok_50_pct"] = _pct(sig["vars_ok_50"], sig["n_events"])
        row["vars_ok_80_pct"] = _pct(sig["vars_ok_80"], sig["n_events"])
    row["n_hospitals"] = eicu_b["n_hospitals"] if eicu_b else None

    # Error de etiqueta ---------------------------------------------------
    if cohort == "mimic":
        row["label_error"] = {"end_error_median_h": 0.0,
                              "label48_agreement_pct": 100.0,
                              "source": "referencia"}
    elif cohort == "eicu":
        weights = _strata_weights(events)
        base = _transfer(artifacts.get("calibracion_mimic"), weights)
        row["label_error_sin_correccion"] = base
        corr = (artifacts.get("calibracion_mimic") or {}).get("end_shift_correction")
        if not corr:
            row["label_error"] = base or {
                "end_error_median_h": None, "label48_agreement_pct": None,
                "source": "pendiente (calibración MIMIC)"}
        else:
            row["correccion_desplazamiento"] = {
                "shifts_h": corr.get("shifts_h"),
                "verdict": corr.get("verdict_strata_1_2h"),
            }
            approved = bool((corr.get("verdict_strata_1_2h") or {}).get("approved"))
            et = (artifacts.get("eicu_b") or {}).get("error_de_etiqueta") or {}
            cc = et.get("con_correccion") or {}
            row["label_error"] = (base if not approved else
                                  {"end_error_median_h": cc.get("end_error_median_h"),
                                   "label48_agreement_pct": cc.get("label48_agreement_pct"),
                                   "source": "corrección del fin por estrato aplicada"})
    else:
        row["label_error"] = {"end_error_median_h": None,
                              "label48_agreement_pct": None,
                              "source": NO_APLICA}
    return row


def _fmt(v, spec: str = "{:.1f}", dash: str = "—") -> str:
    if v is None:
        return dash
    try:
        return spec.format(v)
    except (TypeError, ValueError):
        return str(v)


def to_markdown(rows: dict[str, dict]) -> list[str]:
    lines = ["# Tabla final de las 4 cohortes (Fase 1.6c, punto 8)", "",
             "D13 = evento incluido si tiene FC y SpO2 con ≥ 50 % de horas "
             "útiles (D8). `vars_ok` (6 variables) es indicador de calidad, "
             "no filtro.", "",
             "| Cohorte | Eventos | Incluidos D13 | Éxito 48 h | Censura 48 h | "
             "Fallos (≥1) | vars_ok 50 % | vars_ok 80 % | Error de etiqueta | "
             "Perfil dominante |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name in COHORTS:
        t = rows.get(name)
        if not t or not t.get("n_events"):
            lines.append(f"| {name} | — | — | — | — | — | — | — | "
                         f"{t.get('pendiente', '—') if t else '—'} | — |")
            continue
        d13 = t.get("d13") or {}
        le = t.get("label_error") or {}
        err = (le.get("source") or "—") if le.get("end_error_median_h") is None \
            else f"{le['end_error_median_h']:.2f} h / {le['label48_agreement_pct']:.1f} %"
        prof = t.get("profile_dominante") or {}
        lines.append(
            f"| {name} | {t['n_events']} | "
            f"{d13.get('n_included', '—')} ({_fmt(d13.get('pct_included'), '{:.1f}')} %) | "
            f"{t['labels48h']['success']} | {t['labels48h']['censored']} | "
            f"{t['failure_events_48h']} | "
            f"{_fmt(t.get('vars_ok_50_pct'), '{:.1f}')} % | "
            f"{_fmt(t.get('vars_ok_80_pct'), '{:.1f}')} % | {err} | "
            f"{prof.get('profile', '—')} ({_fmt(prof.get('pct'), '{:.1f}')} %) |")

    lines += ["", "## Detalle", ""]
    for name in COHORTS:
        t = rows.get(name)
        if not t or not t.get("n_events"):
            continue
        le = t.get("label_error") or {}
        lines += [f"### {name}", "",
                  f"- Índice: `{t.get('index')}` ({t['n_events']} eventos"
                  + (f", {t['n_hospitals']} hospitales" if t.get("n_hospitals") else "")
                  + ")",
                  f"- Censura 48 h por causa: {t['labels48h']['causes']}",
                  f"- `end_reason`: {t.get('end_reason')}",
                  f"- Error de etiqueta: {le.get('source') or '—'}"
                  + (f" → {le['end_error_median_h']:.2f} h / "
                     f"{le['label48_agreement_pct']:.1f} %"
                     if le.get("end_error_median_h") is not None else "")]
        if t.get("pendiente"):
            lines.append(f"- **{t['pendiente']}** (las cifras de la fila son del "
                         f"índice antiguo, no de esta fase)")
        lines.append("")
        if t.get("correccion_desplazamiento"):
            c = t["correccion_desplazamiento"]
            lines.append(f"- Corrección del fin por estrato: desplazamientos "
                         f"{c.get('shifts_h')}; ¿mejora en MIMIC (1 h y 2 h)? "
                         f"**{'SÍ' if (c.get('verdict') or {}).get('approved') else 'NO'}**")
            lines.append("")
        if t.get("label_error_sin_correccion"):
            b = t["label_error_sin_correccion"]
            lines.append(f"- eICU sin corrección: {b['end_error_median_h']:.2f} h / "
                         f"{b['label48_agreement_pct']:.1f} % (estratos {b['strata']})")
            lines.append("")
    return lines


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6c"))
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    artifacts = {
        "calibracion_mimic": _load(out_dir / "calibracion_mimic.json"),
        "mimic_coverage": _load(out_dir / "mimic_coverage.json"),
        "eicu_b": _load(out_dir / "eicu_b.json"),
        "clinic_vitaldb": _load(out_dir / "clinic_vitaldb.json"),
        "perfiles": _load(out_dir / "perfiles.json"),
    }
    faltan = [k for k, v in artifacts.items() if v is None]
    if faltan:
        print(f"[aviso] faltan artefactos: {', '.join(faltan)}")

    rows = {c: build_row(c, artifacts) for c in COHORTS}
    (out_dir / "tabla_final.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = to_markdown(rows)
    (out_dir / "tabla_final.md").write_text("\n".join(lines) + "\n",
                                            encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
