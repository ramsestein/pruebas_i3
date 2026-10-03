#!/usr/bin/env python3
"""
scripts/verify/fase1/timelines.py
=================================
Genera las líneas temporales en PNG para la revisión clínica del informe de
Fase 1 (10 eventos al azar por cohorte con semilla fija + todos los eventos
>= 7 días que terminan en "extubación" en Clínic/VitalDB).

Uso:
    python scripts/verify/fase1/timelines.py --cohort clinic
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.create_dataset.build_mimic_cases import output_root as mimic_out  # noqa: E402
from src.create_dataset.build_signal_cases import output_root as signal_out  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
SEED = 20261002
LONG_EVENT_H = 7 * 24


def plot_event(ev: dict, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 2.6))
    for a in ev["attempts"]:
        start, end = a["vent_start_h"], a["vent_end_h"]
        ax.barh(0, max(end - start, 0.02), left=start, height=0.5,
                color="#4C72B0", alpha=0.85)
        if a.get("reintubation_h") is not None:
            ax.plot([a["reintubation_h"]], [0], marker="|", color="#C44E52", ms=14)
    for win, color in (("48h", "#DD8452"), ("72h", "#55A868")):
        lab = ev.get("labels", {}).get(win)
        if not lab:
            continue
        y = 0.35 if win == "48h" else -0.35
        if lab.get("extubation_time_h") is not None:
            ax.plot([lab["extubation_time_h"]], [y], marker="^", color=color,
                    ms=9, label=f"extubación {win}")
        elif lab.get("censor_time_h") is not None:
            ax.plot([lab["censor_time_h"]], [y], marker="x", color=color,
                    ms=9, label=f"censura {win} ({lab.get('censor_cause')})")
    ax.set_yticks([])
    ax.set_xlabel("horas desde t0")
    ax.set_xlim(left=0)
    ax.set_title(f"{ev['event_id']} | intentos={ev['n_attempts']} | "
                 f"end_reason={ev.get('end_reason')}", fontsize=9)
    ax.legend(fontsize=7, loc="upper right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def load_index(cohort: str, config: dict, explicit: str | None) -> dict | None:
    if explicit:
        path = Path(explicit)
    elif cohort == "mimic":
        path = Path(mimic_out(config)[0]) / "mimic_cases_index.json"
    else:
        path = Path(signal_out(config, cohort)[0]) / f"{cohort}_cases_index.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cohort", required=True)
    p.add_argument("--index")
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--n-random", type=int, default=10)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--long-only", action="store_true",
                   help="Solo >= 7 días que terminan en extubación")
    args = p.parse_args()

    config = load_config(args.config)
    idx = load_index(args.cohort, config, args.index)
    if idx is None:
        print(f"[timelines] índice no disponible para {args.cohort}")
        return

    out_dir = Path(args.out_dir) if args.out_dir else (
        ROOT / "reports" / "fase1" / "figs" / args.cohort
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    events = idx["events"]
    selected: list[dict] = []
    if not args.long_only:
        rng = random.Random(SEED)
        selected += rng.sample(events, min(args.n_random, len(events)))
    long_events = (
        [
            e for e in events
            if e["duration_seconds"] >= LONG_EVENT_H * 3600
            and e.get("end_reason") == "extubation_observed"
        ]
        if args.cohort in ("clinic", "vitaldb")  # la lista >=7 d es solo para señal
        else []
    )
    selected += long_events

    seen: set[str] = set()
    n = 0
    for ev in selected:
        if ev["event_id"] in seen:
            continue
        seen.add(ev["event_id"])
        plot_event(ev, out_dir / f"{ev['event_id']}.png")
        n += 1
    print(f"[timelines] {n} PNG escritos en {out_dir}")


if __name__ == "__main__":
    main()
