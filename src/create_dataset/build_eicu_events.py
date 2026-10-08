#!/usr/bin/env python3
"""
create_dataset/build_eicu_events.py
===================================
Fase 1.6b — **punto 2**: eventos y etiquetas de la cohorte de eICU seleccionada,
con el algoritmo de intervalos **calibrado en MIMIC** (``gap_h``).

Construcción (sin ``respiratoryCare.ventstartoffset``, que no marca intubación):

- **t0** = primer ajuste invasivo de ``respiratoryCharting``;
- **fin** = último ajuste del episodio; un hueco > ``gap_h`` cierra el episodio
  (``src/common/vent_intervals.py``);
- **regla 0** (≥ 1 h de estancia sin ventilador antes del alta) con
  ``resolve_extubation``;
- **D5** con ``unitdischargestatus`` (muerte ventilado) y ``airwaytype``
  (traqueostomía);
- **exclusión** de estancias con traqueostomía **previa** a t0.

El índice usa el mismo esquema que las otras cohortes e incluye ``hospital_id`` y
``inter_adj_median_min`` (frecuencia de anotación del hospital), más el error de
etiqueta esperado trasladado de la calibración en MIMIC según esa frecuencia.

Salida (versionada, nunca se sobrescribe):
  ``<eicu_cases_out>/eicu_cases_index.json``
  ``<eicu_cases_out>/eicu_events_summary.json``

Uso:
    python -m src.create_dataset.build_eicu_events
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from src.common.d5_events import d5_censor_for_window, is_trach_text, trach_time_from_offset_rows
from src.common.eicu_levels import hourly_coverage
from src.common.end_reasons import end_reason_from_causes
from src.common.episodes import Span
from src.common.extubation import resolve_extubation
from src.common.labels import FAILURE_WINDOWS_H, assign_label, attempts_from_pairs, labels_to_dict
from src.common.paths import config_path, repo_root
from src.common.vent_intervals import intervals_from_annotations, shift_interval_ends
from src.create_dataset.build_eicu_index import (
    _stay_coverage,
    load_patients,
    load_respcharting,
    load_vitals,
    output_root,
)
from src.stage0.io.versioning import load_config

logger = logging.getLogger(__name__)

_MIN_PER_HOUR = 60.0

# Fase 1.6c (punto 5): de dónde sale la etiqueta de este índice.
LABEL_SOURCE = "anotaciones"

# Estratos de anotación del hospital (min) -> estrato de calibración (h).
STRATUM_BINS = ((0.0, 60.0, "le1h"), (60.0, 120.0, "1_2h"), (120.0, np.inf, "gt2h"))
CALIB_STRATUM_BY_BIN = {"le1h": 1.0, "1_2h": 2.0, "gt2h": 4.0}
BIN_BY_CALIB_STRATUM = {v: k for k, v in CALIB_STRATUM_BY_BIN.items()}


def stratum_of_annotation(median_min: Optional[float]) -> str:
    """Estrato de anotación (``le1h`` / ``1_2h`` / ``gt2h``) de un hospital.

    Los límites son **inclusivos por arriba** (Fase 1.6c): una mediana de
