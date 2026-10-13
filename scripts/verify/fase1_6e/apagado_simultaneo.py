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
import math
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
PROBE_MAX = 4          # cabeceras que se leen por evento al confirmar el hueco


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


def _span_real(sf, t0_h: float) -> tuple[float, float]:
    """Intervalo REAL de un fichero en horas relativas a ``t0_h``.

    Cabecera cuando se puede leer (``dtstart``/``dtend``); si no, hora del
    nombre + 1 h (B2: en Clínic el nombre va por detrás del contenido).
    """
    pr = probe_vital_file(sf.path)
    if pr is not None and float(pr.dtend) > float(pr.dtstart):
        return (float(pr.dtstart) / 3600.0 - t0_h,
                float(pr.dtend) / 3600.0 - t0_h)
    return (sf.dt_unix / 3600.0 - t0_h, sf.dt_unix / 3600.0 - t0_h + 1.0)


def _median_in(row: dict, track: str, lo_min: float, hi_min: float) -> float | None:
    d = (row.get("per_min") or {}).get(track) or {}
    vals = [float(v) for k, v in d.items() if lo_min <= float(k) <= hi_min]
    return float(np.median(vals)) if vals else None


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(b - a, 3)


def _monitor_median(paths: list[str], lo_min: float, hi_min: float,
                    t0_unix: float) -> dict[str, float | None]:
    """Mediana de FC y SpO2 en ``[lo_min, hi_min]`` (minutos desde t0 del evento).

    OJO: hay que pasar ``t0_unix`` para que las series vengan en segundos desde
    t0; sin él ``read_track_series`` las devuelve en epoch y la ventana no casa
    (el error salió como ΔFC/ΔSpO2 = None en todas las filas del informe).
    """
    out: dict[str, float | None] = {"FC": None, "SpO2": None}
    if not paths:
        return out
    series = read_track_series(paths, list(MONITOR), t0_unix=t0_unix)
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
    """Δ de ajustes entre los últimos 30 min de ``prev`` y los primeros de ``nxt``.

    OJO con las CLAVES: deben ser ``d_peep``/``d_fio2``/``d_tv``/``d_rr``, que son
    las que consume `_criterios`. Antes se derivaban del nombre de la variable
    (``d_tv_exp``, ``d_vent_rr``) y por eso las variantes b/c/d salian siempre 0
    pares ("no evaluadas" sin serlo).
    """
    alias = {"PEEP": "d_peep", "FIO2": "d_fio2", "TV_EXP": "d_tv",
             "VENT_RR": "d_rr"}
    fin = prev.get("fin_vent_activa_h")
    if fin is None:
        return {}
    before = {k: _median_in(prev, k, (fin - 0.5) * 60.0, fin * 60.0)
              for k in ("PEEP", "FIO2", "TV_EXP", "VENT_RR")}
    act = _active_minutes(nxt)
    n0 = (min(act) if act else 0) / 60.0
    after = {k: _median_in(nxt, k, n0 * 60.0, (n0 + 0.5) * 60.0)
             for k in ("PEEP", "FIO2", "TV_EXP", "VENT_RR")}
    return {alias[k]: _delta(before[k], after[k]) for k in before}


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


