#!/usr/bin/env python3
"""
scripts/verify/fase1_6b/vitaldb_review.py
=========================================
Fase 1.6b — **punto 4**: revisión de la cohorte VitalDB.

Responde a las tres preguntas del investigador:

1. **Cobertura por variable (D8)**: fracciones por variable y ``vars_ok`` 50/80
   del índice nuevo.
2. **¿Por qué hay un 24 % de eventos sin FC durante la ventilación si D4 exige
   monitor?** Se comprueba, con la caché de sondas, qué pistas de monitor tiene
   cada evento: D4 exige **presencia de monitor** (HR *o* SpO2 *o* onda ECG/PLETH),
   no FC numérica.
3. **Reetiquetado de los ``death_or_transfer``**: mapeo antiguo → nuevo motivo de
   fin (vocabulario único de las 4 cohortes) y si cambian las etiquetas.

Salidas (``reports/fase1_6b/``):
  ``vitaldb_review.json``

Uso:
    python scripts/verify/fase1_6b/vitaldb_review.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

HR_TRACKS = ("Intellivue/ECG_HR", "Intellivue/HR", "Intellivue/PLETH_HR")
SPO2_TRACKS = ("Intellivue/PLETH_SAT_O2",)
WAVE_TRACKS = ("Intellivue/ECG_II", "Intellivue/PLETH")
MANDATORY = ("HR", "SpO2", "MAP", "RR", "FiO2", "PEEP")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_old_index(root: Path) -> Path | None:
    cands = sorted(root.glob("cases_v*/vitaldb_cases_index.json"))
    return cands[-1] if cands else None


def _probes_by_name(probes: dict) -> dict[str, dict]:
    """``{nombre de fichero: sonda}`` a partir de la caché (clave = ruta)."""
    out: dict[str, dict] = {}
    for path_str, data in probes.items():
        out[Path(path_str).name] = data
    return out


def _monitor_profile(event: dict, probes_by_name: dict[str, dict]) -> dict:
    """Qué pistas de monitor tienen los ficheros de un evento."""
    has = Counter()
    for name in event.get("source_files", []):
        data = probes_by_name.get(name)
        if not data:
            continue
        for track in (data.get("tracks") or {}):
            if track in HR_TRACKS:
                has["HR"] += 1
            elif track in SPO2_TRACKS:
                has["SpO2"] += 1
            elif track in WAVE_TRACKS:
                has["onda"] += 1
    return dict(has)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--old-index", default=None)
    p.add_argument("--new-index", default=None)
    p.add_argument("--probe-cache", default=str(ROOT / "reports" / "fase1_5" / "vitaldb_probe.json"))
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6b"))
    args = p.parse_args()

    data_root = ROOT / "datasets" / "vitaldb"
    old_path = Path(args.old_index) if args.old_index else Path(
        str(ROOT / "datasets" / "vitaldb" / "cases_v0.2.0_a805771c" / "vitaldb_cases_index.json"))
    if not old_path.exists():
        found = _find_old_index(data_root)
        old_path = found if found else old_path
    new_cands = sorted(data_root.glob("cases_v0.3.0_*/vitaldb_cases_index.json"))
    new_path = Path(args.new_index) if args.new_index else (
        new_cands[-1] if new_cands else None)

    out: dict = {"old_index": str(old_path), "new_index": str(new_path) if new_path else None}

    # ── 1 y 3: comparación de índices ───────────────────────────────────────
    if new_path and new_path.exists():
        new = _load(new_path)
        events = new["events"]
        out["n_events"] = len(events)
        out["n_excluded"] = new.get("total_excluded_events")
        out["end_reason"] = dict(Counter(e["end_reason"] for e in events))
        out["labels_48h"] = dict(Counter(e["labels"]["48h"]["event_type"] for e in events))
        out["labels_72h"] = dict(Counter(e["labels"]["72h"]["event_type"] for e in events))
        out["failure_events_48h"] = sum(
            1 for e in events if e["labels"]["48h"]["n_failed_attempts"] > 0)
        durs = np.asarray([e["duration_seconds"] / 3600.0 for e in events], dtype=float)
        out["duration_h"] = {
            "median": float(np.median(durs)), "p25": float(np.percentile(durs, 25)),
            "p75": float(np.percentile(durs, 75)),
        }
        out["levels"] = new.get("levels")
        # Cobertura por variable (D8)
        cov: dict[str, list[float]] = defaultdict(list)
        for e in events:
            for var, frac in (e.get("coverage") or {}).items():
                cov[var].append(float(frac))
        out["coverage_median"] = {v: float(np.median(x)) for v, x in cov.items()}
        out["coverage_pct_above_50"] = {
            v: float(100.0 * np.mean([f > 0.5 for f in x])) for v, x in cov.items()}
        out["vars_ok_50"] = int(new.get("vars_ok_50") or 0)
        out["vars_ok_80"] = int(new.get("vars_ok_80") or 0)
        out["limiting_variable"] = (
            min(out["coverage_pct_above_50"], key=out["coverage_pct_above_50"].get)
            if out["coverage_pct_above_50"] else None)

    if old_path.exists():
        old = _load(old_path)
        old_events = old["events"]
        out["old_end_reason"] = dict(Counter(e["end_reason"] for e in old_events))
        out["old_labels_48h"] = dict(Counter(
            e["labels"]["48h"]["event_type"] for e in old_events))

        # Proyeccion al vocabulario nuevo (Fase 1.6b, punto 4): se puede calcular
        # SIN releer senales, porque la muerte por senal ya esta en el indice.
        def new_reason(e: dict) -> str:
            if e.get("death_signal", {}).get("detected"):
                return "death_at_vent"
            if e["labels"]["48h"]["event_type"] == "successful_extubation":
                return "extubation_observed"
            return "end_of_record"

        proj = Counter(new_reason(e) for e in old_events)
        out["projected_end_reason"] = dict(proj)
        d_or_t = [e for e in old_events if e["end_reason"] == "death_or_transfer"]
        out["n_death_or_transfer"] = len(d_or_t)
        out["death_or_transfer_mapping"] = [{
            "event_id": e["event_id"], "box": e["box"],
            "old_end_reason": "death_or_transfer",
            "new_end_reason": new_reason(e),
            "death_signal": bool(e.get("death_signal", {}).get("detected")),
            "old_label_48h": e["labels"]["48h"]["event_type"],
            "signal_loss_at_end": e.get("signal_loss_at_end"),
            "monitor_tail_h": e.get("monitor_tail_h"),
        } for e in d_or_t]
        out["death_or_transfer_new_reasons"] = dict(
            Counter(m["new_end_reason"] for m in out["death_or_transfer_mapping"]))
        # Eventos que la regla fisiologica podria mover (necesitan releer senal).
        out["candidates_physiological_change"] = [
            e["event_id"] for e in old_events
            if e["end_reason"] == "extubation_observed"
            and (e.get("monitor_tail_h") or 0) < 2.0]

    if new_path and new_path.exists():
        new = _load(new_path)
        events = new["events"]
        out["new_end_reason"] = dict(Counter(e["end_reason"] for e in events))

    # ── 2: perfil de monitor por evento (D4 vs FC) ──────────────────────────
    probe_path = Path(args.probe_cache)
    if probe_path.exists() and old_path.exists():
        probes = _probes_by_name(_load(probe_path))
        events = (_load(new_path)["events"] if (new_path and new_path.exists())
                  else old_events)
        profiles = {e["event_id"]: _monitor_profile(e, probes) for e in events}
        no_hr = [eid for eid, p in profiles.items() if not p.get("HR")]
        out["monitor_profile"] = {
            "n_events": len(events),
            "n_without_HR": len(no_hr),
            "pct_without_HR": round(100.0 * len(no_hr) / len(events), 1) if events else 0.0,
            "without_HR_but_with_SpO2": sum(
                1 for eid in no_hr if profiles[eid].get("SpO2")),
            "without_HR_but_with_wave": sum(
                1 for eid in no_hr if profiles[eid].get("onda")),
            "without_HR_and_without_any_monitor": sum(
                1 for eid in no_hr
                if not profiles[eid].get("SpO2") and not profiles[eid].get("onda")),
            "examples": no_hr[:20],
        }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "vitaldb_review.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items()
                      if k not in ("death_or_transfer_mapping",)}, ensure_ascii=False,
                     indent=2, default=str))


if __name__ == "__main__":
    main()
