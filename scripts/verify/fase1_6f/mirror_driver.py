#!/usr/bin/env python3
"""
fase1_6f/mirror_driver.py
=========================
Driver del espejo (D-m) box a box con TIMEOUT, porque ``D:`` tiene rachas en las
que una operación de fichero se queda colgada para siempre (medido: 20 h de
reloj con 0,03 s de CPU en la reconstrucción de Clínic del 12/10, y otra vez en
la primera pasada del espejo del 14/10). Con el script normal, un solo cuelgue
deja la copia parada indefinidamente.

Qué hace:

1. Descubre los boxes del origen (con presupuesto de tiempo: hasta el `iterdir`
   puede colgarse).
2. Por box lanza ``make_mirror.py`` en un SUBPROCESO con timeout y reintentos.
   Es reanudable: el manifiesto hace que lo ya copiado se salte (verificando su
   SHA-256).
3. Si todos los boxes acaban bien, escribe la marca ``_mirror_summary.json`` en
   el espejo (es lo que autoriza a los builders a leer de él) y el resumen
   agregado en el repo.

Uso:
    python scripts/verify/fase1_6f/mirror_driver.py --group clinic \\
        --box-timeout 3600 --retries 3
    python scripts/verify/fase1_6f/mirror_driver.py --group vitaldb
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.raw_mirror import (  # noqa: E402
    MANIFEST_NAME,
    SUMMARY_NAME,
    manifest_merkle_root,
    read_manifest,
    set_readonly,
)

MAKE = ROOT / "scripts" / "verify" / "fase1_6f" / "make_mirror.py"

GROUPS: dict[str, dict] = {
    "clinic": {
        "source": "D:/data/clinic_vitals",
        "dest": "D:/iprove3_raw_mirror/clinic_vitals",
        "exclude": "dataset_clinic",
        "pattern": None,
        "box_mode": "top_dir",
    },
    "vitaldb": {
        "source": "D:/data/vitaldb_sicu",
        "dest": "D:/iprove3_raw_mirror/vitaldb_sicu",
        "exclude": "",
        "pattern": "*.vital",
        "box_mode": "prefix",
        "prefix_regex": r"(SICU\d+_\d+)_",
    },
}


def _con_presupuesto(fn, budget_s: float, default):
    """Ejecuta ``fn`` en un hilo con presupuesto (los cuelgues de D: no vuelven)."""
    caja: dict = {"out": default}

    def _work() -> None:
        try:
            caja["out"] = fn()
        except OSError as exc:  # noqa: BLE001
            caja["err"] = str(exc)

    th = threading.Thread(target=_work, daemon=True)
    th.start()
    th.join(timeout=budget_s)
    if th.is_alive():
        print(f"    [aviso] operación colgada > {budget_s:.0f} s: se usa el "
              f"valor por defecto", flush=True)
        return default
    return caja["out"]


def discover_boxes(cfg: dict, budget_s: float = 120.0) -> list[str]:
    """Boxes del origen (carpetas top o prefijo del nombre del fichero)."""
    src = Path(cfg["source"])

    def _work() -> list[str]:
        if cfg["box_mode"] == "top_dir":
            excl = {x for x in cfg["exclude"].split(",") if x}
            return sorted(p.name for p in src.iterdir()
                          if p.is_dir() and p.name not in excl)
        rx = re.compile(cfg["prefix_regex"])
        seen: set[str] = set()
        for p in src.glob(cfg["pattern"] or "*"):
            m = rx.match(p.name)
            if m:
                seen.add(m.group(1))
        return sorted(seen)

    boxes = _con_presupuesto(_work, budget_s, [])
    if not boxes:
        raise RuntimeError(f"no se han podido descubrir los boxes de {src} "
                           f"(¿cuelgue de D:?)")
    return boxes


def run_box(cfg: dict, box: str, *, timeout_s: float, retries: int,
            dest: Path) -> tuple[bool, str]:
    cmd = [sys.executable, str(MAKE), "--source", cfg["source"],
           "--dest", str(dest), "--no-stamp"]
    if cfg["box_mode"] == "top_dir":
        cmd += ["--only-top", box]
    else:
        cmd += ["--prefix", box + "_"]
    if cfg.get("pattern"):
        cmd += ["--pattern", cfg["pattern"]]
    if cfg.get("exclude"):
        cmd += ["--exclude", cfg["exclude"]]
    last = ""
    for attempt in range(1, retries + 2):
        t0 = time.perf_counter()
        try:
            subprocess.run(cmd, cwd=str(ROOT), timeout=timeout_s, check=True,
                           capture_output=True, text=True)
            dt = time.perf_counter() - t0
            return True, f"ok ({dt:.0f} s, intento {attempt})"
        except subprocess.TimeoutExpired:
            last = f"TIMEOUT tras {timeout_s:.0f} s (intento {attempt})"
            print(f"    [aviso] {last} -> reintento", flush=True)
        except subprocess.CalledProcessError as exc:
            last = f"ERROR (intento {attempt}): " + (
                (exc.stderr or "")[-300:].replace("\n", " "))
            print(f"    [aviso] {box}: {last} -> reintento", flush=True)
    return False, last


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--group", choices=sorted(GROUPS), required=True)
    p.add_argument("--box-timeout", type=float, default=3600.0)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--only", default="", help="Solo estos boxes (coma)")
    p.add_argument("--summary", default=None,
                   help="Resumen agregado en el repo (JSON)")
    args = p.parse_args()

    cfg = GROUPS[args.group]
    dest = Path(cfg["dest"])
    boxes = discover_boxes(cfg)
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        boxes = [b for b in boxes if b in want]
    print(f"[driver] {len(boxes)} boxes en {cfg['source']} -> {dest}", flush=True)

    status: dict[str, str] = {}
    fallidos: list[str] = []
    for i, box in enumerate(boxes, start=1):
        print(f"  [{i}/{len(boxes)}] {box}…", flush=True)
        ok, detail = run_box(cfg, box, timeout_s=args.box_timeout,
                             retries=args.retries, dest=dest)
        status[box] = detail
        print(f"  [{i}/{len(boxes)}] {box}: {detail}", flush=True)
        if not ok:
            fallidos.append(box)

    entries = _con_presupuesto(
        lambda: read_manifest(dest / MANIFEST_NAME), 600.0, {})
    doc = {
        "group": args.group,
        "source": cfg["source"],
        "dest": str(dest),
        "n_boxes": len(boxes),
        "boxes_fallidos": fallidos,
        "status": status,
        "n_files_manifest": len(entries),
        "total_gib": round(sum(int(d["size"]) for d in entries.values()) / 2**30, 3),
        "merkle_root": manifest_merkle_root(entries) if entries else None,
    }
    if fallidos:
        print(f"[driver] {len(fallidos)} boxes PENDIENTES: {fallidos}", flush=True)
        print("[driver] el espejo NO se marca como completo.", flush=True)
    elif entries:
        stamp = dest / SUMMARY_NAME
        if stamp.exists():
            import os
            import stat as _stat
            os.chmod(stamp, _stat.S_IWRITE | _stat.S_IREAD)
        stamp.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        set_readonly(stamp)
        print(f"[driver] espejo COMPLETO: {len(entries)} ficheros, "
              f"{doc['total_gib']} GiB", flush=True)

    if args.summary:
        Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary).write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if fallidos else 0


if __name__ == "__main__":
    sys.exit(main())