def _seccion_union(cohort: str, casos: list[dict], *, gap_max_h: float = 2.0,
                   return_min: float = 15.0) -> list[str]:
    """Seccion del informe: pares que cumplen la regla ESTRECHA de union (2c.2).

    Es la que aplica el builder (`join_courses`): (a) hueco <= ``gap_max_h``,
    (b) sin ficheros en el hueco, (c) la region siguiente empieza ventilada. El
    criterio de continuidad de ajustes se descarto por el control negativo de
    2c.3 (59,3 % de falsos positivos).
    """
    def _ok_hueco(c: dict) -> bool:
        return c.get("hueco_h") is not None and c["hueco_h"] <= gap_max_h

    def _sin_ficheros(c: dict) -> bool:
        # `None` = no sondado: no se puede afirmar que no haya ficheros.
        return c.get("tiene_ficheros") is False

    cumplen = [c for c in casos
               if _ok_hueco(c) and _sin_ficheros(c) and c["empieza_activa"]]
    solo_hueco = [c for c in casos
                  if _ok_hueco(c) and _sin_ficheros(c)
                  and not c["empieza_activa"]]
    sin_sonda = [c for c in casos if c.get("tiene_ficheros") is None]
    lines = ["### Union de cursos (regla estrecha, D-f)", "",
             f"- Cumplen (a) hueco <= {gap_max_h:g} h, (b) sin ficheros en el "
             f"hueco y (c) la region siguiente empieza ventilada: "
             f"**{len(cumplen)}**",
             f"- Descartados por (c) (no vuelve ventilada en <= "
             f"{return_min:g} min): {len(solo_hueco)}",
             f"- Sin sondeo (`tiene_ficheros = None`): {len(sin_sonda)} "
             f"(sin leer el hueco la regla no se puede evaluar)", ""]
    # Variantes: que pasaria si se aceptara tambien el hueco CON grabacion pero
    # sin senal de paciente (el box sigue grabando y el paciente esta fuera) y/o
    # si se ampliara el limite de hueco. Solo se cuentan pares A (vuelve
    # ventilado): la unica evidencia de "mismo curso" que tenemos.
    variantes = []
    for gmax in (gap_max_h, 6.0, 12.0):
        con_senal = [c for c in casos if c["empieza_activa"]
                     and c.get("hueco_h") is not None
                     and c["hueco_h"] <= gmax]
        variantes.append((gmax, len(con_senal)))
    lines += ["| Variante | Uniones en esta cohorte |", "|---|---|",
              f"| Aplicada: sin ficheros y <= {gap_max_h:g} h | {len(cumplen)} |"]
    for gmax, n in variantes[1:]:
        lines.append(f"| Tambien con grabacion (box on, paciente fuera) y "
                     f"<= {gmax:g} h | {n} |")
    lines += ["",
              f"**Conclusion ({cohort})**: se mantiene la regla ESTRICTA. " +
              (f"Se unen **{len(cumplen)}** parejas de {cohort} "
               f"(grabador parado y hueco <= {gap_max_h:g} h)." if cumplen else
               f"En {cohort} no hay ninguna pareja con grabador parado y hueco "
               f"<= {gap_max_h:g} h (la unica candidata de ese tipo tiene un "
               f"hueco mayor), asi que no se une ninguna y los apagados "
               f"simultaneos quedan censurados como hasta ahora "
               f"(`end_of_record`).") +
              " Las variantes de la tabla son informativas, no se aplican. "
              f"Total de eventos con apagado simultaneo en {cohort}: "
              f"{len(casos)}.", ""]
    if cumplen:
        lines += ["| Evento (primero del par) | hueco (h) | dPEEP | dFiO2 | "
                  "dFC | dSpO2 |", "|---|---|---|---|---|---|"]
        for c in sorted(cumplen, key=lambda x: x["event_id"]):
            lines.append(
                f"| {c['event_id']} | {c['hueco_h']} | {c['d_peep']} | "
                f"{c['d_fio2']} | {c['d_fc']} | {c['d_spo2']} |")
        lines.append("")
    return lines


