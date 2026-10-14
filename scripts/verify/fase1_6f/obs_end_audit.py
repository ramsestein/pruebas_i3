#!/usr/bin/env python3
"""
fase1_6f/obs_end_audit.py
=========================
Fase 1.6f, punto 1: auditoría de ``obs_end_h``.

Responde a las tres preguntas del punto:

1. ¿Sale ``obs_end_h`` de la FISIOLOGÍA del monitor (FC/SpO2) y no del fin de la
   ventilación? Se comprueba con el test ``src/create_dataset/tests/test_obs_end.py``
   (no se recorta nunca a ``vent_end_h``).
2. ¿En cuántos eventos del índice se cumple ``obs_end_h == vent_end_h``?
3. ¿Por qué? Para cada evento se mira, con las MUESTRAS del espejo, qué había
   justo después del fin de la ventilación:

   - ``apagon_del_grabador``: no hay NINGUNA muestra de ninguna pista hasta el
     siguiente evento del mismo box (el grabador se paró y volvió).
   - ``fin_de_registro``: no hay muestras y no hay más eventos en el box.
   - ``grabacion_sin_fisiologia``: el box sigue grabando (hay muestras de alguna
     pista) pero ninguna FC/SpO2 fisiológica: no hay observación válida.
   - ``otro``: no encaja en lo anterior.

Uso:
    python scripts/verify/fase1_6f/obs_end_audit.py --cohort clinic \\
        --index datasets/clinic/cases_v0.6.0_cbf8b634/clinic_cases_index.json \\
        --probe --out reports/fase1_6f/obs_end_clinic.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.vital_signals import probe_block_union  # noqa: E402
from src.create_dataset.build_signal_cases import SPECS  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = "src/stage0/config/harmonize.yaml"
_ESP = 1e-3


def _blocks_h(blocks_s):
    return [(a / 3600.0, b / 3600.0) for a, b in blocks_s]


def _covered(blocks, lo_h, hi_h) -> bool:
    return any(a < hi_h and b > lo_h for a, b in blocks)


def classify_event(
    ev: dict, *, config, cohort: str, meta: dict,
) -> dict:
    """Motivo de ``obs_end_h == vent_end_h`` de un evento (por muestras).

    Se sondea SOLO la ventana alrededor del fin de la ventilación (±3 h): el
    evento puede tener decenas de ficheros y sondearlos todos con todas las
    pistas costaría horas.
    """
    from apagones_report import probe_window  # import tardío (sys.path del script)

    t0_h = float(ev["t0_unix"]) / 3600.0
    vent_end_h = float(ev["attempts"][-1]["vent_end_h"])
    v_end_abs = t0_h + vent_end_h
    win = probe_window(config, cohort, ev["box"], v_end_abs - 1.0,
                       v_end_abs + 4.0, margin_h=1.0)
    probes = win["probes"]
    any_blocks = _blocks_h(probe_block_union(probes.values(), "sample"))
    mon_blocks = _blocks_h(probe_block_union(probes.values(), "monitor"))
    # Ventana de 2 h tras el fin de la ventilación (lo que dura un apagón corto).
    win_lo, win_hi = v_end_abs, v_end_abs + 2.0
    has_any = _covered(any_blocks, win_lo, win_hi)
    has_mon = _covered(mon_blocks, win_lo, win_hi)
    meta["raw_dir"] = win["raw_dir"]
    meta["is_mirror"] = win["is_mirror"]
    if has_mon:
        reason = "otro"
    elif has_any:
        reason = "grabacion_sin_fisiologia"
    elif win["n_files"] and ev.get("_has_next"):
        reason = "apagon_del_grabador"
    else:
        reason = "fin_de_registro"
    return {
        "reason": reason,
        "has_any_sample_2h": has_any,
        "has_monitor_2h": has_mon,
        "n_files_window": win["n_files"],
    }


def write_md(doc: dict, path: Path) -> None:
    md: list[str] = []
    md.append(f"# Fase 1.6f, punto 1 — de dónde sale `obs_end_h` ({doc['cohort']})")
    md.append("")
    md.append("## Qué es y de dónde sale")
    md.append("")
    md.append("`obs_end_h` es el fin de la **observación fisiológica** (FC/SpO2 con")
    md.append("valores fisiológicos, unidas con 1 h de tolerancia) posterior al fin de")
    md.append("la ventilación, recortado a la región de paciente (D2). Lo calcula")
    md.append("`episodes.build_episodes`:")
    md.append("")
    md.append("```python")
    md.append("tail = clip_spans(physiological, attempts[-1].end_h, region_end_h)")
    md.append("observation_end_h = tail[-1].end_h if tail else attempts[-1].end_h")
    md.append("```")
    md.append("")
    md.append("La rama `else` es un **suelo**, no un tope: solo se toma cuando NO hay")
    md.append("ninguna muestra fisiológica después del fin de la ventilación, y entonces")
    md.append("vale exactamente lo mismo que `vent_end_h` (el evento queda censurado).")
    md.append("`observation_end_h` **nunca** puede ser menor que el fin de la ventilación")
    md.append("(el recorte es `[vent_end, region_end]` y el fallback es `vent_end`), así")
    md.append("que no hay ningún camino en el código que recorte una observación larga.")
    md.append("El respaldo `_region_end_h → episode.end_h` de `build_signal_cases` no es")
    md.append("alcanzable: `build_episodes` siempre deja `region_end_h` finito.")
    md.append("")
    md.append("Prueba con test: `src/create_dataset/tests/test_obs_end.py`")
    md.append("(cola larga → se extiende; sin cola → igual a `vent_end_h` y censurado;")
    md.append("ondas sin fisiología → no alargan la observación).")
    md.append("")
    md.append("## Auditoría del índice")
    md.append("")
    md.append(f"- Índice: `{doc['index']}`")
    md.append(f"- Eventos: **{doc['n_events']}**")
    md.append(f"- Con `obs_end_h == vent_end_h`: **{doc['n_obs_end_eq_vent_end']}**")
    md.append(f"- Con `monitor_tail_h == 0`: **{doc['n_monitor_tail_zero']}**")
    md.append(f"- ¿Toda igualdad implica cola cero? "
              f"**{'sí' if doc['equality_implies_zero_tail'] else 'NO'}**")
    md.append("")
    md.append("| `end_reason` | eventos | de los que igualan |")
    md.append("|---|---|---|")
    for k in sorted(set(doc["end_reason"]) | set(doc["end_reason_eq"])):
        md.append(f"| {k} | {doc['end_reason'].get(k, 0)} | "
                  f"{doc['end_reason_eq'].get(k, 0)} |")
    md.append("")
    if "motivos" in doc:
        md.append("## Por qué coinciden (medido por muestras)")
        md.append("")
        md.append(f"- Origen: `{doc.get('source_dir')}` "
                  f"(espejo: {'sí' if doc.get('source_is_mirror') else 'NO'})")
        md.append(f"- Eventos sondeados: **{doc.get('n_probed')}** de "
                  f"{doc['n_obs_end_eq_vent_end']} (submuestra)")
        md.append(f"- Motivo por índice (todos los iguales): "
                  f"{doc.get('motivo_indice')}")
        md.append("")
        md.append("| Motivo (muestras) | Eventos |")
        md.append("|---|---|")
        for k, v in sorted(doc["motivos"].items(), key=lambda x: -x[1]):
            md.append(f"| {k} | {v} |")
        md.append("")
        md.append("Definiciones: `apagon_del_grabador` = no hay ninguna muestra de")
        md.append("ninguna pista en las 2 h siguientes al fin de la ventilación y hay")
        md.append("otro evento después en el box; `fin_de_registro` = no hay muestras y")
        md.append("no hay más eventos en el box; `grabacion_sin_fisiologia` = el box")
        md.append("sigue grabando pero sin FC/SpO2 fisiológica; `otro` = hay monitor")
        md.append("fisiológico (no debería contar como igualdad: revisar).")
        md.append("")
        md.append("| Evento | Box | fin vent. (h) | motivo |")
        md.append("|---|---|---|---|")
        for d in doc.get("detalle", []):
            md.append(f"| {d['event_id']} | {d['box']} | {d['vent_end_h']:.2f} | "
                      f"{d['reason']} |")
        md.append("")
    path.write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cohort", choices=sorted(SPECS), required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--probe", action="store_true",
                    help="Clasifica el motivo leyendo las muestras del origen")
    ap.add_argument("--probe-limit", type=int, default=25,
                    help="Nº de eventos a sondear (0 o negativo = todos)")
    ap.add_argument("--out", default=None, help="JSON de salida")
    ap.add_argument("--md", default=None, help="Informe Markdown de salida")
    ap.add_argument("--config", default=CONFIG)
    args = ap.parse_args()

    idx = json.loads(Path(args.index).read_text(encoding="utf-8"))
    events = idx["events"]
    pairs = []
    for e in events:
        ve = float(e["attempts"][-1]["vent_end_h"]) if e.get("attempts") else None
        pairs.append((e, ve))
    eq = [(e, ve) for e, ve in pairs
          if ve is not None and abs(float(e["obs_end_h"]) - ve) < _ESP]

    doc = {
        "cohort": args.cohort,
        "index": args.index,
        "n_events": len(events),
        "n_obs_end_eq_vent_end": len(eq),
        "n_monitor_tail_zero": sum(
            1 for e in events if float(e.get("monitor_tail_h") or 0.0) <= _ESP
        ),
        "end_reason": dict(Counter(e.get("end_reason") for e in events)),
        "end_reason_eq": dict(Counter(e.get("end_reason") for e, _ in eq)),
        "equality_implies_zero_tail": all(
            float(e.get("monitor_tail_h") or 0.0) <= _ESP for e, _ in eq
        ),
    }

    if args.probe:
        cfg = load_config(args.config)
        # Siguiente evento del mismo box (para distinguir el apagón del fin).
        by_box: dict[str, list[dict]] = {}
        for e in events:
            by_box.setdefault(e["box"], []).append(e)
        for evs in by_box.values():
            evs.sort(key=lambda x: float(x["t0_unix"]))
        has_next: set[str] = set()
        for evs in by_box.values():
            for a in evs[:-1]:
                has_next.add(a["event_id"])

        reasons: Counter = Counter()
        detail = []
        meta: dict = {}
        sel = eq if args.probe_limit in (None, 0) else eq[:args.probe_limit]
        for i, (e, ve) in enumerate(sel, start=1):
            ev = dict(e)
            ev["_has_next"] = e["event_id"] in has_next
            info = classify_event(ev, config=cfg, cohort=args.cohort,
                                  meta=meta)
            reasons[info["reason"]] += 1
            detail.append({"event_id": e["event_id"], "box": e["box"],
                           "vent_end_h": ve, **info})
            if i % 10 == 0:
                print(f"[probe] {i}/{len(sel)}…", flush=True)
        # Motivo por índice para TODOS los iguales (barato) y motivo por muestras
        # solo para la submuestra sondeada.
        for e, _ve in eq:
            doc.setdefault("motivo_indice", Counter())[
                "corte_D2_entre_regiones" if e["event_id"] in has_next
                else "fin_de_registro_del_box"] += 1
        doc["motivo_indice"] = dict(doc["motivo_indice"])
        doc["n_probed"] = len(sel)
        doc["source_dir"] = meta.get("raw_dir")
        doc["source_is_mirror"] = meta.get("is_mirror")
        doc["motivos"] = dict(reasons)
        doc["detalle"] = detail

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        write_md(doc, Path(args.md))
    print(json.dumps({k: v for k, v in doc.items() if k != "detalle"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
