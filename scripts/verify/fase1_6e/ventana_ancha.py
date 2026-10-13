"""Ventana ancha alrededor del corte de una pareja (eje de tiempo CONTINUO).

Es la vista que hace falta para juzgar un corte: en el paquete de adjudicación
las dos mitades iban pegadas y con el hueco oculto, así que **no se veía el
corte**, que es justo lo que decide. Aquí se pinta la grabación entera alrededor
del corte, con la zona sin región sombreada.

Uso:
    # corte de la candidata de `box9` con hueco ~0 h
    python scripts/verify/fase1_6e/ventana_ancha.py --box box9 --gap 0.0
    # corte de la candidata de `box11` con hueco ~4,6 h (P01 del paquete)
    python scripts/verify/fase1_6e/ventana_ancha.py --box box11 --gap 4.58

Salida: `reports/fase1_6e/figs/ventana_ancha_<box>.png` (no hay datos de
paciente: solo el número de box, horas epoch y las señales).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.common.vital_signals import read_track_series  # noqa: E402
from src.create_dataset.build_signal_cases import (  # noqa: E402
    SPECS, scan_source_files,
)
from src.stage0.io.versioning import load_config  # noqa: E402

REPORT = ROOT / "reports" / "fase1_6e"
CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
MON = ("Intellivue/ECG_HR", "Intellivue/PLETH_SAT_O2")
VEN = ("Intellivue/TV_EXP", "Intellivue/VENT_RR", "Intellivue/PEEP_CMH2O",
       "Intellivue/FIO2")


def _fmt(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%m-%d %H:%M")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--box", required=True)
    p.add_argument("--gap", type=float, required=True,
                   help="hueco de la pareja a localizar (h)")
    p.add_argument("--cascada", default=str(REPORT / "cascada_clinic.json"))
    p.add_argument("--cohort", default="clinic", choices=sorted(SPECS))
    p.add_argument("--margen-h", type=float, default=12.0)
    p.add_argument("--config", default=str(CONFIG))
    args = p.parse_args()

    casc = json.loads(Path(args.cascada).read_text(encoding="utf-8"))
    cands = [c for c in casc.get("candidatas") or []
             if c["box"] == args.box
             and abs(float(c["gap_h"]) - args.gap) < 1.5]
    if not cands:
        print(f"[error] no hay candidata en {args.box} con hueco ~{args.gap} h")
        sys.exit(1)
    c = min(cands, key=lambda x: abs(float(x["gap_h"]) - args.gap))
    a, b = float(c["prev_end_epoch"]), float(c["next_start_epoch"])
    print(f"{args.box}: hueco={float(c['gap_h']):.2f} h  "
          f"corte={_fmt(a)} -> {_fmt(b)}")

    config = load_config(args.config)
    boxes = scan_source_files(
        config_path(config, "paths", f"{args.cohort}_raw_dir"), SPECS[args.cohort])
    files = sorted(boxes[args.box], key=lambda f: f.dt_unix)
    sel = [f for f in files
           if a - (args.margen_h + 6.0) * 3600 <= f.dt_unix
           <= b + args.margen_h * 3600]
    print(f"ficheros leidos ({len(sel)}):")
    for f in sel:
        print(f"   {f.path.name}  dt={_fmt(f.dt_unix)}")
    series = read_track_series([str(f.path) for f in sel],
                              list(VEN) + list(MON))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 8), sharex=True)
    for nombre, color in zip(MON, ("tab:red", "tab:blue")):
        t, v = series.get(nombre, (None, None))
        if t is not None and len(t):
            ax1.plot(t / 3600.0, v, ".", ms=1, label=nombre.split("/")[-1],
                     color=color)
    for nombre in VEN:
        t, v = series.get(nombre, (None, None))
        if t is not None and len(t):
            ax2.plot(t / 3600.0, v, ".", ms=1, label=nombre.split("/")[-1])
    for ax in (ax1, ax2):
        ax.axvline(a / 3600.0, color="k", ls="--", lw=1.2)
        if b > a:
            ax.axvspan(a / 3600.0, b / 3600.0, color="0.85", zorder=0)
            ax.axvline(b / 3600.0, color="k", ls="--", lw=1.2)
        ax.grid(alpha=0.25)
        if ax.get_legend_handles_labels()[0]:
            ax.legend(loc="upper right", fontsize=8, ncol=4)
    ax1.set_ylabel("monitor")
    ax2.set_ylabel("ventilador")
    ax2.set_xlabel("hora absoluta (epoch/3600)")
    fig.suptitle(f"{args.box}  hueco={float(c['gap_h']):.2f} h  "
                 f"({_fmt(a)} -> {_fmt(b)})  zona gris = hueco sin region")
    fig.tight_layout()
    out = REPORT / "figs" / f"ventana_ancha_{args.box}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=105)
    print(f"PNG: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