exactamente 120 min es ``1_2h``, porque la cohorte eICU-B se define como
    ``mediana <= 2 h``. Con ``<`` un hospital de 120 min caía en ``gt2h`` y se le
    aplicaba la corrección del estrato de 4 h.
    """
    if median_min is None or not np.isfinite(median_min):
        return "le1h"
    if median_min <= 60.0:
        return "le1h"
    if median_min <= 120.0:
        return "1_2h"
    return "gt2h"


def load_airway_trach(eicu_dir: Path) -> dict[int, float]:
    """Offset (min) de la primera traqueostomía documentada por estancia."""
    rc = pd.read_csv(
        eicu_dir / "respiratoryCare.csv.gz",
        usecols=["patientunitstayid", "airwaytype", "respcarestatusoffset"],
        low_memory=False,
    )
    out: dict[int, float] = {}
    mask = rc["airwaytype"].astype(str).map(is_trach_text)
    for pid, off in zip(rc.loc[mask, "patientunitstayid"],
                        rc.loc[mask, "respcarestatusoffset"]):
        off = trach_time_from_offset_rows([off])
        if off is None:
            continue
        pid = int(pid)
        if pid not in out or off < out[pid]:
            out[pid] = float(off)
    return out


def load_hospital_annotation(census_csv: Path) -> dict[int, dict]:
    """``hospital_id`` -> {inter_adj_median_min, usable_stays} del censo v3."""
    if not census_csv.exists():
        return {}
    df = pd.read_csv(census_csv)
    out: dict[int, dict] = {}
    for r in df.itertuples():
        out[int(r.hospital_id)] = {
            "inter_adj_median_min": (None if pd.isna(r.inter_adj_median_min)
                                     else float(r.inter_adj_median_min)),
            "usable_stays": int(getattr(r, "usable_stays", 0)),
        }
    return out


def load_transferable_error(reports_dir: Path) -> dict[float, dict]:
    """Error de etiqueta esperado por estrato (de la calibración en MIMIC)."""
    path = calib_path_for(reports_dir)
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {float(t["subsample_h"]): t for t in data.get("transferable_error", [])}


def calib_path_for(*dirs: Path) -> Optional[Path]:
    """Primera ``calibracion_mimic.json`` disponible (Fase 1.6c → 1.6b)."""
    for d in dirs:
        p = Path(d) / "calibracion_mimic.json"
        if p.exists():
            return p
    return None


def load_end_correction(path: Optional[Path]) -> dict:
    """Corrección del fin reconstruido por estrato de anotación (horas).

    El fin reconstruido desde los ajustes se queda **corto** (los ajustes dejan
    de anotarse antes de la desconexión real): la mediana del error de fin es
    negativa. La corrección suma el opuesto de esa mediana, es decir los valores
    de ``shifts_h`` (positivos). Solo se aplica si la calibración lo aprueba,
    esto es, si mejora la concordancia de la etiqueta a 48 h en MIMIC en **ambos**
    estratos (1 h y 2 h).
    """
    out = {"aplicada": False, "shifts_por_estrato": {},
           "fuente": str(path.name) if path else None, "verdict": None}
    if path is None:
        return out
    block = (json.loads(path.read_text(encoding="utf-8"))
             .get("end_shift_correction") or {})
    verdict = block.get("verdict_strata_1_2h") or {}
    shifts_by_bin = {}
    for key, value in (block.get("shifts_h") or {}).items():
        try:
            calib_h = float(key)
        except (TypeError, ValueError):
            continue
        bin_name = BIN_BY_CALIB_STRATUM.get(calib_h)
        if bin_name is not None:
            shifts_by_bin[bin_name] = float(value)
    out.update({
        "aplicada": bool(verdict.get("approved")),
        "shifts_por_estrato": shifts_by_bin,
        "verdict": verdict or None,
    })
    return out


def _attempts_and_labels(
    spans,
    *,
    t0_h: float,
    discharge_h: float,
    died: bool,
    trach_h: Optional[float],
) -> dict:
    """Intentos, regla 0 y etiquetas D3/D5 para unos intervalos dados.

    ``discharge_h`` es relativo a t0.
    """
    attempts = []
    for i, sp in enumerate(spans):
        attempts.append({
            "attempt_idx": i,
            "vent_start_h": round(sp.start_h - t0_h, 4),
            "vent_end_h": round(sp.end_h - t0_h, 4),
            "reintubation_h": (round(spans[i + 1].start_h - t0_h, 4)
                               if i + 1 < len(spans) else None),
        })
    # Los intentos guardados van redondeados, pero la lógica (muerte
    # ventilado, regla 0) usa los valores SIN redondear: con 4 decimales el
    # ``<=`` de "muerte ventilado" puede fallar por el redondeo.
    start_h = spans[0].start_h - t0_h
    end_h = spans[-1].end_h - t0_h
    death_h = discharge_h if died else None
    died_ventilated = bool(died and discharge_h <= end_h + 1e-9)

    ext = resolve_extubation(
        last_vent_end_h=end_h, observation_end_h=discharge_h,
        stay_end_h=discharge_h, death_h=death_h,
        died_ventilated=died_ventilated,
    )
    pairs = [(a["vent_end_h"], a["reintubation_h"]) for a in attempts]
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
        labels[f"{int(w)}h"] = assign_label(
            attempts_from_pairs(label_pairs), obs_end_h=discharge_h,
            failure_window_h=float(w), censor_cause=cause,
            censor_time_h=t_censor,
        )
        d5_by_window[f"{int(w)}h"] = {"censor_cause": cause,
                                      "censor_time_h": t_censor}
    return {"attempts": attempts, "start_h": start_h, "end_h": end_h,
            "pairs": pairs, "ext": ext, "labels": labels, "d5": d5_by_window}


def _cap_shifted_ends(spans, discharge_abs_h: float):
    """Recorta el fin corregido al alta y al inicio del intento siguiente.

    Una corrección positiva puede dejar el fin después del alta o solapando el
    intento siguiente: son estados imposibles, así que se recortan.
    """
    limits = [sp.start_h for sp in spans[1:]] + [discharge_abs_h]
    # El fin corregido no puede quedar antes del inicio (si el límite, p. ej. el
    # alta, es anterior al inicio de un tramo, el tramo se colapsa a duración 0
    # en vez de invertirse).
    return [Span(sp.start_h, max(sp.start_h, min(sp.end_h, limit)))
            for sp, limit in zip(spans, limits)]


def build_eicu_events(
    patients: pd.DataFrame,
    adjustments: dict[int, list[float]],
    vitals: dict[int, dict],
    resp_vitals: dict[int, dict],
    *,
    gap_h: float,
    hospitals: Optional[set[int]] = None,
    trach_by_pid: Optional[dict[int, float]] = None,
    hospital_meta: Optional[dict[int, dict]] = None,
    with_coverage: bool = True,
    end_shift_by_stratum: Optional[dict[str, float]] = None,
) -> tuple[dict, dict]:
    """Eventos de eICU a partir de los ajustes invasivos, con ``gap_h``.

    ``end_shift_by_stratum``: corrección del fin (horas, positiva) por estrato
    de anotación del hospital. Si un hospital tiene corrección, el evento guarda
    la etiqueta corregida en ``labels`` y la original en
    ``labels_sin_correccion`` (Fase 1.6c, punto 3: las dos versiones).
    """
    trach_by_pid = trach_by_pid or {}
    hospital_meta = hospital_meta or {}
    end_shift_by_stratum = end_shift_by_stratum or {}
    pat_by_id = patients.set_index("patientunitstayid")

    events: list[dict] = []
    excluded: list[dict] = []

    for pid, adj in adjustments.items():
        pid = int(pid)
        if pid not in pat_by_id.index:
            continue
        meta = pat_by_id.loc[pid]
        hospital_id = int(meta["hospitalid"])
        if hospitals is not None and hospital_id not in hospitals:
            continue

        # Los offsets de eICU vienen en MINUTOS; el algoritmo usa HORAS.
        # Se descartan los ajustes posteriores al alta: son anotaciones
        # espurias que caen fuera de la estancia (aparecen al ampliar G y
        # creaban tramos que empezaban después del alta).
        discharge = float(meta["unitdischargeoffset"])
        died = str(meta["unitdischargestatus"]).strip().lower() == "expired"
        discharge_abs_h = discharge / _MIN_PER_HOUR
        adj_h = [float(a) / _MIN_PER_HOUR for a in adj
                 if float(a) <= discharge + 1e-6]
        spans = intervals_from_annotations(adj_h, gap_h, keep_singletons=False)
        if not spans:
            continue
        t0_h = spans[0].start_h
        if discharge_abs_h <= t0_h:
            continue

        # Traqueostomía previa a t0 -> exclusión (D5).
        trach_off = trach_by_pid.get(pid)
        trach_h: Optional[float] = None
        if trach_off is not None:
            trach_h = (trach_off / _MIN_PER_HOUR) - t0_h
            if trach_h < 0:
                excluded.append({
                    "event_id": f"eicu_{pid}_excluded", "cohort": "eicu",
                    "patientunitstayid": pid, "hospital_id": hospital_id,
                    "exclusion_reason": "trach_preexisting",
                })
                continue
            if trach_h > discharge_abs_h - t0_h:
                trach_h = None

        hmeta = hospital_meta.get(hospital_id, {})
        stratum = stratum_of_annotation(hmeta.get("inter_adj_median_min"))
        shift = float(end_shift_by_stratum.get(stratum, 0.0))
        discharge_h = discharge_abs_h - t0_h
        base = _attempts_and_labels(spans, t0_h=t0_h, discharge_h=discharge_h,
                                    died=died, trach_h=trach_h)
        if shift:
            # El fin reconstruido se queda corto: se alarga el intervalo. El
            # fin corregido NO puede pasar del alta (sería ventilación después
            # del alta) ni solaparse con el inicio del intento siguiente.
            spans = _cap_shifted_ends(shift_interval_ends(spans, shift),
                                      discharge_abs_h)
            res = _attempts_and_labels(spans, t0_h=t0_h,
                                       discharge_h=discharge_h,
                                       died=died, trach_h=trach_h)
            labels_sin_correccion = labels_to_dict(base["labels"])
        else:
            res = base
            labels_sin_correccion = None
        attempts = res["attempts"]
        pairs = res["pairs"]
        start_h, end_h = res["start_h"], res["end_h"]
        ext = res["ext"]
        labels, d5_by_window = res["labels"], res["d5"]

        vent_spans_min = [((sp.start_h - t0_h) * _MIN_PER_HOUR,
                           (sp.end_h - t0_h) * _MIN_PER_HOUR) for sp in spans]
        if with_coverage:
            cov = _stay_coverage(
                {"vitals": vitals.get(pid, {}), "resp": resp_vitals.get(pid, {})},
                vent_spans_min)
            ok50, ok80 = cov.all_above(0.5), cov.all_above(0.8)
        else:
            cov, ok50, ok80 = None, False, False

        hmeta = hospital_meta.get(hospital_id, {})
        events.append({
            "event_id": f"eicu_{pid}_event_1",
            "cohort": "eicu",
            "patientunitstayid": pid,
            "uniquepid": (None if "uniquepid" not in meta.index
                          or pd.isna(meta["uniquepid"]) else str(meta["uniquepid"])),
            "hospital_id": hospital_id,
            # Fase 1.6c (punto 5): origen de la etiqueta.
            "label_source": LABEL_SOURCE,
            "t0_minutes": round(t0_h * _MIN_PER_HOUR, 4),
            "t0_source": "first_invasive_adjustment",
            "gap_h": gap_h,
            "inter_adj_median_min": hmeta.get("inter_adj_median_min"),
            "annotation_stratum": stratum,
            # Fase 1.6c (punto 3): corrección del fin y las dos versiones de la
            # etiqueta (corregida en ``labels``, original en
            # ``labels_sin_correccion``; ``None`` si no se aplica).
            "end_correccion_h": round(shift, 4),
            "labels_sin_correccion": labels_sin_correccion,
            "duration_seconds": int(round((end_h - start_h) * 3600.0)),
            "n_attempts": len(pairs),
            "attempts": attempts,
            "end_reason": end_reason_from_causes(
                (v.get("censor_cause") for v in d5_by_window.values()),
                is_extubation=ext.is_extubation,
                fallback=ext.censor_cause or "end_of_record"),
            "extubation_rule": ext.reason,
            "labels": labels_to_dict(labels),
            "d5": d5_by_window,
            "trach_h": trach_h,
            "coverage": ({k: round(v, 4) for k, v in cov.fractions.items()}
                         if cov else {}),
            "vars_ok_50": bool(ok50),
            "vars_ok_80": bool(ok80),
        })

    index = {
        "source": "eicu_respiratorycharting_adjustments",
        "description": ("Eventos de eICU reconstruidos desde ajustes invasivos con "
                        f"gap calibrado en MIMIC (gap_h={gap_h} h)."),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gap_h": gap_h,
        "end_correccion": {
            "aplicada": bool(end_shift_by_stratum),
            "shifts_por_estrato_h": dict(end_shift_by_stratum),
            "nota": ("Corrección del fin reconstruido (se queda corto) por estrato "
                     "de anotación del hospital. La etiqueta vigente es la "
                     "corregida; la original se guarda en "
                     "``labels_sin_correccion``."),
        },
        "total_events": len(events),
        "total_excluded_events": len(excluded),
        "events": events,
        "excluded_events": excluded,
    }
    return index, summarize_events(events, gap_h=gap_h)


def summarize_events(events: list[dict], *, gap_h: float) -> dict:
    """Resumen: éxito/censura 48-72 h, fallos, duración y estratos de anotación."""
    by_stratum: dict[str, dict] = defaultdict(
        lambda: {"events": 0, "success": 0, "censored": 0, "failure_events": 0,
                 "vars_ok50": 0, "durations_h": []})
    total = {"events": len(events), "failure_events": 0, "vars_ok_50": 0,
             "vars_ok_80": 0, "hospitals": set(), "durations_h": []}
    success = {f"{int(w)}h": 0 for w in FAILURE_WINDOWS_H}
    censored = {f"{int(w)}h": 0 for w in FAILURE_WINDOWS_H}
    causas_sin = {f"{int(w)}h": Counter() for w in FAILURE_WINDOWS_H}
    changed = {f"{int(w)}h": 0 for w in FAILURE_WINDOWS_H}
    causes: Counter = Counter()

    for e in events:
        total["hospitals"].add(e["hospital_id"])
        dur = e["duration_seconds"] / 3600.0
        total["durations_h"].append(dur)
        s = by_stratum[e["annotation_stratum"]]
        s["events"] += 1
        s["durations_h"].append(dur)
        if e["vars_ok_50"]:
            total["vars_ok_50"] += 1
            s["vars_ok50"] += 1
        if e["vars_ok_80"]:
            total["vars_ok_80"] += 1
        lab = e["labels"]["48h"]
        if lab["n_failed_attempts"] > 0:
            total["failure_events"] += 1
            s["failure_events"] += 1
        # Fase 1.6c (punto 3): las dos versiones de la etiqueta.
        alt = e.get("labels_sin_correccion")
        if alt:
            for w in FAILURE_WINDOWS_H:
                key = f"{int(w)}h"
                if e["labels"][key]["event_type"] != alt[key]["event_type"]:
                    changed[key] += 1
                if alt[key]["event_type"] != "successful_extubation":
                    causas_sin[key][alt[key].get("censor_cause") or "desconocido"] += 1
        for w in FAILURE_WINDOWS_H:
            key = f"{int(w)}h"
            if e["labels"][key]["event_type"] == "successful_extubation":
                success[key] += 1
            else:
                censored[key] += 1
            if e["labels"][key]["censor_cause"]:
                causes[e["labels"][key]["censor_cause"]] += 1
        if lab["event_type"] == "successful_extubation":
            s["success"] += 1
        else:
            s["censored"] += 1

    def _dur_stats(vals: list[float]) -> dict:
        if not vals:
            return {"median_h": None, "p25_h": None, "p75_h": None}
        q1, med, q3 = np.percentile(np.asarray(vals, dtype=float), [25, 50, 75])
        return {"median_h": float(med), "p25_h": float(q1), "p75_h": float(q3)}

    return {
        "gap_h": gap_h,
        "n_events": len(events),
        "n_hospitals": len(total["hospitals"]),
        "labels_cambiados_por_correccion": dict(changed),
        "censor_causes_sin_correccion": {k: dict(v) for k, v in causas_sin.items()},
        "success_48h": success["48h"], "censored_48h": censored["48h"],
        "success_72h": success["72h"], "censored_72h": censored["72h"],
        "failure_events_48h": total["failure_events"],
        "vars_ok_50": total["vars_ok_50"], "vars_ok_80": total["vars_ok_80"],
        "duration": _dur_stats(total["durations_h"]),
        "censor_causes": dict(causes),
        "by_annotation_stratum": {
            k: {"events": v["events"], "success_48h": v["success"],
                "censored_48h": v["censored"],
                "failure_events_48h": v["failure_events"],
                "vars_ok_50": v["vars_ok50"],
                "duration": _dur_stats(v["durations_h"])}
            for k, v in sorted(by_stratum.items())
        },
    }


def run(config: dict, *, with_coverage: bool = True) -> dict:
    eicu_dir = config_path(config, "paths", "eicu_dir")
    cfg = config.get("fase1_6b", {})
    gaps = cfg.get("vent_intervals", {})
    # Fase 1.6d (punto 1): G = 10 h si está fijado en `fase1_6d`. Si no, el de la
    # 1.6b (G = 8 h).
    gap_h = (config.get("fase1_6d", {}).get("vent_intervals", {}).get("gap_h")
             or gaps.get("gap_h"))
    if gap_h is None:
        raise ValueError(
            "fase1_6b.vent_intervals.gap_h no está fijado: ejecuta antes "
            "scripts/verify/fase1_6b/calibrate_gap_mimic.py")
    # D14 (Fase 1.6c): la cohorte definitiva es eICU-B (hospitales de e2 con
    # anotación mediana <= 2 h). Si aún no está definida, se usa la de la 1.6b.
    # Fase 1.6d: los 27 hospitales de eICU-B se pueden redefinir en `fase1_6d`.
    cfg14 = config.get("fase1_6c", {}).get("eicu_b", {})
    cfg16d = config.get("fase1_6d", {}).get("eicu_b", {})
    cohort_cfg = (cfg16d.get("hospital_ids") or cfg14.get("hospital_ids")
                  or cfg.get("eicu", {}).get("hospital_ids", []))
    hospitals = set(int(h) for h in cohort_cfg)
    if cfg16d.get("hospital_ids"):
        cohort_source = "fase1_6d.eicu_b"
    elif cfg14.get("hospital_ids"):
        cohort_source = "fase1_6c.eicu_b"
    else:
        cohort_source = "fase1_6b.eicu"
    reports_dir = repo_root() / cfg.get("reports_dir", "reports/fase1_6b")
    # Fase 1.6c: la calibración vive en reports/fase1_6c (con respaldo 1.6b) y su
    # corrección del fin por estrato solo se aplica si el veredicto la aprueba.
    # Fase 1.6d: se prefiere reports/fase1_6d (calibración con G = 10).
    reports_16c = repo_root() / config.get("fase1_6c", {}).get(
        "reports_dir", "reports/fase1_6c")
    reports_16d = repo_root() / config.get("fase1_6d", {}).get(
        "reports_dir", "reports/fase1_6d")
    calib = calib_path_for(reports_16d, reports_16c, reports_dir)
    correction = load_end_correction(calib)
    end_shift = (correction["shifts_por_estrato"] if correction["aplicada"] else {})

    out_dir, version = output_root(config)
    out_dir.mkdir(parents=True, exist_ok=True)

    patients = load_patients(eicu_dir)
    hospital_meta = load_hospital_annotation(reports_dir / "eicu_hospitales_v3.csv")
    valid = set(int(x) for x in patients["patientunitstayid"]
                if hospitals and int(patients.loc[patients.patientunitstayid == x,
                                                   "hospitalid"].iloc[0]) in hospitals)
    trach = load_airway_trach(eicu_dir)
    logger.info("[eicu] %d estancias en %d hospitales; cargando charting...",
                len(valid), len(hospitals))
    adjustments, resp_vitals = load_respcharting(eicu_dir, valid)
    vitals = load_vitals(eicu_dir, set(adjustments)) if with_coverage else {}

    index, summary = build_eicu_events(
        patients, adjustments, vitals, resp_vitals, gap_h=float(gap_h),
        hospitals=hospitals, trach_by_pid=trach, hospital_meta=hospital_meta,
        with_coverage=with_coverage, end_shift_by_stratum=end_shift,
    )
    summary["expected_label_error"] = {
        k: v for k, v in load_transferable_error(reports_16c).items()
    }
    summary["end_correccion"] = {
        "aplicada": bool(end_shift),
        "shifts_por_estrato_h": end_shift,
        "fuente": correction["fuente"],
        "veredicto_mimic": correction["verdict"],
    }
    summary["cohort_source"] = cohort_source
    summary["n_hospitals_cohort"] = len(hospitals)
    summary["version"] = version
    (out_dir / "eicu_cases_index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8")
    (out_dir / "eicu_events_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    # Copia versionada del resumen en reports/ (datasets/ está ignorado por git).
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "eicu_events_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[eicu] %d eventos -> %s", index["total_events"], out_dir)
    return {"version": version, "output_dir": str(out_dir), "summary": summary}


def main() -> None:
    p = argparse.ArgumentParser(description="Eventos de eICU (Fase 1.6b, punto 2)")
    p.add_argument("--config", default="src/stage0/config/harmonize.yaml")
    p.add_argument("--no-coverage", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config(args.config)
    res = run(config, with_coverage=not args.no_coverage)
    print(json.dumps(res["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
