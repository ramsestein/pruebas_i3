#!/usr/bin/env python3
"""
scripts/verify/fase1_6e/precheck_vitaldb.py
===========================================
Fase 1.6e, punto 4 — comprobaciones **previas** al rebuild de VitalDB (v0.6.0).

Responde a las dos preguntas que quedaron abiertas en el punto 1 sin leer un
solo byte de los ``.vital`` (solo el listado de directorio, que no toca el
contenido):

1. **Por que los 4 eventos del indice v0.4.0 no tienen ``source_files``.**
   Hipotesis B1: en VitalDB la hora del NOMBRE va desviada respecto al
   contenido, y la seleccion antigua comparaba horas de nombre con horas de
   contenido (nombre + 1 h), asi que ningun fichero casaba.
2. **Que son los eventos sin variable util** (cobertura 0 en todas las
   variables obligatorias): se listan con su box y duracion para poder
   explicarlos con el inventario de pistas del punto 1.

Uso:
    python scripts/verify/fase1_6e/precheck_vitaldb.py
    python scripts/verify/fase1_6e/precheck_vitaldb.py --cohort vitaldb --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.create_dataset.build_signal_cases import SPECS, scan_source_files  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

REPORT = ROOT / "reports" / "fase1_6e"
CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
VARIABLES = ("FiO2", "PEEP", "TV_EXP", "VENT_RR")


def _ultimo_indice(cohort: str) -> Path:
    return sorted((ROOT / "datasets" / cohort).glob(
        f"cases_v*/{cohort}_cases_index.json"))[-1]


def _fmt_h(unix: float) -> str:
    return datetime.fromtimestamp(unix, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cohort", default="vitaldb", choices=sorted(SPECS))
    p.add_argument("--json", default=None)
    p.add_argument("--config", default=str(CONFIG))
    args = p.parse_args()

    idx_path = _ultimo_indice(args.cohort)
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    print(f"# {idx_path.relative_to(ROOT)}")
    print(f"{idx['total_events']} eventos · {idx['total_boxes']} boxes\n")

    config = load_config(args.config)
    boxes = scan_source_files(
        config_path(config, "paths", f"{args.cohort}_raw_dir"), SPECS[args.cohort])

    out: dict = {"indice": str(idx_path.relative_to(ROOT)),
                 "total_events": idx["total_events"], "sin_source_files": [],
                 "sin_variable_util": []}

    # ── 1. Eventos sin source_files ─────────────────────────────────────────
    sin = [e for e in idx["events"] if not e.get("source_files")]
    print(f"## 1. Eventos SIN source_files: {len(sin)}")
    for e in sin:
        box = e["box"]
        t0 = float(e["t0_unix"])
        dur_h = e["duration_seconds"] / 3600.0
        fin = t0 + e["duration_seconds"]
        fs = boxes.get(box, [])
        dentro = [f for f in fs if t0 <= f.dt_unix <= fin]
        primero = fs[0].dt_unix if fs else None
        # Desfase nombre<->contenido: la primera hora del contenido frente al
        # primer nombre del box (si el nombre va por detras, sale positivo).
        desfase_h = ((t0 - primero) / 3600.0) if primero else None
        print(f"- {e['event_id']} [{box}]: t0 {_fmt_h(t0)} · "
              f"fin {_fmt_h(fin)} · {dur_h:.2f} h · "
              f"ficheros del box: {len(fs)} · con NOMBRE en la ventana: "
              f"{len(dentro)} · primer nombre del box: "
              f"{_fmt_h(primero) if primero else '—'} · "
              f"t0 - primer nombre: {desfase_h:+.2f} h"
              if desfase_h is not None else f"- {e['event_id']} [{box}]: sin ficheros")
        out["sin_source_files"].append({
            "event_id": e["event_id"], "box": box, "t0": _fmt_h(t0),
            "fin": _fmt_h(fin), "duration_h": round(dur_h, 3),
            "n_files_box": len(fs), "n_files_nombre_en_ventana": len(dentro),
            "desfase_t0_primer_nombre_h": (round(desfase_h, 3)
                                           if desfase_h is not None else None),
        })

    # ── 2. Eventos sin variable util (cobertura 0 en todas) ─────────────────
    def _sin_variable(e: dict) -> bool:
        cov = e.get("coverage") or {}
        if not cov:
            return True
        return all(float(v) == 0.0 for v in cov.values())

    sin_var = [e for e in idx["events"] if _sin_variable(e)]
    print(f"\n## 2. Eventos sin variable util (cobertura 0): {len(sin_var)}")
    por_box: dict[str, int] = {}
    for e in sin_var:
        por_box[e["box"]] = por_box.get(e["box"], 0) + 1
    for e in sin_var:
        cov = e.get("coverage") or {}
        print(f"- {e['event_id']} [{e['box']}]: "
              f"{e['duration_seconds'] / 3600.0:.2f} h · "
              f"cobertura {cov} · ficheros {len(e.get('source_files') or [])}")
        out["sin_variable_util"].append({
            "event_id": e["event_id"], "box": e["box"],
            "duration_h": round(e["duration_seconds"] / 3600.0, 3),
            "coverage": cov, "n_files": len(e.get("source_files") or []),
            "level": e.get("level"),
        })
    print(f"\nBoxes afectados: {len(por_box)} -> "
          f"{sorted(por_box.items(), key=lambda kv: -kv[1])[:8]}")
    out["sin_variable_util_por_box"] = por_box

    # La instruccion del punto 4 hablaba de "19 sin variable util": aqui se
    # cuentan los que fallan el umbral de cobertura del 50 % en TODAS las
    # variables obligatorias, para no confundir dos criterios distintos.
    sin_ok50 = [e for e in idx["events"]
                if not (e.get("coverage") or {})
                or not any(float(v) > 0.5 for v in (e.get("coverage") or {}).values())]
    print(f"\n## 2b. Eventos sin NINGUNA variable por encima del 50 % "
          f"de cobertura: {len(sin_ok50)}")
    out["sin_ok50"] = [{"event_id": e["event_id"], "box": e["box"],
                        "n_files": len(e.get("source_files") or [])}
                       for e in sin_ok50]
    out["n_sin_ok50"] = len(sin_ok50)
    out["vars_ok_50_indice"] = idx.get("vars_ok_50")

    # ── 3. Que dice el diagnostico de esos eventos sin source_files ─────────
    diag = REPORT / f"diagnostico_vent_activa_{args.cohort}.json"
    if diag.exists() and sin:
        rows = {r["event_id"]: r for r in json.loads(
            diag.read_text(encoding="utf-8"))}
        print("\n## 3. Diagnostico de los eventos sin source_files")
        for e in sin:
            r = rows.get(e["event_id"])
            if r is None:
                print(f"- {e['event_id']}: sin fila en el diagnostico")
                continue
            per_min = r.get("per_min") or {}
            campos = {k: v for k, v in r.items()
                      if k not in ("per_min", "_series")}
            print(f"- {e['event_id']}: {campos}")
            print(f"    ventana con datos del ventilador: "
                  f"{ {k: len(v) for k, v in per_min.items()} }")

    if args.json:
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\nJSON: {args.json}")


if __name__ == "__main__":
    main()