def _bloque_cohort(cohort: str, casos: list[dict],
                   nota: str | None = None) -> list[str]:
    """Bloque del informe de una cohorte (clases, union y tabla de casos)."""
    resumen: dict[str, int] = {}
    for c in casos:
        resumen[c["clase"]] = resumen.get(c["clase"], 0) + 1
    out = [f"## {cohort}", ""]
    if nota:
        out += [nota, ""]
    out += [
        f"- Eventos con apagado simultaneo (<= {EDGE_MIN:.0f} min): "
        f"**{len(casos)}**",
        f"- Clases: {resumen}", ""]
    out += _seccion_union(cohort, casos)
    out += ["| Evento | hueco (h) | ficheros en el hueco | empieza activa | "
            "fragmento | dPEEP | dFiO2 | dTV | dRR | dFC | dSpO2 | clase |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in sorted(casos, key=lambda x: (x["clase"], x["event_id"])):
        out.append(
            f"| {c['event_id']} | {c['hueco_h']} | {c['tiene_ficheros']} | "
            f"{c['empieza_activa']} | {c['fragmento']} | {c['d_peep']} | "
            f"{c['d_fio2']} | {c['d_tv']} | {c['d_rr']} | {c['d_fc']} | "
            f"{c['d_spo2']} | {c['clase']} |")
    out.append("")
    return out


def _solo_informe() -> int:
    """Rehace el informe completo desde el JSON (sin volver a sondear D:)."""
    path = REPORT / "apagado_simultaneo.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    md_path = REPORT / "apagado_simultaneo.md"
    md = ["# Fase 1.6e, punto 2c.1 — apagado simultáneo monitor+ventilador", ""]
    for cohort in sorted(k for k in data if not k.endswith("__neg_control")):
        neg = data.get(f"{cohort}__neg_control") or {}
        nota = ("**PRELIMINAR (B1)**: la base temporal del indice v0.4.0 de "
                "VitalDB mezcla dos relojes (nombre y contenido); estas horas "
                "se recalculan en el rebuild v0.6.0. En Clinic NO ocurre."
                if cohort == "vitaldb" else None)
        md += _seccion_neg(cohort, neg)
        md += _bloque_cohort(cohort, data[cohort], nota=nota)
    md += ["## Notas de interpretacion", "",
           "1. **TV y FR SI se evaluan, y discriminan mucho mejor que los "
           "ajustes.** Las variantes b/c/d salian 0 pares por un fallo de nombres "
           "de clave en `_pair_deltas` (generaba `d_tv_exp`/`d_vent_rr` y el "
           "criterio buscaba `d_tv`/`d_rr`), no porque no hubiera datos: ya estan "
           "calculadas con su IC de Wilson. Con las 27 parejas de control:", "",
           "   - a) PEEP y FiO2: **59,3 %** [40,7 %, 75,5 %] de falsos positivos "
           "- no discrimina (son ajustes que se repiten entre pacientes).", "",
           "   - b) a + |dTV| <= 50 mL: **22,2 %** [10,6 %, 40,8 %].", "",
           "   - c) a + |dRR| <= 2: **11,1 %** [3,9 %, 28,1 %].", "",
           "   - d) a + TV y FR: **3,7 %** [0,7 %, 18,3 %]. El TV y la FR medidos "
           "son propios de cada paciente, y anadirlos baja el FP de 59 % a 3,7 %.", "",
           "   **Consecuencia**: el punto de parada de 2c.3 (> 10 % de FP) se "
           "basaba en la variante a. La variante d queda por debajo en la "
           "estimacion puntual (limite superior 18,3 %). Aplicarla uniria 21 de "
           "las 45 parejas candidatas de Clinic. **Requiere decision del "
           "usuario**: cambiar la regla implica rehacer el rebuild v0.6.0, que ya "
           "esta aplicando la regla estricta.", "",
           "   (La fila con 0 pares de control se imprime como **no evaluado**, "
           "nunca como \"0 %\".)", "",
           "2. **La clase A/B/C/D tiene dos cifras porque son dos criterios "
           "distintos**, no porque haya un error:", "",
           "   - El diagnostico (con lectura de ficheros) da **A = 6** entre los "
           "135 eventos que puede analizar, con la continuidad de ajustes "
           "(PEEP/FiO2) evaluada.", "",
           "   - La anotacion del indice da **A = 20** con el criterio "
           "aproximado (solo senal) y **A = 0** si no se sondea (cae a D, "
           "conservador). El indice no puede evaluar la continuidad de ajustes "
           "sin `--probar`.", "",
           "   - Ademas, `simultaneous_shutdown` del builder compara con el fin "
           "de la REGION, mientras 2c.1 usa el fin de la observacion "
           "FISIOLOGICA. Con el test de 2c.1 (ya implementado en "
           "`annotate_index.py`) los pares pasan de 36 a 106.", "",
           "   **Definicion unica**: la de 2c.1, aplicada con `--probar` sobre el "
           "indice v0.6.0 (misma base que el diagnostico que se corra sobre ese "
           "indice). Hasta esa anotacion, `prev_exit_class` debe leerse como "
           "aproximado.", "",
           "3. **`next_return_ventilated_30min` es casi siempre cierto** (168 de "
           "181 en Clinic): los pacientes llegan intubados de quirofano. Por si "
           "solo no informa. El campo util es el COMBINADO "
           "`next_return_ventilated_12h` (68 de 181), que exige ademas hueco "
           "<= 12 h, y es la base de la columna (c) de la tabla final.", ""]
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"Informe actualizado: {md_path}")
    return 0


