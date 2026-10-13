#!/usr/bin/env python3
"""
scripts/verify/fase1_6e/cascada_candidatas.py
=============================================
Fase 1.6e — cascada de candidatas a unir (D-f revisada, variante d) y medicion
dirigida de las diferencias que falten.

Cascada (cada paso es un AND del anterior), por cohorte:

1. **Pares consecutivos del mismo box con el primero CENSURADO y hueco <= 12 h.**
2. **+ vuelve con ventilacion activa** (criterio del indice: inicio del intento).
3. **+ deltas medibles** (TV y FR a ambos lados; y PEEP/FiO2 para la variante d).
4. **+ variante d**: |dPEEP| <= 3, |dFiO2| <= 20, |dTV| <= 50 mL, |dRR| <= 2.

NO se exige apagado simultaneo <= 15 min: el desfase entre el fin del ventilador
y el fin del monitor se **informa como columna**, no se usa como filtro.

Los deltas ya medidos se toman de la sonda 2c.1 (`apagado_simultaneo.json`);
para los pares que entren y no esten medidos se leen los ficheros de forma
dirigida (`--medir-faltantes`), con presupuesto de tiempo: un `stat`/lectura
colgada de D: no puede bloquear el proceso.

Los identificadores de pareja son ESTABLES (nunca `event_id` posicional):
box + epoch del fin del primer tramo + epoch del inicio del segundo + hash del
NOMBRE del fichero de origen de cada lado.

Uso:
    python scripts/verify/fase1_6e/cascada_candidatas.py --cohort clinic \
        --json reports/fase1_6e/cascada_clinic.json
    python scripts/verify/fase1_6e/cascada_candidatas.py --cohort clinic \
        --medir-faltantes --json ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import threading
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.common.vital_signals import read_track_series  # noqa: E402
from src.create_dataset.build_signal_cases import (  # noqa: E402
    SPECS,
    scan_source_files,
)
from src.stage0.io.versioning import load_config  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from annotate_index import (  # noqa: E402
    EPS,
    MAX_GAP_H,
    RETURN_MIN,
    _median_window,
    _starts_ventilated,
)

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
REPORT = ROOT / "reports" / "fase1_6e"
D_PEEP, D_FIO2, D_TV, D_RR = 3.0, 20.0, 50.0, 2.0
GAP_CONTROL_H = 24.0       # negativo: parejas separadas > 24 h = pacientes distintos
SEMILLA_CONTROLES = 20261013
N_CONTROLES = 12
MAX_INTENTOS_CONTROL = 30
PEEP_T = ("Intellivue/PEEP_CMH2O",)
FIO2_T = ("Intellivue/FIO2",)
TV_T = ("Intellivue/TV_EXP",)
RR_T = ("Intellivue/VENT_RR",)


def _sha_nombre(nombre: str) -> str:
    """SHA-256 del NOMBRE del fichero (identificador estable, sin leerlo)."""
    return hashlib.sha256(nombre.encode("utf-8")).hexdigest()


def _deltas_de_cache(v: dict) -> dict:
    """Los 4 deltas desde una entrada de cache (ignora las claves auxiliares)."""
    return {k: v.get(k) for k in ("d_peep", "d_fio2", "d_tv", "d_rr")}


def _es_censurado(e: dict) -> bool:
    lab = (e.get("labels") or {}).get("48h") or {}
    return lab.get("event_type") != "successful_extubation"


def _medir_con_presupuesto(fn, *, budget_s: float):
    """Ejecuta ``fn`` en un hilo con presupuesto; devuelve (ok, valor).

    ``D:`` tiene rachas en las que una lectura se queda colgada: sin esto el
    proceso se bloquea para siempre (ya paso con el driver).
    """
    caja: dict = {}

    def _work() -> None:
        try:
            caja["v"] = fn()
        except Exception as exc:  # noqa: BLE001
            caja["v"] = None
            caja["err"] = f"{type(exc).__name__}: {exc}"

    th = threading.Thread(target=_work, daemon=True)
    th.start()
    th.join(timeout=budget_s)
    if th.is_alive():
        return False, None
    if caja.get("err"):
        print(f"    [aviso] medida fallida: {caja['err']}", flush=True)
    return True, caja.get("v")


def _deltas_de_ficheros(prev: dict, nxt: dict, meta: dict, *,
                        budget_s: float) -> tuple[dict, str]:
    """Mide los 4 deltas leyendo los ficheros (ventanas de 30 min de 2c.1).

    Devuelve ``(deltas, motivo)``: ``motivo`` explica que falte algo
    (``timeout``, ``sin <pista>``...). Se leen las CUATRO pistas en una sola
    pasada por lado (dos lecturas por pareja, no ocho).
    """
    def _de_corte(meta: dict, corte_epoch: float, e: dict) -> list:
        """Ficheros candidatos a cubrir el corte: union de dos criterios.

        1. los ``source_files`` del evento (como hace la sonda 2c.1, que si
           encuentra las pistas);
        2. los del box cuyo NOMBRE cae en las 26 h anteriores al corte (por si
           la lista del evento omite el ultimo tramo: pasa en v0.5.0, B2).

        La union evita los dos fallos vistos: perder el fichero que cubre el
        corte y elegir ficheros de otro tramo sin las pistas de ventilador.
        """
        corte_h = corte_epoch / 3600.0
        por_nombre = list((meta or {}).values())
        cand = [f for f in por_nombre
                if corte_h - 26.0 <= f.dt_unix / 3600.0 <= corte_h + 1.0]
        cand.sort(key=lambda f: f.dt_unix)
        del_evento = [meta[n] for n in (e.get("source_files") or [])
                      if n in (meta or {})]
        vistos: dict[str, object] = {}
        for f in list(del_evento) + cand[-3:]:
            vistos[str(f.path)] = f
        return list(vistos.values())[:6]

    tracks = [PEEP_T[0], FIO2_T[0], TV_T[0], RR_T[0]]
    att_prev = prev.get("attempts") or []
    att_next = nxt.get("attempts") or []
    if not att_prev or not att_next:
        return {k: None for k in ("d_peep", "d_fio2", "d_tv", "d_rr")}, "sin intentos"
    t0p, t0n = float(prev["t0_unix"]), float(nxt["t0_unix"])
    vp = float(att_prev[-1].get("vent_end_h") or 0.0)
    vn = float(att_next[0].get("vent_start_h") or 0.0)
    f_prev = _de_corte(meta, t0p + vp * 3600.0, prev)
    f_next = _de_corte(meta, t0n + vn * 3600.0, nxt)
    if not f_prev or not f_next:
        return ({k: None for k in ("d_peep", "d_fio2", "d_tv", "d_rr")},
                "sin ficheros del box alrededor del corte")
    ok_a, ser_a = _medir_con_presupuesto(
        lambda: read_track_series([str(f.path) for f in f_prev], tracks,
                                  t0_unix=t0p),
        budget_s=budget_s)
    ok_b, ser_b = _medir_con_presupuesto(
        lambda: read_track_series([str(f.path) for f in f_next], tracks,
                                  t0_unix=t0n),
        budget_s=budget_s)
    motivos: list[str] = []
    if not ok_a or not ok_b:
        motivos.append("timeout al leer")
        ser_a = ser_a or {}
        ser_b = ser_b or {}

    def _med(serie, tr, lo_h, hi_h, *, respaldo: str | None = None) -> float | None:
        """Mediana en la ventana; si esta vacia se usa el tramo CON DATOS mas
        cercano (``respaldo="antes"``: ultimos 30 min; ``"despues"``: primeros).

        Hace falta porque el fin de ventilacion del indice puede caer mas alla de
        lo grabado (v0.5.0 no incluye el ultimo fichero, B2) y porque el inicio
        del tramo siguiente puede caer antes del primer dato del fichero.
        """
        t, v = (serie or {}).get(tr, (None, None))
        if t is None or len(t) == 0:
            return None
        h = t / 3600.0
        sel = (h >= lo_h) & (h <= hi_h)
        if not sel.any() and respaldo == "antes":
            fin = float(h.max())
            sel = (h >= fin - 0.5) & (h <= fin)
        elif not sel.any() and respaldo == "despues":
            ini = float(h.min())
            sel = (h >= ini) & (h <= ini + 0.5)
        return float(np.median(v[sel])) if sel.any() else None

    out: dict[str, float | None] = {}
    for clave, tr in (("d_peep", PEEP_T[0]), ("d_fio2", FIO2_T[0]),
                      ("d_tv", TV_T[0]), ("d_rr", RR_T[0])):
        a = _med(ser_a, tr, vp - 0.5, vp, respaldo="antes")
        b = _med(ser_b, tr, vn, vn + 0.5, respaldo="despues")
        if a is None or b is None:
            motivos.append(f"sin {tr.split('/')[-1]} en la ventana")
            out[clave] = None
        else:
            out[clave] = b - a
    return out, "; ".join(dict.fromkeys(motivos))


def _pares(idx: dict) -> list[dict]:
    """Pares consecutivos del mismo box (paso 1 de la cascada)."""
    por_box: dict[str, list[dict]] = {}
    for e in idx["events"]:
        por_box.setdefault(e["box"], []).append(e)
    pares: list[dict] = []
    for box, evs in por_box.items():
        evs.sort(key=lambda x: float(x["t0_unix"]))
        for i in range(len(evs) - 1):
            prev, nxt = evs[i], evs[i + 1]
            att = prev.get("attempts") or []
            if not att:
                continue
            exit_prev_h = (float(prev["t0_unix"]) / 3600.0
                           + float(prev.get("obs_end_h") or 0.0))
            t0_next_h = float(nxt["t0_unix"]) / 3600.0
            gap = t0_next_h - exit_prev_h
            desfase = abs(float(prev["t0_unix"]) / 3600.0
                          + float(att[-1].get("vent_end_h") or 0.0)
                          - exit_prev_h)
            pares.append({
                "box": box, "prev": prev, "next": nxt,
                "gap_h": round(gap, 4),
                "censurado": _es_censurado(prev),
                "vuelve": _starts_ventilated(nxt, RETURN_MIN),
                "desfase_vent_monitor_h": round(desfase, 4),
            })
    return pares


def _id_estable(par: dict) -> dict:
    prev, nxt = par["prev"], par["next"]
    att = prev.get("attempts") or [{}]
    f_prev = (prev.get("source_files") or [""])[-1]
    f_next = (nxt.get("source_files") or [""])[0]
    return {
        "box": par["box"],
        "prev_end_epoch": round(float(prev["t0_unix"]) / 3600.0 * 3600.0
                                + float(att[-1].get("vent_end_h") or 0.0)
                                * 3600.0, 1),
        "next_start_epoch": round(float(nxt["t0_unix"]), 1),
        "prev_file": f_prev, "prev_file_sha256_nombre": _sha_nombre(f_prev),
        "next_file": f_next, "next_file_sha256_nombre": _sha_nombre(f_next),
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cohort", required=True, choices=sorted(SPECS))
    p.add_argument("--index", default=None)
    p.add_argument("--medir-faltantes", action="store_true",
                   help="Mide leyendo D: las parejas sin deltas de la sonda.")
    p.add_argument("--reintentar-fallos", action="store_true",
                   help="Vuelve a intentar las parejas ya intentadas y fallidas.")
    p.add_argument("--limite", type=int, default=None,
                   help="Mide solo los primeros N pares (depuracion).")
    p.add_argument("--cache-deltas", default=str(REPORT / "deltas_medidos.json"),
                   help="Cache de deltas ya medidos (evita repetir lecturas).")
    p.add_argument("--n-controles", type=int, default=N_CONTROLES,
                   help="Controles medibles que se intentan reunir.")
    p.add_argument("--max-intentos-control", type=int, default=MAX_INTENTOS_CONTROL,
                   help="Tope de pares de control que se intentan medir.")
    p.add_argument("--budget-s", type=float, default=180.0)
    p.add_argument("--json", default=None)
    p.add_argument("--md", default=None)
    p.add_argument("--config", default=str(CONFIG))
    args = p.parse_args()

    index_path = (Path(args.index) if args.index else
                  sorted((ROOT / "datasets" / args.cohort).glob(
                      f"cases_v*/{args.cohort}_cases_index.json"))[-1])
    idx = json.loads(index_path.read_text(encoding="utf-8"))
    apag = json.loads((REPORT / "apagado_simultaneo.json")
                      .read_text(encoding="utf-8")).get(args.cohort) or []
    medidos = {c["event_id"]: c for c in apag}
    print(f"Indice: {index_path.relative_to(ROOT)} ({idx['total_events']} eventos)")
    print(f"Sonda 2c.1: {len(apag)} casos con deltas medidos")

    config = load_config(args.config)
    boxes = scan_source_files(
        config_path(config, "paths", f"{args.cohort}_raw_dir"), SPECS[args.cohort])
    meta_por_box = {b: {sf.path.name: sf for sf in fs} for b, fs in boxes.items()}

    pares = _pares(idx)
    paso1 = [p for p in pares if p["censurado"] and 0.0 <= p["gap_h"] <= MAX_GAP_H + EPS]
    paso2 = [p for p in paso1 if p["vuelve"]]

    # Deltas: de la sonda si existen, si no (y se pide) medidos dirigidamente.
    # Cache de deltas medidos: clave = identificador ESTABLE de la pareja. Leer
    # D: es caro (ficheros de hasta 366 MB) y un relanzamiento no debe repetirlo.
    cache_path = Path(args.cache_deltas)
    cache: dict[str, dict] = ({}
        if not cache_path.exists()
        else json.loads(cache_path.read_text(encoding="utf-8")))

    def _clave_cache(p: dict) -> str:
        prev, nxt = p["prev"], p["next"]
        att = prev.get("attempts") or [{}]
        return (f"{p['box']}|"
                f"{float(prev['t0_unix']) + float(att[-1].get('vent_end_h') or 0.0) * 3600.0:.1f}|"
                f"{float(nxt['t0_unix']):.1f}")

    def _guarda_cache() -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                              encoding="utf-8")

    n_cache = 0
    # Sembrado: si ya hay un JSON de cascada de esta cohorte, sus candidatas ya
    # tienen deltas medidos -> se meten en la cache para no releer lo mismo.
    if args.json and Path(args.json).exists():
        try:
            previo = json.loads(Path(args.json).read_text(encoding="utf-8"))
            for c in previo.get("candidatas") or []:
                if c.get("deltas", {}).get("d_tv") is not None:
                    k = (f"{c['box']}|{float(c['prev_end_epoch']):.1f}|"
                         f"{float(c['next_start_epoch']):.1f}")
                    cache.setdefault(k, dict(c["deltas"]))
            print(f"cache sembrada desde {args.json}: {len(cache)} entradas")
        except Exception as exc:  # noqa: BLE001
            print(f"[aviso] no se pudo sembrar la cache: {exc}")
    n_medidos_sonda = n_medidos_directo = n_sin_medir = 0
    n_intentos = 0
    for p in paso2:
        eid = p["prev"]["event_id"]
        if eid in medidos:
            c = medidos[eid]
            p["deltas"] = {k: c.get(k) for k in
                           ("d_peep", "d_fio2", "d_tv", "d_rr")}
            p["origen_deltas"] = "sonda_2c1"
            n_medidos_sonda += 1
            continue
        if args.medir_faltantes and (args.limite is None
                                     or n_intentos < args.limite):
            clave = _clave_cache(p)
            if clave in cache and not args.reintentar_fallos:
                p["deltas"] = _deltas_de_cache(cache[clave])
                p["origen_deltas"] = "cache"
                p["motivo"] = cache[clave].get("_motivo", "")
                n_cache += 1
                if p["deltas"].get("d_tv") is not None:
                    n_medidos_directo += 1
                else:
                    n_sin_medir += 1
                continue
            n_intentos += 1
            d, motivo = _deltas_de_ficheros(p["prev"], p["next"],
                                            meta_por_box.get(p["box"], {}),
                                            budget_s=args.budget_s)
            p["deltas"] = d
            p["origen_deltas"] = "medido_dirigido"
            p["motivo"] = motivo
            cache[clave] = {**d, "_motivo": motivo}
            _guarda_cache()
            if all(d.get(k) is not None for k in ("d_tv", "d_rr")):
                n_medidos_directo += 1
            else:
                n_sin_medir += 1
        else:
            p["deltas"] = {k: None for k in
                           ("d_peep", "d_fio2", "d_tv", "d_rr")}
            p["origen_deltas"] = "sin_medir"
            p["motivo"] = "no medido (falta --medir-faltantes)"
            n_sin_medir += 1

    paso3 = [p for p in paso2 if p["deltas"].get("d_tv") is not None
             and p["deltas"].get("d_rr") is not None]
    paso4 = [p for p in paso3
             if p["deltas"].get("d_peep") is not None
             and p["deltas"].get("d_fio2") is not None
             and abs(p["deltas"]["d_peep"]) <= D_PEEP
             and abs(p["deltas"]["d_fio2"]) <= D_FIO2
             and abs(p["deltas"]["d_tv"]) <= D_TV
             and abs(p["deltas"]["d_rr"]) <= D_RR]
    # Controles para la adjudicacion: pacientes distintos (hueco > 24 h) del
    # mismo box, con TV y FR medibles a ambos lados. Se miden en orden ALEATORIO
    # con semilla fija y se para al reunir los pedidos (leer D: es caro).
    pool = [p for p in pares if p["gap_h"] > GAP_CONTROL_H]
    rnd = random.Random(SEMILLA_CONTROLES)
    rnd.shuffle(pool)
    controles: list[dict] = []
    intentos_control = 0
    if args.medir_faltantes:
        for p in pool:
            if len(controles) >= args.n_controles:
                break
            if intentos_control >= args.max_intentos_control:
                break
            intentos_control += 1
            clave = _clave_cache(p)
            if clave in cache and not args.reintentar_fallos:
                p["deltas"] = _deltas_de_cache(cache[clave])
                n_cache += 1
            else:
                d, motivo = _deltas_de_ficheros(p["prev"], p["next"],
                                                meta_por_box.get(p["box"], {}),
                                                budget_s=args.budget_s)
                p["deltas"] = d
                p["motivo"] = motivo
                cache[clave] = {**d, "_motivo": motivo}
                _guarda_cache()
            if all(p["deltas"].get(k) is not None for k in ("d_tv", "d_rr")):
                controles.append(p)
    else:
        controles = [p for p in pool
                     if p.get("deltas", {}).get("d_tv") is not None
                     and p.get("deltas", {}).get("d_rr") is not None]
    print(f"controles medidos: {len(controles)} de {len(pool)} pares con "
          f"hueco > {GAP_CONTROL_H:.0f} h (intentos {intentos_control})")
    print(f"deltas desde cache: {n_cache}")

    notas = [
        "Por que estos numeros no coinciden con el 45 -> 21 -> 7 del informe "
        "de 2c.3 (todas las causas son de BASE o de FILTRO, no de calculo):", "",
        "- **45** era 'hueco <= 12 h' contado SOLO sobre los pares cuyo primer "
        "evento analizo la sonda (135 de 181 eventos), y **sin exigir que el "
        "primero estuviera censurado**. Aqui el paso 1 recorre TODOS los pares "
        "(168) y exige censura: 62.",
        "- **21** exigia las cuatro diferencias medidas (cobertura de la sonda). "
        "Aqui el paso 3 cae a 19 porque quedan 38 pares sin medir: con "
        "`--medir-faltantes` suben los pasos 3 y 4.",
        "- **7** (commit 1f048cc) anadia el filtro de **apagado simultaneo <= 15 "
        "min**. Ese filtro YA NO se aplica: el desfase fin-de-ventilador vs "
        "fin-de-monitor se informa como columna por pareja.",
        "- El paso 2 no resta nada porque 'vuelve con ventilacion activa' es "
        "casi siempre cierto en Clinic (llegan intubados de quirofano).",
    ]

    tabla = [
        "| Paso | Pares |", "|---|---|",
        f"| 0. Pares consecutivos del mismo box | {len(pares)} |",
        f"| 1. Primero censurado y hueco <= {MAX_GAP_H:.0f} h | {len(paso1)} |",
        f"| 2. + vuelve con ventilacion activa (indice) | {len(paso2)} |",
        f"| 3. + deltas de TV y FR medibles | {len(paso3)} |",
        f"| 4. + VARIANTE d (PEEP, FiO2, TV, FR) | **{len(paso4)}** |",
    ]
    print("\n".join(tabla))
    print(f"deltas: sonda {n_medidos_sonda} · medidos dirigidos {n_medidos_directo}"
          f" · sin medir {n_sin_medir}")
    print(f"controles disponibles (hueco > 24 h, con TV y FR): {len(controles)}")
    con_motivo = [(p["prev"]["event_id"], p.get("motivo") or "")
                  for p in paso2 if p["deltas"].get("d_tv") is None
                  or p["deltas"].get("d_rr") is None]
    if con_motivo:
        print(f"{len(con_motivo)} parejas sin TV/FR medibles:")
        razones: dict[str, int] = {}
        for _eid, m in con_motivo:
            razones[m] = razones.get(m, 0) + 1
            print(f"  - {_eid}: {m}")
        print(f"  motivos: {razones}")
    if paso4:
        desf = sorted(p["desfase_vent_monitor_h"] for p in paso4)
        print(f"desfase vent-monitor en las candidatas: p10 "
              f"{desf[int(0.1 * (len(desf) - 1))]:.2f} · p50 "
              f"{desf[len(desf) // 2]:.2f} · p90 {desf[int(0.9 * (len(desf) - 1))]:.2f} h")

    salida = {
        "cohort": args.cohort, "index": str(index_path.relative_to(ROOT)),
        "cascada": {"paso0": len(pares), "paso1": len(paso1),
                    "paso2": len(paso2), "paso3": len(paso3),
                    "paso4": len(paso4)},
        "n_deltas_sonda": n_medidos_sonda,
        "n_deltas_directos": n_medidos_directo,
        "n_sin_medir": n_sin_medir,
        "candidatas": [{**_id_estable(p), "gap_h": p["gap_h"],
                        "desfase_vent_monitor_h": p["desfase_vent_monitor_h"],
                        "deltas": p["deltas"], "origen_deltas": p["origen_deltas"]}
                       for p in paso4],
        "sin_medir": [{**_id_estable(p), "motivo": p.get("motivo")}
                      for p in paso2 if p["deltas"].get("d_tv") is None
                      or p["deltas"].get("d_rr") is None],
        "controles_disponibles": [
            {**_id_estable(p), "gap_h": p["gap_h"],
             "desfase_vent_monitor_h": p["desfase_vent_monitor_h"]}
            for p in controles],
        "notas": notas,
    }
    if args.json:
        Path(args.json).write_text(json.dumps(salida, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"JSON: {args.json}")
    if args.md:
        md = [f"# Cascada de candidatas — {args.cohort}", "",
              *tabla, "", *notas, "",
              f"- Indice: `{index_path.relative_to(ROOT)}`",
              f"- Deltas: sonda 2c.1 {n_medidos_sonda} · medidos dirigidos "
              f"{n_medidos_directo} · sin medir {n_sin_medir}"
              f" (desde cache {n_cache})",
              f"- Controles medidos: {len(controles)} de {len(pool)} pares con "
              f"hueco > {GAP_CONTROL_H:.0f} h (intentos {intentos_control})",
              "- Identificadores estables: box + epochs + SHA-256 del NOMBRE de "
              "los ficheros de origen (nunca `event_id` posicional). El SHA-256 "
              "del CONTENIDO va en la clave de adjudicacion.", ""]
        md += [f"## Parejas sin TV/FR medibles ({len(con_motivo)})", "",
               "`motivo` = por que no se pudo medir esa pareja (no es 0, es "
               "`no evaluado`).", "",
               "| Pareja (evento previo) | Motivo |", "|---|---|"]
        for _eid, m in con_motivo:
            md.append(f"| `{_eid}` | {m or '—'} |")
        md += ["", f"### Candidatas variante d ({len(paso4)})", "",
               "| Box | prev_end_epoch | next_start_epoch | Hueco h | "
               "Desfase vent-monitor h | dPEEP | dFiO2 | dTV | dFR |",
               "|---|---|---|---|---|---|---|---|---|"]
        for c in salida["candidatas"]:
            d = c["deltas"]

            def _f(x: object) -> str:
                return "—" if x is None else f"{float(x):.2f}"

            md.append(f"| {c['box']} | {c['prev_end_epoch']:.0f} | "
                      f"{c['next_start_epoch']:.0f} | {c['gap_h']:.2f} | "
                      f"{c['desfase_vent_monitor_h']:.2f} | {_f(d.get('d_peep'))} | "
                      f"{_f(d.get('d_fio2'))} | {_f(d.get('d_tv'))} | "
                      f"{_f(d.get('d_rr'))} |")
        md.append("")
        Path(args.md).write_text("\n".join(md), encoding="utf-8")
        print(f"MD: {args.md}")


if __name__ == "__main__":
    main()
