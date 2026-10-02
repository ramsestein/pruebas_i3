#!/usr/bin/env python3
"""
create_dataset/build_mimic_cases.py
===================================
Builder NUEVO de los casos de MIMIC (Fase 1.3, decisión D6).

Fuente (ver ``src/create_dataset/mimic_itemids.py`` para los itemids y sus
etiquetas oficiales verificadas contra ``D_ITEMS``):

- ``CHARTEVENTS``: ajustes del ventilador (FiO2, PEEP, TV, PIP, RR_V, MV).
- ``PROCEDUREEVENTS_MV``: ``itemid 225792`` = "Invasive Ventilation".
- ``ICUSTAYS``: identificador de estancia (``ICUSTAY_ID``), que es la frontera
  de paciente para MIMIC (D2). Las reintubaciones se cuentan dentro del mismo
  ICUSTAY (D6).

Todo en UTC. Cada evento guarda sus identificadores de origen (D12):
``subject_id`` / ``hadm_id`` / ``icustay_id``.

Salida (versionada, nunca se sobrescribe):
  ``<mimic_cases_out>/mimic_cases_index.json``

Uso:
    python -m src.create_dataset.build_mimic_cases
    python -m src.create_dataset.build_mimic_cases --limit-stays 200
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence

import numpy as np
import pandas as pd

from src.common.d5_events import (
    d5_censor_for_window,
    is_trach_text,
)
from src.common.episodes import DISCONNECT_GAP_H, Span, build_episodes, spans_from_points
from src.common.labels import (
    FAILURE_WINDOWS_H,
    assign_label,
    assign_labels_all_windows,
    attempts_from_pairs,
    labels_to_dict,
)
from src.common.paths import config_path, repo_root
from src.common.timeutils import series_to_epoch_seconds, series_to_utc, to_epoch_utc
from src.create_dataset.mimic_itemids import (
    MIMIC_CHART_ITEMIDS,
    PROCEDURE_ITEMIDS,
    TRACH_PROCEDURE_ITEMIDS,
    VENT_MARKER_KEYS,
    all_catalogued_itemids,
    itemid_to_concept,
    itemids_for,
)
from src.stage0.io.versioning import compute_config_hash, load_config

logger = logging.getLogger(__name__)

_SECONDS_PER_HOUR = 3600.0


# ── Carga de tablas clínicas ─────────────────────────────────────────────────

def load_icustays(clinical_dir: str | Path) -> pd.DataFrame:
    """ICUSTAYS normalizado: icustay_id, subject_id, hadm_id e intime/outtime (epoch UTC)."""
    path = Path(clinical_dir) / "ICUSTAYS.csv.gz"
    df = pd.read_csv(
        path, compression="gzip",
        usecols=["SUBJECT_ID", "HADM_ID", "ICUSTAY_ID", "INTIME", "OUTTIME"],
    )
    df = df.rename(columns=str.lower)
    df["intime_unix"] = series_to_epoch_seconds(df["intime"])
    df["outtime_unix"] = series_to_epoch_seconds(df["outtime"])
    return df


def load_vent_procedures(clinical_dir: str | Path) -> pd.DataFrame:
    """Episodios de ventilación invasiva (itemid 225792) de PROCEDUREEVENTS_MV."""
    path = Path(clinical_dir) / "PROCEDUREEVENTS_MV.csv.gz"
    iid = PROCEDURE_ITEMIDS["invasive_ventilation"]
    df = pd.read_csv(
        path, compression="gzip",
        usecols=["SUBJECT_ID", "HADM_ID", "ICUSTAY_ID", "ITEMID", "STARTTIME", "ENDTIME"],
    )
    df = df[df["ITEMID"] == iid].rename(columns=str.lower)
    df["start_unix"] = series_to_epoch_seconds(df["starttime"])
    df["end_unix"] = series_to_epoch_seconds(df["endtime"])
    return df


def load_trach_procedures(clinical_dir: str | Path) -> pd.DataFrame:
    """Traqueostomías con hora (PROCEDUREEVENTS_MV 225448 / 226237) para D5."""
    path = Path(clinical_dir) / "PROCEDUREEVENTS_MV.csv.gz"
    df = pd.read_csv(
        path, compression="gzip",
        usecols=["SUBJECT_ID", "HADM_ID", "ICUSTAY_ID", "ITEMID", "STARTTIME"],
    )
    df = df[df["ITEMID"].isin(TRACH_PROCEDURE_ITEMIDS)].rename(columns=str.lower)
    df["start_unix"] = series_to_epoch_seconds(df["starttime"])
    return df


def load_deaths(clinical_dir: str | Path) -> pd.DataFrame:
    """DEATHTIME por HADM_ID (ADMISSIONS) para D5."""
    path = Path(clinical_dir) / "ADMISSIONS.csv.gz"
    df = pd.read_csv(
        path, compression="gzip",
        usecols=["SUBJECT_ID", "HADM_ID", "DEATHTIME"],
    ).rename(columns=str.lower)
    df = df.dropna(subset=["deathtime"]).copy()
    df["death_unix"] = series_to_epoch_seconds(df["deathtime"])
    return df[["subject_id", "hadm_id", "death_unix"]]


def load_trach_icd9(clinical_dir: str | Path) -> set[int]:
    """HADM_ID con traqueostomía por ICD-9 (31.1 / 31.2x).

    PROCEDURES_ICD NO tiene hora: solo sirve para MARCAR el caso. La hora se
    busca en tablas con tiempo (PROCEDUREEVENTS_MV / tipo de vía aérea); si no
    aparece, se censura en el último fin de ventilación y se reporta.
    """
    path = Path(clinical_dir) / "PROCEDURES_ICD.csv.gz"
    if not path.exists():
        return set()
    df = pd.read_csv(path, compression="gzip", usecols=["HADM_ID", "ICD9_CODE"])
    mask = df["ICD9_CODE"].astype(str).map(is_trach_icd9)
    return set(int(h) for h in df.loc[mask, "HADM_ID"].dropna().unique())


# ── CHARTEVENTS en streaming: OBSERVACIONES con su hora ──────────────────────

_OBS_COLUMNS = ["SUBJECT_ID", "HADM_ID", "ICUSTAY_ID", "ITEMID",
                "CONCEPT", "t_unix", "VALUENUM", "VALUE_RAW"]


def observations_from_chunk(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte un subconjunto de CHARTEVENTS en observaciones con su hora.

    NO resume a primer/último registro: conserva cada observación (la Fase 2 las
    necesita). Devuelve ``SUBJECT_ID, HADM_ID, ICUSTAY_ID, ITEMID, CONCEPT,
    t_unix, VALUENUM``.
    """
    if df.empty:
        return pd.DataFrame(columns=_OBS_COLUMNS)
    d = df.copy()
    d["CONCEPT"] = d["ITEMID"].map(itemid_to_concept())
    d = d.dropna(subset=["CONCEPT"])
    if d.empty:
        return pd.DataFrame(columns=_OBS_COLUMNS)
    d["t_unix"] = series_to_epoch_seconds(d["CHARTTIME"])
    d["VALUE_RAW"] = d["VALUE"].astype("string")
    return d[_OBS_COLUMNS]


