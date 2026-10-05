#!/usr/bin/env python3
"""
create_dataset/build_eicu_index.py
==================================
Construye el **índice de casos de eICU** (mismo esquema que Clínic/VitalDB/MIMIC,
con ``hospital_id``) y **clasifica cada estancia** ventilada en un nivel de
completitud A/B/C/D (Fase 1.5, punto 1).

Fuente: ``datasets/eicu_collaborative/*.csv.gz``

- ``patient``: ``hospitalid``, ``unitdischargeoffset``, ``unitdischargestatus``.
- ``respiratoryCare``: ``ventstartoffset`` / ``ventendoffset`` (fin casi nunca
  documentado), ``respcarestatusoffset``, ``airwaytype``.
- ``respiratoryCharting``: ajustes de ventilación invasiva (modo, PEEP, volumen
  tidal, PIP, FR total) + FiO2/PEEP para la cobertura.
- ``vitalPeriodic`` / ``vitalAperiodic``: HR, SpO2, MAP y RR para la cobertura.

Salida (versionada, nunca se sobrescribe):
  ``<eicu_cases_out>/eicu_cases_index.json``
  ``<eicu_cases_out>/eicu_levels_summary.json``

Uso:
    python -m src.create_dataset.build_eicu_index
    python -m src.create_dataset.build_eicu_index --no-coverage
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from src.common.d5_events import d5_censor_for_window, is_trach_text, trach_time_from_offset_rows
from src.common.eicu_levels import (
    INVASIVE_ADJUSTMENT_LABELS,
    LEVEL_A,
    LEVEL_B,
    LEVEL_C,
    LEVEL_D,
    CoverageResult,
    StayVentInputs,
    classify_eicu_stay,
    hourly_coverage,
)
from src.common.eicu_rules import eicu_t0_minutes, merge_vent_episodes, sanitize_vent_episodes
from src.common.extubation import CAUSE_DEATH, CAUSE_TRANSFER, resolve_extubation
from src.common.labels import (
    FAILURE_WINDOWS_H,
    assign_label,
    attempts_from_pairs,
    labels_to_dict,
)
from src.common.paths import config_path, repo_root
from src.stage0.io.versioning import compute_config_hash, load_config

logger = logging.getLogger(__name__)

_MIN_PER_HOUR = 60.0

# ── Carga de tablas ──────────────────────────────────────────────────────────

def load_patients(eicu_dir: Path) -> pd.DataFrame:
    return pd.read_csv(
        eicu_dir / "patient.csv.gz",
        usecols=["patientunitstayid", "hospitalid", "unitdischargeoffset",
                 "unitdischargestatus"],
    )


def load_respcare(eicu_dir: Path) -> pd.DataFrame:
    df = pd.read_csv(
        eicu_dir / "respiratoryCare.csv.gz",
        usecols=["patientunitstayid", "ventstartoffset", "ventendoffset",
                 "respcarestatusoffset", "airwaytype"],
        low_memory=False,
    )
    return df.dropna(subset=["ventstartoffset"])


def impute_vent_end_offsets(g: pd.DataFrame) -> pd.DataFrame:
    """Imputa ``ventendoffset`` cuando es 0/nulo (igual que el adaptador).

    eICU no documenta el fin de ventilación (``ventendoffset``) en la práctica:
    se aproxima con el último ``respcarestatusoffset`` del episodio. Esta
    imputación se marca en el evento (``end_documented=False``) para que el
    clasificador la distinga de un fin documentado.
    """
    d = g.copy()
    d["ventendoffset"] = pd.to_numeric(d["ventendoffset"], errors="coerce").fillna(0)
    max_status = d.groupby("ventstartoffset")["respcarestatusoffset"].transform("max")
    mask = d["ventendoffset"] <= 0
    d.loc[mask, "ventendoffset"] = max_status[mask]
    bad = d["ventendoffset"] <= d["ventstartoffset"]
    d.loc[bad, "ventendoffset"] = d.loc[bad, "ventstartoffset"] + 1
    return d


def load_respcharting(
    eicu_dir: Path,
    valid_pids: set[int],
    *,
    chunksize: int = 2_000_000,
) -> tuple[dict[int, list[float]], dict[int, dict[str, tuple[list[float], list[float]]]]]:
    """Carga ajustes invasivos y FiO2/PEEP desde ``respiratoryCharting``.

    Selección **vectorizada** por ``isin`` de etiquetas (no ``groupby`` por
    paciente en cada chunk) y un único agrupamiento final: es lo que hace
    viable recorrer 20 M de filas.

    Devuelve ``(adjustments, vitals)``:
      - ``adjustments``: pid -> offsets (min) de ajustes invasivos (ordenados).
      - ``vitals``: pid -> {"FiO2": (times, values), "PEEP": (times, values)}.
    """
    inv_parts: list[pd.DataFrame] = []
    fi_parts: list[pd.DataFrame] = []
    peep_parts: list[pd.DataFrame] = []

    reader = pd.read_csv(
        eicu_dir / "respiratoryCharting.csv.gz",
        usecols=["patientunitstayid", "respchartoffset", "respchartvaluelabel",
                 "respchartvalue"],
        chunksize=chunksize, low_memory=False,
    )
    for chunk in reader:
        ch = chunk[chunk["patientunitstayid"].isin(valid_pids)]
        if ch.empty:
            continue
        labels = ch["respchartvaluelabel"].astype("string")
        off = pd.to_numeric(ch["respchartoffset"], errors="coerce")
        pid = ch["patientunitstayid"].astype("int64")

        inv = labels.isin(INVASIVE_ADJUSTMENT_LABELS) & off.notna()
        if inv.any():
            inv_parts.append(pd.DataFrame(
                {"pid": pid[inv], "offset": off[inv].astype("float64")}
            ))
        val = pd.to_numeric(ch["respchartvalue"], errors="coerce")
        usable = off.notna() & val.notna()
        fi = labels.eq("FiO2") & usable
        if fi.any():
            fi_parts.append(pd.DataFrame(
                {"pid": pid[fi], "offset": off[fi].astype("float64"),
                 "value": val[fi].astype("float64")}
            ))
        pe = labels.eq("PEEP") & usable
        if pe.any():
            peep_parts.append(pd.DataFrame(
                {"pid": pid[pe], "offset": off[pe].astype("float64"),
                 "value": val[pe].astype("float64")}
            ))

    adjustments: dict[int, list[float]] = {}
    if inv_parts:
        inv = pd.concat(inv_parts, ignore_index=True).sort_values(
            ["pid", "offset"], kind="stable"
        )
        adjustments = {
            int(p): g["offset"].to_numpy(dtype=np.float64).tolist()
            for p, g in inv.groupby("pid", sort=False)
        }

    vitals: dict[int, dict[str, tuple[list[float], list[float]]]] = {}
    for key, parts in (("FiO2", fi_parts), ("PEEP", peep_parts)):
        if not parts:
            continue
        df = pd.concat(parts, ignore_index=True).sort_values(
            ["pid", "offset"], kind="stable"
        )
        for p, g in df.groupby("pid", sort=False):
            vitals.setdefault(int(p), {})[key] = (
                g["offset"].to_numpy(dtype=np.float64).tolist(),
                g["value"].to_numpy(dtype=np.float64).tolist(),
            )
    return adjustments, vitals


def load_vitals(
    eicu_dir: Path, valid_pids: set[int], *, chunksize: int = 2_000_000,
) -> dict[int, dict[str, tuple[list[float], list[float]]]]:
    """Carga HR/SpO2/MAP/RR de ``vitalPeriodic`` y MAP no invasiva.

    Selección vectorizada y un único agrupamiento por variable al final.
    """
    cols = {
        "HR": "heartrate", "SpO2": "sao2", "MAP": "systemicmean", "RR": "respiration",
    }
    parts: dict[str, list[pd.DataFrame]] = defaultdict(list)

    reader = pd.read_csv(
        eicu_dir / "vitalPeriodic.csv.gz",
        usecols=["patientunitstayid", "observationoffset", *cols.values()],
        chunksize=chunksize, low_memory=False,
    )
    for chunk in reader:
        ch = chunk[chunk["patientunitstayid"].isin(valid_pids)]
        if ch.empty:
            continue
        pid = ch["patientunitstayid"].astype("int64")
        off = pd.to_numeric(ch["observationoffset"], errors="coerce")
        for var, col in cols.items():
            v = pd.to_numeric(ch[col], errors="coerce")
            ok = off.notna() & v.notna()
            if ok.any():
                parts[var].append(pd.DataFrame(
                    {"pid": pid[ok], "offset": off[ok].astype("float64"),
                     "value": v[ok].astype("float64")}
                ))

    reader = pd.read_csv(
        eicu_dir / "vitalAperiodic.csv.gz",
        usecols=["patientunitstayid", "observationoffset", "noninvasivemean"],
        chunksize=chunksize, low_memory=False,
    )
    for chunk in reader:
        ch = chunk[chunk["patientunitstayid"].isin(valid_pids)]
        if ch.empty:
            continue
        pid = ch["patientunitstayid"].astype("int64")
        off = pd.to_numeric(ch["observationoffset"], errors="coerce")
        v = pd.to_numeric(ch["noninvasivemean"], errors="coerce")
        ok = off.notna() & v.notna()
        if ok.any():
            parts["MAP_NIBP"].append(pd.DataFrame(
                {"pid": pid[ok], "offset": off[ok].astype("float64"),
                 "value": v[ok].astype("float64")}
            ))

    out: dict[int, dict[str, tuple[list[float], list[float]]]] = defaultdict(dict)
    for var, frames in parts.items():
        if not frames:
            continue
        df = pd.concat(frames, ignore_index=True).sort_values(
            ["pid", "offset"], kind="stable"
        )
        for p, g in df.groupby("pid", sort=False):
            out[int(p)][var] = (
                g["offset"].to_numpy(dtype=np.float64).tolist(),
                g["value"].to_numpy(dtype=np.float64).tolist(),
            )
    return {int(k): dict(v) for k, v in out.items()}


# ── Construcción y clasificación ─────────────────────────────────────────────

def _stay_coverage(
    pdata: dict,
    vent_spans_min: Sequence[tuple[float, float]],
) -> CoverageResult:
    """Fracción de horas ventiladas con valor útil (LOCF 4 h) por variable."""
    fracs: dict[str, float] = {}
    var_sources = {
        "HR": [("vitals", "HR")],
        "SpO2": [("vitals", "SpO2")],
        "MAP": [("vitals", "MAP"), ("vitals", "MAP_NIBP")],
        "RR": [("vitals", "RR")],
        "FiO2": [("resp", "FiO2")],
        "PEEP": [("resp", "PEEP")],
    }
    for var, sources in var_sources.items():
        best = 0.0
        for store, key in sources:
            d = pdata.get(store) or {}
            if key not in d:
                continue
            times, values = d[key]
            best = max(best, hourly_coverage(times, values, vent_spans_min))
        fracs[var] = best
    return CoverageResult(fractions=fracs)


def build_eicu_index(
    patients: pd.DataFrame,
    respcare: pd.DataFrame,
    adjustments: dict[int, list[float]],
    vitals: dict[int, dict],
    resp_vitals: dict[int, dict],
    *,
    with_coverage: bool = True,
) -> tuple[dict, dict]:
    """Construye el índice de eventos y el resumen de niveles."""
    pat_by_id = patients.set_index("patientunitstayid")
    care_groups = {int(pid): g for pid, g in respcare.groupby("patientunitstayid")}

    events: list[dict] = []
    excluded_events: list[dict] = []
    level_counts: Counter = Counter()
    by_hospital_level: dict[int, Counter] = defaultdict(Counter)
    success_by_level: dict[str, Counter] = defaultdict(Counter)
    causes_by_level: dict[str, Counter] = defaultdict(Counter)
    failure_events_by_level: Counter = Counter()
    events_by_level: Counter = Counter()
    vars_ok_50: Counter = Counter()
    vars_ok_80: Counter = Counter()
    hospitals_with_50_A: Counter = Counter()
    duration_by_level: dict[str, list[float]] = defaultdict(list)

    for pid, g in care_groups.items():
        if pid not in pat_by_id.index:
            continue
        meta = pat_by_id.loc[pid]
        discharge = float(meta["unitdischargeoffset"])
        hospital_id = int(meta["hospitalid"])
        died = str(meta["unitdischargestatus"]).strip().lower() == "expired"

        raw_end = g["ventendoffset"].fillna(0).astype(float)
        end_documented = bool((raw_end > 0).any())

        g = impute_vent_end_offsets(g)
        san = sanitize_vent_episodes(g, discharge)
        merged = merge_vent_episodes(san.episodes)
        t0 = eicu_t0_minutes(san.episodes)
        if t0 is None or merged.empty or discharge <= t0:
            continue

        # Traqueostomía (previa a t0 -> exclusión).
        trach_off = None
        if "airwaytype" in g.columns:
            mask = g["airwaytype"].astype(str).map(is_trach_text)
            trach_off = trach_time_from_offset_rows(g.loc[mask, "respcarestatusoffset"])
        trach_h = None if trach_off is None else (trach_off - t0) / _MIN_PER_HOUR
        if trach_h is not None and trach_h < 0:
            excluded_events.append({
                "event_id": f"eicu_{pid}_excluded",
                "cohort": "eicu",
                "patientunitstayid": pid,
                "hospital_id": hospital_id,
                "exclusion_reason": "trach_preexisting",
            })
            continue

        start_h = (float(merged.iloc[0]["ventstartoffset"]) - t0) / _MIN_PER_HOUR
        end_h = (float(merged.iloc[-1]["ventendoffset"]) - t0) / _MIN_PER_HOUR
        discharge_h = (discharge - t0) / _MIN_PER_HOUR
        death_h = discharge_h if died else None
        died_ventilated = bool(died and discharge_h <= end_h + 1e-9)

        # Último ajuste invasivo dentro del episodio (<= fin de VM).
        adj_list = [a for a in adjustments.get(pid, []) if a <= float(merged.iloc[-1]["ventendoffset"]) + 1e-6]
        last_adj_h = ((max(adj_list) - t0) / _MIN_PER_HOUR) if adj_list else None

        inp = StayVentInputs(
            start_h=start_h, end_h=end_h, end_documented=end_documented,
            discharge_h=discharge_h, death_h=death_h,
            died_ventilated=died_ventilated, trach_h=trach_h,
            last_invasive_adj_h=last_adj_h,
        )
        level = classify_eicu_stay(inp)

        events_by_level[level.level] += 1
        level_counts[level.level] += 1
        by_hospital_level[hospital_id][level.level] += 1
        if level.level == LEVEL_A:
            hospitals_with_50_A[hospital_id] += 1
        duration_by_level[level.level].append(end_h - start_h)

        # Etiquetas (D3) con la regla común (punto 0) + D5.
        attempts: list[dict] = []
        for i in range(len(merged)):
            s_h = (float(merged.iloc[i]["ventstartoffset"]) - t0) / _MIN_PER_HOUR
            e_h = (float(merged.iloc[i]["ventendoffset"]) - t0) / _MIN_PER_HOUR
            r_h = (
                (float(merged.iloc[i + 1]["ventstartoffset"]) - t0) / _MIN_PER_HOUR
                if i + 1 < len(merged) else None
            )
            attempts.append({
                "attempt_idx": i,
                "vent_start_h": round(s_h, 4),
                "vent_end_h": round(e_h, 4),
                "reintubation_h": (None if r_h is None else round(r_h, 4)),
            })
        pairs: list[tuple[float, Optional[float]]] = [
            (a["vent_end_h"], a["reintubation_h"]) for a in attempts
        ]

        ext = resolve_extubation(
            last_vent_end_h=end_h, observation_end_h=discharge_h,
            stay_end_h=discharge_h, death_h=death_h,
            died_ventilated=died_ventilated,
        )
        label_pairs = pairs if ext.is_extubation else pairs[:-1]
        labels: dict[str, dict] = {}
        d5_by_window: dict[str, dict] = {}
        for w in FAILURE_WINDOWS_H:
            dec = d5_censor_for_window(
                failure_window_h=float(w), last_disconnect_h=end_h,
                trach_time_h=(trach_h if trach_h is not None and trach_h >= 0 else None),
                death_time_h=death_h, died_ventilated=died_ventilated,
            )
            if dec.censor_cause is not None:
                cause, t_censor = dec.censor_cause, dec.censor_time_h
            elif not ext.is_extubation:
                cause, t_censor = ext.censor_cause, ext.censor_time_h
            else:
                cause, t_censor = None, None
            lab = assign_label(
                attempts_from_pairs(label_pairs), obs_end_h=discharge_h,
                failure_window_h=float(w), censor_cause=cause,
                censor_time_h=t_censor,
            )
            labels[f"{int(w)}h"] = lab
            d5_by_window[f"{int(w)}h"] = {
                "censor_cause": cause, "censor_time_h": t_censor,
            }
        labels_dict = labels_to_dict(labels)

        if labels["48h"].n_failed_attempts > 0:
            failure_events_by_level[level.level] += 1
        success_by_level[level.level][labels["48h"].event_type] += 1
        if labels["48h"].censor_cause:
            causes_by_level[level.level][labels["48h"].censor_cause] += 1

        # Cobertura (sobre los tramos ventilados, en minutos desde t0).
        vent_spans_min = [
            (float(merged.iloc[i]["ventstartoffset"]) - t0,
             float(merged.iloc[i]["ventendoffset"]) - t0)
            for i in range(len(merged))
        ]
        if with_coverage:
            pdata = {
                "vitals": vitals.get(pid, {}),
                "resp": resp_vitals.get(pid, {}),
            }
            cov = _stay_coverage(pdata, vent_spans_min)
            ok50 = cov.all_above(0.5)
            ok80 = cov.all_above(0.8)
        else:
            cov = CoverageResult(fractions={})
            ok50 = ok80 = False
        if ok50:
            vars_ok_50[level.level] += 1
        if ok80:
            vars_ok_80[level.level] += 1

        events.append({
            "event_id": f"eicu_{pid}_event_1",
            "cohort": "eicu",
            "patientunitstayid": pid,
            "hospital_id": hospital_id,
            "t0_minutes": round(t0, 4),
            "t0_source": "vent_start_observed",
            "duration_seconds": int(round((end_h - start_h) * 3600.0)),
            "n_attempts": len(pairs),
            "attempts": attempts,
            "end_reason": (
                "extubation_observed" if ext.is_extubation
                else (ext.censor_cause or "end_of_record")
            ),
            "labels": labels_dict,
            "d5": d5_by_window,
            "level": level.level,
            "level_reason": level.reason,
            "level_outcome": level.outcome,
            "end_documented": end_documented,
            "last_invasive_adj_h": level.last_invasive_adj_h,
            "proposed_extubation_h": (
                level.extubation_h if level.level == LEVEL_C else None
            ),
            "proposed_shift_h": level.proposed_shift_h,
            "coverage": {k: round(v, 4) for k, v in cov.fractions.items()},
            "vars_ok_50": ok50,
            "vars_ok_80": ok80,
        })

    index = {
        "source": "eicu_respiratorycare_charting",
        "description": (
            "Índice de casos de eICU con nivel de completitud A/B/C/D y cobertura "
            "de variables (Fase 1.5, punto 1)."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_events": len(events),
        "total_excluded_events": len(excluded_events),
        "events": events,
        "excluded_events": excluded_events,
    }

    def _n(levels: Iterable[str]) -> int:
        return int(sum(level_counts[l] for l in levels))

    def _window_stats(levels: Iterable[str]) -> dict:
        out: dict[str, dict] = {}
        for l in levels:
            c = success_by_level[l]
            n = sum(c.values())
            succ = c.get("successful_extubation", 0)
            cens = n - succ
            out[l] = {
                "events": n, "success": succ, "censored": cens,
                "causes": dict(causes_by_level[l]),
                "failure_events": int(failure_events_by_level[l]),
                "failure_pct": (100.0 * failure_events_by_level[l] / n) if n else 0.0,
            }
        return out

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_events": len(events),
        "total_excluded_events": len(excluded_events),
        "levels": {l: int(level_counts[l]) for l in ("A", "B", "C", "D")},
        "levels_by_hospital": {
            int(h): {l: int(c[l]) for l in ("A", "B", "C", "D")}
            for h, c in sorted(by_hospital_level.items())
        },
        "hospitals_with_ge50_A": {
            int(h): int(n) for h, n in hospitals_with_50_A.items() if n >= 50
        },
        "window_48h": {
            "A": _window_stats(["A"]),
            "A+B": _window_stats(["A", "B"]),
            "A+B+C": _window_stats(["A", "B", "C"]),
        },
        "vars_ok_50": {l: int(vars_ok_50[l]) for l in ("A", "B", "C", "D")},
        "vars_ok_80": {l: int(vars_ok_80[l]) for l in ("A", "B", "C", "D")},
        "duration_h": {
            l: (sorted(duration_by_level[l]) if duration_by_level[l] else [])
            for l in ("A", "B", "C", "D")
        },
    }
    return index, summary


def output_root(config: dict) -> tuple[Path, str]:
    version = f"v{config.get('version', '0.0.0')}_{compute_config_hash(config)}"
    base = config_path(config, "paths", "eicu_cases_out", required=False)
    if base is None:
        base = repo_root() / "datasets" / "eicu_collaborative" / f"cases_{version}"
    return Path(base), version


def run(config: dict, *, with_coverage: bool = True) -> dict:
    eicu_dir = config_path(config, "paths", "eicu_dir")
    out_dir, version = output_root(config)
    out_dir.mkdir(parents=True, exist_ok=True)

    patients = load_patients(eicu_dir)
    respcare = load_respcare(eicu_dir)
    valid_pids = set(int(x) for x in respcare["patientunitstayid"].unique())
    logger.info("[eicu] %d estancias con VM; cargando respiratoryCharting...", len(valid_pids))
    adjustments, resp_vitals = load_respcharting(eicu_dir, valid_pids)

    # 1ª pasada: clasificar (sin cobertura) para saber qué estancias son A/B y
    # cargar sus vitales solo entonces (vitalPeriodic es enorme).
    index, summary = build_eicu_index(
        patients, respcare, adjustments, {}, resp_vitals, with_coverage=False,
    )
    if with_coverage:
        ab_pids = {
            e["patientunitstayid"] for e in index["events"]
            if e["level"] in (LEVEL_A, LEVEL_B)
        }
        logger.info("[eicu] cobertura: cargando vitales de %d estancias A/B",
                    len(ab_pids))
        vitals = load_vitals(eicu_dir, ab_pids) if ab_pids else {}
        index, summary = build_eicu_index(
            patients, respcare, adjustments, vitals, resp_vitals,
            with_coverage=True,
        )

    (out_dir / "eicu_cases_index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "eicu_levels_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("[eicu] %d eventos -> %s", index["total_events"], out_dir)
    return {"version": version, "output_dir": str(out_dir),
            "total_events": index["total_events"], "summary": summary}


def main() -> None:
    p = argparse.ArgumentParser(description="Índice y niveles de eICU (Fase 1.5)")
    p.add_argument("--config", default="src/stage0/config/harmonize.yaml")
    p.add_argument("--no-coverage", action="store_true",
                   help="No leer vitalPeriodic/vitalAperiodic (más rápido)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    res = run(config, with_coverage=not args.no_coverage)
    print(json.dumps({"version": res["version"], "output_dir": res["output_dir"],
                      "total_events": res["total_events"],
                      "levels": res["summary"]["levels"]},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
