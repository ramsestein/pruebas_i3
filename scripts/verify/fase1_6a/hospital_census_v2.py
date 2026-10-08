#!/usr/bin/env python3
"""
scripts/verify/fase1_6a/hospital_census_v2.py
=============================================
Fase 1.6a-bis — Censo de eICU corregido (sin etiquetas):

1. Evidencia invasiva basada en ``apache_intub`` (``oobIntubDay1``), **no**
   ``apache_vent`` (que incluye VNI). Recalcula ``vent_stays`` y la concordancia.
2. Plausibilidad por hospital: ``rc_invasive/icu_stays_total`` y
   ``rc_invasive/apache_intub``, más diagnóstico de los hospitales 411/413/412/259.
3. Cobertura POR VARIABLE (HR, SpO2, MAP invasiva, MAP no invasiva, RR, FiO2,
   PEEP) y variable limitante de ``vars_ok``.
4. Escenarios a–g recalculados en **estancias utilizables** (invasiva confirmada
   + ``vars_ok`` al 50 %).
5. Exporta ``reports/fase1_6a/eicu_hospitales_v2.csv`` y la lista de hospitales
   de cada escenario (``eicu_scenarios_v2.json``).

Uso:
    python scripts/verify/fase1_6a/hospital_census_v2.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.eicu_census import (  # noqa: E402
    VERDICT_IMPLAUSIBLE,
    classify_plausibility,
    inter_adjustment_intervals,
    median_iqr,
)
from src.common.eicu_levels import hourly_coverage  # noqa: E402
from src.common.eicu_vent import CAT_INVASIVE, classify_careplan, classify_treatment, pairwise_concordance  # noqa: E402
from src.common.paths import config_path  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
V1 = ROOT / "scripts" / "verify" / "fase1_6a" / "hospital_census.py"

CONST_TRACKS = {"HR": "heartrate", "SpO2": "sao2", "MAP": "systemicmean", "RR": "respiration"}
LOCF_CONST_H, LOCF_SET_H = 2.0, 12.0
COVERAGE_VARS = ("HR", "SpO2", "MAP_inv", "MAP_nibp", "RR", "FiO2", "PEEP")
VARS_OK_VARS = ("HR", "SpO2", "MAP", "RR", "FiO2", "PEEP")


def _load_v1():
    spec = importlib.util.spec_from_file_location("census_v1", V1)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _split(pairs):
    if not pairs:
        return [], []
    return [float(a) for a, _ in pairs], [float(b) for _, b in pairs]


def _cov_of(times, values, span, max_age_h):
    return hourly_coverage(times, values, span, max_age_h=max_age_h)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--no-coverage", action="store_true")
    args = p.parse_args()
    config = load_config(args.config)
    eicu_dir = config_path(config, "paths", "eicu_dir")
    out_dir = ROOT / "reports" / "fase1_6a"
    out_dir.mkdir(parents=True, exist_ok=True)
    v1 = _load_v1()

    pat = v1._patient_meta(eicu_dir)
    hosp = v1._hospital_meta(eicu_dir)
    stay2hosp = dict(zip(pat.patientunitstayid, pat.hospitalid))
    discharge = dict(zip(pat.patientunitstayid, pat.unitdischargeoffset))
    valid_pids = set(int(x) for x in pat.patientunitstayid)

    # ── Banderas (v2: apache_intub en lugar de apache_vent) ──────────────────
    ap = pd.read_csv(eicu_dir / "apachePredVar.csv.gz",
                     usecols=["patientunitstayid", "oobventday1", "oobintubday1"], low_memory=False)
    apache_vent = set(ap.loc[ap.oobventday1 == 1, "patientunitstayid"].astype(int))
    apache_intub = set(ap.loc[ap.oobintubday1 == 1, "patientunitstayid"].astype(int))

    rc = pd.read_csv(eicu_dir / "respiratoryCare.csv.gz",
                     usecols=["patientunitstayid", "airwaytype"], low_memory=False)
    airway_ett, airway_trach, has_airway = set(), set(), set()
    for pid, g in rc.groupby("patientunitstayid", sort=False):
        pid = int(pid)
        aw = list(g["airwaytype"].dropna().unique())
        if "Oral ETT" in aw or "Nasal ETT" in aw:
            airway_ett.add(pid)
        if "Tracheostomy" in aw:
            airway_trach.add(pid)
        if aw:
            has_airway.add(pid)

    cg = pd.read_csv(eicu_dir / "carePlanGeneral.csv.gz",
                     usecols=["patientunitstayid", "cplgroup", "cplitemvalue"], low_memory=False)
    cpg_vent = set()
    for pid, g in cg.groupby("patientunitstayid", sort=False):
        if any(classify_careplan(gr, v) == CAT_INVASIVE for gr, v in zip(g["cplgroup"], g["cplitemvalue"])):
            cpg_vent.add(int(pid))

    tx = pd.read_csv(eicu_dir / "treatment.csv.gz",
                     usecols=["patientunitstayid", "treatmentstring"], low_memory=False)
    tx_vent = set()
    for pid, g in tx.groupby("patientunitstayid", sort=False):
        if any(classify_treatment(s) == CAT_INVASIVE for s in g["treatmentstring"]):
            tx_vent.add(int(pid))

    inv, settings, peak = v1._load_respcharting(eicu_dir, valid_pids)
    rc_invasive = set(inv)
    print(f"[v2] ajustes invasivos: {len(rc_invasive)}", flush=True)

    INVASIVE_FLAGS = {"apache_intub": apache_intub, "airway_ett": airway_ett,
                      "airway_trach": airway_trach, "rc_invasive": rc_invasive,
                      "cpg_vent": cpg_vent, "tx_vent": tx_vent}
    invasive_confirmed = set().union(*INVASIVE_FLAGS.values())
    print(f"[v2] invasive_confirmed: {len(invasive_confirmed)}", flush=True)

    # ── Cobertura por variable ───────────────────────────────────────────────
    coverage: dict[int, dict] = {}
    diag: list[dict] = []
    if not args.no_coverage:
        # La cobertura solo se mide sobre estancias con ajustes invasivos (las
        # únicas con un intervalo ventilado definible).
        consts = v1._load_constants(eicu_dir, rc_invasive)
        for pid in rc_invasive:
            adj = sorted(inv.get(pid, []))
            if not adj:
                continue
            span = [(adj[0], adj[-1])]
            c = consts.get(pid, {})
            st = settings.get(pid, {})
            cov = {}
            for var, key, age in (("HR", "HR", LOCF_CONST_H), ("SpO2", "SpO2", LOCF_CONST_H),
                                  ("MAP_inv", "MAP", LOCF_CONST_H), ("MAP_nibp", "MAP_NIBP", LOCF_CONST_H)):
                t, v = _split(c.get(key) or [])
                cov[var] = _cov_of(t, v, span, age)
            t, v = _split(st.get("RR_V") or [])
            cov["RR"] = _cov_of(t, v, span, LOCF_SET_H)
            for var in ("FiO2", "PEEP"):
                t, v = _split(st.get(var) or [])
                cov[var] = _cov_of(t, v, span, LOCF_SET_H)
            map_cov = max(cov["MAP_inv"], cov["MAP_nibp"])
            mandatory = {"HR": cov["HR"], "SpO2": cov["SpO2"], "MAP": map_cov,
                         "RR": cov["RR"], "FiO2": cov["FiO2"], "PEEP": cov["PEEP"]}
            coverage[pid] = {
                **{k: round(float(x), 4) for k, x in cov.items()},
                "MAP": round(float(map_cov), 4),
                "vars_ok50": bool(all(x > 0.5 for x in mandatory.values())),
                "limiting_var": min(mandatory, key=lambda k: mandatory[k]),
            }
    print(f"[v2] cobertura: {len(coverage)}", flush=True)

    # ── Concordancia (v2) ────────────────────────────────────────────────────
    universe = set(int(x) for x in pat.patientunitstayid)
    concordance = pairwise_concordance(INVASIVE_FLAGS, universe)

    # ── Métricas por hospital ────────────────────────────────────────────────
    by_hosp: dict[int, list[int]] = defaultdict(list)
    for pid in invasive_confirmed:
        by_hosp[int(stay2hosp.get(pid))].append(pid)

    rows, hosp_flat = [], {}
    for h, pids in by_hosp.items():
        icu_total = int((pat.hospitalid == h).sum())
        n_vent = len(pids)
        n_apache_intub = sum(1 for x in pids if x in apache_intub)
        n_inv = sum(1 for x in pids if x in rc_invasive)
        n_usable = sum(1 for x in pids if coverage.get(x, {}).get("vars_ok50"))
        n_no_intub = sum(1 for x in pids if x in rc_invasive and x not in apache_intub)
        verdict = classify_plausibility(
            n_inv, icu_total, (n_no_intub / n_inv) if n_inv else None)
        inter = [i for x in pids for i in inter_adjustment_intervals(inv.get(x, []))]
        med, iqr = median_iqr(inter)
        # coberturas por variable (fracción de estancias con >=50%)
        cov_frac = {}
        for v in COVERAGE_VARS:
            vals = [coverage[x][v] for x in pids if x in coverage]
            cov_frac[v] = round(100.0 * sum(1 for z in vals if z > 0.5) / len(vals), 1) if vals else None
        # variable limitante (menor % de estancias >=50%), entre las obligatorias
        mandatory_cov = {"HR": cov_frac["HR"], "SpO2": cov_frac["SpO2"],
                         "MAP": None, "RR": cov_frac["RR"], "FiO2": cov_frac["FiO2"],
                         "PEEP": cov_frac["PEEP"]}
        map_vals = [coverage[x]["MAP"] for x in pids if x in coverage]
        mandatory_cov["MAP"] = (round(100.0 * sum(1 for z in map_vals if z > 0.5) / len(map_vals), 1)
                                if map_vals else None)
        cand = {k: v for k, v in mandatory_cov.items() if v is not None}
        limiting = min(cand, key=lambda k: cand[k]) if cand else None
        hmeta = hosp[hosp.hospital_id == h]
        rows.append({
            "hospital_id": h,
            "region": hmeta["region"].iloc[0] if len(hmeta) else None,
            "teaching": hmeta["teachingstatus"].iloc[0] if len(hmeta) else None,
            "beds_category": hmeta["numbedscategory"].iloc[0] if len(hmeta) else None,
            "unit_types": ";".join(sorted(set(pat.loc[pat.patientunitstayid.isin(pids), "unittype"].dropna()))),
            "icu_stays_total": icu_total,
            "vent_stays": n_vent,
            "apache_intub_stays": n_apache_intub,
            "apache_vent_stays": sum(1 for x in pids if x in apache_vent),
            "rc_invasive_stays": n_inv,
            "airway_ett_stays": sum(1 for x in pids if x in airway_ett),
            "airway_trach_stays": sum(1 for x in pids if x in airway_trach),
            "cpg_vent_stays": sum(1 for x in pids if x in cpg_vent),
            "tx_vent_stays": sum(1 for x in pids if x in tx_vent),
            "rc_over_icu": round(n_inv / icu_total, 3) if icu_total else None,
            "rc_over_intub": round(n_inv / n_apache_intub, 3) if n_apache_intub else None,
            "pct_apache_intub_with_invasive": (
                round(100.0 * sum(1 for x in pids if x in apache_intub and x in rc_invasive)
                      / n_apache_intub, 2) if n_apache_intub else None),
            "pct_vent_with_airway": round(100.0 * sum(1 for x in pids if x in has_airway) / n_vent, 2) if n_vent else None,
            "inter_adj_median_min": med,
            "inter_adj_iqr_min": iqr,
            "cov_HR": cov_frac["HR"], "cov_SpO2": cov_frac["SpO2"],
            "cov_MAP_inv": cov_frac["MAP_inv"], "cov_MAP_nibp": cov_frac["MAP_nibp"],
            "cov_MAP": mandatory_cov["MAP"], "cov_RR": cov_frac["RR"],
            "cov_FiO2": cov_frac["FiO2"], "cov_PEEP": cov_frac["PEEP"],
            "limiting_var": limiting,
            "usable_stays": n_usable,
            "coverage_n": sum(1 for x in pids if x in coverage),
            "rc_without_intub_stays": n_no_intub,
            "frac_rc_without_intub": round(n_no_intub / n_inv, 3) if n_inv else None,
            "implausible": verdict == VERDICT_IMPLAUSIBLE,
            "plausibility": verdict,
        })
        # Veredicto del punto 2: permeable si marca como invasiva una mayoría de
        # estancias sin intubación APACHE ni plan de ventilación documentado.
        diag.append({
            "hospital_id": h,
            "icu_stays_total": icu_total,
            "rc_invasive_stays": n_inv,
            "rc_over_icu": round(n_inv / icu_total, 3) if icu_total else None,
            "rc_over_intub": round(n_inv / n_apache_intub, 3) if n_apache_intub else None,
            "frac_without_intub": round(n_no_intub / n_inv, 3) if n_inv else None,
            "frac_without_intub_nor_cpg": round(
                sum(1 for x in pids if x in rc_invasive and x not in apache_intub
                    and x not in cpg_vent) / n_inv, 3) if n_inv else None,
            "plausibility": verdict,
            "implausible": verdict == VERDICT_IMPLAUSIBLE,
        })
        hosp_flat[h] = {
            "vent_stays": n_vent, "rc_invasive_stays": n_inv,
            "frac_rc_invasive": (n_inv / n_vent) if n_vent else 0.0,
            "inter_adj_median_min": med, "usable": n_usable,
            "rc_over_icu": (n_inv / icu_total) if icu_total else 0.0,
            "apache_intub": n_apache_intub, "apache_intub_invasive": sum(1 for x in pids if x in apache_intub and x in rc_invasive),
            "apache_intub_peak24": sum(1 for x in pids if x in apache_intub and any(o <= 1440 for o in peak.get(x, []))),
            "plausibility": verdict,
            "implausible": verdict == VERDICT_IMPLAUSIBLE,
        }

    df = pd.DataFrame(rows).sort_values("vent_stays", ascending=False)
    df.to_csv(out_dir / "eicu_hospitales_v2.csv", index=False)
    print(f"[v2] hospitales: {len(df)}", flush=True)

    # ── Punto 2: plausibilidad (culpables del todo por encima del umbral) ────
    plaus = pd.DataFrame(diag).sort_values("rc_over_icu", ascending=False)
    flagged = plaus[plaus.plausibility == VERDICT_IMPLAUSIBLE]["hospital_id"].astype(int).tolist()
    dense = plaus[plaus.rc_over_icu.notna() & (plaus.rc_over_icu > 0.6)]
    (out_dir / "eicu_plausibilidad.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "umbral_rc_sobre_icu": 0.6,
        "umbral_frac_sin_intubacion": 0.5,
        "investigados": {str(r.hospital_id): {
            "icu_stays_total": int(r.icu_stays_total),
            "rc_invasive_stays": int(r.rc_invasive_stays),
            "rc_over_icu": r.rc_over_icu,
            "rc_over_intub": r.rc_over_intub,
            "frac_without_intub": r.frac_without_intub,
            "frac_without_intub_nor_cpg": r.frac_without_intub_nor_cpg,
            "plausibility": r.plausibility,
            "implausible": bool(r.implausible),
        } for r in plaus[plaus.hospital_id.isin(dense.hospital_id)].itertuples()},
        "implausible_hospitals": sorted(flagged),
        "dense_hospitals": sorted(dense.hospital_id.astype(int).tolist()),
        "permeable_hospitals": sorted(flagged),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Escenarios ───────────────────────────────────────────────────────────
    def sel(pred):
        return {h for h, m in hosp_flat.items() if pred(m)}

    scenarios = {
        "a_mechanical_power": sel(lambda m: m["apache_intub"] >= 10 and
                                  m["apache_intub"] and m["apache_intub_peak24"] / max(m["apache_intub"], 1) >= 0.10),
        "b_50_80_med4h": sel(lambda m: m["vent_stays"] >= 50 and m["frac_rc_invasive"] >= 0.80
                             and m["inter_adj_median_min"] is not None and m["inter_adj_median_min"] <= 240),
        "c_50_80_med2h": sel(lambda m: m["vent_stays"] >= 50 and m["frac_rc_invasive"] >= 0.80
                             and m["inter_adj_median_min"] is not None and m["inter_adj_median_min"] <= 120),
        "d_b_plus_varsok70": sel(lambda m: m["vent_stays"] >= 50 and m["frac_rc_invasive"] >= 0.80
                                 and m["inter_adj_median_min"] is not None and m["inter_adj_median_min"] <= 240
                                 and m["usable"] / max(m["vent_stays"], 1) >= 0.70),
        "e_plaus_med4h_30us": sel(lambda m: m["vent_stays"] >= 50 and m["frac_rc_invasive"] >= 0.80
                                  and m["inter_adj_median_min"] is not None and m["inter_adj_median_min"] <= 240
                                  and m["rc_over_icu"] <= 0.6 and m["usable"] >= 30),
        "e2_plaus_med4h_50us": sel(lambda m: m["vent_stays"] >= 50 and m["frac_rc_invasive"] >= 0.80
                                   and m["inter_adj_median_min"] is not None and m["inter_adj_median_min"] <= 240
                                   and m["rc_over_icu"] <= 0.6 and m["usable"] >= 50),
        "f_plaus_med2h_30us": sel(lambda m: m["vent_stays"] >= 50 and m["frac_rc_invasive"] >= 0.80
                                  and m["inter_adj_median_min"] is not None and m["inter_adj_median_min"] <= 120
                                  and m["rc_over_icu"] <= 0.6 and m["usable"] >= 30),
        "g_plaus_30us": sel(lambda m: m["rc_over_icu"] <= 0.6 and m["usable"] >= 30),
    }

    region_by_h = dict(zip(df.hospital_id, df.region))
    teach_by_h = dict(zip(df.hospital_id, df.teaching))
    stays_per_h = {h: m["usable"] for h, m in hosp_flat.items()}

    def stats(selected):
        sel_h = sorted(selected)
        counts = np.asarray([stays_per_h.get(h, 0) for h in sel_h], dtype=float)
        if counts.size == 0:
            return {"hospitals": 0, "usable": 0, "min": 0, "p25": 0, "median": 0,
                    "p75": 0, "max": 0, "largest_weight": 0.0, "regions": {}, "n_teaching": 0}
        total = counts.sum()
        q1, med, q3 = np.percentile(counts, [25, 50, 75])
        regions: dict[str, int] = {}
        for h in sel_h:
            regions[str(region_by_h.get(h))] = regions.get(str(region_by_h.get(h)), 0) + 1
        return {
            "hospitals": len(sel_h), "usable": int(total),
            "min": int(counts.min()), "p25": float(q1), "median": float(med),
            "p75": float(q3), "max": int(counts.max()),
            "largest_weight": float(counts.max() / total) if total else 0.0,
            "regions": regions,
            "n_teaching": int(sum(1 for h in sel_h if str(teach_by_h.get(h)) == "t")),
        }

    scen_stats = {name: stats(sel_h) for name, sel_h in scenarios.items()}
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "flags": {k: len(v) for k, v in INVASIVE_FLAGS.items()},
        "apache_vent_stays_reference": len(apache_vent),
        "invasive_confirmed": len(invasive_confirmed),
        "concordance": concordance,
        "scenarios": scen_stats,
        "scenario_hospitals": {k: sorted(v) for k, v in scenarios.items()},
    }
    (out_dir / "eicu_scenarios_v2.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Diagnóstico de etiquetas 411/413/412/259 ────────────────────────────
    diag_labels = _diagnose_invasive_labels(eicu_dir, {411, 413, 412, 259}, valid_pids, inv,
                                            apache_intub, cpg_vent)
    prev = json.loads((out_dir / "eicu_plausibilidad.json").read_text(encoding="utf-8"))
    prev["diagnostico_etiquetas"] = diag_labels
    (out_dir / "eicu_plausibilidad.json").write_text(
        json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: out[k] for k in ("flags", "invasive_confirmed", "scenarios")},
                     ensure_ascii=False, indent=2))


def _diagnose_invasive_labels(eicu_dir: Path, hospitals: set[int], valid_pids: set[int],
                              inv: dict, apache_intub: set, cpg_vent: set) -> dict:
    """Qué etiquetas de respiratoryCharting marcan como invasiva cada hospital y
    permeabilidad (también en pacientes sin intubación APACHE/plan)."""
    from src.common.eicu_vent import classify_respchart_label

    pat = pd.read_csv(eicu_dir / "patient.csv.gz", usecols=["patientunitstayid", "hospitalid"])
    s2h = dict(zip(pat.patientunitstayid, pat.hospitalid))
    # estancias del hospital con ajustes invasivos
    by_h: dict[int, set[int]] = {h: set() for h in hospitals}
    for pid in inv:
        h = s2h.get(pid)
        if h in by_h:
            by_h[h].add(int(pid))
    # etiquetas por hospital
    labels: dict[int, dict[str, int]] = {h: {} for h in hospitals}
    for ch in pd.read_csv(eicu_dir / "respiratoryCharting.csv.gz",
                          usecols=["patientunitstayid", "respchartvaluelabel"],
                          chunksize=3_000_000, low_memory=False):
        ch = ch[ch["patientunitstayid"].isin(
            set().union(*by_h.values()) if by_h else set())]
        if ch.empty:
            continue
        m = ch["respchartvaluelabel"].map(lambda x: classify_respchart_label(x) == "invasiva")
        for pid, lab in zip(ch.loc[m, "patientunitstayid"], ch.loc[m, "respchartvaluelabel"]):
            h = s2h.get(pid)
            if h in labels:
                labels[h][lab] = labels[h].get(lab, 0) + 1
    out = {}
    for h in sorted(hospitals):
        stays = by_h[h]
        n_no_intub = sum(1 for p in stays if p not in apache_intub)
        n_no_cpg = sum(1 for p in stays if p not in cpg_vent)
        both_no = sum(1 for p in stays if p not in apache_intub and p not in cpg_vent)
        out[str(h)] = {
            "rc_invasive_stays": len(stays),
            "n_without_apache_intub": n_no_intub,
            "n_without_cpg_vent": n_no_cpg,
            "n_without_both": both_no,
            "frac_without_intub": round(n_no_intub / len(stays), 3) if stays else None,
            "top_invasive_labels": dict(sorted(labels[h].items(), key=lambda x: -x[1])[:12]),
        }
    return out


if __name__ == "__main__":
    main()
