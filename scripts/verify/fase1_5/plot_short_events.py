#!/usr/bin/env python3
"""
scripts/verify/fase1_5/plot_short_events.py
===========================================
PNG y clasificación de los eventos de **menos de 1 h** de Clínic (Fase 1.5,
punto 3).

Para cada evento < 1 h dibuja (ventilador, onda de presión de vía aérea, FC,
SpO2) y lo clasifica como **ventilación invasiva plausible** o **artefacto**
según la señal (amplitud y periodicidad de la presión de vía aérea y presencia
de volumen tidal), NUNCA según la duración.

Salida:
  reports/fase1_5/figs/clinic_short/<event_id>.png
  reports/fase1_5/clinic_short_events.json

Uso:
    python scripts/verify/fase1_5/plot_short_events.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import vitaldb  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.common.short_events import classify_short_event as _classify  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
AWP_TRACK = "Intellivue/AWP_WAV"
HR_TRACKS = ("Intellivue/ECG_HR", "Intellivue/HR", "Intellivue/PLETH_HR")
SPO2_TRACK = "Intellivue/PLETH_SAT_O2"
TV_TRACKS = ("Intellivue/TV_EXP", "Intellivue/TV")


# ── Lectura de señales ───────────────────────────────────────────────────────

def _series_from_track(trk) -> tuple[np.ndarray, np.ndarray]:
    """Convierte una pista (numérica u onda) en (tiempos_s, valores)."""
    if not trk or not trk.recs:
        return np.array([]), np.array([])
    ts: list[float] = []
    vs: list[float] = []
    for r in trk.recs:
        dt = float(r["dt"])
        val = r["val"]
        if isinstance(val, (list, tuple, np.ndarray)):
            arr = np.asarray(val, dtype=np.float64).ravel()
            if arr.size == 0:
                continue
            srate = float(trk.srate or 0.0)
            if srate <= 0:
                srate = 1.0
            t = dt + np.arange(arr.size) / srate
            ts.extend(t.tolist())
            vs.extend(arr.tolist())
        else:
            ts.append(dt)
            vs.append(float(val))
    return np.asarray(ts), np.asarray(vs)


def read_event_signals(paths: list[Path]) -> dict:
    tracks = [AWP_TRACK, *HR_TRACKS, SPO2_TRACK, *TV_TRACKS]
    out = {"AWP": (np.array([]), np.array([])), "HR": (np.array([]), np.array([])),
           "SpO2": (np.array([]), np.array([])), "TV": (np.array([]), np.array([]))}
    for p in paths:
        if not p.exists():
            continue
        try:
            vf = vitaldb.VitalFile(str(p), track_names=tracks)
        except Exception:  # noqa: BLE001
            continue
        if vf is None:
            continue
        trks = getattr(vf, "trks", None) or {}
        for key, names in (("AWP", (AWP_TRACK,)), ("HR", HR_TRACKS),
                           ("SpO2", (SPO2_TRACK,)), ("TV", TV_TRACKS)):
            for n in names:
                trk = trks.get(n)
                if trk and trk.recs:
                    t, v = _series_from_track(trk)
                    if t.size:
                        t0, v0 = out[key]
                        out[key] = (np.concatenate([t0, t]), np.concatenate([v0, v]))
                    break
    for key in out:
        t, v = out[key]
        if t.size:
            order = np.argsort(t, kind="stable")
            out[key] = (t[order], v[order])
    return out


# ── Clasificación por señal ──────────────────────────────────────────────────

def classify_short_event(sig: dict) -> dict:
    """Clasifica un evento corto como ventilación invasiva o artefacto.

    Delega en ``src/common/short_events.py`` (criterios SOLO de señal, nunca
    duración).
    """
    return _classify(sig["AWP"][1], sig["TV"][1])


# ── Plot ────────────────────────────────────────────────────────────────────

def plot_event(ev: dict, sig: dict, out_path: Path) -> None:
    fig, axes = plt.subplots(4, 1, figsize=(10, 7), sharex=True)
    panels = [("AWP", "P. vía aérea (cmH2O)"), ("HR", "FC (bpm)"),
              ("SpO2", "SpO2 (%)"), ("TV", "TV (mL)")]
    for ax, (key, label) in zip(axes, panels):
        t, v = sig[key]
        if t.size:
            ax.plot(t, v, lw=0.7, color="#2F6DB5")
        ax.set_ylabel(label, fontsize=8)
        ax.grid(alpha=0.3)
    axes[-1].set_xlabel("tiempo (s, epoch)")
    axes[0].set_title(f"{ev['event_id']} | dur={ev['duration_seconds']/60:.1f} min",
                      fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


# ── Main ────────────────────────────────────────────────────────────────────

def find_clinic_index(config: dict) -> Path | None:
    base = config_path(config, "paths", "clinic_cases_out", required=False)
    if base is not None and Path(base).exists():
        for p in sorted(Path(base).glob("*cases_index.json")):
            return p
    candidates = sorted((ROOT / "datasets" / "clinic").glob("cases_*/*cases_index.json"))
    return candidates[-1] if candidates else None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--index", default=None)
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_5"))
    args = p.parse_args()

    config = load_config(args.config)
    index_path = Path(args.index) if args.index else find_clinic_index(config)
    if index_path is None or not index_path.exists():
        print("[short] índice de Clínic no disponible")
        return
    with open(index_path, encoding="utf-8") as fh:
        idx = json.load(fh)
    raw_dir = config_path(config, "paths", "clinic_raw_dir")

    out_dir = Path(args.out_dir)
    figs = out_dir / "figs" / "clinic_short"
    figs.mkdir(parents=True, exist_ok=True)

    short = [e for e in idx["events"] if e["duration_seconds"] < 3600]
    results: list[dict] = []
    for ev in short:
        paths = [raw_dir / ev["box"] / name for name in ev["source_files"]]
        sig = read_event_signals(paths)
        cls = classify_short_event(sig)
        plot_event(ev, sig, figs / f"{ev['event_id']}.png")
        results.append({
            "event_id": ev["event_id"],
            "box": ev["box"],
            "duration_min": round(ev["duration_seconds"] / 60.0, 2),
            "n_attempts": ev["n_attempts"],
            "end_reason": ev["end_reason"],
            **cls,
        })

    summary = {
        "index": str(index_path),
        "n_short_events": len(results),
        "n_plausible": sum(1 for r in results if r["classification"].endswith("plausible")),
        "n_artifact": sum(1 for r in results if r["classification"] == "artefacto"),
        "events": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "clinic_short_events.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "events"},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