def stream_chartevents(
    chart_path: str | Path,
    *,
    chunksize: int = 500_000,
) -> pd.DataFrame:
    """Recorre CHARTEVENTS.csv.gz y devuelve las observaciones de interés."""
    itemids = all_catalogued_itemids()
    parts: list[pd.DataFrame] = []
    reader = pd.read_csv(
        chart_path, compression="gzip", chunksize=chunksize, low_memory=False,
        usecols=["SUBJECT_ID", "HADM_ID", "ICUSTAY_ID", "ITEMID", "CHARTTIME", "VALUENUM", "VALUE"],
        dtype={"SUBJECT_ID": "int64", "HADM_ID": "Int64", "ICUSTAY_ID": "Int64",
               "ITEMID": "int64", "VALUENUM": "float64"},
    )
    for i, chunk in enumerate(reader, start=1):
        sel = chunk[chunk["ITEMID"].isin(itemids)]
        if not sel.empty:
            parts.append(observations_from_chunk(sel))
        if i % 10 == 0:
            logger.info("[mimic] CHARTEVENTS chunk %d (%d filas leídas)", i, i * chunksize)
    if not parts:
        return pd.DataFrame(columns=_OBS_COLUMNS)
    obs = pd.concat(parts, ignore_index=True)
    return obs.sort_values(["ICUSTAY_ID", "t_unix"], kind="stable").reset_index(drop=True)


# ── Tramos de ventilación con huecos REALES (D1) ─────────────────────────────