def _recalc_fc(cohorts: list[str], config_path_str: str) -> int:
    """Recalcula SOLO ΔFC/ΔSpO2 de los bordes y refresca el JSON y el informe.

    Leer cabeceras/pistas de D: es caro, así que no se repite el sondeo de
    huecos: se reaprovecha lo ya guardado en ``apagado_simultaneo.json``.
    """
    path = REPORT / "apagado_simultaneo.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    config = load_config(config_path_str)
    for cohort in cohorts:
        casos = data.get(cohort)
        if not casos:
            continue
        rows = {r["event_id"]: r
                for r in json.loads(
                    (REPORT / f"diagnostico_vent_activa_{cohort}.json")
                    .read_text(encoding="utf-8"))}
        idx = json.loads(sorted((ROOT / "datasets" / cohort).glob(
            f"cases_v*/{cohort}_cases_index.json"))[-1].read_text(
                encoding="utf-8"))
        by_id = {e["event_id"]: e for e in idx["events"]}
        boxes = scan_source_files(
            config_path(config, "paths", f"{cohort}_raw_dir"), SPECS[cohort])
        files_by_box = {b: {sf.path.name: sf for sf in fs}
                        for b, fs in boxes.items()}
        for c in casos:
            nxt = c.get("siguiente")
            ev = by_id.get(c["event_id"])
            meta = files_by_box.get(c.get("box") or "")
            if nxt is None or ev is None or not meta:
                continue
            t0_h = float(ev["t0_unix"]) / 3600.0
            t0_next_h = float(by_id[nxt]["t0_unix"]) / 3600.0
            r = rows.get(c["event_id"])
            if r is None:
                continue
            fin_act = r.get("fin_vent_activa_h")
            if fin_act is None:
                continue
            lo = (fin_act - EDGE_WINDOW_MIN / 60.0) * 60.0
            hi = fin_act * 60.0
            edge = [str(meta[n].path) for n in (ev.get("source_files") or [])
                    if n in meta][-1:]
            nedge = [str(meta[n].path)
                     for n in (by_id[nxt].get("source_files") or [])
                     if n in meta][:1]
            b_fc = _monitor_median(edge, lo, hi, t0_h * 3600.0)
            a_fc = _monitor_median(nedge, 0.0, EDGE_WINDOW_MIN,
                                   t0_next_h * 3600.0)
            c["d_fc"] = _delta(b_fc["FC"], a_fc["FC"])
            c["d_spo2"] = _delta(b_fc["SpO2"], a_fc["SpO2"])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    n_fc = sum(1 for k, v in data.items() if not k.endswith("__neg_control")
               for c in v if c.get("d_fc") is not None)
    n_spo2 = sum(1 for k, v in data.items() if not k.endswith("__neg_control")
                 for c in v if c.get("d_spo2") is not None)
    print(f"Casos con ΔFC medido: {n_fc} · con ΔSpO2: {n_spo2}")
    return _solo_informe()


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Intervalo de confianza del 95 % de Wilson para ``k`` de ``n``.

    Se usa Wilson (y no el normal) porque con n pequeno y proporciones cerca de
    0 o 1 el intervalo normal da limites imposibles.
    """
    if n <= 0:
        return None
    p = k / n
    den = 1.0 + z * z / n
    centro = (p + z * z / (2 * n)) / den
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / den
    return (max(0.0, centro - half), min(1.0, centro + half))


def _pct(x: float | None, dec: int = 1) -> str:
    return "—" if x is None else f"{100 * x:.{dec}f} %"


def _seccion_neg(cohort: str, neg: dict) -> list[str]:
    """Tabla del control negativo de 2c.3 con IC 95 % de Wilson (desde JSON)."""
    cn = neg.get("control_negativo") or {}
    if not cn:
        return []
    out = [f"### {cohort}: 2c.3 control negativo y discriminacion", "",
           f"Candidatos (hueco <= 12 h, posible mismo curso): "
           f"**{neg.get('n_candidatos')}**", "",
           "| Criterio | Pares control (>24 h) | Cumplen | Tasa FP | "
           "IC 95 % (Wilson) |",
           "|---|---|---|---|---|"]
    for k, lab in (("a", "dPEEP <= 3 y dFiO2 <= 20 (D-f)"),
                   ("b", "a + |dTV| <= 50 mL"),
                   ("c", "a + |dRR| <= 2"),
                   ("d", "a + TV y RR")):
        c = cn.get(k) or {}
        n = int(c.get("n") or 0)
        kk = int(c.get("cumplen") or 0)
        if n == 0:
            # 0 pares NO es "0 % de falsos positivos": es que la variable no se
            # pudo medir en las ventanas de +-30 min de los pares de control.
            out.append(f"| {lab} | 0 | — | **no evaluado** | — |")
            continue
        ic = _wilson(kk, n)
        ic_txt = "—" if ic is None else f"[{_pct(ic[0])}, {_pct(ic[1])}]"
        out.append(f"| {lab} | {n} | {kk} | {_pct(c.get('tasa_fp'))} | "
                   f"{ic_txt} |")
    if any(int((cn.get(k) or {}).get("n") or 0) == 0 for k in "bcd"):
        out += ["",
                "**No evaluado**: en los pares de control no hay valores de "
                "TV_EXP/VENT_RR en las ventanas de ±30 min, asi que las filas "
                "b/c/d NO se han podido juzgar (no es que salgan 0 %). La "
                "conclusion de que la continuidad no discrimina esta demostrada "
                "**solo para PEEP y FiO2**, que son AJUSTES y se repiten entre "
                "pacientes; el TV y la FR medidos son propios de cada paciente y "
                "podrian discriminar mejor. No cambia la decision (regla "
                "estricta), pero el criterio queda marcado como no evaluado.", ""]
    cumplen_cand = neg.get("candidatos_cumplen") or {}
    out += ["", f"De los candidatos (<= 12 h), cumplen: {cumplen_cand}", ""]
    dist = neg.get("distribuciones") or {}
    if dist:
        out += ["|Δ| del hueco | grupo | n | p10 | p50 | p90 |",
                "|---|---|---|---|---|---|"]
        for var in ("d_peep", "d_fio2", "d_tv", "d_rr"):
            for grupo in ("control", "candidatos"):
                vals = sorted(float(x) for x in
                              (dist.get(grupo, {}).get(var) or []))
                if not vals:
                    continue
                q = [vals[int(0.1 * (len(vals) - 1))], vals[len(vals) // 2],
                     vals[int(0.9 * (len(vals) - 1))]]
                out.append(f"| {var} | {grupo} | {len(vals)} | {q[0]:.2f} | "
                           f"{q[1]:.2f} | {q[2]:.2f} |")
        out.append("")
    grid = neg.get("rejilla") or []
    if grid:
        out += ["Sensibilidad de la regla a los umbrales de continuidad:", "",
                "| dPEEP <= | dFiO2 <= | FP (control >24 h) | IC 95 % (Wilson) | "
                "Candidatos que pasarian |", "|---|---|---|---|---|"]
        for g in grid:
            ic = _wilson(int(g["n_fp"]), int(g["n_control"]))
            ic_txt = ("—" if ic is None
                      else f"[{_pct(ic[0])}, {_pct(ic[1])}]")
            out.append(f"| {g['d_peep_max']:g} | {g['d_fio2_max']:g} | "
                       f"{_pct(g.get('tasa_fp'))} | {ic_txt} | "
                       f"{_pct(g.get('tasa_candidatos'))} "
                       f"({g.get('n_candidatos_pasan')}/"
                       f"{g.get('n_candidatos')}) |")
        out += ["",
                "Aun con el umbral mas estricto (dPEEP <= 0,5 y dFiO2 <= 2) la "
                "tasa de FP queda en el limite (> 10 %) y solo conserva ~59 % de "
                "los candidatos: el criterio casi no separa, porque PEEP y FiO2 "
                "se repiten entre pacientes.", ""]
    tasa_a = ((cn.get("a") or {}).get("tasa_fp"))
    veredicto = (
        "la tasa de falsos positivos estimada es **> 10 %** → **NO se aplica** "
        "la regla de union (D-f) tal cual" if (tasa_a is None or tasa_a > 0.10)
        else "la tasa de falsos positivos estimada es <= 10 %")
    out += [f"**Veredicto 2c.3**: {veredicto}.", ""]
    return out


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
    p.add_argument("--solo-informe", action="store_true",
                   help="Solo reescribe el informe desde el JSON.")
    p.add_argument("--fc", action="store_true",
                   help="Recalcula solo ΔFC/ΔSpO2 de los bordes y refresca.")
    p.add_argument("--config", default=str(ROOT / "src/stage0/config/harmonize.yaml"))
    args = p.parse_args()
    if args.fc:
        raise SystemExit(_recalc_fc(args.cohorts, args.config))
    if args.solo_informe:
        raise SystemExit(_solo_informe())

    out: dict[str, list[dict]] = {}
    lines = ["# Fase 1.6e, punto 2c.1 — apagado simultáneo monitor+ventilador", ""]
    # Lo ya sondeado (leer D: es caro) se conserva si esta ejecucion no pide
    # `--probe`: antes, rehacer los casos sin el borraba `tiene_ficheros` y
    # dFC/dSpO2 de los casos ya medidos.
    json_path = REPORT / "apagado_simultaneo.json"
    previo: dict = (json.loads(json_path.read_text(encoding="utf-8"))
                    if json_path.exists() else {})
    previo_casos: dict[str, dict[str, dict]] = {
        k: {c["event_id"]: c for c in v}
        for k, v in previo.items()
        if not k.endswith("__neg_control") and isinstance(v, list)
    }
    CAMPOS_SONDA = ("tiene_ficheros", "n_ficheros_hueco", "n_candidatos_hueco",
                    "hueco_dudoso", "d_fc", "d_spo2")
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
            t0_next_h: float | None = None
            nrow: dict | None = None
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
                b_fc = _monitor_median(edge, lo, hi, t0_h * 3600.0)
                a_fc = _monitor_median(nedge, 0.0, EDGE_WINDOW_MIN,
                                       (t0_next_h or t0_h) * 3600.0)
                info["d_fc"] = _delta(b_fc["FC"], a_fc["FC"])
                info["d_spo2"] = _delta(b_fc["SpO2"], a_fc["SpO2"])
                # ¿Hay ficheros dentro del hueco? El nombre no es fiable en
                # Clínic (va 1-2 h por detrás del contenido, B2), así que los
                # candidatos por nombre (±3 h) se confirman con la cabecera,
                # limitados a PROBE_MAX por evento (leer cabeceras de D: es
                # caro). Si quedan candidatos sin sondear y ninguno confirmó,
                # se marca "con ficheros" (conservador: la clase B permite
                # unir sin ajustes, así que no se declara B por duda).
                if info["hueco_h"] is not None:
                    lo_h = fin_mon
                    hi_h = fin_mon + info["hueco_h"]
                    cand = [n for n in meta
                            if lo_h - 3.0 < (meta[n].dt_unix / 3600.0 - t0_h)
                            < hi_h + 3.0]
                    reales = []
                    for n in cand[:PROBE_MAX]:
                        span = _span_real(meta[n], t0_h)
                        if span[0] < hi_h and span[1] > lo_h:
                            reales.append(n)
                    duda = bool(reales) is False and len(cand) > PROBE_MAX
                    info["tiene_ficheros"] = bool(reales) or duda
                    info["n_ficheros_hueco"] = len(reales)
                    info["n_candidatos_hueco"] = len(cand)
                    info["hueco_dudoso"] = duda

            if not args.probe:
                viejo = previo_casos.get(cohort, {}).get(eid) or {}
                for k in CAMPOS_SONDA:
                    if k in viejo:
                        info[k] = viejo[k]

            info["clase"] = classify(info["hueco_h"], info["tiene_ficheros"],
                                     info["empieza_activa"], info["fragmento"],
                                     info.get("continuidad_ajustes"))
            casos.append(info)

        out[cohort] = casos
        resumen: dict[str, int] = {}
        for c in casos:
            resumen[c["clase"]] = resumen.get(c["clase"], 0) + 1
        n0 = len(lines)

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
            dist = {"control": {"d_peep": [], "d_fio2": [], "d_tv": [],
                               "d_rr": []},
                    "candidatos": {"d_peep": [], "d_fio2": [], "d_tv": [],
                                   "d_rr": []}}
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
                        for k in ("d_peep", "d_fio2", "d_tv", "d_rr"):
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
            # Rejilla de umbrales: ¿algun par (dPEEP, dFiO2) discrimina? Se
            # guarda en el JSON y la tabla la pinta `_seccion_neg`.
            ctl = list(zip(dist["control"]["d_peep"], dist["control"]["d_fio2"]))
            can = list(zip(dist["candidatos"]["d_peep"],
                           dist["candidatos"]["d_fio2"]))
            grid = []
            for p_peep, p_fio2 in ((3.0, 20.0), (2.0, 10.0), (1.0, 10.0),
                                   (1.0, 5.0), (0.5, 5.0), (0.5, 2.0)):
                n_fp = sum(1 for a, b in ctl if a <= p_peep and b <= p_fio2)
                n_ca = sum(1 for a, b in can if a <= p_peep and b <= p_fio2)
                grid.append({"d_peep_max": p_peep, "d_fio2_max": p_fio2,
                             "n_control": len(ctl), "n_fp": n_fp,
                             "tasa_fp": (n_fp / len(ctl) if ctl else None),
                             "n_candidatos": len(can),
                             "n_candidatos_pasan": n_ca,
                             "tasa_candidatos": (n_ca / len(can) if can else None)})
            lines += _seccion_neg(cohort, neg | {"distribuciones": dist,
                                                 "rejilla": grid})
            out[f"{cohort}__neg_control"] = neg | {"distribuciones": dist,
                                                   "rejilla": grid}

        lines += _bloque_cohort(cohort, casos)

    # El JSON se actualiza en MODO MERGE: cada ejecucion solo reemplaza las
    # cohortes que se le pasan (antes, correr solo `clinic` borraba el bloque de
    # `vitaldb` del informe, y una ejecucion sin --neg-control borraba la tabla).
    previo.update(out)
    json_path.write_text(json.dumps(previo, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    # El .md SIEMPRE se regenera desde el JSON (una sola fuente de verdad).
    _solo_informe()
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
