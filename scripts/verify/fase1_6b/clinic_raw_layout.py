#!/usr/bin/env python3
"""
scripts/verify/fase1_6b/clinic_raw_layout.py
============================================
Fase 1.6b — **punto 3**: layout de los datos crudos de Clínic y **explicación
de la inconsistencia índice ↔ ficheros**.

Comprueba:

1. cuántos ``.vital`` hay y cómo se reparten por directorio (incluido
   ``dataset_clinic``, que el escáner excluye);
2. si los ficheros de los boxes están **duplicados** en
   ``dataset_clinic/clinic_vitals/<box>/...``;
3. si el MISMO fichero leído desde las dos rutas tiene las mismas pistas
   (la revisión de la Fase 1.5 leía por nombre global y podía leer la copia).

Salida: ``reports/fase1_6b/clinic_raw_layout.json``

Uso:
    python scripts/verify/fase1_6b/clinic_raw_layout.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"

TRACKS = ("Intellivue/AWP_WAV", "Intellivue/TV_EXP", "Intellivue/FIO2",
          "Intellivue/ECG_HR", "Intellivue/PLETH_SAT_O2")


def _track_counts(path: Path) -> dict:
    import vitaldb

    try:
        vf = vitaldb.VitalFile(str(path), track_names=list(TRACKS))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    trks = getattr(vf, "trks", None) or {}
    return {
        "dtstart": float(vf.dtstart), "dtend": float(vf.dtend),
        "tracks": {k: (len(v.recs) if v and v.recs else 0) for k, v in trks.items()},
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--samples", type=int, default=3,
                   help="Ficheros duplicados que se comparan pista a pista")
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6b"))
    args = p.parse_args()

    config = load_config(args.config)
    raw = config_path(config, "paths", "clinic_raw_dir")
    files = list(Path(raw).rglob("*.vital"))

    by_top: Counter = Counter()
    for f in files:
        rel = f.relative_to(raw)
        by_top[rel.parts[0]] += 1

    in_ds = [f for f in files if "dataset_clinic" in f.parts]
    in_box = [f for f in files if "dataset_clinic" not in f.parts]
    names_box = {f.name for f in in_box}
    names_ds = {f.name for f in in_ds}

    by_name: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        by_name[f.name].append(f)
    duplicates = {n: [str(x) for x in ps] for n, ps in by_name.items() if len(ps) > 1}

    compared = []
    for name in sorted(duplicates)[: args.samples]:
        paths = sorted(Path(x) for x in duplicates[name])
        rec = {"name": name, "paths": [str(x) for x in paths],
               "reads": [_track_counts(x) for x in paths]}
        rec["tracks_differ"] = rec["reads"][0].get("tracks") != rec["reads"][-1].get("tracks")
        compared.append(rec)

    out = {
        "raw_dir": str(raw),
        "n_vital": len(files),
        "by_top_dir": dict(by_top),
        "n_dataset_clinic": len(in_ds),
        "n_boxes": len(in_box),
        "n_names_common": len(names_box & names_ds),
        "n_names_only_in_boxes": len(names_box - names_ds),
        "n_names_only_in_dataset_clinic": len(names_ds - names_box),
        "n_duplicated_names": len(duplicates),
        "duplicated_sample": dict(sorted(duplicates.items())[:5]),
        "same_file_two_paths": compared,
        "explanation": (
            "Los .vital existen DOS veces: en <box>/... y en "
            "dataset_clinic/clinic_vitals/<box>/.... Un índice por NOMBRE global "
            "se queda con una sola copia (la última del recorrido, que es la de "
            "dataset_clinic) y puede leer un fichero distinto del que generó el "
            "evento; si esa copia no tiene las pistas de ventilador, el evento "
            "parece un artefacto. dataset_clinic contiene además "
            "physionet_vitals/ (otro conjunto) y NO es una caja de UCI, por lo "
            "que el escáner lo excluye."
        ),
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "clinic_raw_layout.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items()
                      if k not in ("duplicated_sample", "same_file_two_paths")},
                     ensure_ascii=False, indent=2))
    for c in compared:
        print(f"\n{c['name']} (difieren: {c['tracks_differ']})")
        for path, read in zip(c["paths"], c["reads"]):
            print(f"  {path}\n    {read}")


if __name__ == "__main__":
    main()