def vent_spans_for_stay(
    obs: pd.DataFrame,
    proc_df: pd.DataFrame,
    stay_id: int,
    *,
    gap_h: float = DISCONNECT_GAP_H,
) -> list[Span]:
    """Tramos de ventilación invasiva de una estancia, en HORAS desde epoch.

    Los tramos se construyen con los huecos reales entre observaciones de los
    marcadores específicos de ventilador (D1: los huecos <= 2 h se fusionan),
    más los episodios de PROCEDUREEVENTS_MV 225792. La FiO2 NO marca ventilación.
    """
    spans: list[Span] = []
    if not obs.empty:
        sub = obs[(obs["ICUSTAY_ID"] == stay_id) & (obs["CONCEPT"].isin(VENT_MARKER_KEYS))]
        times_h = sub["t_unix"].to_numpy(dtype=np.float64) / _SECONDS_PER_HOUR
        spans.extend(spans_from_points(times_h, gap_h))
    if not proc_df.empty:
        proc = proc_df[proc_df["icustay_id"] == stay_id]
        for _, row in proc.iterrows():
            if np.isfinite(row["start_unix"]) and np.isfinite(row["end_unix"]):
                spans.append(Span(float(row["start_unix"]) / _SECONDS_PER_HOUR,
                                  float(row["end_unix"]) / _SECONDS_PER_HOUR))
    return sorted(spans, key=lambda s: s.start_h)


def monitor_spans_for_stay(
    obs: pd.DataFrame,
    stay_id: int,
    concepts: Sequence[str],
    *,
    gap_h: float = DISCONNECT_GAP_H,
) -> list[Span]:
    """Tramos de presencia de monitor (HR/SpO2), en HORAS desde epoch."""
    if obs.empty:
        return []
    sub = obs[(obs["ICUSTAY_ID"] == stay_id) & (obs["CONCEPT"].isin(concepts))]
    times_h = sub["t_unix"].to_numpy(dtype=np.float64) / _SECONDS_PER_HOUR
    return spans_from_points(times_h, gap_h)


# ── Construcción de eventos ──────────────────────────────────────────────────

@dataclass
class StayInputs:
    """Datos necesarios para segmentar una estancia. Los tramos van en HORAS."""
    stay_id: int
    subject_id: int
    hadm_id: int
    intime_unix: float
    outtime_unix: float
    vent_spans: list[Span]
    hr_spans: list[Span]
    spo2_spans: list[Span]
    # D5 (corrección 3): horas de traqueostomía con hora conocida (epoch),
    # marca ICD-9 sin hora, y hora de muerte (epoch).
    trach_unix: list[float] = field(default_factory=list)
    trach_icd9_no_time: bool = False
    death_unix: Optional[float] = None


def _mimic_d5_censor(
    stay: StayInputs, ep, failure_window_h: float
) -> tuple[Optional[str], Optional[float]]:
    """Decisión D5 para una ventana (horas relativas a t0), o (None, None)."""
    last_vent_end_h = ep.attempts[-1].end_h - ep.start_h
    trach_hours = [
        (t / _SECONDS_PER_HOUR) - ep.start_h for t in stay.trach_unix
        if t is not None and np.isfinite(t)
    ]
    trach_hours = [t for t in trach_hours if t >= -1e-9]
    death_h = None
    died_ventilated = False
    if stay.death_unix is not None and np.isfinite(stay.death_unix):
        death_h = (stay.death_unix / _SECONDS_PER_HOUR) - ep.start_h
        died_ventilated = death_h <= last_vent_end_h + 1e-6
    dec = d5_censor_for_window(
        failure_window_h=failure_window_h,
        last_disconnect_h=last_vent_end_h,
        trach_time_h=(min(trach_hours) if trach_hours else None),
        trach_time_unknown=stay.trach_icd9_no_time and not trach_hours,
        death_time_h=death_h,
        died_ventilated=died_ventilated,
    )
    return dec.censor_cause, dec.censor_time_h


def _mimic_end_reason(stay: StayInputs, ep, d5_by_window: dict) -> str:
    causes = {v.get("censor_cause") for v in d5_by_window.values()}
    if any(c in ("terminal_extubation", "death_at_vent") for c in causes):
        return "death"
    if any(c in ("trach", "trach_time_unknown") for c in causes):
        return "tracheostomy"
    out_h = stay.outtime_unix / _SECONDS_PER_HOUR
    if ep.end_h >= out_h - 30.0 / 3600.0:
        return "end_of_icu_stay"
    return "extubation_observed"


