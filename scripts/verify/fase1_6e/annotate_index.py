#!/usr/bin/env python3
"""
scripts/verify/fase1_6e/annotate_index.py
=========================================
Fase 1.6e (anadido b): anota el indice v0.6.0, **por evento**, con tres campos
que solo se usan para el analisis de sensibilidad:

- ``prev_exit_same_box_gap_h``: horas desde la salida (fin de la observacion
  fisiologica, ``obs_end_h``) del evento ANTERIOR del mismo box hasta el t0 de
  este evento. Solo se rellena si este evento **empieza ya ventilado**
  (``arrived_ventilated``); si no, es ``None``.
- ``prev_exit_class``: clase (A/B/C/D) de esa salida anterior, con los MISMOS
  criterios que 2c.1 y el mismo test de apagado simultaneo (|fin de ventilacion
  activa - fin de la observacion FISIOLOGICA| <= 15 min; NO el flag
  `simultaneous_shutdown` del builder, que compara con el fin de la REGION).
  La continuidad de ajustes (|dPEEP| <= 3 y |dFiO2| <= 20) solo se puede evaluar
  con ``--probar``; sin el, A nunca se asigna (cae a D, conservador) y la clase
  debe leerse como aproximada. **Definicion unica**: la anotacion con
  ``--probar`` sobre el indice v0.6.0.
- ``return_ventilated_within_12h``: **el campo informativo**: el evento siguiente
  del mismo box empieza ventilado **y** el hueco es <= 12 h. (El primitivo "vuelve
  ventilado en <= 30 min" salia cierto en 168 de 181 eventos de Clinic, porque
  los pacientes llegan intubados de quirofano: por si solo no informaba, asi que
  NO se guarda.) Es la base de la columna (c) de la tabla final.
- ``next_same_box_vent_start_gap_h``: hueco desde el fin de la observacion de
  este evento hasta el inicio de la ventilacion del evento siguiente del mismo
  box (crudo, para el analisis de sensibilidad).

No cambia NINGUNA etiqueta: es anotacion para excluir despues los segmentos de
vuelta y las salidas con vuelta ventilada.

Uso:
    python scripts/verify/fase1_6e/annotate_index.py --cohort clinic --dry-run
    python scripts/verify/fase1_6e/annotate_index.py --cohort clinic
    python scripts/verify/fase1_6e/annotate_index.py --cohort clinic --probar
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.common.vital_signals import probe_vital_file, read_track_series  # noqa: E402
from src.create_dataset.build_signal_cases import (  # noqa: E402
    SPECS,
    file_span_h,
    scan_source_files,
)
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
MAX_GAP_H = 12.0          # 2c.1: por encima, la vuelta no es el mismo curso
RETURN_MIN = 15.0 / 60.0  # 2c.1: la region siguiente empieza ventilada
RETURN_MIN_30 = 30.0 / 60.0
EPS = 1e-9


def _classify(gap_h: float | None, has_files: bool | None,
              next_starts_ventilated: bool,
              cont_ok: bool | None = None) -> str:
    """A/B/C/D con la MISMA logica que 2c.1 (`classify` del diagnostico).

    ``cont_ok`` (|dPEEP| <= 3 y |dFiO2| <= 20) solo se puede evaluar con
    ``--probar``; sin el, A significa "vuelta ventilada por evidencia de senal"
    y D "evidencia ausente". Por eso las cifras de A pueden diferir entre el
    diagnostico (con ficheros) y una anotacion sin sondear.
    """
    if gap_h is None or gap_h > MAX_GAP_H + EPS:
        return "C_salida"
    if not next_starts_ventilated:
        return "C_salida"
    if has_files is False:
        return "B_parada_grabador" if cont_ok is not False else "D_indeterminado"
    if cont_ok is True:
        return "A_vuelta_ventilada"
    return "D_indeterminado"


def _es_apagado_simultaneo(prev: dict, *, tol_h: float = 0.25) -> bool:
    """Test de 2c.1: |fin de ventilacion ACTIVA - fin de observacion FISIOLOGICA|.

    NO se usa el flag `simultaneous_shutdown` del builder, que compara el fin del
    intento con el fin de la REGION (otra cosa: la region puede seguir con ondas
    planas). Las dos cifras estan en el indice.
    """
    att = prev.get("attempts") or []
    if not att or prev.get("obs_end_h") is None:
        return False
    fin_vent = float(att[-1].get("vent_end_h") or 0.0)
    return abs(float(prev["obs_end_h"]) - fin_vent) <= tol_h + EPS


PEEP_H2O_MIN = 3.0       # 2c.1: |dPEEP| <= 3 cmH2O
FIO2_PCT_MIN = 20.0      # 2c.1: |dFiO2| <= 20 %
VENTANA_H = 0.5          # 30 min antes de la salida / despues de la vuelta


def _median_window(files: list, t0_unix: float, track_names: tuple[str, ...],
                   lo_h: float, hi_h: float) -> float | None:
    """Mediana de la primera pista disponible en ``[lo_h, hi_h]`` horas de t0."""
    if not files:
        return None
    series = read_track_series([str(f.path) for f in files], list(track_names),
                               t0_unix=t0_unix)
    for name in track_names:
        t, v = series.get(name, (None, None))
        if t is None or len(t) == 0:
            continue
        sel = (t / 3600.0 >= lo_h) & (t / 3600.0 <= hi_h)
        if sel.any():
            return float(np.median(v[sel]))
    return None


def _continuidad_ajustes(prev: dict, nxt: dict, files_prev: list,
                         files_nxt: list, box_files: list) -> bool | None:
    """|dPEEP| <= 3 y |dFiO2| <= 20 (2c.1) leyendo los ficheros (``--probar``).

    Devuelve ``None`` si no se puede medir (como en 2c.1, donde esos pares
    quedaban indeterminados).
    """
    att_prev = prev.get("attempts") or []
    att_next = nxt.get("attempts") or []
    if not att_prev or not att_next:
        return None
    exit_prev = float(att_prev[-1].get("vent_end_h") or 0.0)
    vent_next = float(att_next[0].get("vent_start_h") or 0.0)
    t0_prev = float(prev["t0_unix"])
    t0_next = float(nxt["t0_unix"])
    peep_b = _median_window(files_prev, t0_prev,
                            ("Intellivue/PEEP_CMH2O",),
                            exit_prev - VENTANA_H, exit_prev)
    peep_a = _median_window(files_nxt, t0_next,
                            ("Intellivue/PEEP_CMH2O",),
                            vent_next, vent_next + VENTANA_H)
    fio2_b = _median_window(files_prev, t0_prev, ("Intellivue/FIO2",),
                            exit_prev - VENTANA_H, exit_prev)
    fio2_a = _median_window(files_nxt, t0_next, ("Intellivue/FIO2",),
                            vent_next, vent_next + VENTANA_H)
    if peep_b is None or peep_a is None or fio2_b is None or fio2_a is None:
        return None
    return (abs(peep_a - peep_b) <= PEEP_H2O_MIN + EPS
            and abs(fio2_a - fio2_b) <= FIO2_PCT_MIN + EPS)


def _hueco_con_ficheros(
    files: list, t0_h: float, exit_h: float, t0_next_h: float,
    *, probar: bool = False, margen_h: float = 24.0,
) -> tuple[bool | None, int]:
    """¿Hay ficheros dentro del hueco? (intervalos REALES si ``probar``).

    El prefiltro por NOMBRE usa un margen amplio (24 h) porque en Clinic las
    grabaciones duran 19-48 h y su nombre va 1-2 h por detras del contenido: con
    ±2 h el fichero que SI cubre el hueco se quedaba fuera y el par se marcaba
    como "sin ficheros" (falsos B). Misma leccion que B2.

    Sin ``probar`` solo se afirma que SI hay ficheros (un nombre dentro del
    hueco); lo demas es "no concluyente" -> clase D. Para tener B de verdad hace
    falta ``--probar`` (leer cabeceras).
    """
    lo, hi = exit_h, t0_next_h
    if hi <= lo + EPS:
        # Hueco vacio (eventos CONTIGUOS): la grabacion continuaba, asi que NO
        # es una parada del grabador. Tratar esto como "sin ficheros" fue un
        # error: daba 25 falsos B con hueco 0,00 h.
        return True, 0
    cand = [f for f in files
            if (f.dt_unix / 3600.0) < hi + margen_h
            and (f.dt_unix / 3600.0 + 1.0) > lo - margen_h]
    if not cand:
        return False, 0
    if not probar:
        # Con solo el nombre NO se puede afirmar "no hay ficheros" (B2: en
        # Clinic el nombre va 1-2 h por detras del contenido). Solo se afirma
        # que SI los hay cuando un nombre cae dentro del hueco; en el resto,
        # "no concluyente" -> clase D. Para tener B de verdad hace falta
        # `--probar` (leer cabeceras).
        dentro = [f for f in cand if lo < (f.dt_unix / 3600.0) < hi]
        return (True if dentro else None), len(cand)
    # Con `--probar` se leen cabeceras (D: es caro): se ordenan los candidatos
    # por cercania al hueco y se para en el primer solape, para no leer 24
    # ficheros por par.
    def _dist(f) -> float:
        t = f.dt_unix / 3600.0
        return max(lo - t, t - hi, 0.0)

    for f in sorted(cand, key=_dist):
        pr = probe_vital_file(f.path)
        span = file_span_h(f, pr)
        if span.start_h < hi and span.end_h > lo:
            return True, len(cand)
    return False, len(cand)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cohort", required=True, choices=sorted(SPECS))
    p.add_argument("--index", default=None,
                   help="indice a anotar (por defecto, el cases_v* mas reciente)")
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--probar", action="store_true",
                   help="Confirmar el hueco con la cabecera (lee D:).")
    args = p.parse_args()

    index_path = (Path(args.index) if args.index else
                  sorted((ROOT / "datasets" / args.cohort).glob(
                      f"cases_v*/{args.cohort}_cases_index.json"))[-1])
    idx = json.loads(index_path.read_text(encoding="utf-8"))
    print(f"Indice: {index_path.relative_to(ROOT)} ({idx['total_events']} eventos)")

    config = load_config(args.config)
    boxes = scan_source_files(
        config_path(config, "paths", f"{args.cohort}_raw_dir"), SPECS[args.cohort])
    by_box: dict[str, list[dict]] = {}
    for e in idx["events"]:
        by_box.setdefault(e["box"], []).append(e)
    for b in by_box:
        by_box[b].sort(key=lambda x: float(x["t0_unix"]))

    def _exit_h(e: dict) -> float:
        return (float(e["t0_unix"]) / 3600.0 + float(e.get("obs_end_h") or 0.0))

    def _starts_ventilated(e: dict, max_min: float) -> bool:
        if e.get("arrived_ventilated"):
            return True
        att = e.get("attempts") or []
        if not att:
            return False
        return float(att[0].get("vent_start_h") or 0.0) <= max_min + EPS

    def _files_of(e: dict) -> list:
        por_nombre = {f.path.name: f for f in boxes.get(e["box"], [])}
        return [por_nombre[n] for n in (e.get("source_files") or [])
                if n in por_nombre]

    n_prev_gap = n_cls = n_ret12 = n_cont = 0
    clases: dict[str, int] = {}
    detalle: list[dict] = []
    for b, evs in by_box.items():
        files = boxes.get(b, [])
        for i, e in enumerate(evs):
            # return_ventilated_within_12h: vuelve ventilado Y hueco <= 12 h. El
            # primitivo "en <= 30 min" no se guarda: salia cierto en 168 de 181
            # eventos de Clinic (llegan intubados de quirofano) y no informaba.
            nxt = evs[i + 1] if i + 1 < len(evs) else None
            gap_next = (round(float(nxt["t0_unix"]) / 3600.0 - _exit_h(e), 4)
                        if nxt is not None else None)
            e["next_same_box_vent_start_gap_h"] = gap_next
            e["return_ventilated_within_12h"] = bool(
                gap_next is not None and 0.0 <= gap_next <= MAX_GAP_H
                and _starts_ventilated(nxt, RETURN_MIN_30))
            n_ret12 += int(e["return_ventilated_within_12h"])
            if i == 0:
                e["prev_exit_same_box_gap_h"] = None
                e["prev_exit_class"] = None
                continue
            prev = evs[i - 1]
            gap = float(e["t0_unix"]) / 3600.0 - _exit_h(prev)
            if e.get("arrived_ventilated"):
                e["prev_exit_same_box_gap_h"] = round(gap, 4)
                n_prev_gap += 1
            else:
                e["prev_exit_same_box_gap_h"] = None
            if _es_apagado_simultaneo(prev):
                has_files, n_cand = _hueco_con_ficheros(
                    files, float(prev["t0_unix"]) / 3600.0, _exit_h(prev),
                    float(e["t0_unix"]) / 3600.0, probar=args.probar)
                cont_ok = (_continuidad_ajustes(prev, e, _files_of(prev),
                                               _files_of(e), files)
                           if args.probar else None)
                if cont_ok is not None:
                    n_cont += 1
                cls = _classify(gap, has_files, _starts_ventilated(e, RETURN_MIN),
                                cont_ok)
            else:
                # La salida anterior NO fue un apagado simultaneo: no es una de
                # las clases de 2c.1. Para la sensibilidad se usan el hueco y
                # `next_return_ventilated_12h`.
                has_files, n_cand, cls = None, None, None
                cont_ok = None
            e["prev_exit_class"] = cls
            detalle.append({"box": b, "prev": prev["event_id"], "next": e["event_id"],
                            "gap_h": round(gap, 3), "has_files": has_files
                            if has_files is not None else None,
                            "clase": cls, "n_files_box": len(files),
                            "n_cand": n_cand, "cont_ok": cont_ok})
            if cls:
                clases[cls] = clases.get(cls, 0) + 1
                n_cls += 1

    print(f"- prev_exit_same_box_gap_h relleno: {n_prev_gap}")
    print(f"- prev_exit_class: {n_cls} -> {clases} (continuidad de ajustes "
          f"evaluada en {n_cont} pares)")
    print(f"- return_ventilated_within_12h = True: {n_ret12}")
    if args.dry_run:
        # Por que se asigna cada clase: hueco y evidencia de ficheros del par.
        por_clase: dict[str, list[dict]] = {}
        for d in detalle:
            if d["clase"]:
                por_clase.setdefault(d["clase"], []).append(d)
        for cls, ds in sorted(por_clase.items()):
            gaps = sorted(x["gap_h"] for x in ds)
            print(f"  {cls}: n={len(ds)} hueco p10/p50/p90 = "
                  f"{gaps[int(0.1 * (len(gaps) - 1))]:.2f} / "
                  f"{gaps[len(gaps) // 2]:.2f} / "
                  f"{gaps[int(0.9 * (len(gaps) - 1))]:.2f} h · "
                  f"con ficheros={sum(1 for x in ds if x['has_files'] is True)} "
                  f"sin={sum(1 for x in ds if x['has_files'] is False)} "
                  f"dudoso={sum(1 for x in ds if x['has_files'] is None)}")
            for x in ds[:3]:
                print(f"    {x['prev']} -> {x['next']} gap {x['gap_h']} h "
                      f"ficheros={x['has_files']} n_files_box={x['n_files_box']} "
                      f"n_cand={x['n_cand']}")
        print("(dry-run: no se escribe nada)")
        return
    bak = index_path.with_suffix(".json.pre_anot")
    if not bak.exists():
        shutil.copy2(index_path, bak)
        print(f"Copia de seguridad: {bak.name}")
    index_path.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    print(f"Anotado: {index_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
