#!/usr/bin/env python3
"""
scripts/verify/fase1/plot_deaths.py
===================================
Genera un PNG por CADA muerte detectada por señales en Clínic/VitalDB
(FC, SpO2, MAP y pulsatilidad ABP/PPG), para revisión clínica.

Uso:
    python scripts/verify/fase1/plot_deaths.py --cohort clinic
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _xy(pairs):
    if not pairs:
        return [], []
    return [p[0] for p in pairs], [p[1] for p in pairs]


def plot_death(d: dict, out_path: Path) -> None:
    fig, axes = plt.subplots(4, 1, figsize=(10, 8), sharex=True)
    ser = d.get("series", {})

    x, y = _xy(ser.get("HR"))
    axes[0].plot(x, y, color="#C44E52", lw=1.2, label="FC (bpm)")
    x, y = _xy(ser.get("SpO2"))
    if x:
        ax2 = axes[0].twinx()
        ax2.plot(x, y, color="#4C72B0", lw=1.0, alpha=0.7, label="SpO2 (%)")
        ax2.set_ylabel("SpO2 (%)")
    axes[0].set_ylabel("FC (bpm)")
    axes[0].legend(loc="upper right", fontsize=7)

    x, y = _xy(ser.get("MAP"))
    axes[1].plot(x, y, color="#55A868", lw=1.1, label="MAP (mmHg)")
    axes[1].axhline(40, color="grey", ls="--", lw=0.8)
    axes[1].set_ylabel("MAP")
    axes[1].legend(loc="upper right", fontsize=7)

    for key, color in (("ABP_amp", "#8172B2"), ("PPG_amp", "#CCB974")):
        x, y = _xy(ser.get(key))
        if x:
            axes[2].plot(x, y, color=color, lw=1.0, label=key)
    axes[2].set_ylabel("pulsatilidad")
    axes[2].legend(loc="upper right", fontsize=7)

    end = d.get("region_end_h")
    for a in d.get("attempts", []):
        axes[3].barh(0, max(a["vent_end_h"] - a["vent_start_h"], 0.01),
                     left=a["vent_start_h"], height=0.5, color="#4C72B0", alpha=0.8)
    axes[3].set_yticks([])
    axes[3].set_xlabel("horas desde t0")

    death_t = d.get("death_time_h")
    for ax in axes:
        if death_t is not None:
            ax.axvline(death_t, color="black", lw=1.2)
        if end is not None:
            ax.axvline(end, color="grey", lw=0.8, ls=":")
        ax.set_xlim(left=0)

    d5 = d.get("d5", {}).get("48h", {})
    fig.suptitle(
        f"{d['event_id']} | muerte a {death_t:.3f} h ({d.get('reason')}) | "
        f"ventilado={d.get('died_ventilated')} | 48h={d5.get('censor_cause')}",
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cohort", required=True)
    p.add_argument("--out-dir", default=None)
    args = p.parse_args()

    files = glob.glob(str(ROOT / "datasets" / args.cohort / "cases_*" / "signal_deaths.json"))
    if not files:
        print(f"[plot_deaths] sin signal_deaths.json para {args.cohort}")
        return
    out_dir = Path(args.out_dir) if args.out_dir else (
        ROOT / "reports" / "fase1" / "muertes_senal"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    n = 0
    for f in files:
        data = json.load(open(f, encoding="utf-8"))
        for d in data.get("deaths", []):
            plot_death(d, out_dir / f"{d['event_id']}.png")
            n += 1
    print(f"[plot_deaths] {n} PNG en {out_dir}")


if __name__ == "__main__":
    main()