def build_stay_events(stay: StayInputs) -> list[dict]:
    """Segmenta una estancia MIMIC en eventos (D1/D2/D4) y los devuelve como dicts."""
    stay_bounds = [(
        stay.intime_unix / _SECONDS_PER_HOUR,
        stay.outtime_unix / _SECONDS_PER_HOUR,
    )]
    episodes = build_episodes(
        stay.vent_spans,
        hr_spans=stay.hr_spans,
        spo2_spans=stay.spo2_spans,
        stay_bounds=stay_bounds,
    )

    out: list[dict] = []
    for i, ep in enumerate(episodes):
        t0_unix = ep.start_h * _SECONDS_PER_HOUR
        attempts = [
            {
                "attempt_idx": a.attempt_idx,
                "vent_start_h": round(a.start_h - ep.start_h, 4),
                "vent_end_h": round(a.end_h - ep.start_h, 4),
                "reintubation_h": (
                    round(ep.attempts[j + 1].start_h - ep.start_h, 4)
                    if j + 1 < len(ep.attempts) else None
                ),
            }
            for j, a in enumerate(ep.attempts)
        ]
        arrived = abs(ep.start_h - stay.intime_unix / _SECONDS_PER_HOUR) < (1.0 / 60.0)
        obs_end_h = max(
            (stay.outtime_unix - t0_unix) / _SECONDS_PER_HOUR, ep.duration_h
        )
        pairs = [(a["vent_end_h"], a["reintubation_h"]) for a in attempts]
        labels: dict[str, dict] = {}
        d5_by_window: dict[str, dict] = {}
        for w in FAILURE_WINDOWS_H:
            cause, t_censor = _mimic_d5_censor(stay, ep, float(w))
            lab = assign_label(
                attempts_from_pairs(pairs), obs_end_h=obs_end_h,
                failure_window_h=float(w),
                censor_cause=cause, censor_time_h=t_censor,
            )
            labels[f"{int(w)}h"] = lab
            d5_by_window[f"{int(w)}h"] = {
                "censor_cause": cause, "censor_time_h": t_censor,
            }
        labels_dict = labels_to_dict(labels)
        out.append({
            "event_id": f"mimic_{stay.stay_id}_event_{i + 1}",
            "cohort": "mimic",
            "subject_id": stay.subject_id,
            "hadm_id": stay.hadm_id,
            "icustay_id": stay.stay_id,
            "t0_unix": t0_unix,
            "t0_source": (
                "already_ventilated_at_icu_admission" if arrived
                else "vent_start_observed"
            ),
            "arrived_ventilated": bool(arrived),
            "duration_seconds": int(round(ep.duration_h * _SECONDS_PER_HOUR)),
            "n_attempts": ep.n_attempts,
            "attempts": attempts,
            "end_reason": _mimic_end_reason(stay, ep, d5_by_window),
            "ventilated_hours": round(ep.ventilated_hours, 4),
            "excluded": bool(ep.excluded),
            "exclusion_reason": ep.exclusion_reason,
            "labels": labels_dict,   # Fase 1.6 (D3) + D5
            "trach": {
                "times_unix": list(stay.trach_unix),
                "icd9_marked_without_time": bool(stay.trach_icd9_no_time),
            },
            "terminal": {
                "death_unix": stay.death_unix,
            },
            "d5": d5_by_window,
        })
    return out


# ── Orquestación ─────────────────────────────────────────────────────────────

def _airway_trach_unix(obs: pd.DataFrame) -> dict[int, list[float]]:
    """Hora de traqueostomía por tipo de vía aérea en CHARTEVENTS (D5)."""
    out: dict[int, list[float]] = {}
    if obs.empty or "VALUE_RAW" not in obs.columns:
        return out
    sub = obs[(obs["CONCEPT"] == "AirwayType") & (obs["VALUE_RAW"].notna())]
    for _, row in sub.iterrows():
        if is_trach_text(row["VALUE_RAW"]):
            sid = int(row["ICUSTAY_ID"])
            out.setdefault(sid, []).append(float(row["t_unix"]))
    return out


