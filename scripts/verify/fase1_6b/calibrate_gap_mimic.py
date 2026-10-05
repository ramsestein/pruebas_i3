#!/usr/bin/env python3
"""
scripts/verify/fase1_6b/calibrate_gap_mimic.py
==============================================
Fase 1.6b — **punto 1**: calibración en MIMIC-MetaVision del algoritmo de
intervalos a partir de anotaciones, y elección de ``G``.

Algoritmo (``src/common/vent_intervals.py``): una anotación invasiva abre o
mantiene el episodio; un hueco **> G h** lo cierra. El inicio es la primera
anotación y el fin la última.

Calibración:

- **Anotaciones**: ``mimic_observations.parquet`` (CHARTEVENTS de MIMIC-III ya
  extraído por concepto), conceptos marcadores
  (``VentMode, PEEP, TV_set, TV_observed, PIP, RR_V``).
- **Referencia**: intervalos ``225792`` ("Invasive Ventilation" de
  PROCEDUREEVENTS_MV) ya construidos en ``mimic_cases_index.json``, recortados
  con los eventos de extubación (D1).
- **Estratos de anotación**: submuestreo a 1, 2 y 4 h para emular la frecuencia
  de anotación de eICU.
- **Estratos de duración**: corto (< 24 h), medio (24–96 h), largo (> 96 h).

Métricas por ``G`` y estrato: intervalo de inicio y de fin (mediana, IQR, P90,
% dentro de ±1/±2/±4 h), **pérdida** de intervalos reales, **minutos
reconstruidos fuera** de los intervalos reales (“inventados”), **fragmentación**
(trozos por intervalo real), sensibilidad y VPP de reintubaciones, concordancia
de la etiqueta a 48 h y diferencia en ``extubation_time_h``.

Elección de ``G`` (solo con MIMIC, documentada en el JSON):

1. se exige, en TODOS los estratos: minutos fuera ≤ 10 % y pérdida acotada
   (≤ 5 % con anotación ≤ 2 h, ≤ 15 % con anotación de 4 h, donde la pérdida es
   estructural);
2. entre los que cumplen, se maximiza el mínimo entre estratos de
   ``media(% inicio ±2 h, % fin ±2 h, F1 reintubación)``.

Salidas (``reports/fase1_6b/``):
  ``calibracion_mimic.json``     métricas completas + elección de G
  ``calibracion_mimic.md``       tabla legible

Uso:
    python scripts/verify/fase1_6b/calibrate_gap_mimic.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.labels import assign_label, attempts_from_pairs  # noqa: E402
from src.common.paths import config_path  # noqa: E402
from src.common.vent_intervals import (  # noqa: E402
    GAP_CANDIDATES_H,
    intervals_from_annotations,
    subsample_times,
)
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"

MARKER_CONCEPTS = ("VentMode", "PEEP", "TV_set", "TV_observed", "PIP", "RR_V")
SECONDS_PER_HOUR = 3600.0
MATCH_TOL_H = 24.0        # tolerancia para emparejar intervalos (referencia vs anotación)
REINTUB_TOL_H = 2.0       # tolerancia para emparejar reintubaciones

DURATION_BINS = ((0.0, 24.0, "corta_lt24h"), (24.0, 96.0, "media_24_96h"),
                 (96.0, np.inf, "larga_gt96h"))


def latest_cases_dir(root: Path) -> Path:
    """Directorio ``cases_v*/`` más reciente (la versión lleva el hash de config)."""
    dirs = sorted(p for p in root.glob("cases_v*") if p.is_dir())
    if not dirs:
        raise FileNotFoundError(f"no hay cases_v*/ en {root}")
    return dirs[-1]


def load_reference(cases_dir: Path, clinical_dir: Path) -> dict[int, dict]:
    """Referencia por estancia: intentos (h absolutas) y metadatos del 1er evento."""
    idx = json.loads((cases_dir / "mimic_cases_index.json").read_text(encoding="utf-8"))
    icu = pd.read_csv(
        clinical_dir / "ICUSTAYS.csv.gz", compression="gzip",
        usecols=["ICUSTAY_ID", "INTIME", "OUTTIME"],
    ).rename(columns=str.lower)
    outtime = dict(zip(icu.icustay_id.astype(int),
                       pd.to_datetime(icu.outtime, utc=True).astype("int64") / 1e9))

    refs: dict[int, dict] = {}
    for ev in idx["events"]:
        if ev.get("excluded"):
            continue
        stay = int(ev["icustay_id"])
        t0_h = float(ev["t0_unix"]) / SECONDS_PER_HOUR
        attempts = [
            (t0_h + float(a["vent_start_h"]), t0_h + float(a["vent_end_h"]))
            for a in ev["attempts"]
        ]
        lab47 = (ev.get("labels") or {}).get("48h") or {}
        obs_end_h = None
        if stay in outtime:
            obs_end_h = (outtime[stay] - float(ev["t0_unix"])) / SECONDS_PER_HOUR
        entry = {
            "icustay_id": stay,
            "t0_h": t0_h,
            "attempts": attempts,
            "duration_h": (
                (attempts[-1][1] - attempts[0][0]) if attempts else 0.0
            ),
            "label_event_type": lab47.get("event_type"),
            "label_censor_cause": lab47.get("censor_cause"),
            "label_censor_time_h": lab47.get("censor_time_h"),
            "label_extubation_time_h": lab47.get("extubation_time_h"),
            "label_n_failed": int(lab47.get("n_failed_attempts") or 0),
            "obs_end_h": obs_end_h,
        }
        prev = refs.get(stay)
        if prev is None or entry["t0_h"] < prev["t0_h"]:
            refs[stay] = entry       # el primer evento de la estancia manda
    return refs


def load_annotations(cases_dir: Path, stays: set[int]) -> dict[int, np.ndarray]:
    """Horas (absolutas) de las anotaciones marcadoras, por estancia."""
    dset = ds.dataset(cases_dir / "mimic_observations.parquet", format="parquet")
    table = dset.to_table(
        columns=["ICUSTAY_ID", "CONCEPT", "t_unix"],
        filter=ds.field("CONCEPT").isin(list(MARKER_CONCEPTS)),
    )
    df = table.to_pandas()
    df = df[df["ICUSTAY_ID"].isin(stays)]
    df["h"] = df["t_unix"].astype("float64") / SECONDS_PER_HOUR
    return {
        int(stay): np.sort(g["h"].to_numpy(dtype=np.float64))
        for stay, g in df.groupby("ICUSTAY_ID", sort=False)
    }


def _pct(values: np.ndarray, tol: float) -> float:
    if values.size == 0:
        return float("nan")
    return float(100.0 * np.mean(np.abs(values) <= tol))


def _err_stats(err: np.ndarray) -> dict:
    if err.size == 0:
        return {"n": 0, "median": None, "iqr": None, "p90_abs": None,
                "pct_1h": None, "pct_2h": None, "pct_4h": None}
    q1, med, q3 = np.percentile(err, [25, 50, 75])
    return {
        "n": int(err.size),
        "median": float(med),
        "iqr": float(q3 - q1),
        "p90_abs": float(np.percentile(np.abs(err), 90)),
        "pct_1h": _pct(err, 1.0),
        "pct_2h": _pct(err, 2.0),
        "pct_4h": _pct(err, 4.0),
    }


def _fmt(value, spec: str = "%.1f") -> str:
    """Formatea tolerando ``None``/NaN."""
    if value is None or not np.isfinite(value):
        return "n/a"
    return spec % value


def run_stratum(refs: dict[int, dict], ann_by_stay: dict[int, np.ndarray],
                gap_h: float, subsample_h: float) -> dict:
    """Métricas de un (G, submuestreo).

    Emparejamiento por **solape**: cada intervalo de referencia se compara con
    la UNIÓN de los intervalos reconstruidos que lo solapan, de modo que la
    fragmentación (varios trozos dentro del mismo intervalo real) NO se cuenta
    como error de inicio/fin; su efecto se mide aparte (fragmentos por intervalo
    y F1 de reintubación).

    - ``lost``: intervalos de referencia sin ningún solape reconstruido.
    - ``spurious_min``: minutos reconstruidos FUERA de todo intervalo real.
    - ``start_error``/``end_error``: (primer inicio reconstruido dentro del
      intervalo real) − inicio real, y (último fin reconstruido dentro) − fin real.
    - ``n_fragments``: trozos reconstruidos por intervalo real (fragmentación).
    """
    err_s: list[float] = []
    err_e: list[float] = []
    err_x: list[float] = []
    frag: list[int] = []
    n_ref = n_ann = n_lost = 0
    spurious_min = 0.0
    ann_total_min = 0.0
    ref_reintub: list[float] = []
    ann_reintub: list[float] = []
    matched_reintub = 0
    label_ok = label_n = 0
    fail_ok = fail_n = 0

    for stay, ref in refs.items():
        refs_list = ref["attempts"]
        n_ref += len(refs_list)
        times = ann_by_stay.get(stay)
        if times is None or times.size == 0:
            n_lost += len(refs_list)
            ref_reintub.extend(s for s, _ in refs_list[1:])
            continue
        spans = intervals_from_annotations(
            subsample_times(times, subsample_h), gap_h, keep_singletons=False)
        ann = [(s.start_h, s.end_h) for s in spans]
        n_ann += len(ann)
        ann_total_min += sum((b - a) * 60.0 for a, b in ann)
        # minutos reconstruidos fuera de todo intervalo real
        for a, b in ann:
            covered = 0.0
            for ra, rb in refs_list:
                covered += max(0.0, min(b, rb) - max(a, ra))
            spurious_min += max(0.0, (b - a) - covered) * 60.0
        # cobertura de cada intervalo real por la unión de los reconstruidos
        for ra, rb in refs_list:
            inside = [(a, b) for a, b in ann if b > ra and a < rb]
            if not inside:
                n_lost += 1
                continue
            frag.append(len(inside))
            err_s.append(min(a for a, _ in inside) - ra)
            err_e.append(max(b for _, b in inside) - rb)
        # reintubaciones (inicio de los intervalos 2..n)
        ref_re = [s for s, _ in refs_list[1:]]
        ann_re = [s for s, _ in ann[1:]]
        used = [False] * len(ann_re)
        for rs in ref_re:
            best, best_d = -1, np.inf
            for k, as_ in enumerate(ann_re):
                if used[k]:
                    continue
                d = abs(as_ - rs)
                if d < best_d:
                    best, best_d = k, d
            if best >= 0 and best_d <= REINTUB_TOL_H:
                used[best] = True
                matched_reintub += 1
        ref_reintub.extend(ref_re)
        ann_reintub.extend(ann_re)

        # Etiqueta a 48 h del evento (con la misma censura que la referencia).
        if ref["label_event_type"] and ref["obs_end_h"] is not None:
            t0_h = ref["t0_h"]
            pairs_lab = [(b - t0_h, (ann[k + 1][0] - t0_h if k + 1 < len(ann) else None))
                         for k, (_, b) in enumerate(ann)]
            lab = assign_label(
                attempts_from_pairs(pairs_lab), obs_end_h=ref["obs_end_h"],
                failure_window_h=48.0,
                censor_cause=(ref["label_censor_cause"]
                              if str(ref["label_event_type"]).startswith("censored") else None),
                censor_time_h=ref["label_censor_time_h"],
            )
            label_n += 1
            if lab.event_type == ref["label_event_type"]:
                label_ok += 1
            fail_n += 1
            if (lab.n_failed_attempts > 0) == (ref["label_n_failed"] > 0):
                fail_ok += 1
            if (ref["label_event_type"] == "successful_extubation"
                    and ref["label_extubation_time_h"] is not None and ann):
                err_x.append((ann[0][1] - t0_h) - float(ref["label_extubation_time_h"]))

    err_s_a = np.asarray(err_s, dtype=float)
    err_e_a = np.asarray(err_e, dtype=float)
    err_x_a = np.asarray(err_x, dtype=float)
    tp = matched_reintub
    sens = tp / len(ref_reintub) if ref_reintub else float("nan")
    ppv = tp / len(ann_reintub) if ann_reintub else float("nan")
    f1 = (2 * sens * ppv / (sens + ppv)) if ref_reintub and ann_reintub and (sens + ppv) > 0 else float("nan")
    return {
        "gap_h": gap_h,
        "subsample_h": subsample_h,
        "n_stays": len(refs),
        "n_intervals_ref": int(n_ref),
        "n_intervals_ann": int(n_ann),
        "lost": int(n_lost),
        "lost_pct": (100.0 * n_lost / n_ref) if n_ref else float("nan"),
        "fragments_per_interval": (float(np.mean(frag)) if frag else float("nan")),
        "extra_minutes_pct": ((100.0 * spurious_min / ann_total_min)
                              if ann_total_min else float("nan")),
        "start_error": _err_stats(err_s_a),
        "end_error": _err_stats(err_e_a),
        "reintub_ref": len(ref_reintub),
        "reintub_ann": len(ann_reintub),
        "reintub_matched": int(tp),
        "reintub_sensitivity": sens,
        "reintub_ppv": ppv,
        "reintub_f1": f1,
        "label48_n": int(label_n),
        "label48_agreement": (100.0 * label_ok / label_n) if label_n else float("nan"),
        "label48_fail_agreement": (100.0 * fail_ok / fail_n) if fail_n else float("nan"),
        "extubation_diff": _err_stats(err_x_a),
    }


def _score(stats: dict) -> float:
    """Puntuación de un estrato: cobertura (inicio/fin ±2 h) y F1 de reintubación."""
    parts = []
    for key in ("start_error", "end_error"):
        v = stats[key]["pct_2h"]
        if v is not None and np.isfinite(v):
            parts.append(v / 100.0)
    if np.isfinite(stats["reintub_f1"]):
        parts.append(stats["reintub_f1"])
    return float(np.mean(parts)) if parts else float("nan")


def choose_gap(all_stats: list[dict]) -> dict:
    """Elección de G con las métricas de MIMIC (ver docstring del módulo).

    Restricciones duras, en TODOS los estratos:

    - ``extra_minutes_pct <= 10 %``: no inventar ventilación fuera de los reales;
    - ``lost_pct <= 15 %``: acotar la pérdida de intervalos reales. Con
      anotación cada 4 h la pérdida es estructural (huecos de anotación durante
      ventilación real): con G=8 h es del 13 %, el mínimo alcanzable en la
      rejilla evaluada. En los estratos de 1 h y 2 h el límite es del 5 %.

    Entre los que cumplen se maximiza el mínimo entre estratos de ``_score``
    (media de ``% inicio ±2 h``, ``% fin ±2 h`` y ``F1`` de reintubación). En
    caso de empate a menos de 0.02 se prefiere el ``G`` **menor** (parsimonia:
    fusionar menos reduce el riesgo de unir episodios distintos).
    """
    by_gap: dict[float, list[dict]] = {}
    for s in all_stats:
        by_gap.setdefault(s["gap_h"], []).append(s)

    eligible: list[tuple[float, float]] = []
    table: list[dict] = []
    for gap, stats in sorted(by_gap.items()):
        ok = all(
            (np.isfinite(s["extra_minutes_pct"]) and s["extra_minutes_pct"] <= 10.0)
            and (np.isfinite(s["lost_pct"])
                 and s["lost_pct"] <= (5.0 if s["subsample_h"] <= 2.0 else 15.0))
            for s in stats
        )
        scores = [s for s in (_score(x) for x in stats) if np.isfinite(s)]
        worst = min(scores) if scores else float("nan")
        table.append({"gap_h": gap, "eligible": bool(ok), "worst_stratum_score": worst})
        if ok:
            eligible.append((worst, gap))
    chosen = None
    if eligible:
        best = max(w for w, _ in eligible)
        chosen = min(g for w, g in eligible if w >= best - 0.02)
    return {
        "chosen_gap_h": chosen,
        "candidates": table,
        "regla": ("extra<=10% en todos los estratos; perdidos<=5% (anotacion<=2h) y "
                  "<=15% (anotacion 4h); entre los que cumplen, "
                  "max(min_estrato(score)); empate <0.02 -> G menor"),
    }


# Mapeo estrato de calibración -> intervalo de anotación del hospital (min).
def stratum_for_annotation(annotation_median_min: float | None) -> float:
    """Estrato de calibración que corresponde a la anotación de un hospital."""
    if annotation_median_min is None:
        return 1.0
    if annotation_median_min <= 60.0:
        return 1.0
    if annotation_median_min <= 120.0:
        return 2.0
    return 4.0


def transferable_error(all_stats: list[dict], gap_h: float) -> list[dict]:
    """Error de etiqueta esperado por estrato de anotación, con el ``G`` elegido.

    Es lo que se traslada a eICU: para cada frecuencia de anotación, la
    incertidumbre del fin del episodio y su efecto en las reintubaciones y en la
    etiqueta a 48 h.
    """
    out: list[dict] = []
    for s in all_stats:
        if s["gap_h"] != gap_h:
            continue
        out.append({
            "subsample_h": s["subsample_h"],
            "end_error_median_h": s["end_error"]["median"],
            "end_error_iqr_h": s["end_error"]["iqr"],
            "end_error_p90_abs_h": s["end_error"]["p90_abs"],
            "end_within_1h_pct": s["end_error"]["pct_1h"],
            "end_within_2h_pct": s["end_error"]["pct_2h"],
            "end_within_4h_pct": s["end_error"]["pct_4h"],
            "start_within_2h_pct": s["start_error"]["pct_2h"],
            "lost_pct": s["lost_pct"],
            "extra_minutes_pct": s["extra_minutes_pct"],
            "reintub_sensitivity": s["reintub_sensitivity"],
            "reintub_ppv": s["reintub_ppv"],
            "reintub_f1": s["reintub_f1"],
            "label48_agreement_pct": s["label48_agreement"],
            "extubation_diff_median_h": s["extubation_diff"]["median"],
        })
    return out


def main() -> None:
    config = load_config(CONFIG)
    cfg = config.get("fase1_6b", {})
    root = ROOT / cfg.get("mimic_cases_root", "datasets/mimic3wdb")
    cases_dir = latest_cases_dir(root)
    clinical_dir = config_path(config, "paths", "mimic_clinical_dir")
    out_dir = ROOT / cfg.get("reports_dir", "reports/fase1_6b")
    out_dir.mkdir(parents=True, exist_ok=True)
    strata = tuple(cfg.get("vent_intervals", {}).get("annotation_strata_h", [1, 2, 4]))
    gaps = tuple(cfg.get("vent_intervals", {}).get("gap_candidates_h", [2, 4, 6, 8]))

    print(f"[cal] casos: {cases_dir}", flush=True)
    refs = load_reference(cases_dir, clinical_dir)
    print(f"[cal] estancias referencia: {len(refs)}", flush=True)
    ann = load_annotations(cases_dir, set(refs))
    print(f"[cal] estancias con anotaciones: {len(ann)}", flush=True)

    all_stats: list[dict] = []
    for gap in gaps:
        for sub in strata:
            st = run_stratum(refs, ann, float(gap), float(sub))
            all_stats.append(st)
            print(f"[cal] G={gap}h anot>={sub}h: "
                  f"perdidos={_fmt(st['lost_pct'])}% fuera={_fmt(st['extra_minutes_pct'])}% "
                  f"frag={_fmt(st['fragments_per_interval'], '%.2f')} "
                  f"inicio±2h={_fmt(st['start_error']['pct_2h'])}% "
                  f"fin±2h={_fmt(st['end_error']['pct_2h'])}% "
                  f"F1reint={_fmt(st['reintub_f1'], '%.3f')} "
                  f"etiq48={_fmt(st['label48_agreement'])}%", flush=True)

    choice = choose_gap(all_stats)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases_dir": str(cases_dir.relative_to(ROOT)),
        "marker_concepts": list(MARKER_CONCEPTS),
        "match_tol_h": MATCH_TOL_H,
        "reintub_tol_h": REINTUB_TOL_H,
        "strata_h": list(strata),
        "gaps_h": list(gaps),
        "metrics": all_stats,
        "choice": choice,
        "transferable_error": (
            transferable_error(all_stats, choice["chosen_gap_h"])
            if choice["chosen_gap_h"] is not None else []),
    }
    (out_dir / "calibracion_mimic.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # Tabla legible
    lines = ["# Calibración de G en MIMIC-MetaVision (Fase 1.6b, punto 1)", ""]
    lines.append("| G (h) | anotación | perdidos | fuera | fragmentos/int | "
                 "inicio med (P90, ±2h) | fin med (P90, ±2h) | F1 reintub | "
                 "etiq 48 h | Δ extubación med |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for s in all_stats:
        lines.append(
            f"| {s['gap_h']:.0f} | {s['subsample_h']:.0f} h | {_fmt(s['lost_pct'])} % | "
            f"{_fmt(s['extra_minutes_pct'])} % | "
            f"{_fmt(s['fragments_per_interval'], '%.2f')} | "
            f"{_fmt(s['start_error']['median'], '%.2f')} "
            f"({_fmt(s['start_error']['p90_abs'], '%.2f')}, "
            f"{_fmt(s['start_error']['pct_2h'])} %) | "
            f"{_fmt(s['end_error']['median'], '%.2f')} "
            f"({_fmt(s['end_error']['p90_abs'], '%.2f')}, "
            f"{_fmt(s['end_error']['pct_2h'])} %) | "
            f"{_fmt(s['reintub_f1'], '%.3f')} | "
            f"{_fmt(s['label48_agreement'])} % | "
            f"{_fmt(s['extubation_diff']['median'], '%.2f')} h |")
    lines += ["", "## Elección", "",
              f"**G elegido = {choice['chosen_gap_h']} h**", "",
              f"Regla: {choice['regla']}", "",
              "| G (h) | cumple criterios | peor estrato (score) |",
              "|---|---|---|"]
    for c in choice["candidates"]:
        lines.append(f"| {c['gap_h']:.0f} | {'sí' if c['eligible'] else 'no'} | "
                     f"{_fmt(c['worst_stratum_score'], '%.3f')} |")
    lines += ["", "## Error de etiqueta esperado con el G elegido", "",
              "Traslado a eICU según la frecuencia de anotación del hospital", "",
              "| Anotación | fin med (IQR) | fin P90 | fin ±2 h | reintub sens | "
              "reintub VPP | F1 | etiq 48 h |",
              "|---|---|---|---|---|---|---|---|"]
    for t in transferable_error(all_stats, choice["chosen_gap_h"] or 0.0):
        lines.append(
            f"| {t['subsample_h']:.0f} h | {_fmt(t['end_error_median_h'], '%.2f')} h "
            f"({_fmt(t['end_error_iqr_h'], '%.2f')}) | "
            f"{_fmt(t['end_error_p90_abs_h'], '%.2f')} h | "
            f"{_fmt(t['end_within_2h_pct'])} % | "
            f"{_fmt(t['reintub_sensitivity'], '%.3f')} | "
            f"{_fmt(t['reintub_ppv'], '%.3f')} | {_fmt(t['reintub_f1'], '%.3f')} | "
            f"{_fmt(t['label48_agreement_pct'])} % |")
    (out_dir / "calibracion_mimic.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(choice, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
