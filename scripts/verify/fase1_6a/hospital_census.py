#!/usr/bin/env python3
"""
scripts/verify/fase1_6a/hospital_census.py
==========================================
Fase 1.6a — Evidencia de ventilación invasiva por estancia y **censo por
hospital** de eICU (sin etiquetas de desenlace).

Genera:
  reports/fase1_6a/eicu_evidence.json     (concordancia entre fuentes + escenarios)
  reports/fase1_6a/eicu_hospitales.csv    (una fila por hospital)

Uso:
    python scripts/verify/fase1_6a/hospital_census.py
"""

from __future__ import annotations

import argparse
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
    LOCF_CONSTANT_H,
    LOCF_SETTING_H,
    documentation_metrics,
    median_iqr,
    scenario_a,
    scenario_b,
    scenario_d,
    scenario_stats,
)
from src.common.eicu_levels import hourly_coverage  # noqa: E402
from src.common.eicu_vent import (  # noqa: E402
    CAT_INVASIVE,
    classify_airway,
    classify_careplan,
    classify_respchart_label,
    classify_treatment,
    pairwise_concordance,
)
from src.common.paths import config_path  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"

CONST_TRACKS = {  # vitalPeriodic (LOCF 2 h)
    "HR": "heartrate", "SpO2": "sao2", "MAP": "systemicmean", "RR": "respiration",
}
SETTING_LABELS = ("FiO2", "PEEP", "Total RR", "Resp Rate Total", "f Total")


def _hospital_meta(eicu_dir: Path) -> pd.DataFrame:
    h = pd.read_csv(eicu_dir / "hospital.csv.gz")
    return h.rename(columns={"hospitalid": "hospital_id"})


def _patient_meta(eicu_dir: Path) -> pd.DataFrame:
    return pd.read_csv(
        eicu_dir / "patient.csv.gz",
        usecols=["patientunitstayid", "hospitalid", "unitdischargeoffset", "unittype"],
    )


def _load_respcharting(eicu_dir: Path, valid_pids: set[int], chunksize: int = 3_000_000):
    """Devuelve (ajustes_invasivos, ajustes_setting) por estancia.

    - ``invasive``: offsets (min) de ajustes de ventilación invasiva.
    - ``settings``: {"FiO2": (t, v), "PEEP": (t, v), "RR_V": (t, v)}.
    """
    inv: dict[int, list[float]] = defaultdict(list)
    peak: dict[int, list[float]] = defaultdict(list)
    fio2: dict[int, list[tuple[float, float]]] = defaultdict(list)
    peep: dict[int, list[tuple[float, float]]] = defaultdict(list)
    rrv: dict[int, list[tuple[float, float]]] = defaultdict(list)

    reader = pd.read_csv(
        eicu_dir / "respiratoryCharting.csv.gz",
        usecols=["patientunitstayid", "respchartoffset", "respchartvaluelabel",
                 "respchartvalue"],
        chunksize=chunksize, low_memory=False,
    )
    for ch in reader:
        ch = ch[ch["patientunitstayid"].isin(valid_pids)]
        if ch.empty:
            continue
        ch["off"] = pd.to_numeric(ch["respchartoffset"], errors="coerce")
        ch["val"] = pd.to_numeric(ch["respchartvalue"], errors="coerce")
        for lab, g in ch.groupby("respchartvaluelabel", sort=False):
            cat = classify_respchart_label(lab)
            if cat == CAT_INVASIVE:
                sub = g[["patientunitstayid", "off"]].dropna()
                for pid, gg in sub.groupby("patientunitstayid", sort=False):
                    inv[int(pid)].extend(gg["off"].astype(float).tolist())
            if lab in ("Peak Insp. Pressure", "Peak Pressure"):
                sub = g[["patientunitstayid", "off"]].dropna()
                for pid, gg in sub.groupby("patientunitstayid", sort=False):
                    peak[int(pid)].extend(gg["off"].astype(float).tolist())
            if lab in ("FiO2", "PEEP"):
                sub = g[["patientunitstayid", "off", "val"]].dropna()
                target = fio2 if lab == "FiO2" else peep
                for pid, gg in sub.groupby("patientunitstayid", sort=False):
                    target[int(pid)].extend(zip(gg["off"].astype(float), gg["val"].astype(float)))
            if lab in ("Total RR", "Resp Rate Total", "f Total"):
                sub = g[["patientunitstayid", "off", "val"]].dropna()
                for pid, gg in sub.groupby("patientunitstayid", sort=False):
                    rrv[int(pid)].extend(zip(gg["off"].astype(float), gg["val"].astype(float)))
    settings: dict[int, dict] = {}
    for pid in set(fio2) | set(peep) | set(rrv):
        settings[pid] = {
            "FiO2": fio2.get(pid, []), "PEEP": peep.get(pid, []), "RR_V": rrv.get(pid, []),
        }
    return dict(inv), settings, dict(peak)


