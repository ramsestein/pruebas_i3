#!/usr/bin/env python3
"""
scripts/verify/report_final.py
==============================
Genera `reports/fase0/fase0_final.md`: informe de resultados y descripción de
datos final tras completar los datos. Lee los JSON de verificación ya generados
y las salidas harmonized (parquet) más recientes.

Uso:
    python scripts/verify/report_final.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

REPORT_DIR = PROJECT_ROOT / "reports" / "fase0"
HARMONIZED_DIR = PROJECT_ROOT / "datasets" / "harmonized"
CLINIC_INDEX = PROJECT_ROOT / "datasets" / "clinic_vitals" / "clinic_full_cases_index.json"
VITALDB_INDEX = PROJECT_ROOT / "datasets" / "vitaldb_sicu" / "vitaldb_full_cases_index.json"
OUT_MD = REPORT_DIR / "fase0_final.md"


def load_json(path: Path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def latest_harmonized() -> Path | None:
    if not HARMONIZED_DIR.exists():
        return None
    versions = [p for p in HARMONIZED_DIR.iterdir() if p.is_dir()]
    return max(versions, key=lambda p: p.stat().st_mtime) if versions else None


def md_table(headers, rows):
    out = ["| " + " | ".join(str(h) for h in headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def fmt_pct(x):
    try:
        return f"{float(x):.1f}%"
    except Exception:
        return "—"


def index_summary(index_path: Path, name: str):
    idx = load_json(index_path)
    if not idx:
        return {"name": name, "n": None, "durations": [], "t0_present": False}
    events = idx.get("events", [])
    durs = []
    t0_present = True
    for ev in events:
        if ev.get("t0_unix") is not None and ev.get("tend_unix") is not None:
            durs.append((ev["tend_unix"] - ev["t0_unix"]) / 3600.0)
        else:
            t0_present = False
        if ev.get("duration_seconds"):
            durs.append(ev["duration_seconds"] / 3600.0)
    return {"name": name, "n": len(events), "durations": durs, "t0_present": t0_present}


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    comp = load_json(REPORT_DIR / "completeness.json")
    labels = load_json(REPORT_DIR / "labels.json")
    tz = load_json(REPORT_DIR / "timezone.json")

    lines: list[str] = []
    A = lines.append

    A("# Fase 0 — Resultados y descripción de datos final")
    A("")
    A(f"> Generado el {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    A("")

    # ── 1. Tests de completitud ───────────────────────────────────────────────
    A("## 1. Tests de completitud")
    A("")
    if comp and "summary" in comp:
        s = comp["summary"]
        state = "✅ TODO VERDE" if s.get("all_green") else "❌ HAY ROJOS"
        A(f"**Estado global:** {state} — {s['n_checks'] - s['n_failed']}/{s['n_checks']} verdes.")
        A("")
        rows = []
        for k, v in comp.items():
            if k == "summary":
                continue
            passed = bool(v.get("passed"))
            # detalle breve
            detail = ""
            if v.get("reason"):
                detail = v["reason"]
            elif v.get("low_availability"):
                detail = f"bajo umbral: {v['low_availability']}"
            elif v.get("missing_files"):
                detail = f"faltan: {v['missing_files']}"
            elif v.get("max_abs_offset_s") is not None:
                detail = f"max offset {v['max_abs_offset_s']}s"
            rows.append(["✅" if passed else "❌", k, detail])
        A(md_table(["Resultado", "Comprobación", "Detalle"], rows))
        A("")
    else:
        A("_No existe `reports/fase0/completeness.json`. Ejecuta primero `verify_completeness.py`._")
        A("")

    # ── 2. Descripción de datos final por cohorte ────────────────────────────
    A("## 2. Descripción de datos final")
    A("")

    for idx_path, name in ((CLINIC_INDEX, "Clínic"), (VITALDB_INDEX, "VitalDB SICU")):
        s = index_summary(idx_path, name)
        A(f"### {name}")
        A("")
        if s["n"] is None:
            A("_Índice no disponible._")
        else:
            durs = [d for d in s["durations"] if d]
            med = sorted(durs)[len(durs) // 2] if durs else None
            A(f"- **Casos:** {s['n']}")
            A(f"- **t0_unix/tend_unix en índice:** {'sí' if s['t0_present'] else 'no'}")
            if med is not None:
                A(f"- **Duración mediana del episodio:** {med:.1f} h (mín {min(durs):.1f} h, máx {max(durs):.1f} h)")
        A("")

    A("### MIMIC-III")
    A("")
    labels_mimic = None
    if comp and "M2_mimic_labels" in comp:
        m2 = comp["M2_mimic_labels"]
        if m2.get("n_patients") is not None:
            A(f"- **Pacientes:** {m2['n_patients']}")
            A(f"- **Censurados (sin extubación):** {m2.get('n_censored')}")
    A("- **Fuente:** tablas clínicas `datasets/mimic3wdb/clinical/` + `.vital` enriquecidos existentes.")
    A("")

    # ── 3. Disponibilidad de canales (harmonized) ─────────────────────────────
    A("## 3. Disponibilidad de canales")
    A("")
    ver = latest_harmonized()
    if ver and (ver / "channel_availability.parquet").exists():
        import pandas as pd
        df = pd.read_parquet(ver / "channel_availability.parquet")
        A(f"Versión harmonized: `{ver.name}`")
        A("")
        cols = ["ecg_waveform", "ppg_waveform", "abp_waveform",
                "HR", "SBP", "DBP", "MAP", "SpO2", "RR",
                "FiO2", "PEEP", "TV", "MV", "PIP"]
        rows = []
        for cohort in ("clinic", "vitaldb", "mimic"):
            sub = df[df["cohort"].astype(str) == cohort]
            if sub.empty:
                rows.append([cohort, "—"] + ["—"] * len(cols))
                continue
            row = [cohort, str(len(sub))]
            for c in cols:
                row.append(fmt_pct(100.0 * sub[c].astype(bool).mean()) if c in df.columns else "—")
            rows.append(row)
        A(md_table(["Cohorte", "Casos", "ECG", "PPG", "ABP", "HR", "SBP", "DBP", "MAP", "SpO2", "RR", "FiO2", "PEEP", "TV", "MV", "PIP"], rows))
        A("")
        A("> Disponibilidad de ondas calculada sobre la rama `get_waveforms`; numéricas sobre `get_numerics` (≥1 valor no-NaN).")
        A("")
    else:
        A("_Sin `channel_availability.parquet`. Ejecuta `run_stage0.py`._")
        A("")

    # ── 4. Etiquetas (supervivencia) ──────────────────────────────────────────
    A("## 4. Etiquetas de supervivencia")
    A("")
    ver = latest_harmonized()
    if ver and (ver / "survival_48h.parquet").exists():
        import pandas as pd
        surv = pd.read_parquet(ver / "survival_48h.parquet")
        rows = []
        for cohort in ("clinic", "vitaldb", "mimic"):
            sub = surv[surv["cohort"].astype(str) == cohort]
            if sub.empty:
                rows.append([cohort, "—", "—", "—", "—"])
                continue
            success = int((sub["event_type"] == "successful_extubation").sum())
            failure = int((sub["event_type"] == "failed_extubation").sum())
            censored = int(sub["event_type"].isin(["censored", "censored_no_extubation"]).sum())
            rows.append([cohort, len(sub), success, failure, censored])
        A(md_table(["Cohorte", "Total", "Éxito", "Fallo", "Censurados"], rows))
        A("")
    else:
        A("_No existe `survival_48h.parquet`. Ejecuta `run_stage0.py`._")
        A("")

    # ── 5. Offset t0 ──────────────────────────────────────────────────────────
    A("## 5. Offset t0 (adapter vs primer timestamp)")
    A("")
    rows = []
    c3 = (comp or {}).get("C3_clinic_t0_vs_file", {})
    v3 = (comp or {}).get("V3_vitaldb_t0_vs_file", {})
    rows.append(["clinic", c3.get("max_abs_offset_s")])
    rows.append(["vitaldb", v3.get("max_abs_offset_s")])
    rows.append(["mimic", "—"])
    A(md_table(["Cohorte", "Offset máx. absoluto (s)"], rows))
    A("")

    # ── 6. Rutas de salida ────────────────────────────────────────────────────
    A("## 6. Artefactos")
    A("")
    A(f"- Tests de completitud: `reports/fase0/completeness.json`")
    A(f"- Casos Clínic: `datasets/clinic_vitals/clinic_full_cases/` + índice")
    A(f"- Casos VitalDB: `datasets/vitaldb_sicu/vitaldb_full_cases/` + índice")
    A(f"- Tablas clínicas MIMIC-III: `datasets/mimic3wdb/clinical/`")
    A(f"- Salidas harmonized: `{ver}`" if ver else "- Salidas harmonized: pendientes")
    A("")

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"[report_final] escrito {OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
