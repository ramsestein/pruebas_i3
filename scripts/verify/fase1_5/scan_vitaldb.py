#!/usr/bin/env python3
"""
scripts/verify/fase1_5/scan_vitaldb.py
======================================
Recorre **TODOS** los ficheros ``.vital`` de origen de VitalDB (no una muestra)
y reporta cuántos no se pueden leer y con qué error (Fase 1.5, punto 2).

Un fichero ilegible es "sin dato" (no "sin señal"): sus horas no crean huecos de
ventilador (D1) ni cortes de paciente (D2). Los que siguen siendo ilegibles con
la versión actual de ``vitaldb`` se listan como **corruptos**.

Salida:
  reports/fase1_5/vitaldb_scan.json

Uso:
    python scripts/verify/fase1_5/scan_vitaldb.py
    python scripts/verify/fase1_5/scan_vitaldb.py --retry   # reintenta los ilegibles
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
from src.common.vital_signals import probe_vital_file_detail  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"


def _probe(path_str: str) -> tuple[str, bool, str | None]:
    probe, err = probe_vital_file_detail(path_str)
    return path_str, probe is not None, err


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--raw-dir", default=None)
    p.add_argument("--out", default=str(ROOT / "reports" / "fase1_5" / "vitaldb_scan.json"))
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--retry", action="store_true",
                   help="Reintenta los ilegibles (útil tras actualizar vitaldb)")
    p.add_argument("--previous", default=None,
                   help="JSON previo del que tomar los ficheros a reintentar")
    args = p.parse_args()

    import vitaldb
    try:
        import importlib.metadata as _md
        vitaldb_version = _md.version("vitaldb")
    except Exception:  # noqa: BLE001
        vitaldb_version = getattr(vitaldb, "__version__", "desconocida")

    config = load_config(args.config)
    raw_dir = Path(args.raw_dir) if args.raw_dir else config_path(
        config, "paths", "vitaldb_raw_dir"
    )
    files = sorted(raw_dir.glob("*.vital"))

    if args.retry:
        prev = Path(args.previous or args.out)
        targets = [Path(f) for f in json.loads(prev.read_text(encoding="utf-8"))["unreadable"]]
        files = [f for f in targets if f.exists()]
        print(f"[scan] reintentando {len(files)} ficheros ilegibles...")

    errors: Counter = Counter()
    unreadable: list[str] = []
    readable = 0
    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for path_str, ok, err in ex.map(_probe, [str(f) for f in files], chunksize=16):
                if ok:
                    readable += 1
                else:
                    unreadable.append(path_str)
                    errors[str(err)] += 1
    else:
        for f in files:
            path_str, ok, err = _probe(str(f))
            if ok:
                readable += 1
            else:
                unreadable.append(path_str)
                errors[str(err)] += 1

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vitaldb_version": vitaldb_version,
        "raw_dir": str(raw_dir),
        "total_files": len(files),
        "readable": readable,
        "unreadable": unreadable,
        "n_unreadable": len(unreadable),
        "errors": dict(errors),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("unreadable", "errors")}, indent=2, ensure_ascii=False))
    print("errores:", dict(errors))


if __name__ == "__main__":
    main()
