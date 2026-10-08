#!/usr/bin/env python3
"""
scripts/verify/fase1_6d/gap8_vs_gap10.py
========================================
Fase 1.6d — **punto 1**: informe **antes/después** (G = 8 h frente a G = 10 h).

- Compara la **calibración en MIMIC** de los dos G (métricas de intervalo, F1 de
  reintubación, concordancia de la etiqueta a 48 h) desde
  ``reports/fase1_6d/calibracion_mimic.json``.
- Compara los **eventos y etiquetas de eICU-B** entre el índice con G = 8
  (v0.4.0) y el reconstruido con G = 10 (v0.5.0): eventos, éxito/censura,
  fallos, `end_reason`, y cuántos eventos cambian de etiqueta.

Salida: ``reports/fase1_6d/gap8_vs_gap10.json`` / ``.md``.

Uso:
    python scripts/verify/fase1_6d/gap8_vs_gap10.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

WINDOW = "48h"
EICU_GLOBS = "datasets/eicu_collaborative/cases_v*/eicu_cases_index.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _summary(events: list[dict]) -> dict:
    success = censored = failures = d13 = 0
    end_reasons = Counter()
    for e in events:
        lab = (e.get("labels") or {}).get(WINDOW) or {}
        if lab.get("event_type") == "successful_extubation":
            success += 1
        else:
            censored += 1
        if int(lab.get("n_failed_attempts") or 0) > 0:
            failures += 1
        cov = e.get("coverage") or {}
        if cov and all(float(cov.get(v, 0.0)) > 0.5 for v in ("HR", "SpO2")):
            d13 += 1
        end_reasons[e.get("end_reason")] += 1
    return {"n": len(events), "success_48h": success, "censored_48h": censored,
            "failure_events": failures, "d13": d13,
            "end_reason": dict(end_reasons.most_common())}


def calibration_section() -> dict | None:
    path = ROOT / "reports" / "fase1_6d" / "calibracion_mimic.json"
    if not path.exists():
        return None
    d = _load(path)
    choice = d.get("choice", {})
    metrics = d.get("metrics", [])

    def _g(gap: float) -> dict:
        m = [x for x in metrics if float(x.get("gap_h", -1)) == gap]
        out = {"gap_h": gap}
        for strat in (1.0, 2.0):
            s = [x for x in m if float(x.get("subsample_h", -1)) == strat]
            if s:
                x = s[0]
                out[f"f1_reintub_{int(strat)}h"] = x.get("reintub_f1")
                out[f"label48_agreement_{int(strat)}h"] = x.get("label48_agreement")
                out[f"start_pct_2h_{int(strat)}h"] = (
                    x.get("start_error", {}).get("pct_2h"))
                out[f"end_pct_2h_{int(strat)}h"] = (
                    x.get("end_error", {}).get("pct_2h"))
        return out
    return {"chosen_gap_h": choice.get("chosen_gap_h"),
            "candidates": choice.get("candidates"),
            "g8": _g(8.0), "g10": _g(10.0)}


def eicu_section() -> dict | None:
    cands = sorted(ROOT.glob(EICU_GLOBS))
    if len(cands) < 2:
        return None
    old = _load(cands[-2])   # penúltimo = G = 8 (v0.4.0)
    new = _load(cands[-1])   # último = G = 10 (v0.5.0)
    old_ev = {e["event_id"]: e for e in old["events"]}
    new_ev = {e["event_id"]: e for e in new["events"]}

    def _lbl(ev):
        return ((ev.get("labels") or {}).get(WINDOW) or {}).get("event_type")

    only_old = sorted(set(old_ev) - set(new_ev))
    only_new = sorted(set(new_ev) - set(old_ev))
    changed = [k for k in set(old_ev) & set(new_ev)
               if _lbl(old_ev[k]) != _lbl(new_ev[k])]
    changed_kind = Counter(
        f"{_lbl(old_ev[k])}->{_lbl(new_ev[k])}" for k in changed)
    return {
        "old_index": str(cands[-2].relative_to(ROOT)),
        "new_index": str(cands[-1].relative_to(ROOT)),
        "gap_old": old.get("gap_h"), "gap_new": new.get("gap_h"),
        "old": _summary(old["events"]), "new": _summary(new["events"]),
        "only_old": len(only_old), "only_new": len(only_new),
        "n_changed_label": len(changed),
        "changed_label_kind": dict(changed_kind.most_common()),
        "examples_changed": changed[:10],
    }


def _md(rep: dict) -> list[str]:
    lines = ["# G = 8 h frente a G = 10 h (Fase 1.6d, punto 1)", ""]
    cal = rep.get("calibracion_mimic")
    if cal:
        lines += ["## Calibración en MIMIC", "",
                  f"- G elegido: **{cal['chosen_gap_h']} h**", "",
                  "| Métrica | G = 8 h | G = 10 h |", "|---|---|---|"]
        for key in ("f1_reintub_1h", "f1_reintub_2h", "label48_agreement_1h",
                    "label48_agreement_2h", "start_pct_2h_1h",
                    "start_pct_2h_2h", "end_pct_2h_1h", "end_pct_2h_2h"):
            g8 = cal["g8"].get(key)
            g10 = cal["g10"].get(key)
            fmt = (lambda v: "—" if v is None else f"{v:.3f}")
            lines.append(f"| {key} | {fmt(g8)} | {fmt(g10)} |")
        lines.append("")
    eicu = rep.get("eicu_b")
    if eicu:
        lines += ["## eICU-B", "",
                  f"- Índices: `{eicu['old_index']}` ({eicu['gap_old']} h) → "
                  f"`{eicu['new_index']}` ({eicu['gap_new']} h)", "",
                  "| Métrica | G = 8 h | G = 10 h |", "|---|---|---|",
                  f"| Eventos | {eicu['old']['n']} | {eicu['new']['n']} |",
                  f"| Éxito 48 h | {eicu['old']['success_48h']} | "
                  f"{eicu['new']['success_48h']} |",
                  f"| Censura 48 h | {eicu['old']['censored_48h']} | "
                  f"{eicu['new']['censored_48h']} |",
                  f"| Fallos (≥1) | {eicu['old']['failure_events']} | "
                  f"{eicu['new']['failure_events']} |",
                  f"| D13 | {eicu['old']['d13']} | {eicu['new']['d13']} |", "",
                  f"- Eventos sólo en G = 8: {eicu['only_old']}; sólo en "
                  f"G = 10: {eicu['only_new']}",
                  f"- Eventos que **cambian de etiqueta** a 48 h: "
                  f"{eicu['n_changed_label']}",
                  f"- Tipos de cambio: {eicu['changed_label_kind']}", "",
                  f"- `end_reason` G = 8: {eicu['old']['end_reason']}",
                  f"- `end_reason` G = 10: {eicu['new']['end_reason']}", ""]
    return lines


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6d"))
    args = p.parse_args()

    rep = {"calibracion_mimic": calibration_section(), "eicu_b": eicu_section()}
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gap8_vs_gap10.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "gap8_vs_gap10.md").write_text(
        "\n".join(_md(rep)) + "\n", encoding="utf-8")
    print("\n".join(_md(rep)))


if __name__ == "__main__":
    main()
