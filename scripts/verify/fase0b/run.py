"""
scripts/verify/fase0b/run.py
=============================
Runner de Fase 0b (solo lectura). Ejecuta las comprobaciones de
`checks.py` sobre los datos reales y escribe JSON en `reports/fase0b/`.

Uso:
    python scripts/verify/fase0b/run.py \
        --clinic-raw D:/data/clinic_vitals \
        --vitaldb-raw D:/data/vitaldb_sicu
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import math
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import numpy as np

from scripts.verify.fase0b.checks import (
    check_expected_channels,
    check_fused_vs_source,
    segment_events_and_attempts,
    summarize,
)

REPORT_DIR = ROOT / "reports" / "fase0b"

# ── canales esperados: LA MISMA LISTA para todas las cohortes ─────────────────
BASE_VARIABLES = ["RR", "HR", "SpO2", "PEEP", "MAP", "FiO2", "TV", "PIP"]
WAVE_VARIABLES = ["ECG", "PPG", "ABP"]

# Mapa canónico -> pistas que lo representan en cada cohorte (estado ACTUAL).
# Se intentan en orden; se toma la primera con cobertura > 0.
CHANNEL_TRACKS: dict[str, dict[str, list[str]]] = {
    "clinic": {
        "RR": ["Intellivue/VENT_RR"],
        "HR": ["Intellivue/ECG_HR", "Intellivue/HR", "Intellivue/PLETH_HR"],
        "SpO2": ["Intellivue/PLETH_SAT_O2"],
        "PEEP": ["Intellivue/PEEP_CMH2O"],
        "MAP": ["Intellivue/ART_MEAN", "Intellivue/ABP_MEAN", "Intellivue/NIBP_MEAN"],
        "FiO2": ["Intellivue/FIO2"],
        "TV": ["Intellivue/TV_EXP"],
        "PIP": ["Intellivue/PIP_CMH2O"],
        "ECG": ["Intellivue/ECG_II"],
        "PPG": ["Intellivue/PLETH"],
        "ABP": ["Intellivue/ART", "Intellivue/ABP"],
    },
    "vitaldb": {
        "RR": ["Intellivue/VENT_RR", "Intellivue/RR"],
        "HR": ["Intellivue/ECG_HR", "Intellivue/PLETH_HR", "Intellivue/HR"],
        "SpO2": ["Intellivue/PLETH_SAT_O2"],
        "PEEP": ["Intellivue/PEEP_CMH2O"],
        "MAP": ["Intellivue/ABP_MEAN", "Intellivue/NIBP_MEAN"],
        "FiO2": ["Intellivue/FIO2"],
        "TV": ["Intellivue/TV_EXP"],
        "PIP": ["Intellivue/PIP_CMH2O"],
        "ECG": ["Intellivue/ECG_II", "Intellivue/ECG_II_WAV"],
        "PPG": ["Intellivue/PLETH"],
        "ABP": ["Intellivue/ABP"],
    },
    "mimic": {
        "RR": ["MIMIC/RR_V", "MIMIC/RESP"],
        "HR": ["MIMIC/HR"],
        "SpO2": ["MIMIC/SpO2"],
        "PEEP": ["MIMIC/PEEP"],
        "MAP": ["MIMIC/ABP_M"],
        "FiO2": ["MIMIC/FiO2"],
        "TV": ["MIMIC/TV"],
        "PIP": ["MIMIC/PIP"],
        "ECG": [],
        "PPG": [],
        "ABP": ["MIMIC/ABP", "MIMIC/ART"],
    },
    "eicu": {
        "RR": ["eICU/RR_V"],
        "HR": ["eICU/HR"],
        "SpO2": ["eICU/SpO2"],
        "PEEP": ["eICU/PEEP"],
        "MAP": ["eICU/ABP_M"],
        "FiO2": ["eICU/FiO2"],
        "TV": ["eICU/TV"],
        "PIP": ["eICU/PIP"],
        "ECG": [],
        "PPG": [],
        "ABP": [],
    },
}


def read_vital_header_epoch(path: Path) -> tuple[float, float] | None:
    """Lee (dtstart, dtend) en epoch desde la cabecera VITA sin descomprimir todo."""
    try:
        with open(path, "rb") as fh:
            gz = gzip.GzipFile(fileobj=fh)
            if gz.read(4) != b"VITA":
                return None
            gz.read(4)
            hb = gz.read(2)
            if len(hb) < 2:
                return None
            hl = struct.unpack("<H", hb)[0]
            if hl < 26:
                return None
            h = gz.read(hl)
            if len(h) < 26:
                return None
            return struct.unpack("<d", h[10:18])[0], struct.unpack("<d", h[18:26])[0]
    except Exception:
        return None


def track_hour_coverage(vf, track_name: str, t0_epoch: float, tend_epoch: float) -> float:
    """Fracción de horas (bins de 1 h) de [t0, tend] cubiertas por la pista."""
    trk = vf.trks.get(track_name)
    if trk is None or not trk.recs:
        return 0.0
    span_h = (tend_epoch - t0_epoch) / 3600.0
    if span_h <= 0:
        return 0.0
    n_bins = max(1, int(math.ceil(span_h)))
    covered = np.zeros(n_bins, dtype=bool)
    srate = float(trk.srate) if trk.srate and trk.srate > 0 else 0.0
    for r in trk.recs:
        dt = float(r["dt"])
        if srate > 0:
            v = np.asarray(r["val"], dtype=np.float32)
            n = v.size
            end = dt + n / srate
        else:
            end = dt  # punto (numerico)
        b0 = int((dt - t0_epoch) / 3600.0)
        b1 = int((end - t0_epoch) / 3600.0)
        lo = max(0, b0)
        hi = min(n_bins - 1, b1)
        if hi >= lo:
            covered[lo : hi + 1] = True
    return float(covered.mean())


def first_vital(vf, t0_epoch: float, names: list[str]) -> str | None:
    """Devuelve la primera pista de `names` que existe y tiene datos."""
    for n in names:
        trk = vf.trks.get(n)
        if trk is not None and trk.recs:
            return n
    return None


def _import_vitaldb():
    import vitaldb
    return vitaldb


# ── recogida de datos ─────────────────────────────────────────────────────────

def collect_cohort_cases(cohort: str, cases_dir: Path, index_path: Path | None,
                         raw_dir: Path | None, max_cases: int | None = None) -> dict:
    """Recolecta métricas por caso (duración de cabecera y cobertura por canal)."""
    vitaldb = _import_vitaldb()
    out = {
        "cohort": cohort,
        "n_cases": 0,
        "durations_h": [],
        "coverage_by_channel": collections.defaultdict(list),
        "channel_present_cases": collections.defaultdict(int),
        "fused_vs_source": [],
        "omitted": [],
    }
    files = sorted(cases_dir.glob("*.vital"))
    if max_cases and len(files) > max_cases:
        import random as _random
        _random.seed(42)
        files = _random.sample(files, max_cases)

    for f in files:
        # 1) cabecera + lista de pistas SIN cargar registros (rápido)
        try:
            vh = vitaldb.VitalFile(str(f), header_only=True)
        except Exception:
            continue
        track_names_present = set(vh.get_track_names())

        # 2) solo las pistas numéricas (baratas) para calcular cobertura horaria
        num_cands = _numeric_track_candidates(cohort)
        try:
            vn = vitaldb.VitalFile(str(f), track_names=num_cands) if num_cands else None
        except Exception:
            vn = None

        t0, tend = _case_bounds(cohort, f, vh, vn)
        if t0 is None or tend is None or tend <= t0:
            continue
        dur_h = (tend - t0) / 3600.0
        out["n_cases"] += 1
        out["durations_h"].append(dur_h)

        for canon in BASE_VARIABLES:
            track = first_vital(vn, t0, CHANNEL_TRACKS[cohort].get(canon, [])) if vn else None
            if track is None:
                out["coverage_by_channel"][canon].append(0.0)
            else:
                cov = track_hour_coverage(vn, track, t0, tend)
                out["coverage_by_channel"][canon].append(cov)
                if cov > 0:
                    out["channel_present_cases"][canon] += 1

        # 3) ondas: presencia de la pista en el fichero (sin cargar la señal)
        for canon in WAVE_VARIABLES:
            present = any(t in track_names_present for t in CHANNEL_TRACKS[cohort].get(canon, []))
            out["coverage_by_channel"][canon].append(1.0 if present else 0.0)
            if present:
                out["channel_present_cases"][canon] += 1
    return out


def _numeric_track_candidates(cohort: str) -> list[str]:
    out: list[str] = []
    for canon in BASE_VARIABLES:
        out.extend(CHANNEL_TRACKS[cohort].get(canon, []))
    return sorted(set(out))


MIMIC_CASE_PAT = re.compile(r"mimic_(\d+)_(\d{8})_(\d{6})_to_(\d{8})_(\d{6})\.vital")


def _parse_yyyy_to_epoch(date_part: str, time_part: str) -> float:
    from datetime import datetime, timezone
    dt = datetime.strptime(date_part + time_part, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return dt.timestamp()


def mimic_filename_bounds(fname: str) -> tuple[float, float] | None:
    m = MIMIC_CASE_PAT.match(fname)
    if not m:
        return None
    return _parse_yyyy_to_epoch(m.group(2), m.group(3)), _parse_yyyy_to_epoch(m.group(4), m.group(5))


def _max_track_end(vf) -> float:
    """Máximo dt (o dt+len/srate) entre todas las pistas del fichero."""
    mx = None
    for trk in vf.trks.values():
        if not trk.recs:
            continue
        srate = float(trk.srate) if trk.srate and trk.srate > 0 else 0.0
        for r in trk.recs:
            dt = float(r["dt"])
            end = dt + (np.asarray(r["val"], dtype=np.float32).size / srate if srate > 0 else 0.0)
            mx = end if mx is None else max(mx, end)
    return mx


def _case_bounds(cohort: str, f: Path, vh, vn) -> tuple[float | None, float | None]:
    """
    t0/tend fiables por cohorte. Los ficheros MIMIC/eICU tienen la cabecera
    corrupta (dtstart≈'ahora'), así que NO se usa la cabecera para ellos.
    `vh` es un VitalFile abierto con header_only; `vn` con solo numéricos.
    """
    if cohort == "mimic":
        b = mimic_filename_bounds(f.name)
        return b if b else (None, None)
    if cohort == "eicu":
        # los tracks eICU guardan segundos relativos a t0=0
        tend = _max_track_end(vn)
        return (0.0, tend) if tend and tend > 0 else (None, None)
    return (vh.dtstart, vh.dtend)


def collect_index_durations(index_path: Path) -> list[float]:
    """Duraciones por evento según el índice (duration_seconds y t0/tend)."""
    durs: list[float] = []
    with open(index_path, encoding="utf-8") as fh:
        idx = json.load(fh)
    for ev in idx.get("events", []):
        if ev.get("t0_unix") is not None and ev.get("tend_unix") is not None:
            durs.append((ev["tend_unix"] - ev["t0_unix"]) / 3600.0)
    return sorted(durs)


def collect_vitaldb_fusion(vitaldb_raw: Path, index_path: Path, cases_dir: Path) -> dict:
    """P1: duración fusionada vs tramo de origen, y ficheros omitidos (VitalDB).

    Los ficheros de origen tienen cabecera corta (hlen=10, sin dtstart/dtend),
    así que sus timestamps se derivan del NOMBRE de fichero (independiente de
    la cabecera del fichero fusionado).
    """
    with open(index_path, encoding="utf-8") as fh:
        idx = json.load(fh)
    # nombre -> epoch de inicio (desde el nombre de fichero)
    src_times: dict[str, float] = {}
    src_pat = re.compile(r"(SICU\d+_\d+)_(\d{6})_(\d{6})\.vital", re.IGNORECASE)
    for p in vitaldb_raw.glob("*.vital"):
        m = src_pat.match(p.name)
        if m:
            src_times[p.name] = _parse_yy(m.group(2), m.group(3))

    pat = re.compile(r"(SICU\d+_\d+)_(\d{6})_(\d{6})_to_(\d{6})_(\d{6})\.vital", re.IGNORECASE)
    rows = []
    for ev in idx.get("events", []):
        m = pat.match(ev["file"])
        if not m:
            continue
        box = m.group(1).upper()
        start_dt = _parse_yy(m.group(2), m.group(3))
        end_dt = _parse_yy(m.group(4), m.group(5))
        source_names = [n for n, t in src_times.items()
                        if n.upper().startswith(box + "_") and start_dt - 3600 <= t <= end_dt + 3600]
        span = None
        if source_names:
            times = sorted(src_times[n] for n in source_names)
            span = (times[-1] - times[0] + 3600.0) / 3600.0  # cada fichero ~1 h
        fused_h = None
        fp = cases_dir / ev["file"]
        h = read_vital_header_epoch(fp)
        if h is not None:
            fused_h = (h[1] - h[0]) / 3600.0
        ck = check_fused_vs_source(fused_h, span) if (fused_h is not None and span) else {
            "passed": False, "reason": "sin datos", "fused_hours": fused_h,
            "source_span_hours": span, "rel_error": None,
        }
        # omitidos: ficheros de origen cuyo inicio queda fuera del rango fusionado
        omitted = []
        if h is not None:
            for n in source_names:
                t = src_times[n]
                if t < h[0] - 3600 or t > h[1] + 3600:
                    omitted.append(n)
        rows.append({
            "event_id": ev.get("event_id"),
            "file": ev["file"],
            "index_duration_seconds": ev.get("duration_seconds"),
            "index_t0_tend_h": (ev["tend_unix"] - ev["t0_unix"]) / 3600.0
            if ev.get("t0_unix") is not None and ev.get("tend_unix") is not None else None,
            "fused_header_h": fused_h,
            "source_span_h": span,
            "n_source_in_range": len(source_names),
            "n_merged_declared": ev.get("num_vital_files_merged"),
            "n_omitted": len(omitted),
            "omitted": omitted[:20],
            "check_fused_vs_source": ck,
        })
    return rows


def collect_clinic_fusion(clinic_raw: Path, index_path: Path, cases_dir: Path) -> dict:
    """P1 para Clínic: ficheros de origen por box (recursivo) con timestamps del
    nombre de fichero."""
    with open(index_path, encoding="utf-8") as fh:
        idx = json.load(fh)
    # box -> lista de (epoch, name); el box es el directorio de primer nivel
    src: dict[str, list[tuple[float, str]]] = collections.defaultdict(list)
    src_pat = re.compile(r"([a-z0-9]+)_(\d{6})_(\d{6})\.vital", re.IGNORECASE)
    for box_dir in clinic_raw.iterdir():
        if not box_dir.is_dir() or box_dir.name == "dataset_clinic":
            continue
        for p in box_dir.rglob("*.vital"):
            m = src_pat.match(p.name)
            if m:
                src[box_dir.name].append((_parse_yy(m.group(2), m.group(3)), p.name))
    for b in src:
        src[b].sort()

    pat = re.compile(r"(box\d+)_(\d{6})_(\d{6})_to_(\d{6})_(\d{6})\.vital", re.IGNORECASE)
    rows = []
    for ev in idx.get("events", []):
        m = pat.match(ev["file"])
        if not m:
            continue
        box = m.group(1).lower()
        start_dt = _parse_yy(m.group(2), m.group(3))
        end_dt = _parse_yy(m.group(4), m.group(5))
        src_list = [x for x in src.get(box, []) if start_dt - 3600 <= x[0] <= end_dt + 3600]
        span = None
        if src_list:
            times = [x[0] for x in src_list]
            span = (max(times) - min(times) + 3600.0) / 3600.0
        fused_h = None
        h = read_vital_header_epoch(cases_dir / ev["file"])
        if h is not None:
            fused_h = (h[1] - h[0]) / 3600.0
        ck = check_fused_vs_source(fused_h, span) if (fused_h is not None and span) else {
            "passed": False, "reason": "sin datos", "fused_hours": fused_h,
            "source_span_hours": span, "rel_error": None,
        }
        omitted = []
        if h is not None:
            for t, name in src_list:
                if t < h[0] - 3600 or t > h[1] + 3600:
                    omitted.append(name)
        rows.append({
            "event_id": ev.get("event_id"),
            "file": ev["file"],
            "fused_header_h": fused_h,
            "source_span_h": span,
            "n_source_in_range": len(src_list),
            "n_omitted": len(omitted),
            "omitted": omitted[:20],
            "check_fused_vs_source": ck,
        })
    return rows


def _parse_yy(date_part: str, time_part: str) -> float:
    from datetime import datetime, timezone
    dt = datetime.strptime(date_part + time_part, "%y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ── análisis de plausibilidad (P2) sobre un caso ──────────────────────────────

VENT_TRACKS = ["VENT_RR", "PEEP_CMH2O", "PIP_CMH2O", "TV_EXP", "AWP_WAV", "FLOW_WAV"]
MONITOR_TRACKS = ["HR", "SpO2", "ECG_II", "PLETH", "ECG_HR", "PLETH_SAT_O2", "ECG_II_WAV"]

# Pistas numéricas (baratas) que se cargan para la segmentación D1/D2.
# Las ondas (AWP_WAV/FLOW_WAV/ECG/PLETH) no se cargan para acotar memoria.
PLAUSIBILITY_TRACKS = {
    "clinic": [
        "Intellivue/VENT_RR", "Intellivue/PEEP_CMH2O", "Intellivue/PIP_CMH2O",
        "Intellivue/TV_EXP", "Intellivue/MV_EXP",
        "Intellivue/ECG_HR", "Intellivue/PLETH_SAT_O2", "Intellivue/HR", "Intellivue/PLETH_HR",
    ],
    "vitaldb": [
        "Intellivue/VENT_RR", "Intellivue/PEEP_CMH2O", "Intellivue/PIP_CMH2O",
        "Intellivue/TV_EXP", "Intellivue/MV_EXP",
        "Intellivue/ECG_HR", "Intellivue/PLETH_SAT_O2", "Intellivue/RR",
    ],
}


def _track_intervals_hours(vf, t0_epoch: float, base_names: set[str]) -> list[tuple[float, float]]:
    """Intervalos activos (horas desde t0) para pistas cuyo nombre base coincide."""
    iv: list[tuple[float, float]] = []
    for name, trk in vf.trks.items():
        base = name.split("/")[-1]
        if base not in base_names or not trk.recs:
            continue
        srate = float(trk.srate) if trk.srate and trk.srate > 0 else 0.0
        for r in trk.recs:
            dt = float(r["dt"])
            if srate > 0:
                n = np.asarray(r["val"], dtype=np.float32).size
                end = dt + n / srate
            else:
                end = dt
            iv.append(((dt - t0_epoch) / 3600.0, (end - t0_epoch) / 3600.0))
    return iv


def collect_plausibility(cases_dir: Path, cohort: str, max_cases: int = 15) -> dict:
    """P2: histogramas, >21d, intentos y cortes D2 por señal."""
    vitaldb = _import_vitaldb()
    files = sorted(cases_dir.glob("*.vital"))
    if len(files) > max_cases:
        import random as _random
        _random.seed(7)
        files = _random.sample(files, max_cases)
    durs = []
    n_gt_21d = 0
    attempts_per_event = []
    d2_cuts = []
    for f in files:
        try:
            vh = vitaldb.VitalFile(str(f), header_only=True)
        except Exception:
            continue
        t0, tend = vh.dtstart, vh.dtend
        if tend <= t0:
            continue
        dur_h = (tend - t0) / 3600.0
        durs.append(dur_h)
        if dur_h > 21 * 24:
            n_gt_21d += 1
        try:
            vf = vitaldb.VitalFile(str(f), track_names=PLAUSIBILITY_TRACKS[cohort])
        except Exception:
            continue
        vent_iv = _track_intervals_hours(vf, t0, set(VENT_TRACKS))
        mon_iv = _track_intervals_hours(vf, t0, set(MONITOR_TRACKS))
        seg = segment_events_and_attempts(vent_iv, mon_iv)
        attempts_per_event.append(seg["n_attempts_total"])
        d2_cuts.append(len(seg["events"]))
    return {
        "cohort": cohort,
        "n_cases": len(durs),
        "duration_summary_h": summarize(sorted(durs)),
        "n_gt_21d": n_gt_21d,
        "attempts_per_event_summary": summarize(sorted(attempts_per_event)),
        "d2_events_summary": summarize(sorted(d2_cuts)),
    }


def collect_full_durations(cohort: str, cases_dir: Path) -> list[float]:
    """Duración de TODOS los casos (cabecera leída con gzip parcial; mimic: nombre)."""
    durs: list[float] = []
    for f in sorted(cases_dir.glob("*.vital")):
        if cohort == "mimic":
            b = mimic_filename_bounds(f.name)
            if b and b[1] > b[0]:
                durs.append((b[1] - b[0]) / 3600.0)
            continue
        h = read_vital_header_epoch(f)
        if h and h[1] > h[0]:
            durs.append((h[1] - h[0]) / 3600.0)
    return sorted(durs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clinic-raw", default=None, help="directorio de .vital crudos de Clínic (opcional)")
    ap.add_argument("--vitaldb-raw", default=None, help="directorio de .vital crudos de VitalDB (opcional)")
    args = ap.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict = {}

    clinic_cases = ROOT / "datasets" / "clinic_vitals" / "clinic_full_cases"
    clinic_index = ROOT / "datasets" / "clinic_vitals" / "clinic_full_cases_index.json"
    vitaldb_cases = ROOT / "datasets" / "vitaldb_sicu" / "vitaldb_full_cases"
    vitaldb_index = ROOT / "datasets" / "vitaldb_sicu" / "vitaldb_full_cases_index.json"
    mimic_cases = ROOT / "datasets" / "mimic3wdb" / "mimic_full_cases_enriched"
    eicu_cases = ROOT / "datasets" / "eicu_collaborative" / "eicu_full_cases"

    # P1 + P2 + P3 (coberturas y canales esperados) por cohorte
    for cohort, cdir, idx_path in [
        ("clinic", clinic_cases, clinic_index),
        ("vitaldb", vitaldb_cases, vitaldb_index),
        ("mimic", mimic_cases, None),
        ("eicu", eicu_cases, None),
    ]:
        cc = collect_cohort_cases(cohort, cdir, idx_path, None, max_cases=12)
        cc["duration_summary_h"] = summarize(sorted(cc["durations_h"]))
        # canales con cobertura > 0 agregada (mediana de cobertura por canal)
        cov_agg = {}
        for c, vals in cc["coverage_by_channel"].items():
            cov_agg[c] = {"median": percentile_sorted(vals, 0.5),
                          "p5": percentile_sorted(vals, 0.05),
                          "p95": percentile_sorted(vals, 0.95),
                          "n_cases_with_data": cc["channel_present_cases"].get(c, 0)}
        cc["coverage_summary"] = cov_agg
        # canal presente = cobertura mediana > 0
        present = [c for c, v in cov_agg.items() if (v["median"] or 0) > 0]
        cc["expected_channels"] = check_expected_channels(
            present, BASE_VARIABLES + WAVE_VARIABLES
        )
        report[f"{cohort}_cases"] = cc

    # P1 fusión Clínic/VitalDB (si se proporciona el raw)
    if args.clinic_raw and Path(args.clinic_raw).exists():
        report["clinic_fusion"] = collect_clinic_fusion(Path(args.clinic_raw), clinic_index, clinic_cases)
    else:
        report["clinic_fusion"] = {"skipped": "sin --clinic-raw"}
    if args.vitaldb_raw and Path(args.vitaldb_raw).exists():
        report["vitaldb_fusion"] = collect_vitaldb_fusion(Path(args.vitaldb_raw), vitaldb_index, vitaldb_cases)
    else:
        report["vitaldb_fusion"] = {"skipped": "sin --vitaldb-raw"}

    # P2 plausibilidad (muestra)
    report["clinic_plausibility"] = collect_plausibility(clinic_cases, "clinic")
    report["vitaldb_plausibility"] = collect_plausibility(vitaldb_cases, "vitaldb")

    # Duración completa (todos los casos; eICU no tiene cabecera fiable)
    report["full_durations"] = {
        cohort: summarize(collect_full_durations(cohort, cdir))
        for cohort, cdir in [
            ("clinic", clinic_cases),
            ("vitaldb", vitaldb_cases),
            ("mimic", mimic_cases),
        ]
    }

    with open(REPORT_DIR / "fase0b.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, default=str)
    print(f"[fase0b] escrito {REPORT_DIR / 'fase0b.json'}")
    return 0


def percentile_sorted(vals, p):
    if not vals:
        return None
    a = sorted(vals)
    k = max(0, min(len(a) - 1, int(round((len(a) - 1) * p))))
    return round(float(a[k]), 4)


if __name__ == "__main__":
    raise SystemExit(main())
