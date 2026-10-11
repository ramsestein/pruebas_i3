#!/usr/bin/env python3
"""
scripts/verify/fase1_6e/umbrales_hist.py
========================================
Fase 1.6e — **punto 2**: ¿los umbrales fijos (D-a) separan la ventilación
**clara** de la **cola sospechosa**?

No se ajustan umbrales: el objetivo es **comprobar** si los valores elegidos
(TV_EXP ≥ 100 mL, MV_EXP ≥ 1 L/min, amplitud de AWP ≥ 5 cmH2O con VENT_RR > 0)
separan dos poblaciones de minutos:

- **clara**: minutos de eventos CON extubación confirmada (controles) — ahí hay
  ventilación real;
- **sospechosa**: minutos con registro de pistas de ventilador pero sin
  actividad, en los eventos censurados (afectados).

Si las distribuciones se solapan, el criterio no sirve: **PARAR y reportar**, no
tocar los umbrales.

También reporta los minutos en **espera** (``vent_standby``): minutos con
registro de pistas de ventilador pero sin ventilación activa.

Uso:
    python scripts/verify/fase1_6e/umbrales_hist.py --cohorts clinic vitaldb
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.vital_signals import (  # noqa: E402
    ACTIVITY_AWP_AMP_MIN_CMH2O,
    ACTIVITY_MV_EXP_MIN_LPM,
    ACTIVITY_TV_EXP_MIN_ML,
)

REPORT = ROOT / "reports" / "fase1_6e"


def _values(rows: list[dict], track: str, *, active_only: bool,
            want_active: bool) -> np.ndarray:
    """Valores por minuto de una pista, filtrando por si el minuto es activo."""
    vals: list[float] = []
    for r in rows:
        d = r.get("per_min", {}).get(track) or {}
        if active_only:
            act = r.get("_activos") or []
            d = {k: v for k, v in d.items() if (int(k) in act) == want_active}
        vals.extend(float(v) for v in d.values())
    return np.asarray(vals, dtype=np.float64)


def _active_minutes(r: dict) -> set[int]:
    """Minutos activos de un evento (misma regla que el diagnóstico)."""
    rr = r.get("per_min", {}).get("VENT_RR", {})
    out: set[int] = set()
    for k, v in (r.get("per_min", {}).get("TV_EXP") or {}).items():
        if float(v) >= ACTIVITY_TV_EXP_MIN_ML:
            out.add(int(k))
    for k, v in (r.get("per_min", {}).get("MV_EXP") or {}).items():
        if float(v) >= ACTIVITY_MV_EXP_MIN_LPM:
            out.add(int(k))
    for k, v in (r.get("per_min", {}).get("AWP_WAV") or {}).items():
        if float(v) >= ACTIVITY_AWP_AMP_MIN_CMH2O and float(rr.get(k, 0.0)) > 0.0:
            out.add(int(k))
    return out


def _quantiles(v: np.ndarray) -> dict:
    if v.size == 0:
        return {"n": 0}
    q = np.percentile(v, [5, 25, 50, 75, 95])
    return {"n": int(v.size), "p5": round(float(q[0]), 3),
            "p25": round(float(q[1]), 3), "mediana": round(float(q[2]), 3),
            "p75": round(float(q[3]), 3), "p95": round(float(q[4]), 3)}


def _frac(v: np.ndarray, thr: float) -> float:
    return round(100.0 * float((v >= thr).mean()), 1) if v.size else 0.0


TRACKS = (("TV_EXP", ACTIVITY_TV_EXP_MIN_ML, "mL"),
          ("MV_EXP", ACTIVITY_MV_EXP_MIN_LPM, "L/min"),
          ("AWP_WAV", ACTIVITY_AWP_AMP_MIN_CMH2O, "cmH2O"))


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--cohorts", nargs="+", default=["clinic", "vitaldb"])
    p.add_argument("--control", type=int, default=20)
    p.add_argument("--png", type=int, default=1)
    args = p.parse_args()

    out: dict[str, dict] = {}
    lines = ["# Fase 1.6e, punto 2 — ¿separan los umbrales fijos?", ""]
    for cohort in args.cohorts:
        path = REPORT / f"diagnostico_vent_activa_{cohort}.json"
        if not path.exists():
            print(f"[aviso] sin diagnóstico para {cohort}")
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        for r in rows:
            r["_activos"] = sorted(_active_minutes(r))
        n_ctl = args.control
        ctl = rows[len(rows) - n_ctl:] if n_ctl else []
        afc = rows[: len(rows) - n_ctl] if n_ctl else rows

        # Espera: minutos con alguna pista de ventilador registrada pero sin
        # actividad (ni TV_EXP, ni MV_EXP, ni amplitud AWP con VENT_RR > 0).
        standby = {}
        for name in ("TV_EXP", "MV_EXP", "AWP_WAV", "VENT_RR", "PIP", "PEEP"):
            n = 0
            for r in afc:
                d = r.get("per_min", {}).get(name) or {}
                act = set(r["_activos"])
                n += sum(1 for k in d if int(k) not in act)
            if n:
                standby[name] = n
        min_activos = [len(r["_activos"]) for r in afc]

        res = {"n_afectados": len(afc), "n_controles": len(ctl),
               "minutos_activos_afectados": _quantiles(
                   np.asarray(min_activos, dtype=float)),
               "vent_standby_min_por_pista": standby,
               "minutos_standby_totales": int(sum(standby.values())),
               "separacion": {}}
        for track, thr, unit in TRACKS:
            sus = _values(afc, track, active_only=True, want_active=False)
            cla = _values(ctl, track, active_only=True, want_active=True)
            res["separacion"][track] = {
                "umbral": thr, "unidad": unit,
                "clara": _quantiles(cla), "sospechosa": _quantiles(sus),
                "clara_ge_umbral_pct": _frac(cla, thr),
                "sospechosa_ge_umbral_pct": _frac(sus, thr),
            }
        out[cohort] = res

        lines += [f"## {cohort}", "",
                  f"- Afectados {res['n_afectados']} · controles {res['n_controles']}",
                  f"- Minutos activos (afectados): {res['minutos_activos_afectados']}",
                  f"- Minutos en espera (`vent_standby`): "
                  f"{res['minutos_standby_totales']} {standby}", ""]
        for track, thr, unit in TRACKS:
            s = res["separacion"][track]
            lines += [f"### {track} (umbral {thr} {unit})",
                      f"- **Clara** (controles, minutos activos): {s['clara']}",
                      f"- **Sospechosa** (afectados, minutos sin actividad): "
                      f"{s['sospechosa']}",
                      f"- % ≥ umbral → clara **{s['clara_ge_umbral_pct']}** "
                      f"vs sospechosa **{s['sospechosa_ge_umbral_pct']}**", ""]

    (REPORT / "umbrales_hist.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT / "umbrales_hist.md").write_text("\n".join(lines) + "\n",
                                             encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
