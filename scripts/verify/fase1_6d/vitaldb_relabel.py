#!/usr/bin/env python3
"""
scripts/verify/fase1_6d/vitaldb_relabel.py
==========================================
Fase 1.6d — **punto 3b/3c/3d**: auditoría de los eventos de VitalDB que pasaron
de "éxito" a ``end_of_record`` entre la 1.6b y la 1.6c (75 → 40), explicación de
los eventos con perfil "(ninguna)" y resolución de los eventos sin
``source_files``.

Modos
-----
- ``--report`` (por defecto): compara el índice ANTIGUO (por defecto el v0.2.0,
  éxito 75) con el NUEVO (v0.4.0/v0.5.0) y escribe la explicación **caso a caso**
  (``relabel_vitaldb.json`` / ``.md``). No lee señales: es rápido.
- ``--png N``: además genera ``N`` PNG de revisión de los eventos que cambiaron
  (HR/SpO2/MAP + tramos ventilados), leyendo los ``source_files``.

Salida en ``reports/fase1_6d/``.

Uso:
    python scripts/verify/fase1_6d/vitaldb_relabel.py --report
    python scripts/verify/fase1_6d/vitaldb_relabel.py --png 10 --png-events <id,id>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from src.common.paths import config_path  # noqa: E402
from src.common.vital_signals import read_track_series  # noqa: E402
from src.create_dataset.build_signal_cases import SPECS, scan_source_files  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
WINDOW = "48h"
# Variables del perfil de disponibilidad (una variable "disponible" si su
# cobertura > 50 %).
PROFILE_VARS = ("HR", "SpO2", "MAP", "RR", "FiO2", "PEEP")
PLOT_TRACKS = ("Intellivue/ECG_HR", "Intellivue/PLETH_HR", "Intellivue/HR",
               "Intellivue/PLETH_SAT_O2", "Intellivue/ABP_MEAN",
               "Intellivue/NIBP_MEAN", "Intellivue/ART_MEAN")


def _indices(cohort: str) -> list[Path]:
    return sorted((ROOT / "datasets" / cohort).glob(
        f"cases_v*/{cohort}_cases_index.json"))


def _label(index: dict, event_id: str) -> dict | None:
    for e in index["events"]:
        if e["event_id"] == event_id:
            return e
    return None


def _is_success(ev: dict) -> bool:
    lab = (ev.get("labels") or {}).get(WINDOW) or {}
    return lab.get("event_type") == "successful_extubation"


def _profile(ev: dict) -> str:
    cov = ev.get("coverage") or {}
    have = [v for v in PROFILE_VARS if float(cov.get(v, 0.0)) > 0.5]
    return "+".join(have) if have else "(ninguna)"


def build_report(old: dict, new: dict) -> dict:
    changed = []
    only_old = []
    for e in old["events"]:
        ev_id = e["event_id"]
        n = _label(new, ev_id)
        if n is None:
            only_old.append({"event_id": ev_id})
            continue
        if _is_success(e) and not _is_success(n):
            changed.append({
                "event_id": ev_id,
                "old_event_type": (e["labels"][WINDOW]["event_type"]),
                "new_event_type": (n["labels"][WINDOW]["event_type"]),
                "new_end_reason": n.get("end_reason"),
                "new_extubation_rule": n.get("extubation_rule"),
                "n_attempts": n.get("n_attempts"),
                "monitor_tail_h": n.get("monitor_tail_h"),
                "new_censor_cause": (n["labels"][WINDOW].get("censor_cause")),
                "profile": _profile(n),
                "has_missing_files": n.get("has_missing_files"),
                "level": n.get("level"),
                "n_source_files": len(n.get("source_files") or []),
            })
    profiles = Counter(_profile(e) for e in new["events"])
    ninguna = [e["event_id"] for e in new["events"] if _profile(e) == "(ninguna)"]
    no_files = [e["event_id"] for e in new["events"]
                if not (e.get("source_files") or [])]
    by_rule = Counter(c["new_extubation_rule"] for c in changed)
    return {
        "old_index": old.get("_path"),
        "new_index": new.get("_path"),
        "n_old": len(old["events"]),
        "n_new": len(new["events"]),
        "n_changed_success_to_censored": len(changed),
        "n_only_old": len(only_old),
        "changed": changed,
        "changed_by_extubation_rule": dict(by_rule),
        "profiles": dict(profiles.most_common()),
        "ninguna_events": ninguna,
        "n_ninguna": len(ninguna),
        "no_source_files": no_files,
        "n_no_source_files": len(no_files),
    }


def _to_md(rep: dict) -> list[str]:
    lines = ["# VitalDB: reetiquetado 75 → 40 (Fase 1.6d, punto 3)", "",
             f"- Índice antiguo: `{rep['old_index']}` ({rep['n_old']} eventos)",
             f"- Índice nuevo: `{rep['new_index']}` ({rep['n_new']} eventos)",
             f"- Eventos que pasaron de **éxito a censurado**: "
             f"{rep['n_changed_success_to_censored']}",
             f"- Causa (extubation_rule): {rep['changed_by_extubation_rule']}", "",
             "## Caso a caso", "",
             "| Evento | Etiqueta antigua | Etiqueta nueva | Regla | Cola de "
             "monitor (h) | Perfil | Ficheros |",
             "|---|---|---|---|---|---|---|"]
    for c in rep["changed"]:
        tail = c["monitor_tail_h"]
        lines.append(
            f"| {c['event_id']} | {c['old_event_type']} | {c['new_event_type']} | "
            f"{c['new_extubation_rule']} | "
            f"{'—' if tail is None else f'{tail:.2f}'} | {c['profile']} | "
            f"{c['n_source_files']} |")
    lines += ["", "## Perfiles de disponibilidad", "",
              f"{rep['profiles']}", "",
              f"## Eventos con perfil «(ninguna)» ({rep['n_ninguna']})", "",
              ", ".join(rep["ninguna_events"]) or "—", "",
              f"## Eventos sin `source_files` ({rep['n_no_source_files']})", "",
              ", ".join(rep["no_source_files"]) or "—", ""]
    return lines


def plot_event(event: dict, paths: list[str], out_png: Path) -> bool:
    """PNG de revisión: HR/SpO2/MAP + tramos ventilados (horas desde t0)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    series = read_track_series(paths, PLOT_TRACKS, t0_unix=float(event["t0_unix"]))
    t0 = float(event["t0_unix"])
    fig, ax = plt.subplots(figsize=(11, 6))
    plotted = False
    for canon, tracks in (("HR", ("Intellivue/ECG_HR", "Intellivue/PLETH_HR",
                                  "Intellivue/HR")),
                          ("SpO2", ("Intellivue/PLETH_SAT_O2",)),
                          ("MAP", ("Intellivue/ABP_MEAN",
                                   "Intellivue/NIBP_MEAN",
                                   "Intellivue/ART_MEAN"))):
        for trk in tracks:
            t, v = series.get(trk, (np.asarray([]), np.asarray([])))
            if v.size:
                ax.plot(t / 3600.0, v, ".", ms=1, label=f"{canon} ({trk})")
                plotted = True
                break
    for a in event["attempts"]:
        ax.axvspan(a["vent_start_h"], a["vent_end_h"], color="tab:red",
                   alpha=0.15)
    end = event.get("obs_end_h")
    if end is not None:
        ax.axvline(end, color="k", ls="--", lw=1, label="fin observación")
    ax.set_xlabel("horas desde t0")
    ax.set_ylabel("valor")
    ax.set_title(f"{event['event_id']} — {event.get('end_reason')} "
                 f"({event.get('extubation_rule')})")
    ax.legend(loc="upper right", fontsize=6, ncol=2)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=110)
    plt.close(fig)
    return plotted


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--cohort", default="vitaldb")
    p.add_argument("--old-index", default=None)
    p.add_argument("--new-index", default=None)
    p.add_argument("--report", action="store_true", default=True)
    p.add_argument("--png", type=int, default=0)
    p.add_argument("--png-events", default=None,
                   help="Lista separada por comas de event_id para los PNG")
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6d"))
    args = p.parse_args()

    cands = _indices(args.cohort)
    if not cands:
        raise SystemExit(f"no hay índices de {args.cohort}")
    old_path = Path(args.old_index) if args.old_index else cands[0]
    new_path = Path(args.new_index) if args.new_index else cands[-1]
    old = json.loads(old_path.read_text(encoding="utf-8"))
    new = json.loads(new_path.read_text(encoding="utf-8"))
    old["_path"] = str(old_path.relative_to(ROOT))
    new["_path"] = str(new_path.relative_to(ROOT))

    rep = build_report(old, new)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"relabel_{args.cohort}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"relabel_{args.cohort}.md").write_text(
        "\n".join(_to_md(rep)) + "\n", encoding="utf-8")
    print(f"cambiados exito->censura: {rep['n_changed_success_to_censored']}")
    print(f"perfiles: {rep['profiles']}")
    print(f"sin source_files: {rep['n_no_source_files']}")

    if args.png:
        config = load_config(CONFIG)
        boxes = scan_source_files(
            config_path(config, "paths", f"{args.cohort}_raw_dir"),
            SPECS[args.cohort])
        by_box = {b: {sf.path.name: sf.path for sf in files}
                  for b, files in boxes.items()}
        if args.png_events:
            ids = [x.strip() for x in args.png_events.split(",") if x.strip()]
        else:
            ids = [c["event_id"] for c in rep["changed"][: args.png]]
        png_dir = out_dir / f"figs_relabel_{args.cohort}"
        n_ok = 0
        for ev_id in ids:
            ev = _label(new, ev_id)
            if ev is None:
                continue
            paths = [str(by_box.get(ev["box"], {}).get(n))
                     for n in (ev.get("source_files") or [])]
            paths = [x for x in paths if x and x != "None"]
            if not paths:
                continue
            try:
                if plot_event(ev, paths, png_dir / f"{ev_id}.png"):
                    n_ok += 1
                    print(f"  PNG {ev_id}")
            except Exception as exc:  # noqa: BLE001
                print(f"  [error] {ev_id}: {exc}")
        print(f"PNGs generados: {n_ok}")


if __name__ == "__main__":
    main()
