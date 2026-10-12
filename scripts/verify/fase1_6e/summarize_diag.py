#!/usr/bin/env python3
"""
scripts/verify/fase1_6e/summarize_diag.py
=========================================
Fase 1.6e — **punto 1**: agregado del diagnóstico de ventilación activa.

Lee ``reports/fase1_6e/diagnostico_vent_activa_<cohort>.json`` (por minuto, por
evento) y resume:

- cuántos eventos **no cambian** con la ventilación activa
  (``fin_vent_activa ≈ fin_vent_actual``);
- cuántos **cambian** (la actividad termina antes) y cuánto;
- cuántos **desaparecen** (sin actividad ninguna);
- la distribución de la **cola nueva** (fin del monitor fisiológico − fin activo);
- las **pistas que prolongan** la ventilación sin actividad;
- el control (eventos de éxito que se mantienen): su cola debe ser ≥ 1 h.

Salida: ``reports/fase1_6e/diagnostico_resumen.json`` / ``.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

REPORT = ROOT / "reports" / "fase1_6e"
EPS_H = 0.1   # 6 min: diferencia irrelevante entre fin actual y fin activo


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def _n_afectados_en_indice(cohort: str) -> int | None:
    """Nº de eventos afectados según el índice (censurados ``end_of_record``).

    El JSON del diagnóstico puede tener menos filas (eventos sin ``source_files``
    o con timeout), así que el llamador recorta con ``min``.
    """
    idxs = sorted((ROOT / "datasets" / cohort).glob(
        f"cases_v*/{cohort}_cases_index.json"))
    if not idxs:
        return None
    idx = json.loads(idxs[-1].read_text(encoding="utf-8"))
    return sum(1 for e in idx["events"]
               if (e.get("labels") or {}).get("48h", {}).get("censor_cause")
               == "end_of_record")


def summarize(rows: list[dict], n_control: int,
              n_afectados: int | None = None) -> dict:
    if n_afectados is None:
        n_afectados = len(rows) - n_control if n_control else len(rows)
    affected = rows[: n_afectados]
    controls = rows[n_afectados:] if n_control else []
    sin_actividad = [r for r in affected if r["fin_vent_activa_h"] is None]
    con_actividad = [r for r in affected if r["fin_vent_activa_h"] is not None]
    iguales = [r for r in con_actividad
               if abs(r["fin_vent_activa_h"] - r["fin_vent_actual_h"]) <= EPS_H]
    cambian = [r for r in con_actividad
               if abs(r["fin_vent_activa_h"] - r["fin_vent_actual_h"]) > EPS_H]
    difs = sorted(r["fin_vent_actual_h"] - r["fin_vent_activa_h"]
                  for r in cambian)
    prolongan = Counter(p for r in affected for p in r["pistas_que_prolongan"])
    colas = [r["cola_nueva_h"] for r in affected
             if r["cola_nueva_h"] is not None]
    colas_ok = [c for c in colas if c >= 1.0]
    # Recuperados: el fin ACTIVO va antes que el fin grabado (había cola de
    # presencia falsa) y la cola fisiológica corregida llega a >= 1 h, que es
    # el mínimo para confirmar extubación (D3). Esos eventos pasarían de
    # ``end_of_record`` a éxito.
    recuperados = [
        r for r in affected
        if r["fin_vent_activa_h"] is not None
        and r["fin_vent_actual_h"] - r["fin_vent_activa_h"] > EPS_H
        and r["cola_nueva_h"] is not None and r["cola_nueva_h"] >= 1.0
    ]
    ctrl_colas = [r["cola_nueva_h"] for r in controls
                  if r["cola_nueva_h"] is not None]

    # Detalle de los que se quedan SIN actividad: qué pistas tienen registradas y
    # con qué valores máximos (un ventilador en espera deja FiO2/PEEP y ondas,
    # no TV entregado). Sirve para separar "en espera/alto flujo" de "fallo de
    # detección".
    detalle_sin: list[dict] = []
    for r in sin_actividad:
        tracks: dict[str, dict] = {}
        for name, d in (r.get("per_min") or {}).items():
            if not d:
                continue
            vals = [float(v) for v in d.values()]
            tracks[name] = {"n_min": len(vals), "max": round(max(vals), 2),
                            "mediana": round(sorted(vals)[len(vals) // 2], 2)}
        detalle_sin.append({"event_id": r["event_id"], "tracks": tracks,
                            "n_min_pista": sorted(
                                ((len(v), k) for k, v in tracks.items()),
                                reverse=True)[0][1] if tracks else None})

    return {
        "n_afectados": len(affected), "n_controles": len(controls),
        "n_sin_actividad": len(sin_actividad),
        "detalle_sin_actividad": detalle_sin,
        "n_no_cambian": len(iguales),
        "n_cambian": len(cambian),
        "n_recuperados": len(recuperados),
        "ejemplos_recuperados": [r["event_id"] for r in recuperados[:12]],
        "n_siguen_censurados": (len(affected) - len(recuperados)
                                - len(sin_actividad)),
        "mediana_recorte_h": (difs[len(difs) // 2] if difs else None),
        "p90_recorte_h": (difs[int(0.9 * (len(difs) - 1))] if difs else None),
        "pistas_que_prolongan": dict(prolongan.most_common()),
        "cola_nueva_ge_1h": len(colas_ok),
        "cola_nueva_lt_1h": len(colas) - len(colas_ok),
        "control_cola_mediana_h": (sorted(ctrl_colas)[len(ctrl_colas) // 2]
                                   if ctrl_colas else None),
        "control_cola_ge_1h": sum(1 for c in ctrl_colas if c >= 1.0),
        "ejemplos_cambian": [r["event_id"] for r in cambian[:12]],
        "ejemplos_sin_actividad": [r["event_id"] for r in sin_actividad[:12]],
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--cohorts", nargs="+", default=["clinic", "vitaldb"])
    p.add_argument("--control", type=int, default=20)
    p.add_argument("--n-afectados", type=int, default=None,
                   help="Nº de afectados ANTES de los controles (si algún "
                        "evento pudo quedar fuera del análisis).")
    args = p.parse_args()

    out: dict[str, dict] = {}
    lines = ["# Diagnóstico de ventilación activa — resumen (Fase 1.6e, punto 1)",
             ""]
    for cohort in args.cohorts:
        path = REPORT / f"diagnostico_vent_activa_{cohort}.json"
        if not path.exists():
            print(f"[aviso] sin diagnóstico para {cohort}")
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        n_afc = args.n_afectados
        if n_afc is None:
            n_afc = _n_afectados_en_indice(cohort)
        if n_afc is not None:
            n_afc = min(int(n_afc), max(0, len(rows) - args.control))
        out[cohort] = summarize(rows, args.control, n_afc)
        r = out[cohort]
        lines += [f"## {cohort}", "",
                  f"- Afectados: **{r['n_afectados']}** · controles: {r['n_controles']}",
                  f"- Con ventilación activa detectada: "
                  f"{r['n_afectados'] - r['n_sin_actividad']} "
                  f"({_pct(r['n_afectados'] - r['n_sin_actividad'], r['n_afectados'])} %)",
                  f"- **No cambian** con el fix (fin activo ≈ fin actual): "
                  f"{r['n_no_cambian']}",
                  f"- **Sí cambian** (la actividad acaba antes): {r['n_cambian']} "
                  f"(mediana del recorte {r['mediana_recorte_h']} h, "
                  f"p90 {r['p90_recorte_h']} h)",
                  f"- **Sin actividad ninguna** (el evento desaparece): "
                  f"{r['n_sin_actividad']}",
                  f"- Pistas que prolongan sin actividad: "
                  f"{r['pistas_que_prolongan'] or 'ninguna'}",
                  f"- Cola nueva ≥ 1 h: {r['cola_nueva_ge_1h']} · < 1 h: "
                  f"{r['cola_nueva_lt_1h']}",
                  f"- **Recuperados** (pasarían a éxito): {r['n_recuperados']} "
                  f"· siguen censurados: {r['n_siguen_censurados']} "
                  f"· desaparecen: {r['n_sin_actividad']}",
                  f"- **Control**: cola mediana {r['control_cola_mediana_h']} h; "
                  f"{r['control_cola_ge_1h']}/{r['n_controles']} con cola ≥ 1 h",
                  ""]
        for d in r["detalle_sin_actividad"]:
            tr = " · ".join(f"{k}={v['n_min']}min(max {v['max']})"
                            for k, v in d["tracks"].items())
            lines.append(f"  - sin actividad: `{d['event_id']}` → {tr or 'nada'}")
        lines.append("")
    (REPORT / "diagnostico_resumen.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT / "diagnostico_resumen.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
