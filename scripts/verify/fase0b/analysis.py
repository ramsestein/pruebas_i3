"""
scripts/verify/fase0b/analysis.py
==================================
Análisis de solo-lectura para los puntos 4-8 de Fase 0b (MIMIC, VitalDB, eICU,
sesgo de selección). Escribe `reports/fase0b/analysis.json`.

Uso:
    python scripts/verify/fase0b/analysis.py [--vitaldb-raw D:/data/vitaldb_sicu]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

REPORT_DIR = ROOT / "reports" / "fase0b"


def _merge_episodes(g: pd.DataFrame) -> list[list[float]]:
    rows = g.to_dict("records")
    merged: list[list[float]] = []
    for r in rows:
        if merged and r["ventstartoffset"] <= merged[-1][1] + 120:
            merged[-1][1] = max(merged[-1][1], r["ventendoffset"])
        else:
            merged.append([r["ventstartoffset"], r["ventendoffset"]])
    return merged


def analyze_mimic_monitor() -> dict:
    """P4: monitor MIMIC — raw WFDB, parquets, tracks vacíos por paso."""
    mdir = ROOT / "datasets" / "mimic3wdb"
    raw = mdir / "raw"
    base = mdir / "mimic_full_cases"
    enriched = mdir / "mimic_full_cases_enriched"
    import vitaldb

    out = {
        "raw_wfdb_dir_exists": raw.exists(),
        "n_base_vital": len(list(base.glob("*.vital"))),
        "n_enriched_vital": len(list(enriched.glob("*.vital"))),
        "n_base_parquet": len(list(base.glob("*.parquet"))),
        "n_enriched_parquet": len(list(enriched.glob("*.parquet"))),
        "base_tracks_empty_sample": {},
        "enriched_tracks_empty_sample": {},
    }
    # pistas de monitor y ABP que deben estar pobladas
    MONITOR = ["HR", "PULSE", "RESP", "SpO2", "ABP_S", "ABP_D", "ABP_M"]
    for label, d in [("base", base), ("enriched", enriched)]:
        empties = collections.Counter()
        totals = collections.Counter()
        for f in sorted(d.glob("*.vital"))[:10]:
            vf = vitaldb.VitalFile(str(f))
            for name, trk in vf.trks.items():
                base_name = name.split("/")[-1]
                if base_name in MONITOR:
                    totals[base_name] += 1
                    if not trk.recs:
                        empties[base_name] += 1
        out[f"{label}_tracks_empty_sample"] = {
            b: {"n_files_with_track": totals[b], "n_empty": empties[b]}
            for b in MONITOR if totals[b] > 0
        }
    return out


def analyze_mimic_labels(mv: pd.DataFrame, adm: pd.DataFrame) -> dict:
    """P5: etiquetas MIMIC (mv y adm ya cargados una sola vez)."""
    clin = ROOT / "datasets" / "mimic3wdb" / "clinical"
    out = {
        "admissions_exists": (clin / "ADMISSIONS.csv.gz").exists(),
        "icustays_exists": (clin / "ICUSTAYS.csv.gz").exists(),
        "procedureevents_exists": (clin / "PROCEDUREEVENTS_MV.csv.gz").exists(),
    }
    enriched = ROOT / "datasets" / "mimic3wdb" / "mimic_full_cases_enriched"
    cur_subjects = set()
    for f in enriched.glob("mimic_*.vital"):
        m = re.match(r"mimic_(\d+)_", f.name)
        if m:
            cur_subjects.add(int(m.group(1)))

    mv_cur = mv[mv["SUBJECT_ID"].isin(cur_subjects)]
    adm_cur = adm[adm["SUBJECT_ID"].isin(cur_subjects)]
    per_subj = mv_cur.groupby("SUBJECT_ID").size()
    hadm_per_subj = mv_cur.groupby("SUBJECT_ID")["HADM_ID"].nunique()

    out.update({
        "n_mv_225792_total": int(len(mv)),
        "n_mv_subjects_total": int(mv["SUBJECT_ID"].nunique()),
        "n_subjects_with_gt1_225792": int((mv.groupby("SUBJECT_ID").size() > 1).sum()),
        "n_current_cases": len(cur_subjects),
        "n_current_with_mv": int(mv_cur["SUBJECT_ID"].nunique()),
        "n_current_with_deathtime": int(adm_cur["DEATHTIME"].notna().sum()),
        "current_episodes_per_subject": per_subj.value_counts().sort_index().to_dict(),
        "current_subjects_with_gt1_episode": int((per_subj > 1).sum()),
        "current_subjects_with_gt1_hadm": int((hadm_per_subj > 1).sum()),
        "current_mv_duration_summary_h": {
            "min": round(float(mv_cur["duration_h"].min()), 2),
            "median": round(float(mv_cur["duration_h"].median()), 2),
            "max": round(float(mv_cur["duration_h"].max()), 2),
        },
        # explicación de por qué la tabla da 0 fallos / 0 censurados
        "why_zero_failures": (
            "El campo event_type de las tablas de supervivencia solo toma dos valores "
            "('successful_extubation' y 'censored_no_extubation'): nunca 'failure'. "
            "Los fallos existen en n_failed_attempts (mimic: 33/82 con >=1 fallo a 48h "
            "en v0.1.0_df652b7b) y en extubation_attempts (169 intentos 'failure')."
        ),
        "why_zero_censored": (
            "censored_no_extubation solo se activa para 'death_at_vent_end' "
            "(DEATHTIME dentro de +/-5 min del vent_end del fichero). Ninguno de los 82 "
            "sujetos cumple esa coincidencia; las demás reglas de censura de D3 "
            "(fin de monitor/datos <=15 min o seguimiento < ventana) no están implementadas."
        ),
    })
    return out


def analyze_vitaldb_tracks(vitaldb_raw: Path | None) -> dict:
    """P6: pistas de ventilador en origen y cuántas se pierden con MERGE_TRACK_NAMES."""
    from src.create_dataset.build_vitaldb_cases import MERGE_TRACK_NAMES
    import vitaldb

    out = {"raw_dir": None, "source_vent_tracks": {}, "merge_track_names": MERGE_TRACK_NAMES}
    if vitaldb_raw is None or not Path(vitaldb_raw).exists():
        out["raw_dir"] = "no proporcionado"
        return out
    out["raw_dir"] = str(vitaldb_raw)
    VENT_BASES = {"FLOW_WAV", "AWP_WAV", "TV_EXP", "MV_EXP", "VENT_RR",
                  "PEEP_CMH2O", "PIP_CMH2O", "FIO2", "FiO2"}
    counts = collections.Counter()
    files = sorted(Path(vitaldb_raw).glob("*.vital"))
    import random
    random.seed(42)
    sample = random.sample(files, min(300, len(files)))
    for f in sample:
        try:
            vf = vitaldb.VitalFile(str(f))
            for name in vf.trks:
                base = name.split("/")[-1]
                if base in VENT_BASES:
                    counts[name] += 1
        except Exception:
            continue
    merge_kept = set(MERGE_TRACK_NAMES)
    lost = {k: v for k, v in counts.items() if k not in merge_kept}
    kept = {k: v for k, v in counts.items() if k in merge_kept}
    out.update({
        "source_vent_tracks_sample300": dict(counts),
        "kept_by_merge": dict(kept),
        "lost_by_merge": dict(lost),
        "n_vent_tracks_in_source": len(counts),
        "n_vent_tracks_lost": len(lost),
    })
    return out


def analyze_eicu_reintubations(resp: pd.DataFrame) -> dict:
    """P7: reintubaciones eICU 48-72h desde tablas crudas (resp ya cargado)."""
    gaps: list[float] = []
    n_gt1 = 0
    durations: list[float] = []
    for pid, g in resp.groupby("patientunitstayid"):
        m = _merge_episodes(g)
        if len(m) > 1:
            n_gt1 += 1
        for s, e in m:
            durations.append((e - s) / 60.0)  # horas
        for i in range(len(m) - 1):
            gaps.append((m[i + 1][0] - m[i][1]) / 60.0)  # horas
    gaps = pd.Series(gaps, dtype=float)
    return {
        "n_patients_with_mv": int(resp["patientunitstayid"].nunique()),
        "n_patients_with_gt1_episode": n_gt1,
        "n_reintubation_gaps": int(len(gaps)),
        "gap_le_48h": int((gaps <= 48).sum()),
        "gap_48_72h": int(((gaps > 48) & (gaps <= 72)).sum()),
        "gap_gt_72h": int((gaps > 72).sum()),
        "gap_min_h": round(float(gaps.min()), 2) if len(gaps) else None,
        "gap_median_h": round(float(gaps.median()), 2) if len(gaps) else None,
        "gap_max_h": round(float(gaps.max()), 2) if len(gaps) else None,
        "duration_summary_h": {
            "min": round(float(min(durations)), 2),
            "median": round(float(np.median(durations)), 2),
            "max": round(float(max(durations)), 2),
        },
    }


def analyze_selection_bias(mv: pd.DataFrame, eicu_durs: list[float]) -> dict:
    """P8: sesgo de selección vigente (12h MIMIC, 2h resto, top-150)."""
    mv_h = sorted(mv["duration_h"].tolist())
    n_mimic_lt12 = int(sum(1 for x in mv_h if x < 12))
    eicu_h = sorted(eicu_durs)
    n_eicu_lt2 = int(sum(1 for x in eicu_h if x < 2))

    return {
        "mimic_all_mv_episodes": {
            "n": len(mv_h),
            "duration_summary_h": {
                "min": round(mv_h[0], 2),
                "p5": round(_pct(mv_h, 0.05), 2),
                "median": round(_pct(mv_h, 0.5), 2),
                "p95": round(_pct(mv_h, 0.95), 2),
                "max": round(mv_h[-1], 2),
            },
            "n_lost_by_min_12h": n_mimic_lt12,
            "note_top150": "El selector actual toma los 150 episodios más largos "
                           "(>=12h) de la cohorte matched; de ellos quedan 82 casos.",
        },
        "eicu_merged_episodes": {
            "n": len(eicu_h),
            "duration_summary_h": {
                "min": round(eicu_h[0], 2),
                "p5": round(_pct(eicu_h, 0.05), 2),
                "median": round(_pct(eicu_h, 0.5), 2),
                "p95": round(_pct(eicu_h, 0.95), 2),
                "max": round(eicu_h[-1], 2),
            },
            "n_lost_by_min_2h": n_eicu_lt2,
        },
        "clinic_note": "La distribución pre-filtro de Clínic/VitalDB requiere "
                       "re-ejecutar la detección sin MIN_EVENT_SECONDS=7200 (Fase 1).",
    }


def _pct(sorted_vals, p):
    a = sorted_vals
    k = max(0, min(len(a) - 1, int(round((len(a) - 1) * p))))
    return float(a[k])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vitaldb-raw", default=None)
    args = ap.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # carga única de tablas pesadas
    clin = ROOT / "datasets" / "mimic3wdb" / "clinical"
    proc = pd.read_csv(clin / "PROCEDUREEVENTS_MV.csv.gz", compression="gzip",
                       usecols=["SUBJECT_ID", "HADM_ID", "ICUSTAY_ID", "ITEMID", "STARTTIME", "ENDTIME"])
    adm = pd.read_csv(clin / "ADMISSIONS.csv.gz", compression="gzip",
                      usecols=["SUBJECT_ID", "HADM_ID", "DEATHTIME"])
    mv = proc[proc["ITEMID"] == 225792].copy()
    mv["duration_h"] = (pd.to_datetime(mv["ENDTIME"]) - pd.to_datetime(mv["STARTTIME"])).dt.total_seconds() / 3600.0

    d = ROOT / "datasets" / "eicu_collaborative"
    resp = pd.read_csv(d / "respiratoryCare.csv.gz", compression="gzip",
                       usecols=["patientunitstayid", "ventstartoffset", "ventendoffset", "respcarestatusoffset"])
    resp = resp.dropna(subset=["ventstartoffset"])
    resp["ventendoffset"] = resp["ventendoffset"].fillna(0)
    ms = resp.groupby(["patientunitstayid", "ventstartoffset"])["respcarestatusoffset"].transform("max")
    resp.loc[resp["ventendoffset"] <= 0, "ventendoffset"] = ms[resp["ventendoffset"] <= 0]
    resp.loc[resp["ventendoffset"] <= resp["ventstartoffset"], "ventendoffset"] = resp["ventstartoffset"] + 1
    resp = resp.sort_values(["patientunitstayid", "ventstartoffset"])

    eicu_durs: list[float] = []
    for pid, g in resp.groupby("patientunitstayid"):
        for s, e in _merge_episodes(g):
            eicu_durs.append((e - s) / 60.0)

    report = {
        "mimic_monitor": analyze_mimic_monitor(),
        "mimic_labels": analyze_mimic_labels(mv, adm),
        "vitaldb_tracks": analyze_vitaldb_tracks(Path(args.vitaldb_raw) if args.vitaldb_raw else None),
        "eicu_reintubations": analyze_eicu_reintubations(resp),
        "selection_bias": analyze_selection_bias(mv, eicu_durs),
    }
    with open(REPORT_DIR / "analysis.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, default=str)
    print(f"[fase0b] escrito {REPORT_DIR / 'analysis.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
