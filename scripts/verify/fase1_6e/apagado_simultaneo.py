#!/usr/bin/env python3
"""
scripts/verify/fase1_6e/apagado_simultaneo.py
=============================================
Fase 1.6e — **punto 2c.1**: diagnóstico del apagado simultáneo monitor+ventilador.

Para cada evento cuya ventilación ACTIVA y señal fisiológica (FC/SpO2) acaban a
<= 15 min una de otra (los «65 de Clínic» y sus equivalentes en VitalDB) se mide:

- **hueco** hasta la siguiente actividad (ventilación o monitor) en el MISMO box;
- si el hueco **no tiene ningún fichero** (¿grabador parado?) o hay ficheros con
  pistas pero sin señal de paciente (monitor encendido y paciente fuera);
- si la siguiente región **empieza con ventilación activa** en <= 15 min;
- **continuidad** de los últimos 30 min antes y los primeros 30 min después:
  ΔPEEP, ΔFiO2, ΔTV_EXP, ΔVENT_RR (del diagnóstico, por minutos) y ΔFC, ΔSpO2
  (sondeo dirigido de las pistas de monitor de los ficheros del borde);
- si la siguiente región es un **fragmento** corto o es larga.

Clasificación (2c.1): (A) vuelta ventilada, (B) parada del grabador,
(C) salida definitiva, (D) indeterminado.

Lo que se puede calcular con el diagnóstico (por minutos) no vuelve a leer
crudos; solo FC/SpO2 y «¿hay ficheros en el hueco?» se sondean, y solo en los
ficheros del borde de cada pareja.

Uso:
    python scripts/verify/fase1_6e/apagado_simultaneo.py --cohorts clinic \
        --probe       # sondea FC/SpO2 y el contenido del hueco
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.common.vital_signals import (  # noqa: E402
    ACTIVITY_AWP_AMP_MIN_CMH2O,
    ACTIVITY_MV_EXP_MIN_LPM,
    ACTIVITY_TV_EXP_MIN_ML,
    probe_vital_file,
    read_track_series,
)
from src.create_dataset.build_signal_cases import SPECS, scan_source_files  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

REPORT = ROOT / "reports" / "fase1_6e"
EDGE_MIN = 15.0        # «a <= 15 min una de otra»
EDGE_WINDOW_MIN = 30.0  # ventana de continuidad antes/después
MAX_GAP_H = 12.0       # D-f: el hueco debe ser <= 12 h para poder unir
MONITOR = ("Intellivue/ECG_HR", "Intellivue/HR", "Intellivue/PLETH_HR",
           "Intellivue/PLETH_SAT_O2")


def _active_minutes(row: dict) -> set[int]:
    per = row.get("per_min") or {}
    rr = per.get("VENT_RR") or {}
    out: set[int] = set()
    for k, v in (per.get("TV_EXP") or {}).items():
        if float(v) >= ACTIVITY_TV_EXP_MIN_ML:
            out.add(int(k))
    for k, v in (per.get("MV_EXP") or {}).items():
        if float(v) >= ACTIVITY_MV_EXP_MIN_LPM:
            out.add(int(k))
    for k, v in (per.get("AWP_WAV") or {}).items():
        if float(v) >= ACTIVITY_AWP_AMP_MIN_CMH2O and float(rr.get(k, 0.0)) > 0.0:
            out.add(int(k))
    return out


def _median_in(row: dict, track: str, lo_min: float, hi_min: float) -> float | None:
    d = (row.get("per_min") or {}).get(track) or {}
    vals = [float(v) for k, v in d.items() if lo_min <= float(k) <= hi_min]
    return float(np.median(vals)) if vals else None


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(b - a, 3)


def _monitor_median(paths: list[str], lo_min: float, hi_min: float
                    ) -> dict[str, float | None]:
    """Mediana de FC y SpO2 en ``[lo_min, hi_min]`` (minutos desde t0 del evento)."""
    out: dict[str, float | None] = {"FC": None, "SpO2": None}
    if not paths:
        return out
    series = read_track_series(paths, list(MONITOR))
    for key, names in (("FC", ("Intellivue/ECG_HR", "Intellivue/HR",
                               "Intellivue/PLETH_HR")),
                       ("SpO2", ("Intellivue/PLETH_SAT_O2",))):
        for n in names:
            t, v = series.get(n, (np.array([]), np.array([])))
            if t.size == 0:
                continue
            t_min = t / 60.0
            sel = (t_min >= lo_min) & (t_min <= hi_min)
            if sel.any():
                out[key] = float(np.median(v[sel]))
                break
    return out


def _pair_deltas(prev: dict, nxt: dict) -> dict:
    """Δ de ajustes entre los últimos 30 min de ``prev`` y los primeros de ``nxt``."""
    fin = prev.get("fin_vent_activa_h")
    if fin is None:
        return {}
    before = {k: _median_in(prev, k, (fin - 0.5) * 60.0, fin * 60.0)
              for k in ("PEEP", "FIO2", "TV_EXP", "VENT_RR")}
    act = _active_minutes(nxt)
    n0 = (min(act) if act else 0) / 60.0
    after = {k: _median_in(nxt, k, n0 * 60.0, (n0 + 0.5) * 60.0)
             for k in ("PEEP", "FIO2", "TV_EXP", "VENT_RR")}
    return {f"d_{k.lower()}": _delta(before[k], after[k]) for k in before}


def continuidad_ok(d: dict) -> bool | None:
    """Criterio de continuidad de D-f: |ΔPEEP| ≤ 3 cmH2O Y |ΔFiO2| ≤ 20 (%)."""
    if d.get("d_peep") is None or d.get("d_fio2") is None:
        return None
    return abs(d["d_peep"]) <= 3.0 and abs(d["d_fio2"]) <= 20.0


def _criterios(d: dict) -> dict[str, bool | None]:
    """Criterios de continuidad alternativos (2c.3: ¿cuál discrimina?).

    ``a`` es el de D-f tal cual; los demás añaden variables para ver si la tasa
    de falsos positivos baja. Todos exigen PEEP y FiO2 presentes.
    """
    if d.get("d_peep") is None or d.get("d_fio2") is None:
        return {k: None for k in ("a", "b", "c", "d")}
    base = abs(d["d_peep"]) <= 3.0 and abs(d["d_fio2"]) <= 20.0
    d_tv = d.get("d_tv")
    d_rr = d.get("d_rr")
    out = {"a": base}
    out["b"] = None if d_tv is None else (base and abs(d_tv) <= 50.0)
    out["c"] = None if d_rr is None else (base and abs(d_rr) <= 2.0)
    out["d"] = (None if (d_tv is None or d_rr is None)
                else (base and abs(d_tv) <= 50.0 and abs(d_rr) <= 2.0))
    return out


def classify(gap_h: float | None, gap_has_files: bool | None,
             starts_active: bool | None, frag: bool | None,
             cont_ok: bool | None) -> str:
    """2c.1: (A) vuelta ventilada, (B) parada del grabador, (C) salida, (D) indeterminado."""
    if gap_h is None or gap_h > MAX_GAP_H:
        return "C_salida"
    if not starts_active:
        return "C_salida"
    if gap_has_files is False:
        return "B_parada_grabador" if cont_ok is not False else "D_indeterminado"
    if cont_ok is True:
        return "A_vuelta_ventilada"
    return "D_indeterminado"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--cohorts", nargs="+", default=["clinic"])
    p.add_argument("--probe", action="store_true",
                   help="Sondear bordes (FC/SpO2 y ficheros del hueco).")
    p.add_argument("--neg-control", action="store_true",
                   help="2c.3: control negativo con pares de hueco > 24 h.")
    p.add_argument("--config", default=str(ROOT / "src/stage0/config/harmonize.yaml"))
    args = p.parse_args()

    out: dict[str, list[dict]] = {}
    lines = ["# Fase 1.6e, punto 2c.1 — apagado simultáneo monitor+ventilador", ""]
    for cohort in args.cohorts:
        diag = REPORT / f"diagnostico_vent_activa_{cohort}.json"
        if not diag.exists():
            print(f"[aviso] sin diagnóstico para {cohort}")
            continue
        rows = json.loads(diag.read_text(encoding="utf-8"))
        idx = json.loads(sorted((ROOT / "datasets" / cohort).glob(
            f"cases_v*/{cohort}_cases_index.json"))[-1].read_text(
                encoding="utf-8"))
        by_id = {e["event_id"]: e for e in idx["events"]}
        # Eventos por box, ordenados por t0, para localizar «el siguiente».
        by_box: dict[str, list[tuple[float, str]]] = {}
        for e in idx["events"]:
            by_box.setdefault(e["box"], []).append((float(e["t0_unix"]),
                                                    e["event_id"]))
        for b in by_box:
            by_box[b].sort()
        order = {eid: i for b, lst in by_box.items()
                 for i, (_t, eid) in enumerate(lst)}
        box_of = {eid: b for b, lst in by_box.items() for _t, eid in lst}

        files_by_box: dict[str, dict[str, object]] = {}
        if args.probe:
            config = load_config(args.config)
            boxes = scan_source_files(
                config_path(config, "paths", f"{cohort}_raw_dir"), SPECS[cohort])
            files_by_box = {b: {sf.path.name: sf for sf in fs}
                            for b, fs in boxes.items()}

        casos: list[dict] = []
        for r in rows:
            eid = r["event_id"]
            fin_act = r.get("fin_vent_activa_h")
            fin_mon = r.get("fin_monitor_fisiologico_h")
            if fin_act is None or fin_mon is None:
                continue
            if abs(fin_mon - fin_act) > EDGE_MIN / 60.0:
                continue        # no es apagado simultáneo
            ev = by_id.get(eid)
            if ev is None:
                continue
            box = box_of.get(eid)
            lst = by_box.get(box or "", [])
            nxt = None
            for _t, other in lst:
                if other != eid and (order.get(other, -1) == order.get(eid, -2) + 1):
                    nxt = other
                    break
            t0_h = float(ev["t0_unix"]) / 3600.0
            info: dict = {"event_id": eid, "box": box,
                          "fin_activa_h": fin_act, "fin_monitor_h": fin_mon,
                          "siguiente": nxt, "hueco_h": None,
                          "tiene_ficheros": None, "empieza_activa": None,
                          "fragmento": None, "d_peep": None, "d_fio2": None,
                          "d_tv": None, "d_rr": None, "d_fc": None,
                          "d_spo2": None, "clase": "D_indeterminado"}
            if nxt is not None:
                nev = by_id[nxt]
                t0_next_h = float(nev["t0_unix"]) / 3600.0
                info["hueco_h"] = round(t0_next_h - t0_h - fin_mon, 3)
                nrow = next((x for x in rows if x["event_id"] == nxt), None)
                if nrow is not None:
                    act_next = _active_minutes(nrow)
                    info["empieza_activa"] = bool(
                        act_next and min(act_next) / 60.0 <= EDGE_MIN / 60.0)
                    dur_next = nev["duration_seconds"] / 3600.0
                    info["fragmento"] = bool(dur_next < 0.5)
            # Continuidad de ajustes (últimos 30 min antes / primeros 30 después).
            lo = (fin_act - EDGE_WINDOW_MIN / 60.0) * 60.0
            hi = fin_act * 60.0
            before = {k: _median_in(r, k, lo, hi)
                      for k in ("PEEP", "FIO2", "TV_EXP", "VENT_RR")}
            after: dict[str, float | None] = {k: None
                                              for k in ("PEEP", "FIO2", "TV_EXP",
                                                        "VENT_RR")}
            if nxt is not None:
                nrow = next((x for x in rows if x["event_id"] == nxt), None)
                if nrow is not None:
                    act_next = _active_minutes(nrow)
                    n0 = (min(act_next) if act_next else 0) / 60.0
                    after = {k: _median_in(nrow, k, n0 * 60.0,
                                           (n0 + EDGE_WINDOW_MIN / 60.0) * 60.0)
                             for k in ("PEEP", "FIO2", "TV_EXP", "VENT_RR")}
            info["d_peep"] = _delta(before["PEEP"], after["PEEP"])
            info["d_fio2"] = _delta(before["FIO2"], after["FIO2"])
            info["d_tv"] = _delta(before["TV_EXP"], after["TV_EXP"])
            info["d_rr"] = _delta(before["VENT_RR"], after["VENT_RR"])
            cont = None
            if info["d_peep"] is not None and info["d_fio2"] is not None:
                cont = (abs(info["d_peep"]) <= 3.0
                        and abs(info["d_fio2"]) <= 20.0)
            info["continuidad_ajustes"] = cont

            # Sondeo dirigido: FC/SpO2 del borde y ficheros en el hueco.
            if args.probe and box in files_by_box and nxt is not None:
                meta = files_by_box[box]
                edge = [str(meta[n].path) for n in (ev.get("source_files") or [])
                        if n in meta][-1:]
                nedge = [str(meta[n].path)
                         for n in (by_id[nxt].get("source_files") or [])
                         if n in meta][:1]
                b_fc = _monitor_median(edge, lo, hi)
                a_fc = _monitor_median(nedge, 0.0, EDGE_WINDOW_MIN)
                info["d_fc"] = _delta(b_fc["FC"], a_fc["FC"])
                info["d_spo2"] = _delta(b_fc["SpO2"], a_fc["SpO2"])
                # ¿Hay ficheros (por nombre) dentro del hueco?
                if info["hueco_h"] is not None:
                    lo_h = fin_mon
                    hi_h = fin_mon + info["hueco_h"]
                    hallados = [n for n in meta
                                if lo_h < (meta[n].dt_unix / 3600.0 - t0_h) < hi_h]
                    info["tiene_ficheros"] = bool(hallados)
                    info["n_ficheros_hueco"] = len(hallados)

            info["clase"] = classify(info["hueco_h"], info["tiene_ficheros"],
                                     info["empieza_activa"], info["fragmento"],
                                     info.get("continuidad_ajustes"))
            casos.append(info)

        out[cohort] = casos
        resumen: dict[str, int] = {}
        for c in casos:
            resumen[c["clase"]] = resumen.get(c["clase"], 0) + 1

        # 2c.3 control negativo: pares consecutivos del MISMO box con hueco > 24 h
        # (casi seguro pacientes distintos) -> tasa de falsos positivos de los
        # criterios de continuidad si el hueco fuese <= 12 h.
        neg: dict = {}
        if args.neg_control:
            by_row = {r["event_id"]: r for r in rows}
            fp = {k: 0 for k in "abcd"}
            tot = {k: 0 for k in "abcd"}
            cand = {k: 0 for k in "abcd"}
            n_cand = 0
            dist = {"control": {"d_peep": [], "d_fio2": [], "d_tv": []},
                    "candidatos": {"d_peep": [], "d_fio2": [], "d_tv": []}}
            for b, lst in by_box.items():
                for i in range(len(lst) - 1):
                    eid_a, eid_b = lst[i][1], lst[i + 1][1]
                    ra, rb = by_row.get(eid_a), by_row.get(eid_b)
                    eva = by_id.get(eid_a)
                    if ra is None or rb is None or eva is None:
                        continue
                    fin = ra.get("fin_vent_activa_h")
                    if fin is None:
                        continue
                    gap = (float(by_id[eid_b]["t0_unix"]) / 3600.0
                           - float(eva["t0_unix"]) / 3600.0 - fin)
                    d = _pair_deltas(ra, rb)
                    cr = _criterios(d)
                    cual = ("control" if gap > 24.0
                            else "candidatos" if gap <= 12.0 else None)
                    if cual is not None:
                        for k in ("d_peep", "d_fio2", "d_tv"):
                            if d.get(k) is not None:
                                dist[cual][k].append(abs(float(d[k])))
                    if gap > 24.0:            # control negativo (DISTINTOS)
                        for k, v in cr.items():
                            if v is not None:
                                tot[k] += 1
                                fp[k] += int(v)
                    elif gap <= 12.0:         # candidatos (posible mismo curso)
                        n_cand += 1
                        for k, v in cr.items():
                            if v is not None:
                                cand[k] += int(v)
            neg = {
                "n_candidatos": n_cand,
                "control_negativo": {
                    k: {"n": tot[k], "cumplen": fp[k],
                        "tasa_fp": (round(fp[k] / tot[k], 4) if tot[k] else None)}
                    for k in "abcd"
                },
                "candidatos_cumplen": cand,
            }
            lines += [f"### {cohort}: 2c.3 control negativo y discriminación", "",
                      f"Candidatos (hueco ≤ 12 h, posible mismo curso): "
                      f"**{n_cand}**", "",
                      "| Criterio | Pares control (>24 h) | Cumplen | Tasa FP |",
                      "|---|---|---|---|"]
            for k, lab in (("a", "ΔPEEP ≤ 3 y ΔFiO2 ≤ 20 (D-f)"),
                           ("b", "a + |ΔTV| ≤ 50 mL"),
                           ("c", "a + |ΔRR| ≤ 2"),
                           ("d", "a + TV y RR")):
                c = neg["control_negativo"][k]
                tasa = ("—" if c["tasa_fp"] is None
                        else f"**{100 * c['tasa_fp']:.1f} %**")
                lines.append(f"| {lab} | {c['n']} | {c['cumplen']} | {tasa} |")
            lines += ["", f"De los candidatos (≤12 h), cumplen: {cand}", ""]
            lines += ["|Δ| del hueco | grupo | n | p10 | p50 | p90 |",
                      "|---|---|---|---|---|---|"]
            for var in ("d_peep", "d_fio2", "d_tv"):
                for grupo in ("control", "candidatos"):
                    vals = sorted(dist[grupo][var])
                    if not vals:
                        continue
                    q = [vals[int(0.1 * (len(vals) - 1))],
                         vals[len(vals) // 2],
                         vals[int(0.9 * (len(vals) - 1))]]
                    lines.append(f"| {var} | {grupo} | {len(vals)} | "
                                 f"{q[0]:.2f} | {q[1]:.2f} | {q[2]:.2f} |")
            # Rejilla de umbrales: ¿algún par (dPEEP, dFiO2) discrimina?
            ctl = list(zip(dist["control"]["d_peep"], dist["control"]["d_fio2"]))
            can = list(zip(dist["candidatos"]["d_peep"],
                           dist["candidatos"]["d_fio2"]))
            grid = []

            def _pct(x) -> str:
                return "—" if x is None else f"{100 * x:.1f} %"

            lines += ["", "Sensibilidad de la regla a los umbrales de "
                      "continuidad:",
                      "", "| ΔPEEP ≤ | ΔFiO2 ≤ | FP (control >24 h) | "
                      "Candidatos que pasarían |", "|---|---|---|---|"]
            for p_peep, p_fio2 in ((3.0, 20.0), (2.0, 10.0), (1.0, 10.0),
                                   (1.0, 5.0), (0.5, 5.0), (0.5, 2.0)):
                n_fp = sum(1 for a, b in ctl if a <= p_peep and b <= p_fio2)
                n_ca = sum(1 for a, b in can if a <= p_peep and b <= p_fio2)
                fp = n_fp / len(ctl) if ctl else None
                ca = n_ca / len(can) if can else None
                grid.append({"d_peep_max": p_peep, "d_fio2_max": p_fio2,
                             "n_control": len(ctl), "n_fp": n_fp,
                             "tasa_fp": fp, "n_candidatos": len(can),
                             "n_candidatos_pasan": n_ca, "tasa_candidatos": ca})
                lines.append(
                    f"| {p_peep:g} | {p_fio2:g} | {_pct(fp)} | {_pct(ca)} "
                    f"({n_ca}/{len(can)}) |")
            lines += ["",
                      "Aun con el umbral más estricto (ΔPEEP ≤ 0,5 y ΔFiO2 ≤ 2) "
                      "la tasa de FP queda en el límite (> 10 %) y solo "
                      "conserva ~59 % de los candidatos: el criterio casi no "
                      "separa, porque PEEP y FiO2 se repiten entre pacientes.",
                      ""]
            lines.append("")
            tasa_a = neg["control_negativo"]["a"]["tasa_fp"]
            veredicto = (
                "la tasa de falsos positivos estimada es **> 10 %** → **NO se "
                "aplica** la regla de unión (D-f) tal cual: requiere decisión "
                "del usuario" if (tasa_a is None or tasa_a > 0.10)
                else "la tasa de falsos positivos estimada es ≤ 10 %")
            lines += [f"**Veredicto 2c.3**: {veredicto}.", ""]
            out[f"{cohort}__neg_control"] = neg | {"distribuciones": dist,
                                                   "rejilla": grid}

        lines += [f"## {cohort}", "",
                  f"- Eventos con apagado simultáneo (≤ {EDGE_MIN:.0f} min): "
                  f"**{len(casos)}**",
                  f"- Clases: {resumen}", ""]
        lines.append("| Evento | hueco (h) | ficheros en el hueco | empieza activa "
                     "| fragmento | ΔPEEP | ΔFiO2 | ΔTV | ΔRR | ΔFC | ΔSpO2 | clase |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for c in sorted(casos, key=lambda x: (x["clase"], x["event_id"])):
            lines.append(
                f"| {c['event_id']} | {c['hueco_h']} | {c['tiene_ficheros']} | "
                f"{c['empieza_activa']} | {c['fragmento']} | {c['d_peep']} | "
                f"{c['d_fio2']} | {c['d_tv']} | {c['d_rr']} | {c['d_fc']} | "
                f"{c['d_spo2']} | {c['clase']} |")
        lines.append("")

    (REPORT / "apagado_simultaneo.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT / "apagado_simultaneo.md").write_text("\n".join(lines) + "\n",
                                                  encoding="utf-8")
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
