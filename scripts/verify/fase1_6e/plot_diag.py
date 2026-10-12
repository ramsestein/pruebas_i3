#!/usr/bin/env python3
"""
scripts/verify/fase1_6e/plot_diag.py
====================================
Fase 1.6e — **punto 1b**: PNG del diagnóstico de ventilación activa.

Reutiliza ``plot_event`` del diagnóstico sobre el JSON ya calculado: no recalcula
nada, solo vuelve a leer las pistas de MONITOR de los eventos elegidos (los
minutos del ventilador ya están en el JSON). Los PNG van **anonimizados** (sin
identificadores de paciente, solo el `event_id` del dataset y horas relativas a
t0).

Selección:
- ``recuperados``  eventos que pasan a éxito con la corrección (lo importante);
- ``cambian``      eventos cuyo fin de ventilación se recorta;
- ``sin_actividad`` eventos que desaparecen;
- ``controles``    eventos con extubación confirmada (validación del criterio).

Uso:
    python scripts/verify/fase1_6e/plot_diag.py --cohort clinic \
        --select recuperados --n 9
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.create_dataset.build_signal_cases import SPECS, scan_source_files  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diag_vent_activa import REPORT, _event_paths, plot_event  # noqa: E402

EPS_H = 0.1


def _apagado(cohort: str, clases: str, n_por_clase: int) -> list[str]:
    """Eventos de 2c.1 (apagado simultáneo) de las clases pedidas.

    Toma los ids de ``apagado_simultaneo.json`` (clase A/B/C/D) y devuelve hasta
    ``n_por_clase`` por clase. OJO: la clase B (grabador parado) puede tener muy
    pocos casos: en Clínic hay UNO, así que no se pueden dibujar cinco.
    """
    rep = REPORT / "apagado_simultaneo.json"
    if not rep.exists():
        return []
    data = json.loads(rep.read_text(encoding="utf-8")).get(cohort) or []
    out: list[str] = []
    for letra in [c.strip().upper() for c in clases.split(",") if c.strip()]:
        sel = [c["event_id"] for c in sorted(data, key=lambda x: x["event_id"])
               if str(c.get("clase") or "").upper().startswith(letra)]
        out += sel[:n_por_clase]
    return out


def _select(rows: list[dict], how: str, n: int, n_afectados: int) -> list[dict]:
    afc = rows[:n_afectados]
    if how == "recuperados":
        out = [r for r in afc if r["fin_vent_activa_h"] is not None
               and r["fin_vent_actual_h"] - r["fin_vent_activa_h"] > EPS_H
               and r["cola_nueva_h"] is not None and r["cola_nueva_h"] >= 1.0]
    elif how == "cambian":
        out = [r for r in afc if r["fin_vent_activa_h"] is not None
               and r["fin_vent_actual_h"] - r["fin_vent_activa_h"] > EPS_H]
    elif how == "sin_actividad":
        out = [r for r in afc if r["fin_vent_activa_h"] is None]
    elif how == "controles":
        out = rows[n_afectados:]
    elif how == "vni_no_verificable":
        # Tramos post-extubación marcados como `posible_vni_no_verificable`
        # (punto 3): se dibujan para poder ver si parecen o no una VNI.
        rep = REPORT / "vni_reporte.json"
        ids = set()
        if rep.exists():
            data = json.loads(rep.read_text(encoding="utf-8"))
            for cohort_data in data.values():
                for d in cohort_data.get("detalle", []):
                    if d.get("no_verificables"):
                        ids.add(d["event_id"])
        out = [r for r in afc if r["event_id"] in ids]
    else:
        raise SystemExit(f"selección desconocida: {how}")
    return out[:n] if n else out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--cohort", required=True, choices=sorted(SPECS))
    p.add_argument("--config", default=str(ROOT / "src/stage0/config/harmonize.yaml"))
    p.add_argument("--select", default="recuperados",
                   choices=["recuperados", "cambian", "sin_actividad",
                            "controles", "vni_no_verificable", "apagado"])
    p.add_argument("--clase", default="A,B,C",
                   help="Clases de 2c.1 para --select apagado (A,B,C,D).")
    p.add_argument("--n", type=int, default=9)
    p.add_argument("--n-afectados", type=int, default=None,
                   help="Nº de filas de afectados (antes de los controles).")
    args = p.parse_args()

    config = load_config(args.config)
    boxes = scan_source_files(config_path(config, "paths", f"{args.cohort}_raw_dir"),
                              SPECS[args.cohort])
    by_box_meta = {b: {sf.path.name: sf for sf in files}
                   for b, files in boxes.items()}
    indices = sorted((ROOT / "datasets" / args.cohort).glob(
        f"cases_v*/{args.cohort}_cases_index.json"))
    idx = json.loads(indices[-1].read_text(encoding="utf-8"))
    by_id = {e["event_id"]: e for e in idx["events"]}

    rows = json.loads((REPORT / f"diagnostico_vent_activa_{args.cohort}.json")
                      .read_text(encoding="utf-8"))
    n_afc = args.n_afectados
    if n_afc is None:
        n_afc = sum(1 for r in rows
                    if (by_id.get(r["event_id"], {}).get("labels") or {})
                    .get("48h", {}).get("censor_cause") == "end_of_record")
    if args.select == "apagado":
        ids = set(_apagado(args.cohort, args.clase, args.n))
        sel = [r for r in rows if r["event_id"] in ids]
    else:
        sel = _select(rows, args.select, args.n, n_afc)
    print(f"[{args.cohort}] seleccionados {len(sel)} ({args.select})", flush=True)

    out_dir = REPORT / "figs_diag"
    for r in sel:
        ev = by_id.get(r["event_id"])
        if ev is None:
            continue
        paths = _event_paths(ev, by_box_meta)
        if not paths:
            print(f"  [sin ficheros] {r['event_id']}", flush=True)
            continue
        row = dict(r)
        row.pop("_series", None)
        try:
            plot_event(row, paths, float(ev["t0_unix"]),
                       out_dir / f"{args.cohort}_{args.select}_{r['event_id']}.png")
            print(f"  PNG {r['event_id']}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  [error png] {r['event_id']}: {type(exc).__name__}: {exc}",
                  flush=True)


if __name__ == "__main__":
    main()
