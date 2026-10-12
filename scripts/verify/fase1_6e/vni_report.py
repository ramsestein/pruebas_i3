#!/usr/bin/env python3
"""
scripts/verify/fase1_6e/vni_report.py
=====================================
Fase 1.6e — **punto 3**: efecto de la regla de VNI post-extubación (D-b) medido
sobre el diagnóstico de ventilación activa, **sin volver a leer los crudos**.

Para cada evento del JSON de ``diag_vent_activa.py`` (que guarda los minutos de
``TV_EXP``, ``MV_EXP``, ``VENT_RR``, ``AWP_WAV`` y **``TV_INSP``**):

1. se reconstruyen los **tramos de ventilación** (minutos activos separados por
   huecos de >= ``MIN_GAP_MIN``);
2. cada tramo posterior al primero se clasifica con ``vni.classify_vni``:
   - ``vni_confirmada`` si la firma de interfaz ``(TV_INSP − TV_EXP)/TV_INSP >= 0,2``
     se cumple en >= 50 % de los minutos del tramo (firma de TV inspirado);
   - ``posible_vni_no_verificable`` si el tramo no tiene minutos con ``TV_INSP``
     (no hay firma de fuga en ninguna cohorte, así que no se puede verificar);
   - ``interfaz_invasiva``/``sin_desconexion_real`` en el resto.
   El monitor se considera presente en el hueco (``monitor_present=True``) porque
   el hueco cae dentro de la observación fisiológica del evento.

3. **Sensibilidad de la etiqueta**: con la regla, el último tramo confirmado como
   VNI deja de contar como ventilación invasiva, así que el fin de la ventilación
   invasiva es el fin del tramo anterior y la cola fisiológica crece. Se cuenta
   cuántos eventos pasarían de censurados por ``end_of_record`` a éxito
   (cola >= 1 h).

Salida: ``reports/fase1_6e/vni_reporte.json`` / ``.md``.

Uso:
    python scripts/verify/fase1_6e/vni_report.py --cohorts clinic vitaldb
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
)
from src.common.vni import (  # noqa: E402
    MIN_GAP_MIN,
    TV_INSP_FRACTION,
    classify_vni,
)
from src.stage0.io.versioning import load_config  # noqa: E402

REPORT = ROOT / "reports" / "fase1_6e"
MIN_TAIL_H = 1.0    # D3: cola de monitor sin ventilador para confirmar extubación


def _active_minutes(row: dict) -> set[int]:
    """Minutos con ventilación ACTIVA (mismos criterios que el punto 2)."""
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


def _segments(minutes: set[int], gap_min: float) -> list[tuple[int, int]]:
    """Tramos ``(primer_minuto, ultimo_minuto)`` separados por huecos > gap."""
    ms = sorted(minutes)
    if not ms:
        return []
    out: list[list[int]] = [[ms[0], ms[0]]]
    for m in ms[1:]:
        if m - out[-1][1] > gap_min:
            out.append([m, m])
        else:
            out[-1][1] = m
    return [(a, b) for a, b in out]


def _tv_insp_fraction(row: dict, lo: int, hi: int) -> float | None:
    """``(TV_INSP − TV_EXP)/TV_INSP >= 0,2`` en los minutos ``[lo, hi]`` del tramo."""
    per = row.get("per_min") or {}
    ti = per.get("TV_INSP") or {}
    tv = per.get("TV_EXP") or {}
    common = [k for k in ti
              if lo <= int(k) <= hi and float(ti[k]) > 0.0 and k in tv]
    if not common:
        return None
    ok = sum(1 for k in common
             if (float(ti[k]) - float(tv[k])) / float(ti[k])
             >= TV_INSP_FRACTION - 1e-9)
    return ok / len(common)


def make_mode_probe(cohort: str, by_id: dict, boxes: dict):
    """``f(event_id, lo_h, hi_h) -> bool | None``: firma de MODO del tramo.

    Sondea **solo** los ficheros que solapan el tramo (por hora de nombre, que en
    las cohortes de señal es una selección de partida suficiente para saber si el
    aparato declara una modalidad invasiva): devuelve ``False`` si algún fichero
    declara modalidad invasiva (``SET_SIMV``), ``True`` si todos declaran no
    invasiva y ``None`` si ningún fichero trae pista de modo.
    """
    files_by_box = {b: {sf.path.name: sf for sf in fs} for b, fs in boxes.items()}
    cache: dict[str, bool | None] = {}

    def probe(event_id: str, lo_h: float, hi_h: float) -> bool | None:
        ev = by_id.get(event_id)
        if ev is None:
            return None
        t0_h = float(ev["t0_unix"]) / 3600.0
        modes: list[bool] = []
        for n in dict.fromkeys(ev.get("source_files") or []):
            sf = files_by_box.get(ev.get("box"), {}).get(n)
            if sf is None:
                continue
            f_h = sf.dt_unix / 3600.0 - t0_h
            if f_h + 1.0 < lo_h - 2.0 or f_h > hi_h + 2.0:
                continue
            key = str(sf.path)
            if key not in cache:
                pr = probe_vital_file(sf.path)
                cache[key] = None if pr is None else pr.mode_is_niv
            if cache[key] is not None:
                modes.append(bool(cache[key]))
        return None if not modes else all(modes)

    return probe


def analyse(rows: list[dict], n_afectados: int, mode_of=None) -> dict:
    afc = rows[:n_afectados]
    eventos_vni: list[dict] = []
    n_vni = n_unver = n_inv = n_nodisc = 0
    n_por_modo = n_por_tv_insp = 0
    recuperados: list[str] = []
    for r in afc:
        active = _active_minutes(r)
        segs = _segments(active, MIN_GAP_MIN)
        vni_segs: list[tuple[int, int]] = []
        unver_segs: list[tuple[int, int]] = []
        modo_tramo: bool | None = None
        for i, (lo, hi) in enumerate(segs):
            if i == 0:
                continue
            prev_hi = segs[i - 1][1]
            frac = _tv_insp_fraction(r, lo, hi)
            if mode_of is not None and modo_tramo is None:
                modo_tramo = mode_of(r["event_id"], float(lo) / 60.0,
                                     float(hi) / 60.0)
            dec = classify_vni(
                prev_end_min=float(prev_hi + 1),
                next_start_min=float(lo),
                monitor_present=True,
                tv_insp_frac=frac,
                mode_is_niv=modo_tramo,
                min_gap_min=MIN_GAP_MIN,
            )
            if dec.verifiable:
                if modo_tramo is not None:
                    n_por_modo += 1
                else:
                    n_por_tv_insp += 1
            if dec.is_vni:
                vni_segs.append((lo, hi))
                n_vni += 1
            elif dec.verifiable:
                n_inv += 1
            else:
                unver_segs.append((lo, hi))
                n_unver += 1
        if len(segs) > 1 and not vni_segs and not unver_segs:
            n_nodisc += 1
        if not vni_segs and not unver_segs:
            continue
        # Sensibilidad: si el PRIMER tramo confirmado como VNI (o no verificable,
        # que NO se reclasifica) cierra la ventilación invasiva, la cola crece.
        obs_end = r.get("fin_monitor_fisiologico_h")
        inv_end_min = (vni_segs[0][0] - 1) if vni_segs else None
        cola = None
        if obs_end is not None and inv_end_min is not None:
            cola = float(obs_end) - (inv_end_min + 1) / 60.0
        info = {
            "event_id": r["event_id"],
            "n_tramos": len(segs),
            "vni_confirmados": len(vni_segs),
            "no_verificables": len(unver_segs),
            "reclasificado": bool(vni_segs),
            "tv_insp_frac_primer_tramo": (
                None if not (vni_segs or unver_segs)
                else _tv_insp_fraction(r, *(vni_segs or unver_segs)[0])
            ),
            # Sin reclasificación la cola es la de siempre: solo cambia (crece)
            # cuando el tramo posterior queda confirmado como VNI.
            "cola_con_regla_h": (round(cola, 3) if cola is not None
                                 else r.get("cola_nueva_h")),
            "cola_sin_regla_h": r.get("cola_nueva_h"),
        }
        eventos_vni.append(info)
        if cola is not None and cola >= MIN_TAIL_H and (
                r.get("cola_nueva_h") is None or r["cola_nueva_h"] < MIN_TAIL_H):
            recuperados.append(r["event_id"])
    return {
        "n_eventos": len(afc),
        "n_con_tramos_multiples": sum(1 for e in eventos_vni if e["n_tramos"] > 1),
        "n_vni_confirmados": n_vni,
        "n_no_verificables": n_unver,
        "n_invasivos_por_firma": n_inv,
        "n_tramos_decididos_por_modo": n_por_modo,
        "n_tramos_decididos_por_tv_insp": n_por_tv_insp,
        "nota_modo": (
            "**Nota sobre la pista de modo**: en Clínic **no hay** pista de modo "
            "(ver el inventario), así que todos los tramos con firma verificable "
            "se deciden por `TV_INSP`; en VitalDB sí existe `SET_SIMV` (SIMV = "
            "modalidad INVASIVA) y manda el modo cuando está presente: "
            f"{n_por_modo} tramos por modo y {n_por_tv_insp} por `TV_INSP`."
        ),
        "n_eventos_con_vni": sum(1 for e in eventos_vni
                                 if e["vni_confirmados"] > 0),
        "n_eventos_no_verificables": sum(1 for e in eventos_vni
                                         if e["no_verificables"] > 0),
        "recuperados_por_la_regla": recuperados,
        "n_recuperados_por_la_regla": len(recuperados),
        "detalle": eventos_vni,
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--cohorts", nargs="+", default=["clinic", "vitaldb"])
    p.add_argument("--n-afectados", type=int, default=None)
    p.add_argument("--with-mode", action="store_true",
                   help="Sondear la pista de MODO (SET_SIMV) solo en los "
                        "ficheros de los tramos candidatos (lectura dirigida).")
    p.add_argument("--config", default=str(ROOT / "src/stage0/config/harmonize.yaml"))
    args = p.parse_args()

    out: dict[str, dict] = {}
    lines = ["# Fase 1.6e, punto 3 — efecto de la regla de VNI post-extubación (D-b)",
             ""]
    for cohort in args.cohorts:
        path = REPORT / f"diagnostico_vent_activa_{cohort}.json"
        if not path.exists():
            print(f"[aviso] sin diagnóstico para {cohort}")
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        n_afc = args.n_afectados or sum(
            1 for r in rows if r.get("cola_nueva_h") is not None
            or r.get("fin_vent_activa_h") is None)
        n_afc = min(n_afc, len(rows))
        mode_of = None
        if args.with_mode:
            config = load_config(args.config)
            from src.create_dataset.build_signal_cases import (  # noqa: E402
                SPECS, scan_source_files)
            boxes = scan_source_files(
                config_path(config, "paths", f"{cohort}_raw_dir"), SPECS[cohort])
            idx = json.loads(sorted((ROOT / "datasets" / cohort).glob(
                f"cases_v*/{cohort}_cases_index.json"))[-1].read_text(
                    encoding="utf-8"))
            by_id = {e["event_id"]: e for e in idx["events"]}
            mode_of = make_mode_probe(cohort, by_id, boxes)
            print(f"[{cohort}] sondeando la pista de modo en los tramos…",
                  flush=True)
        res = analyse(rows, n_afc, mode_of)
        out[cohort] = res
        lines += [
            f"## {cohort}", "",
            f"- Eventos analizados: **{res['n_eventos']}** (afectados)",
            f"- Tramos de VNI **confirmados** (reclasificados): "
            f"**{res['n_vni_confirmados']}** en "
            f"{res['n_eventos_con_vni']} eventos",
            f"- Tramos **no verificables** (`posible_vni_no_verificable`, NO se "
            f"reclasifican): **{res['n_no_verificables']}** en "
            f"{res['n_eventos_no_verificables']} eventos",
            f"- Tramos posteriores con firma **verificable** que resulta "
            f"**invasiva**: **{res['n_invasivos_por_firma']}**",
            "  (esta medida NO puede usar la pista de modo: la firma se calcula "
            "con `TV_INSP`; ver la nota de modo más abajo)",
            f"- **Sensibilidad de la etiqueta**: eventos que pasarían de censura "
            f"(`end_of_record`) a éxito con la regla: "
            f"**{res['n_recuperados_por_la_regla']}**",
            "",
            res["nota_modo"],
            "",
        ]
        for e in res["detalle"]:
            if e["vni_confirmados"] or e["no_verificables"]:
                lines.append(
                    f"  - `{e['event_id']}`: tramos {e['n_tramos']}, VNI "
                    f"{e['vni_confirmados']}, no verificables "
                    f"{e['no_verificables']}, firma TV_INSP "
                    f"{e['tv_insp_frac_primer_tramo']}, cola con regla "
                    f"{e['cola_con_regla_h']} h (sin regla "
                    f"{e['cola_sin_regla_h']} h)")
        lines.append("")

    (REPORT / "vni_reporte.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT / "vni_reporte.md").write_text("\n".join(lines) + "\n",
                                           encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
