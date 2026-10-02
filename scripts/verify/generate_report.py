"""
scripts/verify/generate_report.py
==================================
Genera `reports/fase0_verificacion.md` a partir de los JSON producidos por los
scripts de verificación (solo lectura de los resúmenes ya calculados).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

REPORT_DIR = PROJECT_ROOT / "reports" / "fase0"
OUT_MD = PROJECT_ROOT / "reports" / "fase0_verificacion.md"


def load(name: str):
    p = REPORT_DIR / name
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def main():
    fusion = load("fusion.json")
    waves = load("waves.json")
    mim = load("mimic_numerics.json")
    tz = load("timezone.json")
    labels = load("labels.json")

    lines = []
    A = lines.append

    A("# Fase 0 — Verificación con los datos reales")
    A("")
    A("> Informe generado con scripts de solo lectura en `scripts/verify/`.")
    A("> Los datos crudos de `datasets/` no han sido modificados.")
    A("")

    # ── (a) fusión ────────────────────────────────────────────────────────────
    A("## (a) Fusión de archivos `.vital`")
    A("")
    if fusion:
        s = fusion.get("summary", {})
        A("### Resumen")
        A("")
        A(md_table(
            ["Cohorte", "Casos", "Truncados", "Íntegros", "Casos con pistas de onda vacías", "Ratio mediano real/esperado"],
            [
                [
                    c,
                    s[c]["n_cases"],
                    s[c]["n_truncated"],
                    s[c]["n_not_truncated"],
                    s[c]["n_with_empty_wave_tracks"],
                    s[c]["median_ratio_actual_expected"],
                ]
                for c in ("clinic", "vitaldb")
            ],
        ))
        A("")
        A("**Criterio de truncado:** duración real de datos leída con `vitaldb` < 95 % de la duración esperada (nombre de fichero/índice).")
        A("")
        A("### Primeros casos (ejemplos)")
        A("")
        for cohort in ("clinic", "vitaldb"):
            rows = fusion.get(cohort, [])
            A(f"#### {cohort}")
            A("")
            A(md_table(
                ["Fichero", "Esperado (h)", "Cabecera (h)", "Real (h)", "Ratio", "Truncado", "Onda vacía"],
                [[
                    r["file"],
                    r["expected_duration_h"],
                    r["header_duration_h"],
                    r["actual_duration_h"],
                    r["ratio_actual_expected"],
                    r["truncated"],
                    r["n_wave_tracks_empty"],
                ] for r in rows[:10]],
            ))
            A("")
    else:
        A("_No se pudo ejecutar `verify_vital_fusion.py`._")
        A("")

    # ── (b) ondas ─────────────────────────────────────────────────────────────
    A("## (b) Ondas (waveforms)")
    A("")
    if waves:
        A("### Resumen de la rama de ondas de stage0 (`get_waveforms`)")
        A("")
        A(md_table(
            ["Cohorte", "Pacientes", "Waveforms disponibles", "Esperados", "Muestras totales"],
            [[
                c,
                waves["summary"][c]["n_patients"],
                waves["summary"][c]["n_waveforms_available"],
                waves["summary"][c]["n_waveforms_expected"],
                waves["summary"][c].get("total_waveform_samples", "—"),
            ] for c in ("clinic", "vitaldb", "mimic", "eicu")
            if c in waves.get("summary", {})
            ],
        ))
        A("")
        A("### Detalle de pistas de onda en crudo (primeros casos)")
        A("")
        for cohort in ("clinic", "vitaldb"):
            raw = waves.get("raw", {}).get(cohort, {})
            A(f"#### {cohort}")
            A("")
            for fname, tracks in list(raw.items())[:3]:
                A(f"**{fname}**")
                A("")
                A(md_table(
                    ["Track", "Presente", "srate", "fmt", "n_recs", "primer val"],
                    [[
                        t,
                        d.get("present"),
                        d.get("srate"),
                        d.get("fmt"),
                        d.get("n_recs"),
                        d.get("first_val"),
                    ] for t, d in tracks.items()],
                ))
                A("")
            A("")
    else:
        A("_No se pudo ejecutar `verify_waves.py`._")
        A("")

    # ── (c) MIMIC numerics ────────────────────────────────────────────────────
    A("## (c) Numéricas MIMIC a 1 Hz")
    A("")
    if mim:
        A(f"- Directorio clínico MIMIC existe: `{mim['clinical_dir_exists']}`")
        A("")
        A("**Pacientes con datos por columna canónica** (`get_numerics`):")
        A("")
        A(md_table(
            ["Columna", "Pacientes con datos (de %d)" % mim["summary"]["n_patients_sampled"]],
            [[c, n] for c, n in mim["summary"]["patients_with_data_per_column"].items()],
        ))
        A("")
        A("**Inventario de tracks del primer `.vital` enriquecido** (los tracks `MIMIC/*` a 1 Hz están vacíos; los `Derived/*` tienen 28 002 registros a 1 Hz):")
        A("")
        first_name = next(iter(mim.get("track_inventory", {})), None)
        if first_name:
            inv = mim["track_inventory"][first_name]
            rows = [[t, d["srate"], d["n_recs"], d["fmt"], d["unit"]] for t, d in inv.items()]
            A(f"**{first_name}**")
            A("")
            A(md_table(["Track", "srate", "n_recs", "fmt", "unit"], rows[:20]))
            A("")
            A(f"… y {len(rows) - 20} tracks más (ver `reports/fase0/mimic_numerics.json`).")
            A("")
    else:
        A("_No se pudo ejecutar `verify_mimic_numerics.py`._")
        A("")

    # ── (d) zona horaria ──────────────────────────────────────────────────────
    A("## (d) Zona horaria")
    A("")
    if tz:
        A("### t0 del adaptador vs primer timestamp de la señal")
        A("")
        for cohort in ("clinic", "vitaldb", "mimic"):
            rows = tz.get(cohort, [])
            if not isinstance(rows, list):
                continue
            A(f"#### {cohort}")
            A("")
            A(md_table(
                ["Paciente", "t0 adaptador", "1er timestamp señal", "Offset (h)", "dgmt cabecera (min)", "record_end (h)"],
                [[
                    r.get("patient_id"),
                    r.get("t0_unix_adapter"),
                    r.get("first_sample_epoch"),
                    r.get("offset_hours"),
                    r.get("vital_dgmt_min"),
                    r.get("record_end_hours"),
                ] for r in rows],
            ))
            A("")
        A("### Offset mediano por cohorte")
        A("")
        A(md_table(
            ["Cohorte", "Offset mediano (h)", "Offsets muestreados (h)"],
            [[c, tz["summary"][c]["median_offset_hours"], tz["summary"][c]["offset_hours_sample"]] for c in ("clinic", "vitaldb", "mimic")],
        ))
        A("")
        mc = tz.get("mimic_death_check", {})
        A("### Tiempos de muerte de MIMIC")
        A("")
        A(f"- `clinical_dir` existe: **{mc.get('clinical_dir_exists')}**")
        A(f"- `ADMISSIONS.csv.gz`: **{mc.get('admissions_exists')}**")
        A(f"- `PROCEDUREEVENTS_MV.csv.gz`: **{mc.get('procedureevents_exists')}**")
        A(f"- `ICUSTAYS.csv.gz`: **{mc.get('icustays_exists')}**")
        A("")
        A(mc.get("note", ""))
        A("")
    else:
        A("_No se pudo ejecutar `verify_timezone.py`._")
        A("")

    # ── (e) etiquetas ─────────────────────────────────────────────────────────
    A("## (e) Etiquetas en la tabla de supervivencia actual")
    A("")
    if labels:
        A("### Distribución de `event_type` por cohorte (consolidado, sin duplicar pacientes)")
        A("")
        cons = labels["summary"]["event_type_by_cohort_consolidated"]
        A(md_table(
            ["Cohorte", "successful_extubation", "censored_no_extubation", "n_rows", "fuente"],
            [[
                c,
                d["event_type_counts"].get("successful_extubation", 0),
                d["event_type_counts"].get("censored_no_extubation", 0),
                d["n_rows"],
                d["source"],
            ] for c, d in sorted(cons.items())],
        ))
        A("")
        A("### Detalle por versión")
        A("")
        for vname, vdata in labels["versions"].items():
            for stem, d in vdata.items():
                if not isinstance(d, dict) or "event_type_by_cohort" not in d:
                    continue
                A(f"**{vname}/{stem}** — {d['n_rows']} filas")
                A("")
                A(f"- `censored_no_extubation` con tiempo finito: **{d['censored_with_finite_extub']}**")
                A(f"- tiempos de extubación negativos: **{d['negative_extubation_time']}**")
                A(f"- `t0_unix` negativos: **{d['negative_t0_unix']}**")
                A("")
            gaps = vdata.get("eicu_reintubation_gaps")
            if gaps:
                A(f"- eICU reintubaciones (tabla de intentos): {gaps['n_failures']} fallos; gap 48–72 h: **{gaps['gap_48_72h']}**; gap min/med/max = {gaps['gap_min_h']} / {gaps['gap_median_h']} / {gaps['gap_max_h']} h")
                A("")
        A("### MIMIC: episodios procedentes de otro HADM")
        A("")
        h = labels["summary"]["mimic_hadm_check"]
        A(f"- `clinical_dir` existe: **{h['clinical_dir_exists']}**")
        A("")
        A(h["note"])
        A("")
    else:
        A("_No se pudo ejecutar `verify_labels.py`._")
        A("")

    # ── Limitaciones ──────────────────────────────────────────────────────────
    A("## Limitaciones de esta verificación")
    A("")
    A("- Los ficheros horarios de origen (pre-fusión) no están en el repositorio: "
      "no se puede comparar la suma de los ficheros de origen con el fusionado, solo "
      "la duración declarada en nombre/índice frente a la real.")
    A("- Las tablas clínicas de MIMIC (`ADMISSIONS`, `PROCEDUREEVENTS_MV`, `ICUSTAYS`) no están "
      "en el repositorio, por lo que la verificación de muerte y de HADM queda limitada.")
    A("- Los tiempos de las cohortes con datos reales se comparan contra el reloj UTC; las zonas "
      "horarias locales se infieren de los nombres de fichero y del campo `dgmt` de la cabecera `.vital`.")
    A("")

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"[verificacion] informe escrito en {OUT_MD}")


if __name__ == "__main__":
    main()
