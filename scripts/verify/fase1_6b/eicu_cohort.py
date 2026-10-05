#!/usr/bin/env python3
"""
scripts/verify/fase1_6b/eicu_cohort.py
======================================
Fase 1.6b — **punto 0**: regla de permeabilidad sin requisito de densidad y
cohorte definitiva de eICU.

Regla nueva (Fase 1.6b): un hospital es ``implausible`` si **≥ 50 %** de sus
estancias con ajustes invasivos de ``respiratoryCharting`` **no** tienen ni
``apache_intub`` (``oobIntubDay1``) ni ``cpg_vent`` (plan de ventilación en
``carePlanGeneral``). A diferencia del veredicto de la Fase 1.6a-bis, **no** se
exige que ``rc_invasive/icu_stays_total`` sea alto: la permeabilidad se juzga
solo por la falta de corroboración clínica.

Cohorte de eICU = escenario **e2 con la regla nueva** (≥ 50 estancias ventiladas,
≥ 80 % con ajustes invasivos, anotación mediana ≤ 4 h, ≥ 50 estancias
utilizables, hospital no implausible), que incluye los hospitales
``concentrado_concordante`` (p. ej. el 259).

Salidas (``reports/fase1_6b/``):
  ``eicu_cohort.json``          cohorte, recuentos y cambios de veredicto
  ``eicu_hospitales_v3.csv``    censo con la regla nueva

Uso:
    python scripts/verify/fase1_6b/eicu_cohort.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.eicu_census import classify_permeability, frac_without_support  # noqa: E402
from src.common.eicu_vent import RESPCHART_INVASIVE, classify_careplan  # noqa: E402
from src.common.paths import config_path  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
V2_CSV = ROOT / "reports" / "fase1_6a" / "eicu_hospitales_v2.csv"
OUT_DIR = ROOT / "reports" / "fase1_6b"


def load_invasive_adjustment_stays(eicu_dir: Path, valid_pids: set[int],
                                   *, chunksize: int = 3_000_000) -> set[int]:
    """Estancias con ≥ 1 ajuste invasivo (selección vectorizada por etiqueta)."""
    out: set[int] = set()
    reader = pd.read_csv(
        eicu_dir / "respiratoryCharting.csv.gz",
        usecols=["patientunitstayid", "respchartvaluelabel"],
        chunksize=chunksize, low_memory=False,
    )
    for ch in reader:
        ch = ch[ch["patientunitstayid"].isin(valid_pids)]
        if ch.empty:
            continue
        sel = ch["respchartvaluelabel"].isin(RESPCHART_INVASIVE)
        if sel.any():
            out.update(int(x) for x in ch.loc[sel, "patientunitstayid"].unique())
    return out


def load_careplan_vent_stays(eicu_dir: Path, valid_pids: set[int],
                             *, chunksize: int = 2_000_000) -> set[int]:
    """Estancias con plan de ventilación invasiva (``carePlanGeneral``).

    Solo se clasifican las filas de los grupos ``Ventilation``/``Airway`` (el
    resto devuelve ``ambigua`` por construcción), lo que evita recorrer millones
    de filas irrelevantes.
    """
    out: set[int] = set()
    reader = pd.read_csv(
        eicu_dir / "carePlanGeneral.csv.gz",
        usecols=["patientunitstayid", "cplgroup", "cplitemvalue"],
        chunksize=chunksize, low_memory=False,
    )
    for ch in reader:
        ch = ch[ch["patientunitstayid"].isin(valid_pids)]
        if ch.empty:
            continue
        grp = ch["cplgroup"].astype("string").str.strip().str.lower()
        ch = ch[grp.isin(("ventilation", "airway"))]
        if ch.empty:
            continue
        sel = [
            classify_careplan(g, v) == "invasiva"
            for g, v in zip(ch["cplgroup"], ch["cplitemvalue"])
        ]
        if any(sel):
            out.update(int(x) for x in ch.loc[sel, "patientunitstayid"].unique())
    return out


def main() -> None:
    config = load_config(CONFIG)
    eicu_dir = config_path(config, "paths", "eicu_dir")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    pat = pd.read_csv(eicu_dir / "patient.csv.gz",
                      usecols=["patientunitstayid", "hospitalid"])
    valid = set(int(x) for x in pat["patientunitstayid"])
    stay2hosp = dict(zip(pat.patientunitstayid.astype(int), pat.hospitalid.astype(int)))

    ap = pd.read_csv(eicu_dir / "apachePredVar.csv.gz",
                     usecols=["patientunitstayid", "oobintubday1"], low_memory=False)
    apache_intub = set(ap.loc[ap.oobintubday1 == 1, "patientunitstayid"].astype(int))

    print("[p0] carePlanGeneral...", flush=True)
    cpg_vent = load_careplan_vent_stays(eicu_dir, valid)
    print("[p0] respiratoryCharting...", flush=True)
    rc_invasive = load_invasive_adjustment_stays(eicu_dir, valid)
    print(f"[p0] rc_invasive={len(rc_invasive)} cpg_vent={len(cpg_vent)} "
          f"apache_intub={len(apache_intub)}", flush=True)

    v2 = pd.read_csv(V2_CSV)
    rows: list[dict] = []
    for h, g in v2.groupby("hospital_id"):
        h = int(h)
        pids = [p for p in rc_invasive if stay2hosp.get(p) == h]
        n_inv = len(pids)
        n_no_both = sum(1 for p in pids if p not in apache_intub and p not in cpg_vent)
        frac = frac_without_support(n_inv, n_no_both)
        rows.append({
            "hospital_id": h,
            "rc_invasive_stays_v3": n_inv,
            "rc_without_intub_nor_cpg_stays": n_no_both,
            "frac_rc_without_intub_nor_cpg": (round(frac, 3) if frac is not None else None),
            "plausibility_v2": (
                v2.loc[v2.hospital_id == h, "plausibility"].iloc[0]
                if "plausibility" in v2.columns else None
            ),
            "implausible_v2": bool(v2.loc[v2.hospital_id == h, "implausible"].iloc[0]),
            "implausible_v3": classify_permeability(frac) == "implausible_permeable",
        })
    new = pd.DataFrame(rows)
    merged = v2.merge(new, on="hospital_id", how="left")
    # Autocomprobación: el recuento nuevo debe reproducir el del censo v2.
    dif = (merged["rc_invasive_stays_v3"] - merged["rc_invasive_stays"]).abs().max()
    print(f"[p0] comprobacion rc_invasive_stays (v3 vs v2): max|dif| = {dif}", flush=True)
    merged["plausibility"] = merged["implausible_v3"].map(
        {True: "implausible_permeable", False: "ok"})
    merged["implausible"] = merged["implausible_v3"]
    merged.to_csv(OUT_DIR / "eicu_hospitales_v3.csv", index=False)

    # ── Cambios de veredicto ────────────────────────────────────────────────
    newly = sorted(new.loc[new.implausible_v3 & ~new.implausible_v2, "hospital_id"].tolist())
    recovered = sorted(new.loc[~new.implausible_v3 & new.implausible_v2, "hospital_id"].tolist())

    # ── Cohorte: escenario e2 con la regla nueva ────────────────────────────
    ok = merged[~merged.implausible]
    e2 = ok[(ok.vent_stays >= 50) & (ok.rc_invasive_stays / ok.vent_stays >= 0.80)
            & ok.inter_adj_median_min.notna() & (ok.inter_adj_median_min <= 240)
            & (ok.usable_stays >= 50)]
    cohort = sorted(int(x) for x in e2.hospital_id)
    cohorts_dense = sorted(int(x) for x in e2.loc[
        e2.frac_rc_without_intub_nor_cpg.notna()
        & (e2.frac_rc_without_intub_nor_cpg >= 0.1), "hospital_id"])

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regla": ("implausible si >= 50 % de las estancias con ajustes invasivos "
                  "no tienen ni apache_intub ni cpg_vent"),
        "n_hospitales": int(len(new)),
        "n_implausible_v3": int(new.implausible_v3.sum()),
        "implausible_v3": sorted(new.loc[new.implausible_v3, "hospital_id"].tolist()),
        "nuevos_implausible": newly,
        "recuperados": recovered,
        "detalle_cambios": new[new.hospital_id.isin(newly + recovered)][
            ["hospital_id", "rc_invasive_stays_v3", "rc_without_intub_nor_cpg_stays",
             "frac_rc_without_intub_nor_cpg", "implausible_v2", "implausible_v3"]
        ].to_dict(orient="records"),
        "cohorte_escenario": "e2 + regla de permeabilidad nueva (incluye concentrado_concordante)",
        "n_cohorte": len(cohort),
        "hospitals": cohort,
        "n_cohorte_usable": int(e2.usable_stays.sum()),
        "cohorte_detalle": [
            {"hospital_id": int(r.hospital_id),
             "usable_stays": int(r.usable_stays),
             "vent_stays": int(r.vent_stays),
             "inter_adj_median_min": (None if pd.isna(r.inter_adj_median_min)
                                      else float(r.inter_adj_median_min)),
             "frac_rc_without_intub_nor_cpg": (None if pd.isna(r.frac_rc_without_intub_nor_cpg)
                                               else float(r.frac_rc_without_intub_nor_cpg))}
            for r in e2.sort_values("usable_stays", ascending=False).itertuples()
        ],
    }
    (OUT_DIR / "eicu_cohort.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in (
        "n_implausible_v3", "implausible_v3", "nuevos_implausible", "recuperados",
        "n_cohorte", "n_cohorte_usable")}, ensure_ascii=False, indent=2))
    print("hospitales cohorte:", cohort)
    print("de la cohorte con >=10 % sin corroboracion:", cohorts_dense)


if __name__ == "__main__":
    main()