def build_mimic_index(
    icustays: pd.DataFrame,
    obs: pd.DataFrame,
    proc_df: pd.DataFrame,
    *,
    trach_df: Optional[pd.DataFrame] = None,
    deaths: Optional[pd.DataFrame] = None,
    trach_icd9: Optional[set[int]] = None,
    limit_stays: Optional[int] = None,
) -> dict:
    """Construye el índice completo de casos MIMIC (con D5, corrección 3)."""
    stays_with_vent = obs["ICUSTAY_ID"].dropna().unique() if not obs.empty else []
    proc_stays = proc_df["icustay_id"].dropna().unique() if not proc_df.empty else []
    candidate_ids = set(int(x) for x in stays_with_vent) | set(int(x) for x in proc_stays)

    icu = icustays[icustays["icustay_id"].isin(candidate_ids)]
    if limit_stays:
        icu = icu.head(limit_stays)

    trach_by_stay: dict[int, list[float]] = {}
    if trach_df is not None and not trach_df.empty:
        for _, r in trach_df.iterrows():
            trach_by_stay.setdefault(int(r["icustay_id"]), []).append(float(r["start_unix"]))
    for sid, times in _airway_trach_unix(obs).items():
        trach_by_stay.setdefault(sid, []).extend(times)

    deaths_by_hadm: dict[int, float] = {}
    if deaths is not None and not deaths.empty:
        deaths_by_hadm = {
            int(r["hadm_id"]): float(r["death_unix"]) for _, r in deaths.iterrows()
        }
    trach_icd9 = trach_icd9 or set()

    events: list[dict] = []
    for _, row in icu.iterrows():
        sid = int(row["icustay_id"])
        hadm = int(row["hadm_id"])
        stay = StayInputs(
            stay_id=sid,
            subject_id=int(row["subject_id"]),
            hadm_id=hadm,
            intime_unix=float(row["intime_unix"]),
            outtime_unix=float(row["outtime_unix"]),
            vent_spans=vent_spans_for_stay(obs, proc_df, sid),
            hr_spans=monitor_spans_for_stay(obs, sid, ("HR",)),
            spo2_spans=monitor_spans_for_stay(obs, sid, ("SpO2",)),
            trach_unix=trach_by_stay.get(sid, []),
            trach_icd9_no_time=(hadm in trach_icd9),
            death_unix=deaths_by_hadm.get(hadm),
        )
        events.extend(build_stay_events(stay))

    kept = [e for e in events if not e["excluded"]]
    excluded = [e for e in events if e["excluded"]]
    n_trach_no_time = sum(
        1 for e in events
        if e["trach"]["icd9_marked_without_time"] and not e["trach"]["times_unix"]
    )
    return {
        "source": "mimic_chartevents_procedureevents",
        "description": (
            "Casos MIMIC (D6) desde CHARTEVENTS (marcadores específicos de VM) y "
            "PROCEDUREEVENTS_MV (225792). Frontera de paciente = ICUSTAY. D5 "
            "conectado (traqueostomía y muerte)."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_stays_with_vent": int(len(icu)),
        "total_events": len(kept),
        "total_excluded_events": len(excluded),
        "n_trach_time_unknown": int(n_trach_no_time),
        "events": kept,
        "excluded_events": excluded,
    }


def output_root(config: dict) -> tuple[Path, str]:
    version = f"v{config.get('version', '0.0.0')}_{compute_config_hash(config)}"
    base = config_path(config, "paths", "mimic_cases_out", required=False)
    if base is None:
        base = repo_root() / "datasets" / "mimic3wdb" / f"cases_{version}"
    return Path(base), version


def run(config: dict, *, limit_stays: Optional[int] = None) -> dict:
    clinical_dir = config_path(config, "paths", "mimic_clinical_dir")
    chart_path = config_path(config, "paths", "mimic_chartevents_raw")
    out_dir, version = output_root(config)

    icustays = load_icustays(clinical_dir)
    proc_df = load_vent_procedures(clinical_dir)
    trach_df = load_trach_procedures(clinical_dir)
    deaths = load_deaths(clinical_dir)
    trach_icd9 = load_trach_icd9(clinical_dir)
    obs = stream_chartevents(chart_path)
    index = build_mimic_index(
        icustays, obs, proc_df,
        trach_df=trach_df, deaths=deaths, trach_icd9=trach_icd9,
        limit_stays=limit_stays,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    if not obs.empty:
        # Observaciones con su hora: las necesita la Fase 2 (no se resumen).
        obs_path = out_dir / "mimic_observations.parquet"
        obs.to_parquet(obs_path, index=False)
        logger.info("[mimic] %d observaciones -> %s", len(obs), obs_path)

    index_path = out_dir / "mimic_cases_index.json"
    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)
    logger.info(
        "[mimic] %d estancias con VM, %d eventos -> %s",
        index["total_stays_with_vent"], index["total_events"], index_path,
    )
    return {"version": version, "output_dir": str(out_dir), "index": index}


def main() -> None:
    p = argparse.ArgumentParser(description="Casos MIMIC desde CHARTEVENTS/PROCEDUREEVENTS_MV")
    p.add_argument("--config", default="src/stage0/config/harmonize.yaml")
    p.add_argument("--limit-stays", type=int, default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    res = run(config, limit_stays=args.limit_stays)
    print(json.dumps(
        {"version": res["version"], "output_dir": res["output_dir"],
         "total_events": res["index"]["total_events"],
         "total_excluded_events": res["index"]["total_excluded_events"]},
        indent=2, ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
