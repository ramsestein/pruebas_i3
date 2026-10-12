"""Fase 1.6e, punto 2b/D-g — conjunto de ficheros que escanea el builder.

Reutiliza ``scan_source_files`` (la funcion real, no una reimplementacion)
para verificar que:

1. cada box ve exactamente sus ficheros (sin copias de ``dataset_clinic`` ni
   los ``.vital`` de otras procedencias que no casan con el patron);
2. los homonimos de un mismo box quedan reducidos a uno (D-g) y se sabe
   cuantos se descartaron y de donde salian;
3. ningun box queda con dos ficheros de nombre repetido.

Uso:
    python scripts/verify/fase1_6e/scan_set.py --cohort clinic
    python scripts/verify/fase1_6e/scan_set.py --cohort clinic --json out.json
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.create_dataset.build_signal_cases import (  # noqa: E402
    SPECS,
    scan_source_files,
)
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
RAW_KEYS = {"clinic": "clinic_raw_dir", "vitaldb": "vitaldb_raw_dir"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cohort", choices=sorted(SPECS), required=True)
    p.add_argument("--raw-dir", default=None,
                   help="directorio de origen (por defecto, el de la config)")
    p.add_argument("--json", default=None)
    p.add_argument("--md", default=None)
    p.add_argument("--config", default=str(CONFIG))
    args = p.parse_args(argv)

    config = load_config(args.config)
    raw = args.raw_dir or config_path(config, "paths", RAW_KEYS[args.cohort])
    spec = SPECS[args.cohort]
    boxes = scan_source_files(raw, spec)

    out: dict = {"cohort": args.cohort, "raw_dir": str(raw), "boxes": {}}
    tot_files = tot_dropped = 0
    print(f"# {args.cohort}: {raw}")
    print(f"{'box':<10} {'ficheros':>9} {'tokens':>7} {'homonimos descartados':>22}")
    for box, files in sorted(boxes.items()):
        # Cuantos homonimos habria sin deduplicar: se recuentan los nombres en
        # el directorio de origen del box.
        names = [f.path.name for f in files]
        dup = {k: v for k, v in collections.Counter(names).items() if v > 1}
        tokens = sorted({f.token for f in files})
        src_dirs = sorted({str(f.path.parent) for f in files})
        out["boxes"][box] = {"n_files": len(files), "n_tokens": len(tokens),
                             "tokens": tokens, "names_duplicados": dup,
                             "dirs": src_dirs}
        tot_files += len(files)
        print(f"{box:<10} {len(files):>9} {len(tokens):>7} {len(dup):>22}")
    print(f"TOTAL: {tot_files} ficheros, {len(boxes)} boxes")

    # Comprobacion directa de D-g sobre el directorio crudo: cuantos nombres
    # repetidos habia por box ANTES de deduplicar y desde cuantas carpetas,
    # con el detalle de que par de carpetas se repite (p.ej. 14 <-> 14_2).
    raw_path = Path(raw)
    if raw_path.is_dir():
        for top in sorted(p for p in raw_path.iterdir() if p.is_dir()):
            if top.name in {"dataset_clinic"}:
                continue
            por_nombre: dict[str, list[Path]] = collections.defaultdict(list)
            for f in top.rglob("*.vital"):
                if spec.filename_regex.search(f.name):
                    por_nombre[f.name].append(f)
            dup = {k: v for k, v in por_nombre.items() if len(v) > 1}
            if not dup:
                continue
            # Par de ubicaciones (relativas al box) de cada homonimo.
            pares = collections.Counter(
                tuple(sorted(str(p.parent.relative_to(top)) for p in v))
                for v in dup.values())
            n_copias = collections.Counter(len(v) for v in dup.values())
            n_extra = sum(len(v) - 1 for v in dup.values())
            print(f"\nD-g: {top.name} tiene {len(dup)} nombres repetidos "
                  f"({n_extra} ficheros de mas); copias por nombre: "
                  f"{dict(n_copias)}")
            for par, n in pares.most_common(6):
                print(f"    {n:>5} nombres en {par}")
            out.setdefault("homonimos_crudos", {})[top.name] = {
                "n_nombres_repetidos": len(dup), "n_ficheros_extra": n_extra,
                "copias_por_nombre": {str(k): v for k, v in n_copias.items()},
                "ubicaciones": [{"carpetas": list(par), "n": n}
                                for par, n in pares.most_common()],
            }

    if args.json:
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\nJSON: {args.json}")
    if args.md:
        md = [f"# Fase 1.6e, punto 2b/D-g — conjunto de ficheros de `{args.cohort}`",
              "",
              f"Origen: `{raw}` (la funcion real `scan_source_files`).",
              "",
              "## Ficheros que ve el builder", "",
              "| box | ficheros | tokens | homonimos descartados |", "|---|---|---|---|"]
        for box, info in sorted(out["boxes"].items()):
            md.append(f"| {box} | {info['n_files']} | {info['n_tokens']} | "
                      f"{len(info['names_duplicados'])} |")
        md += ["", f"**TOTAL: {tot_files} ficheros, {len(boxes)} boxes.**", ""]
        if "homonimos_crudos" in out:
            md += ["## Homonimos en el crudo (D-g)", "",
                   "`dataset_clinic` queda excluido por `exclude_dirs`; los "
                   "ficheros de `physionet_vitals` no casan con el patron de "
                   "nombres de la cohorte.", ""]
            for box, h in out["homonimos_crudos"].items():
                md.append(f"### `{box}`")
                md.append("")
                md.append(f"- Nombres repetidos: **{h['n_nombres_repetidos']}** "
                          f"({h['n_ficheros_extra']} ficheros de mas); copias "
                          f"por nombre: {h['copias_por_nombre']}")
                md.append("")
                md.append("| nombres | carpetas de las copias |")
                md.append("|---|---|")
                for u in h["ubicaciones"][:8]:
                    md.append(f"| {u['n']} | `{'` ↔ `'.join(u['carpetas'])}` |")
                md.append("")
                md.append("Verificacion de contenido en "
                          "`reports/fase1_6e/box14_dedupe.md` (SHA-256).")
                md.append("")
        Path(args.md).write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"MD: {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
