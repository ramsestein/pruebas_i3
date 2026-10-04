#!/usr/bin/env python3
"""
scripts/verify/fase1_5/scan_vitaldb.py
======================================
Recorre **TODOS** los ficheros ``.vital`` de origen de VitalDB (no una muestra)
y reporta cuántos no se pueden leer y con qué error (Fase 1.5, punto 2).

Además guarda una **caché de sondas** (``--cache``) con el resumen de cada
fichero, de modo que el rebuild posterior no tenga que volver a parsearlos.

Un fichero ilegible es "sin dato" (no "sin señal"): sus horas no crean huecos de
ventilador (D1) ni cortes de paciente (D2). Los que siguen siendo ilegibles con
la versión actual de ``vitaldb`` se listan como **corruptos**.

Salida:
  reports/fase1_5/vitaldb_scan.json      (resumen + lista de ilegibles)
  reports/fase1_5/vitaldb_probe_<tag>.json  (caché de sondas, si --cache)

Uso:
    # 1) escaneo completo con la versión fijada + caché
    python scripts/verify/fase1_5/scan_vitaldb.py --workers 16 \
        --cache reports/fase1_5/vitaldb_probe_160.json \
        --out   reports/fase1_5/vitaldb_scan_160.json
    # 2) reintento de los ilegibles con una versión nueva (actualiza la caché)
    python scripts/verify/fase1_5/scan_vitaldb.py --retry \
        --previous reports/fase1_5/vitaldb_scan_160.json \
        --cache-in reports/fase1_5/vitaldb_probe_160.json \
        --cache    reports/fase1_5/vitaldb_probe.json \
        --out      reports/fase1_5/vitaldb_scan.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.common.vital_signals import (  # noqa: E402
    probe_to_dict,
    probe_vital_file_detail,
)
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"


def _probe(path_str: str) -> tuple[str, bool, str | None, dict | None]:
    probe, err = probe_vital_file_detail(path_str)
    return path_str, probe is not None, err, probe_to_dict(probe)


def _vitaldb_version() -> str:
    try:
        import importlib.metadata as md
        return md.version("vitaldb")
    except Exception:  # noqa: BLE001
        return "desconocida"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--raw-dir", default=None)
    p.add_argument("--out", default=str(ROOT / "reports" / "fase1_5" / "vitaldb_scan.json"))
    p.add_argument("--cache", default=None, help="Ruta de la caché de sondas a escribir")
    p.add_argument("--cache-in", default=None,
                   help="Caché previa de la que arrastrar las sondas ya leídas")
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--retry", action="store_true",
                   help="Reintenta los ilegibles de un escaneo previo")
    p.add_argument("--previous", default=None,
                   help="JSON previo del que tomar los ficheros a reintentar")
    p.add_argument("--every", type=int, default=500,
                   help="Muestra el progreso cada N ficheros")
    args = p.parse_args()

    config = load_config(args.config)
    raw_dir = Path(args.raw_dir) if args.raw_dir else config_path(
        config, "paths", "vitaldb_raw_dir"
    )

    cache: dict[str, dict | None] = {}
    if args.cache_in and Path(args.cache_in).exists():
        cache = json.loads(Path(args.cache_in).read_text(encoding="utf-8"))

    if args.retry:
        prev = Path(args.previous or args.out)
        targets = [Path(f) for f in json.loads(
            prev.read_text(encoding="utf-8"))["unreadable"]]
        files = [f for f in targets if f.exists()]
        print(f"[scan] reintentando {len(files)} ficheros ilegibles...", flush=True)
    else:
        files = sorted(raw_dir.glob("*.vital"))

    errors: Counter = Counter()
    unreadable: list[str] = []
    readable = 0
    n_done = 0

    def _consume(results):
        nonlocal readable, n_done
        for path_str, ok, err, probe_dict in results:
            n_done += 1
            cache[path_str] = probe_dict
            if ok:
                readable += 1
            else:
                unreadable.append(path_str)
                errors[str(err)] += 1
            if args.every and n_done % args.every == 0:
                print(f"[scan] {n_done}/{len(files)}", flush=True)

    if args.workers > 1 and len(files) > args.workers:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            _consume(ex.map(_probe, [str(f) for f in files], chunksize=8))
    else:
        _consume(_probe(str(f)) for f in files)

    if args.cache:
        out_cache = Path(args.cache)
        out_cache.parent.mkdir(parents=True, exist_ok=True)
        out_cache.write_text(json.dumps(cache), encoding="utf-8")

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vitaldb_version": _vitaldb_version(),
        "raw_dir": str(raw_dir),
        "total_files": len(files),
        "readable": readable,
        "unreadable": unreadable,
        "n_unreadable": len(unreadable),
        "errors": dict(errors),
        "cache": args.cache,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("unreadable", "errors")},
                     indent=2, ensure_ascii=False))
    print("errores:", dict(errors))


if __name__ == "__main__":
    main()