def _load_constants(eicu_dir: Path, valid_pids: set[int], chunksize: int = 3_000_000):
    out: dict[int, dict[str, list[tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
    reader = pd.read_csv(
        eicu_dir / "vitalPeriodic.csv.gz",
        usecols=["patientunitstayid", "observationoffset", *CONST_TRACKS.values()],
        chunksize=chunksize, low_memory=False,
    )
    for ch in reader:
        ch = ch[ch["patientunitstayid"].isin(valid_pids)]
        if ch.empty:
            continue
        ch["off"] = pd.to_numeric(ch["observationoffset"], errors="coerce")
        for var, col in CONST_TRACKS.items():
            sub = ch[["patientunitstayid", "off", col]].dropna()
            if sub.empty:
                continue
            for pid, gg in sub.groupby("patientunitstayid", sort=False):
                out[int(pid)][var].extend(zip(gg["off"].astype(float), gg[col].astype(float)))
    reader = pd.read_csv(
        eicu_dir / "vitalAperiodic.csv.gz",
        usecols=["patientunitstayid", "observationoffset", "noninvasivemean"],
        chunksize=chunksize, low_memory=False,
    )
    for ch in reader:
        ch = ch[ch["patientunitstayid"].isin(valid_pids)]
        if ch.empty:
            continue
        sub = ch.dropna(subset=["noninvasivemean"])
        for pid, gg in sub.groupby("patientunitstayid", sort=False):
            out[int(pid)]["MAP_NIBP"].extend(
                zip(gg["observationoffset"].astype(float), gg["noninvasivemean"].astype(float))
            )
    return {int(k): dict(v) for k, v in out.items()}


def _split_tv(pairs):
    if not pairs:
        return [], []
    t = [float(a) for a, _ in pairs]
    v = [float(b) for _, b in pairs]
    return t, v


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--no-coverage", action="store_true")
    args = p.parse_args()
    config = load_config(args.config)
    eicu_dir = config_path(config, "paths", "eicu_dir")
    out_dir = ROOT / "reports" / "fase1_6a"
    out_dir.mkdir(parents=True, exist_ok=True)

    pat = _patient_meta(eicu_dir)
    hosp = _hospital_meta(eicu_dir)
    stay2hosp = dict(zip(pat.patientunitstayid, pat.hospitalid))
    discharge = dict(zip(pat.patientunitstayid, pat.unitdischargeoffset))
    valid_pids = set(int(x) for x in pat.patientunitstayid)

    # ── Banderas ─────────────────────────────────────────────────────────────
    ap = pd.read_csv(eicu_dir / "apachePredVar.csv.gz",
                     usecols=["patientunitstayid", "oobventday1", "oobintubday1"],
                     low_memory=False)
    apache_vent = set(ap.loc[ap.oobventday1 == 1, "patientunitstayid"].astype(int))
    apache_intub = set(ap.loc[ap.oobintubday1 == 1, "patientunitstayid"].astype(int))

    rc = pd.read_csv(eicu_dir / "respiratoryCare.csv.gz",
                     usecols=["patientunitstayid", "airwaytype", "ventstartoffset"],
                     low_memory=False)
    airway_ett, airway_trach, has_airway, ventstart = set(), set(), set(), {}
    for pid, g in rc.groupby("patientunitstayid", sort=False):
        pid = int(pid)
        aw = [a for a in g["airwaytype"].dropna().unique()]
        cats = {classify_airway(a) for a in aw}
        if "Oral ETT" in aw or "Nasal ETT" in aw:
            airway_ett.add(pid)
        if "Tracheostomy" in aw:
            airway_trach.add(pid)
        if aw:
            has_airway.add(pid)
        vs = pd.to_numeric(g["ventstartoffset"], errors="coerce").dropna()
        vs = vs[vs > 0]
        ventstart[pid] = float(vs.min()) if len(vs) else None

    cg = pd.read_csv(eicu_dir / "carePlanGeneral.csv.gz",
                     usecols=["patientunitstayid", "cplgroup", "cplitemvalue"],
                     low_memory=False)
    cpg_vent = set()
    for pid, g in cg.groupby("patientunitstayid", sort=False):
        if any(classify_careplan(gr, v) == CAT_INVASIVE
               for gr, v in zip(g["cplgroup"], g["cplitemvalue"])):
            cpg_vent.add(int(pid))

    tx = pd.read_csv(eicu_dir / "treatment.csv.gz",
                     usecols=["patientunitstayid", "treatmentstring"], low_memory=False)
    tx_vent = set()
    for pid, g in tx.groupby("patientunitstayid", sort=False):
        if any(classify_treatment(s) == CAT_INVASIVE for s in g["treatmentstring"]):
            tx_vent.add(int(pid))

    # ── respiratoryCharting (ajustes invasivos) ──────────────────────────────
    inv, settings, peak = _load_respcharting(eicu_dir, valid_pids)
    rc_invasive = set(inv)
    print(f"[census] estancias con ajustes invasivos: {len(rc_invasive)}")

    # ── Cobertura ────────────────────────────────────────────────────────────
    vent_pids = rc_invasive | apache_vent | airway_ett | airway_trach | cpg_vent | tx_vent
    coverage: dict[int, dict] = {}
    if not args.no_coverage:
        consts = _load_constants(eicu_dir, vent_pids)
        for pid in vent_pids:
            adj = sorted(inv.get(pid, []))
            if not adj:
                continue
            span = [(adj[0], adj[-1])]
            c = consts.get(pid, {})
            st = settings.get(pid, {})
            cov = {}
            for var, src in (("HR", c.get("HR")), ("SpO2", c.get("SpO2")),
                             ("MAP", c.get("MAP")), ("RR", st.get("RR_V"))):
                t, v = _split_tv(src or [])
                cov[var] = hourly_coverage(t, v, span, max_age_h=LOCF_CONSTANT_H)
            mapnibp = c.get("MAP_NIBP")
            t, v = _split_tv(mapnibp or [])
            cov["MAP"] = max(cov["MAP"], hourly_coverage(t, v, span, max_age_h=LOCF_CONSTANT_H))
            for var in ("FiO2", "PEEP"):
                t, v = _split_tv(st.get(var) or [])
                cov[var] = hourly_coverage(t, v, span, max_age_h=LOCF_SETTING_H)
            coverage[pid] = {
                **{k: round(float(x), 4) for k, x in cov.items()},
                "vars_ok50": bool(all(v > 0.5 for v in cov.values())),
                "vars_ok80": bool(all(v > 0.8 for v in cov.values())),
                "n_vars": len(cov),
            }

    # ── Métricas por estancia ────────────────────────────────────────────────
    stay_metrics: dict[int, dict] = {}
    for pid in vent_pids:
        m = documentation_metrics(
            adj_offsets=inv.get(pid, []),
            discharge_min=float(discharge.get(pid, 0.0)),
            ventstartoffset=ventstart.get(pid),
            has_airway=pid in has_airway,
        )
        m["hospital_id"] = int(stay2hosp.get(pid))
        m["cov"] = coverage.get(pid)
        stay_metrics[pid] = m

    # ── Concordancia entre fuentes ───────────────────────────────────────────
    flags = {
        "apache_vent": apache_vent, "apache_intub": apache_intub,
        "airway_ett": airway_ett, "airway_trach": airway_trach,
        "rc_invasive": rc_invasive, "cpg_vent": cpg_vent, "tx_vent": tx_vent,
    }
    universe = set(int(x) for x in pat.patientunitstayid)
    concordance = pairwise_concordance(flags, universe)

    # ── Censo por hospital ───────────────────────────────────────────────────
    by_hosp: dict[int, list[int]] = defaultdict(list)
    for pid, m in stay_metrics.items():
        by_hosp[m["hospital_id"]].append(pid)

    rows = []
    hosp_flat: dict[int, dict] = {}
    for h, pids in by_hosp.items():
        n_vent = len(pids)
        n_apache = sum(1 for x in pids if x in apache_vent)
        n_apache_inv = sum(1 for x in pids if x in apache_vent and x in rc_invasive)
        n_apache_peak = sum(
            1 for x in pids
            if x in apache_vent and any(o <= 24 * 60 for o in peak.get(x, []))
        )
        inter = [i for x in pids for i in stay_metrics[x]["inter_adj"]]
        med, iqr = median_iqr(inter)
        n_with_airway = sum(1 for x in pids if stay_metrics[x]["has_airway"])
        n_vs = sum(1 for x in pids if stay_metrics[x]["has_ventstart"])
        n_vs_sup = sum(1 for x in pids if stay_metrics[x]["ventstart_supported"])
        n_last1h = sum(1 for x in pids if stay_metrics[x]["last_adj_lt1h"])
        n_cov = sum(1 for x in pids if stay_metrics[x]["cov"])
        n_ok50 = sum(1 for x in pids if stay_metrics[x]["cov"]
                     and stay_metrics[x]["cov"]["vars_ok50"])
        n_ok80 = sum(1 for x in pids if stay_metrics[x]["cov"]
                     and stay_metrics[x]["cov"]["vars_ok80"])
        unit_types = sorted(set(pat.loc[pat.patientunitstayid.isin(pids), "unittype"].dropna()))
        hmeta = hosp[hosp.hospital_id == h]
        rows.append({
            "hospital_id": h,
            "region": hmeta["region"].iloc[0] if len(hmeta) else None,
            "teaching": hmeta["teachingstatus"].iloc[0] if len(hmeta) else None,
            "beds_category": hmeta["numbedscategory"].iloc[0] if len(hmeta) else None,
            "unit_types": ";".join(unit_types),
            "icu_stays_total": int((pat.hospitalid == h).sum()),
            "vent_stays": n_vent,
            "apache_vent_stays": n_apache,
            "rc_invasive_stays": sum(1 for x in pids if x in rc_invasive),
            "airway_ett_stays": sum(1 for x in pids if x in airway_ett),
            "airway_trach_stays": sum(1 for x in pids if x in airway_trach),
            "cpg_vent_stays": sum(1 for x in pids if x in cpg_vent),
            "tx_vent_stays": sum(1 for x in pids if x in tx_vent),
            "pct_apache_vent_with_invasive": round(100.0 * n_apache_inv / n_apache, 2) if n_apache else None,
            "pct_vent_with_airway": round(100.0 * n_with_airway / n_vent, 2) if n_vent else None,
            "pct_ventstart_supported": round(100.0 * n_vs_sup / n_vs, 2) if n_vs else None,
            "pct_last_adj_lt1h": round(100.0 * n_last1h / n_vent, 2) if n_vent else None,
            "inter_adj_median_min": med,
            "inter_adj_iqr_min": iqr,
            "n_with_adjustments": sum(1 for x in pids if stay_metrics[x]["rc_invasive"]),
            "vars_ok50_n": n_ok50, "vars_ok80_n": n_ok80, "coverage_n": n_cov,
        })
        hosp_flat[h] = {
            "vent_stays": n_vent,
            "apache_vent": n_apache,
            "apache_vent_invasive": n_apache_inv,
            "apache_vent_peak24": n_apache_peak,
            "frac_rc_invasive": (sum(1 for x in pids if x in rc_invasive) / n_vent) if n_vent else 0.0,
            "inter_adj_median_min": med,
            "vars_ok50_n": n_ok50,
        }

    hosp_df = pd.DataFrame(rows).sort_values("vent_stays", ascending=False)
    hosp_df.to_csv(out_dir / "eicu_hospitales.csv", index=False)

    # ── Escenarios ───────────────────────────────────────────────────────────
    sa = scenario_a(hosp_flat)
    sb4 = scenario_b(hosp_flat, max_median_gap_min=240)
    sb2 = scenario_b(hosp_flat, max_median_gap_min=120)
    sd = scenario_d(hosp_flat)
    stays_per_hosp = {h: m["vent_stays"] for h, m in hosp_flat.items()}
    scenarios = {
        "a_mechanical_power": scenario_stats(stays_per_hosp, sa),
        "b_50stays_80pct_med4h": scenario_stats(stays_per_hosp, sb4),
        "c_50stays_80pct_med2h": scenario_stats(stays_per_hosp, sb2),
        "d_b_plus_varsok70": scenario_stats(stays_per_hosp, sd),
    }

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_hospitals": int(pat.hospitalid.nunique()),
        "n_stays_total": int(len(pat)),
        "n_vent_stays": len(vent_pids),
        "flags": {k: len(v) for k, v in flags.items()},
        "concordance": concordance,
        "scenarios": scenarios,
        "scenario_hospitals": {
            "a": sorted(sa), "b": sorted(sb4), "c": sorted(sb2), "d": sorted(sd),
        },
    }
    (out_dir / "eicu_evidence.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: summary[k] for k in
                      ("n_hospitals", "n_stays_total", "n_vent_stays", "flags", "scenarios")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
