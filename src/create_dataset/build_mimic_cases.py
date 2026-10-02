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

from src.common.episodes import Span, build_episodes
from src.common.labels import (
    assign_labels_all_windows,
    attempts_from_pairs,
    labels_to_dict,
)
from src.common.paths import config_path, repo_root
from src.common.timeutils import series_to_utc, to_epoch_utc
from src.create_dataset.mimic_itemids import (
    MIMIC_CHART_ITEMIDS,
    PROCEDURE_ITEMIDS,
    VENT_CHART_KEYS,
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
    df["intime_unix"] = series_to_utc(df["intime"]).astype("int64") / 1e9
    df["outtime_unix"] = series_to_utc(df["outtime"]).astype("int64") / 1e9
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
    df["start_unix"] = series_to_utc(df["starttime"]).astype("int64") / 1e9
    df["end_unix"] = series_to_utc(df["endtime"]).astype("int64") / 1e9
    return df


# ── CHARTEVENTS en streaming ─────────────────────────────────────────────────

@dataclass
class StayVentAggregate:
    """Agregado por estancia y concepto de ventilador."""
    stay_id: int
    subject_id: int
    hadm_id: int
    per_concept: dict[str, tuple[float, float, int]] = field(default_factory=dict)


def aggregate_vent_chartevents_df(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega un subconjunto de CHARTEVENTS (ya filtrado) por (ICUSTAY, itemid).

    Función pura (testeable). Devuelve columnas:
    ``ICUSTAY_ID, SUBJECT_ID, HADM_ID, ITEMID, CONCEPT, t_min, t_max, n``.
    """
    if df.empty:
        return pd.DataFrame(
            columns=["ICUSTAY_ID", "SUBJECT_ID", "HADM_ID", "ITEMID",
                     "CONCEPT", "t_min", "t_max", "n"]
        )
    d = df.copy()
    d["CONCEPT"] = d["ITEMID"].map(itemid_to_concept())
    d = d.dropna(subset=["CONCEPT"])
    if d.empty:
        return pd.DataFrame(
            columns=["ICUSTAY_ID", "SUBJECT_ID", "HADM_ID", "ITEMID",
                     "CONCEPT", "t_min", "t_max", "n"]
        )
    d["t_unix"] = series_to_utc(d["CHARTTIME"]).astype("int64") / 1e9
    g = d.groupby(
        ["ICUSTAY_ID", "SUBJECT_ID", "HADM_ID", "ITEMID", "CONCEPT"],
        as_index=False,
    ).agg(t_min=("t_unix", "min"), t_max=("t_unix", "max"), n=("t_unix", "size"))
    return g


def stream_vent_chartevents(
    chart_path: str | Path,
    *,
    chunksize: int = 500_000,
) -> pd.DataFrame:
    """Recorre CHARTEVENTS.csv.gz y agrega los itemids de ventilador y monitor."""
    itemids = all_catalogued_itemids()
    parts: list[pd.DataFrame] = []
    reader = pd.read_csv(
        chart_path, compression="gzip", chunksize=chunksize, low_memory=False,
        usecols=["SUBJECT_ID", "HADM_ID", "ICUSTAY_ID", "ITEMID", "CHARTTIME", "VALUENUM"],
        dtype={"SUBJECT_ID": "int64", "HADM_ID": "Int64", "ICUSTAY_ID": "Int64",
               "ITEMID": "int64", "VALUENUM": "float64"},
    )
    for i, chunk in enumerate(reader, start=1):
        sel = chunk[chunk["ITEMID"].isin(itemids)]
        if not sel.empty:
            parts.append(aggregate_vent_chartevents_df(sel))
        if i % 10 == 0:
            logger.info("[mimic] CHARTEVENTS chunk %d (%d filas leídas)", i, i * chunksize)
    if not parts:
        return pd.DataFrame(
            columns=["ICUSTAY_ID", "SUBJECT_ID", "HADM_ID", "ITEMID",
                     "CONCEPT", "t_min", "t_max", "n"]
        )
    agg = pd.concat(parts, ignore_index=True)
    # Re-agrega por si un ICUSTAY aparece en varios chunks.
    return agg.groupby(
        ["ICUSTAY_ID", "SUBJECT_ID", "HADM_ID", "ITEMID", "CONCEPT"],
        as_index=False,
    ).agg(t_min=("t_min", "min"), t_max=("t_max", "max"), n=("n", "sum"))


def vent_spans_for_stay(
    chart_agg: pd.DataFrame,
    proc_df: pd.DataFrame,
    stay_id: int,
) -> list[Span]:
    """Tramos con actividad de ventilador de una estancia (segundos epoch).

    Combina los ajustes observados en CHARTEVENTS y los episodios de
    PROCEDUREEVENTS_MV (225792).
    """
    spans: list[Span] = []
    sub = chart_agg[chart_agg["ICUSTAY_ID"] == stay_id] if not chart_agg.empty else chart_agg
    sub = sub[sub["CONCEPT"].isin(VENT_CHART_KEYS)] if not sub.empty else sub
    for _, row in sub.iterrows():
        spans.append(Span(float(row["t_min"]), float(row["t_max"])))
    proc = proc_df[proc_df["icustay_id"] == stay_id] if not proc_df.empty else proc_df
    for _, row in proc.iterrows():
        if np.isfinite(row["start_unix"]) and np.isfinite(row["end_unix"]):
            spans.append(Span(float(row["start_unix"]), float(row["end_unix"])))
    return spans


def monitor_spans_for_stay(
    chart_agg: pd.DataFrame,
    stay_id: int,
    concepts: Sequence[str],
) -> list[Span]:
    """Tramos de presencia de monitor (p.ej. HR o SpO2) de una estancia."""
    spans: list[Span] = []
    if chart_agg.empty:
        return spans
    sub = chart_agg[(chart_agg["ICUSTAY_ID"] == stay_id) & (chart_agg["CONCEPT"].isin(concepts))]
    for _, row in sub.iterrows():
        spans.append(Span(float(row["t_min"]), float(row["t_max"])))
    return spans


# ── Construcción de eventos ──────────────────────────────────────────────────

@dataclass
class StayInputs:
    """Datos necesarios para segmentar una estancia."""
    stay_id: int
    subject_id: int
    hadm_id: int
    intime_unix: float
    outtime_unix: float
    vent_spans: list[Span]
    hr_spans: list[Span]
    spo2_spans: list[Span]


def build_stay_events(stay: StayInputs) -> list[dict]:
    """Segmenta una estancia MIMIC en eventos (D1/D2/D4) y los devuelve como dicts."""
    def _hours(spans: Iterable[Span]) -> list[Span]:
        return [Span(s.start_h / _SECONDS_PER_HOUR, s.end_h / _SECONDS_PER_HOUR) for s in spans]

    stay_bounds = [(
        stay.intime_unix / _SECONDS_PER_HOUR,
        stay.outtime_unix / _SECONDS_PER_HOUR,
    )]
    episodes = build_episodes(
        _hours(stay.vent_spans),
        hr_spans=_hours(stay.hr_spans),
        spo2_spans=_hours(stay.spo2_spans),
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
        obs_end_h = (stay.outtime_unix - t0_unix) / _SECONDS_PER_HOUR
        labels = labels_to_dict(assign_labels_all_windows(
            attempts_from_pairs([
                (a["vent_end_h"], a["reintubation_h"]) for a in attempts
            ]),
            obs_end_h=obs_end_h,
        ))
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
            "end_reason": _mimic_end_reason(ep, stay),
            "ventilated_hours": round(ep.ventilated_hours, 4),
            "excluded": bool(ep.excluded),
            "exclusion_reason": ep.exclusion_reason,
            "labels": labels,   # Fase 1.6 (D3)
            "trach": None,      # Fase 1.5
            "terminal": None,   # Fase 1.5
        })
    return out


def _mimic_end_reason(ep, stay: StayInputs) -> str:
    out_h = stay.outtime_unix / _SECONDS_PER_HOUR
    if ep.end_h >= out_h - 30.0 / 3600.0:
        return "end_of_icu_stay"
    return "extubation_observed"


# ── Orquestación ─────────────────────────────────────────────────────────────

def build_mimic_index(
    icustays: pd.DataFrame,
    chart_agg: pd.DataFrame,
    proc_df: pd.DataFrame,
    *,
    limit_stays: Optional[int] = None,
) -> dict:
    """Construye el índice completo de casos MIMIC."""
    stays_with_vent = chart_agg["ICUSTAY_ID"].dropna().unique() if not chart_agg.empty else []
    proc_stays = proc_df["icustay_id"].dropna().unique() if not proc_df.empty else []
    candidate_ids = set(int(x) for x in stays_with_vent) | set(int(x) for x in proc_stays)

    icu = icustays[icustays["icustay_id"].isin(candidate_ids)]
    if limit_stays:
        icu = icu.head(limit_stays)

    events: list[dict] = []
    for _, row in icu.iterrows():
        stay = StayInputs(
            stay_id=int(row["icustay_id"]),
            subject_id=int(row["subject_id"]),
            hadm_id=int(row["hadm_id"]),
            intime_unix=float(row["intime_unix"]),
            outtime_unix=float(row["outtime_unix"]),
            vent_spans=vent_spans_for_stay(chart_agg, proc_df, int(row["icustay_id"])),
            hr_spans=monitor_spans_for_stay(chart_agg, int(row["icustay_id"]), ("HR",)),
            spo2_spans=monitor_spans_for_stay(chart_agg, int(row["icustay_id"]), ("SpO2",)),
        )
        events.extend(build_stay_events(stay))

    kept = [e for e in events if not e["excluded"]]
    excluded = [e for e in events if e["excluded"]]
    return {
        "source": "mimic_chartevents_procedureevents",
        "description": (
            "Casos MIMIC (D6) desde CHARTEVENTS (ajustes de ventilador) y "
            "PROCEDUREEVENTS_MV (225792). Frontera de paciente = ICUSTAY."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_stays_with_vent": int(len(icu)),
        "total_events": len(kept),
        "total_excluded_events": len(excluded),
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
    chart_agg = stream_vent_chartevents(chart_path)
    index = build_mimic_index(icustays, chart_agg, proc_df, limit_stays=limit_stays)

    out_dir.mkdir(parents=True, exist_ok=True)
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
