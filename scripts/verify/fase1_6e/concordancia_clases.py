#!/usr/bin/env python3
"""
scripts/verify/fase1_6e/concordancia_clases.py
=============================================
Fase 1.6e (correccion 2): tabla de concordancia de la clase A/B/C/D entre

- **la sonda** (diagnostico 2c.1 con lectura de ficheros: ``apagado_simultaneo.json``,
  que evalua si el hueco tiene grabacion y la continuidad de ajustes), y
- **el indice** (anotacion ``prev_exit_class`` de ``annotate_index.py``, que sin
  ``--probar`` NO puede evaluar ninguna de las dos cosas y por diseno cae a D).

Ambas salen de la MISMA definicion (la de 2c.1: hueco <= 12 h, la region
siguiente empieza ventilada, ficheros en el hueco y |dPEEP| <= 3 / |dFiO2| <= 20).
La diferencia esperada es que el indice, sin sondear, es conservador: A solo se
asigna con ``--probar``.

Uso:
    python scripts/verify/fase1_6e/concordancia_clases.py --cohort clinic
    python scripts/verify/fase1_6e/concordancia_clases.py --cohort clinic --md out.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from annotate_index import (  # noqa: E402
    MAX_GAP_H,
    RETURN_MIN,
    RETURN_MIN_30,
    EPS,
    _classify,
    _es_apagado_simultaneo,
    _hueco_con_ficheros,
)

REPORT = ROOT / "reports" / "fase1_6e"
CLASES = ("A_vuelta_ventilada", "B_parada_grabador", "C_salida", "D_indeterminado")


def _corto(c: str | None) -> str:
    """Forma corta de la clase (A/B/C/D); "—" si no hay clase o no se evaluo."""
    if c is None or c == "—":
        return "—"
    return c.split("_")[0]


def _clase_indice(idx: dict, cohort: str) -> dict[str, str | None]:
    """Clase de la salida ANTERIOR por evento, con la definicion de 2c.1.

    Sin leer ficheros: ``has_files=None`` y ``cont_ok=None``, que es exactamente
    el caso conservador (cae a D). Es lo que hace ``annotate_index.py`` sin
    ``--probar``.
    """
    out: dict[str, str | None] = {}
    por_box: dict[str, list[dict]] = {}
    for e in idx["events"]:
        por_box.setdefault(e["box"], []).append(e)
    for evs in por_box.values():
        evs.sort(key=lambda x: float(x["t0_unix"]))
        for i, e in enumerate(evs):
            if i == 0:
                out[e["event_id"]] = None
                continue
            prev = evs[i - 1]
            gap = (float(e["t0_unix"]) - (float(prev["t0_unix"])
                                         + float(prev.get("obs_end_h") or 0.0)
                                         * 3600.0)) / 3600.0
            att = e.get("attempts") or []
            starts = bool(e.get("arrived_ventilated")) or (
                bool(att) and float(att[0].get("vent_start_h") or 0.0)
                <= RETURN_MIN + EPS)
            if _es_apagado_simultaneo(prev):
                out[e["event_id"]] = _classify(gap, None, starts, None)
            else:
                out[e["event_id"]] = None
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cohort", default="clinic")
    p.add_argument("--md", default=None)
    args = p.parse_args()

    idx_path = sorted((ROOT / "datasets" / args.cohort).glob(
        f"cases_v*/{args.cohort}_cases_index.json"))[-1]
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    apag = json.loads((REPORT / "apagado_simultaneo.json")
                      .read_text(encoding="utf-8")).get(args.cohort) or []
    sonda = {c["event_id"]: c["clase"] for c in apag}
    indice = _clase_indice(idx, args.cohort)

    filas = [e["event_id"] for e in idx["events"]]
    por_box: dict[str, list[dict]] = {}
    for e in idx["events"]:
        por_box.setdefault(e["box"], []).append(e)

    # La clase de la SONDA del evento i describe SU salida (el par i -> i+1);
    # la del INDICE para el evento i+1 describe la salida de i. Hay que emparejar
    # sonda[i] con indice[i+1], no indice[i].
    pares: list[tuple[str, str, str, str]] = []
    for evs in por_box.values():
        evs.sort(key=lambda x: float(x["t0_unix"]))
        for i in range(len(evs) - 1):
            eid_i, eid_next = evs[i]["event_id"], evs[i + 1]["event_id"]
            s = sonda.get(eid_i)
            k = indice.get(eid_next)
            if s is None:
                continue
            pares.append((eid_i, eid_next, s, k or "—"))

    matriz: dict[tuple[str, str], int] = {}
    for _i, _n, s, k in pares:
        clave = (_corto(s), _corto(k))
        matriz[clave] = matriz.get(clave, 0) + 1

    en_sonda = [e for e in filas if e in sonda]
    print(f"Indice: {idx_path.relative_to(ROOT)}")
    print(f"Eventos: {len(filas)} · con clase de sonda (2c.1): {len(en_sonda)} "
          f"· sin sonda: {len(filas) - len(en_sonda)} · pares comparables: "
          f"{len(pares)}")
    cols = [_corto(c) for c in CLASES] + ["—"]
    print("\n| sonda \\ indice | " + " | ".join(cols) + " | total |")
    print("|" + "---|" * (len(cols) + 2))
    for sc in CLASES + (None,):
        scs = _corto(sc)
        fila = [str(matriz.get((scs, ic), 0)) for ic in cols]
        tot = sum(int(x) for x in fila)
        print(f"| {scs} | " + " | ".join(fila) + f" | {tot} |")
    print("\nAqui cada celda es (clase de la sonda, clase del indice). El indice "
          "sin `--probar` nunca asigna A ni B: cae a D (conservador).")

    acuerdos = sum(v for (a, b), v in matriz.items() if a == b)
    sonda_a_indice_d = sum(v for (a, b), v in matriz.items()
                           if a == "A" and b == "D")
    sonda_c_indice_d = sum(v for (a, b), v in matriz.items()
                           if a == "C" and b == "D")
    interp = [
        f"- Pares comparables: **{len(pares)}** · coinciden: **{acuerdos}** "
        f"({100.0 * acuerdos / len(pares):.0f} %)." if pares else "- Sin pares.",
    ]
    if sonda_a_indice_d:
        interp.append(
            f"- Sonda A -> indice D: **{sonda_a_indice_d}**. Es lo ESPERADO: sin "
            f"`--probar` el indice no puede ver ni la grabacion del hueco ni la "
            f"continuidad de ajustes, asi que no asigna A.")
    if sonda_c_indice_d:
        interp.append(
            f"- Sonda C -> indice D: **{sonda_c_indice_d}**. Difieren en el paso "
            f"'vuelve ventilada': la sonda mide la actividad por minuto DENTRO de "
            f"su ventana de analisis (que para el evento siguiente puede estar "
            f"recortada), mientras el indice usa el inicio del intento (el t0). "
            f"Por eso la definicion unica se cierra corriendo la sonda Y la "
            f"anotacion `--probar` sobre el MISMO indice (v0.6.0).")
    if not sonda_a_indice_d and not sonda_c_indice_d:
        interp.append("- Sin discrepancias en esta cohorte (la columna del indice "
                      "coincide con la de la sonda en todos los pares).")
    for ln in interp:
        print(ln)

    if args.md:
        lines = [f"# Concordancia de clases sonda vs indice ({args.cohort})", "",
                 f"- Indice: `{idx_path.relative_to(ROOT)}`",
                 f"- Eventos: {len(filas)} · con clase de sonda: {len(en_sonda)} "
                 f"· sin sonda: {len(filas) - len(en_sonda)} · pares "
                 f"comparables: {len(pares)}", "",
                 "| sonda \\ indice | " + " | ".join(cols) + " | total |",
                 "|" + "---|" * (len(cols) + 2)]
        for sc in CLASES + (None,):
            scs = _corto(sc)
            fila = [str(matriz.get((scs, ic), 0)) for ic in cols]
            tot = sum(int(x) for x in fila)
            lines.append(f"| {scs} | " + " | ".join(fila) + f" | {tot} |")
        lines += ["", "## Interpretacion", ""] + interp + [
            "", "Definicion unica (2c.1) en ambas columnas. Con `--probar` la "
            "columna del indice debe acercarse fila a fila a la de la sonda.", ""]
        Path(args.md).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nMD: {args.md}")


if __name__ == "__main__":
    main()
