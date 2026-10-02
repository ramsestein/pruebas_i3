"""
scripts/verify/fase0b/report.py
================================
Genera `reports/fase0b/resumen.md` a partir de fase0b.json y analysis.json.

Uso:
    python scripts/verify/fase0b/report.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

REPORT_DIR = ROOT / "reports" / "fase0b"


def load(name: str):
    p = REPORT_DIR / name
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def md_table(headers, rows):
    out = ["| " + " | ".join(str(h) for h in headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join("—" if c is None else str(c) for c in r) + " |")
    return "\n".join(out)


def fmt(x, nd=1):
    if x is None:
        return "—"
    return f"{float(x):.{nd}f}"


def main() -> int:
    fb = load("fase0b.json") or {}
    an = load("analysis.json") or {}

    lines: list[str] = []
    A = lines.append

    A("# Fase 0b — Verificación honesta (solo lectura)")
    A("")
    A("> Este informe se genera exclusivamente a partir de datos, sin modificar "
      "`datasets/`. Las comprobaciones con `passed` tienen controles negativos "
      "demostrados en `scripts/verify/fase0b/tests/test_checks.py`.")
    A("")
    A("## Hallazgos clave")
    A("")
    A("1. **Fusión ≠ origen.** En VitalDB, 80/83 ficheros fusionados no cubren el "
      "tramo de origen dentro del 1 % (el merge descarta ficheros separados >7 días; "
      "ej. `SICU1_01_event_1` fusiona 69 h de un tramo de 746 h). En Clínic, 47/50 "
      "fuera de tolerancia: la cabecera fusionada se extiende más allá del tramo "
      "declarado en el nombre de fichero (ej. `box14_event_1`: cabecera 89.8 h vs "
      "tramo 42.8 h).")
    A("2. **MIMIC sin monitor.** Las pistas HR/SpO2/RESP/ABP existen pero con 0 "
      "registros (se vacían ya en `mimic_full_cases`); `datasets/mimic3wdb/raw/` no "
      "existe y no hay `.parquet`. El RR de la config apunta a `MIMIC/RESP` (vacío) "
      "cuando el dato está en `MIMIC/RR_V`.")
    A("3. **Etiquetas engañosas.** `event_type` nunca toma `failure`: los fallos de "
      "MIMIC están en `n_failed_attempts` (33/82 a 48 h) y en `extubation_attempts` "
      "(169 fallos). `censored_no_extubation` solo cubre muerte en vent_end; las "
      "demás reglas de D3 no están implementadas.")
    A("4. **VitalDB pierde el ventilador.** `MERGE_TRACK_NAMES` descarta 6 de 8 "
      "pistas de ventilador (VENT_RR, FIO2, PEEP_CMH2O, PIP_CMH2O, FLOW_WAV, AWP_WAV).")
    A("5. **eICU sin regenerar.** 59 reintubaciones en 48-72 h (1182 ≤48 h) que el "
      "adaptador clasifica mal por su corte fijo `gap <= 48.0`.")
    A("")
    A("---")
    A("")

    # ── 1. Completitud real por caso ──────────────────────────────────────────
    A("## 1. Completitud real por caso")
    A("")
    A("Duración fusionada (cabecera del fichero) frente al tramo cubierto por los "
      "ficheros de origen (tolerancia 1 %).")
    A("")
    for key, title in [("clinic_fusion", "Clínic"), ("vitaldb_fusion", "VitalDB")]:
        rows = fb.get(key)
        if not rows or isinstance(rows, dict):
            A(f"### {title}")
            A("")
            A("> Sin datos: " + str(rows) if rows else "> Sin datos.")
            A("")
            continue
        A(f"### {title}")
        A("")
        n_fail = sum(1 for r in rows if not r.get("check_fused_vs_source", {}).get("passed", True))
        n_ok = sum(1 for r in rows if r.get("check_fused_vs_source", {}).get("passed", False))
        A(f"- Casos: **{len(rows)}** | fusión fuera de tolerancia: **{n_fail}** | dentro: **{n_ok}**")
        rel_errs = [r["check_fused_vs_source"].get("rel_error") for r in rows
                    if r.get("check_fused_vs_source", {}).get("rel_error") is not None]
        if rel_errs:
            rel_errs.sort()
            A(f"- Error relativo de la duración: mediana {fmt(rel_errs[len(rel_errs)//2], 3)}, "
              f"máx {fmt(rel_errs[-1], 3)}")
        A("")
        A("| Evento | Dur. fusionada (h) | Tramo origen (h) | Error rel. | Omitidos |")
        for r in rows[:15]:
            ck = r.get("check_fused_vs_source", {})
            A("| " + " | ".join([
                str(r.get("event_id")),
                fmt(r.get("fused_header_h")),
                fmt(r.get("source_span_h")),
                fmt(ck.get("rel_error"), 3),
                str(r.get("n_omitted")),
            ]) + " |")
        A("")
        A("*Ficheros de origen dentro del tramo del evento que no están reflejados "
          "en la fusión (`n_omitted`).*")
        A("")

    # ── cobertura por canal ───────────────────────────────────────────────────
    A("## 1b. % de horas con datos por canal (mediana por cohorte)")
    A("")
    A("> `n_cases_with_data` = casos (de la muestra) con cobertura > 0. La lista de "
      "canales es la misma en las 4 cohortes.")
    A("")
    header = ["Cohorte"] + ["RR", "HR", "SpO2", "PEEP", "MAP", "FiO2", "TV", "PIP", "ECG", "PPG", "ABP"]
    rows = []
    for cohort in ["clinic", "vitaldb", "mimic", "eicu"]:
        cc = fb.get(f"{cohort}_cases") or {}
        cov = cc.get("coverage_summary", {})
        row = [cohort]
        for c in ["RR", "HR", "SpO2", "PEEP", "MAP", "FiO2", "TV", "PIP", "ECG", "PPG", "ABP"]:
            m = cov.get(c, {}).get("median")
            n = cov.get(c, {}).get("n_cases_with_data")
            row.append(f"{fmt(m*100, 0)}% (n={n})" if m is not None else "—")
        rows.append(row)
    A(md_table(header, rows))
    A("")
    A("### Canales ausentes (ROJO)")
    A("")
    for cohort in ["clinic", "vitaldb", "mimic", "eicu"]:
        cc = fb.get(f"{cohort}_cases") or {}
        exp = cc.get("expected_channels", {})
        missing = exp.get("missing", [])
        A(f"- **{cohort}**: {', '.join(missing) if missing else 'ninguno'}")
    A("")

    # ── 2. Plausibilidad ──────────────────────────────────────────────────────
    A("## 2. Plausibilidad de los eventos")
    A("")
    for cohort in ["clinic", "vitaldb"]:
        p = fb.get(f"{cohort}_plausibility") or {}
        s = p.get("duration_summary_h", {})
        A(f"### {cohort}")
        A("")
        A(f"- Casos muestreados: {p.get('n_cases')} | eventos > 21 días: **{p.get('n_gt_21d')}**")
        A(f"- Duración (h): min {fmt(s.get('min'))}, P5 {fmt(s.get('p5'))}, "
          f"mediana {fmt(s.get('median'))}, P95 {fmt(s.get('p95'))}, máx {fmt(s.get('max'))}")
        a = p.get("attempts_per_event_summary", {})
        d = p.get("d2_events_summary", {})
        A(f"- Intentos por evento (señal): min {fmt(a.get('min'),0)}, mediana {fmt(a.get('median'),0)}, "
          f"máx {fmt(a.get('max'),0)}")
        A(f"- Eventos tras cortes D2 (hueco monitor > 1 h): min {fmt(d.get('min'),0)}, "
          f"mediana {fmt(d.get('median'),0)}, máx {fmt(d.get('max'),0)}")
        A("")
    # duraciones por índice (todas las cohortes)
    A("### Duración por cohorte (todos los casos)")
    A("")
    A("> Clínic/VitalDB: cabecera del fichero fusionado. MIMIC: nombre de fichero. "
      "eICU: episodios fusionados de `respiratoryCare` (la cabecera de eICU está corrupta).")
    A("")
    hdr = ["Cohorte", "N", "min (h)", "P5 (h)", "mediana (h)", "P95 (h)", "máx (h)"]
    rows = []
    fd = fb.get("full_durations", {})
    eicu_dur = (an.get("eicu_reintubations") or {}).get("duration_summary_h", {})
    for cohort in ["clinic", "vitaldb", "mimic", "eicu"]:
        if cohort == "eicu":
            s = {"n": an.get("eicu_reintubations", {}).get("n_patients_with_mv"),
                 "min": eicu_dur.get("min"), "p5": None,
                 "median": eicu_dur.get("median"), "p95": None, "max": eicu_dur.get("max")}
        else:
            s = fd.get(cohort, {})
        rows.append([cohort, s.get("n"), fmt(s.get("min")), fmt(s.get("p5")),
                     fmt(s.get("median")), fmt(s.get("p95")), fmt(s.get("max"))])
    A(md_table(hdr, rows))
    A("")

    # ── 4. MIMIC monitor ──────────────────────────────────────────────────────
    A("## 4. MIMIC — monitor")
    A("")
    mm = an.get("mimic_monitor", {})
    A(f"- WFDB numéricos en `datasets/mimic3wdb/raw/`: **{'sí' if mm.get('raw_wfdb_dir_exists') else 'NO'}**")
    A(f"- `.vital` base: {mm.get('n_base_vital')} | enriquecidos: {mm.get('n_enriched_vital')}")
    A(f"- `.parquet` en `mimic_full_cases`: **{mm.get('n_base_parquet')}** | en enriquecidos: **{mm.get('n_enriched_parquet')}**")
    A("")
    A("Pistas de monitor (con 0 registros) por paso:")
    A("")
    A("| Pista | En base (n vacíos/n presentes) | En enriched (n vacíos/n presentes) |")
    b = mm.get("base_tracks_empty_sample", {})
    e = mm.get("enriched_tracks_empty_sample", {})
    for k in sorted(set(b) | set(e)):
        bb = b.get(k, {})
        ee = e.get(k, {})
        A("| " + " | ".join([
            f"`{k}`",
            f"{bb.get('n_empty',0)}/{bb.get('n_files_with_track',0)}",
            f"{ee.get('n_empty',0)}/{ee.get('n_files_with_track',0)}",
        ]) + " |")
    A("")

    # ── 5. MIMIC etiquetas ────────────────────────────────────────────────────
    A("## 5. MIMIC — etiquetas")
    A("")
    ml = an.get("mimic_labels", {})
    A(f"- ADMISSIONS: **{'sí' if ml.get('admissions_exists') else 'no'}** | "
      f"ICUSTAYS: **{'sí' if ml.get('icustays_exists') else 'no'}** | "
      f"PROCEDUREEVENTS_MV: **{'sí' if ml.get('procedureevents_exists') else 'no'}**")
    A(f"- Episodios 225792 totales: {ml.get('n_mv_225792_total')} "
      f"({ml.get('n_mv_subjects_total')} sujetos; {ml.get('n_subjects_with_gt1_225792')} con >1 episodio)")
    A(f"- Casos actuales: {ml.get('n_current_cases')} | con DEATHTIME: {ml.get('n_current_with_deathtime')}")
    A(f"- Episodios 225792 por sujeto (actuales): {ml.get('current_episodes_per_subject')}")
    A(f"- Sujetos actuales con >1 episodio: {ml.get('current_subjects_with_gt1_episode')} | "
      f"con episodios en >1 HADM: {ml.get('current_subjects_with_gt1_hadm')}")
    A("")
    A("**Por qué la tabla da 0 fallos:** " + str(ml.get("why_zero_failures")))
    A("")
    A("**Por qué la tabla da 0 censurados:** " + str(ml.get("why_zero_censored")))
    A("")

    # ── 6. VitalDB tracks de ventilador ───────────────────────────────────────
    A("## 6. VitalDB — pistas de ventilador perdidas en MERGE_TRACK_NAMES")
    A("")
    vt = an.get("vitaldb_tracks", {})
    if vt.get("raw_dir") == "no proporcionado":
        A("> No se proporcionó `--vitaldb-raw`.")
    else:
        A("| Pista de origen | Frecuencia en muestra | ¿Conservada por MERGE_TRACK_NAMES? |")
        merge = set(vt.get("merge_track_names", []))
        for k, v in sorted(vt.get("source_vent_tracks_sample300", {}).items(), key=lambda x: -x[1]):
            A("| " + " | ".join([f"`{k}`", str(v), "sí" if k in merge else "**NO**"]) + " |")
        A("")
        A(f"- Pistas de ventilador en origen: {vt.get('n_vent_tracks_in_source')} | "
          f"perdidas: **{vt.get('n_vent_tracks_lost')}**")
    A("")

    # ── 7. eICU reintubaciones ────────────────────────────────────────────────
    A("## 7. eICU — reintubaciones 48-72 h (tablas crudas, sin adaptador)")
    A("")
    ei = an.get("eicu_reintubations", {})
    A(f"- Pacientes con VM: {ei.get('n_patients_with_mv')} | con >1 episodio: {ei.get('n_patients_with_gt1_episode')}")
    A(f"- Huecos de reintubación: {ei.get('n_reintubation_gaps')} | "
      f"≤48 h: {ei.get('gap_le_48h')} | **48-72 h: {ei.get('gap_48_72h')}** | >72 h: {ei.get('gap_gt_72h')}")
    A(f"- Hueco min/mediana/máx (h): {fmt(ei.get('gap_min_h'),1)} / {fmt(ei.get('gap_median_h'),1)} / {fmt(ei.get('gap_max_h'),1)}")
    A("")

    # ── 8. Sesgo de selección ─────────────────────────────────────────────────
    A("## 8. Sesgo de selección actual")
    A("")
    sb = an.get("selection_bias", {})
    mm = sb.get("mimic_all_mv_episodes", {})
    A("### MIMIC (mín. 12 h + top-150)")
    A("")
    s = mm.get("duration_summary_h", {})
    A(f"- Episodios 225792 totales: {mm.get('n')} | duración: min {fmt(s.get('min'))}, "
      f"mediana {fmt(s.get('median'))}, máx {fmt(s.get('max'))}")
    A(f"- Se perderían con el mínimo de 12 h: **{mm.get('n_lost_by_min_12h')}** episodios")
    A(f"- {mm.get('note_top150')}")
    A("")
    ee = sb.get("eicu_merged_episodes", {})
    A("### eICU (mín. 2 h)")
    A("")
    s = ee.get("duration_summary_h", {})
    A(f"- Episodios fusionados: {ee.get('n')} | duración: min {fmt(s.get('min'))}, "
      f"mediana {fmt(s.get('median'))}, máx {fmt(s.get('max'))}")
    A(f"- Se perderían con el mínimo de 2 h: **{ee.get('n_lost_by_min_2h')}** episodios")
    A("")
    A(f"- Clínic/VitalDB: {sb.get('clinic_note')}")
    A("")

    # ── limitaciones ──────────────────────────────────────────────────────────
    A("## Limitaciones")
    A("")
    A("- La cobertura horaria por canal se calcula sobre una **muestra** de casos "
      "(12 por cohorte) para no saturar memoria con ficheros de cientos de horas.")
    A("- La segmentación D1/D2 de la sección 2 usa señales leídas de los ficheros "
      "de caso ya construidos; no sustituye a la re-segmentación de Fase 1.")
    A("- La cabecera VITA de los ficheros MIMIC y eICU está **corrupta** "
      "(`dtstart` ≈ fecha actual, `dtend` = 0 o erróneo); por eso la duración de "
      "esas cohortes se deriva del nombre de fichero (MIMIC) o de los tracks "
      "relativos (eICU), y no de la cabecera.")
    A("")

    out = REPORT_DIR / "resumen.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[fase0b] escrito {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
