#!/usr/bin/env python3
"""
scripts/verify/fase1_6d/build_cohort_resumable.py
=================================================
Fase 1.6d — **punto 5**: reconstrucción **resumible** de una cohorte de señal.

Motivación: en Clínic el disco `D:` puede **colgar una lectura durante minutos o
indefinidamente**. Con el builder normal, un único fichero atascado (y el
`ProcessPoolExecutor.map`, que consume en orden) deja **todos** los workers
ociosos y el build no termina (medido: +178 s de CPU en 3 h 20 min).

Este driver procesa la cohorte **box a box** en subprocesos independientes, cada
uno con un **timeout**:

1. escanea los boxes (rápido);
2. por box: si ya existe su índice parcial, **lo reutiliza** (reanudable); si no,
   lanza ``build_signal_cases --boxes <box> --out-dir <parcial>`` con timeout;
   si el box agota el tiempo, se marca como **fallido** y se continúa;
3. **fusiona** los índices parciales en el índice final.

Salidas:
  ``<out-dir>/<cohort>_cases_index.json``  (índice fusionado)
  ``reports/fase1_6d/resumible_<cohort>.json`` / ``.md``  (estado por box)

Uso:
    python scripts/verify/fase1_6d/build_cohort_resumable.py --cohort clinic \
        --workers 2 --box-timeout 3600
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.create_dataset.build_signal_cases import (  # noqa: E402
    SPECS,
    merge_partial_indices,
    output_root,
    scan_source_files,
)
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--cohort", required=True, choices=sorted(SPECS))
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--box-timeout", type=float, default=3600.0,
                   help="Segundos máximos por box (si se agota, se salta)")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--cache-dir", default=None)
    p.add_argument("--max-boxes", type=int, default=None)
    p.add_argument("--report-dir", default=str(ROOT / "reports" / "fase1_6d"))
    args = p.parse_args()

    config = load_config(args.config)
    cohort = args.cohort
    raw_dir = config_path(config, "paths", f"{cohort}_raw_dir")
    out_dir, _version = output_root(config, cohort)
    if args.out_dir:
        out_dir = Path(args.out_dir)
    cache_dir = (Path(args.cache_dir) if args.cache_dir
                 else ROOT / "datasets" / cohort / f"_partials_{cohort}")
    cache_dir.mkdir(parents=True, exist_ok=True)

    boxes = scan_source_files(raw_dir, SPECS[cohort])
    names = sorted(boxes)
    if args.max_boxes:
        names = names[: args.max_boxes]
    total_files = sum(len(boxes[b]) for b in names)
    print(f"[{cohort}] {len(names)} boxes, {total_files} ficheros de origen",
          flush=True)

    status: dict[str, dict] = {}
    for i, box in enumerate(names, start=1):
        box_out = cache_dir / box
        index_path = box_out / f"{cohort}_cases_index.json"
        if index_path.exists():
            status[box] = {"estado": "reutilizado", "n_events":
                           len(json.loads(index_path.read_text(
                               encoding="utf-8")).get("events", []))}
            print(f"  [{i}/{len(names)}] {box}: reutilizado", flush=True)
            continue
        cmd = [sys.executable, "-m", "src.create_dataset.build_signal_cases",
               "--cohort", cohort, "--no-merge", "--boxes", box,
               "--workers", str(args.workers), "--out-dir", str(box_out)]
        t0 = time.perf_counter()
        try:
            subprocess.run(cmd, cwd=str(ROOT), timeout=args.box_timeout,
                           check=True, capture_output=True, text=True)
            n = len(json.loads(index_path.read_text(
                encoding="utf-8")).get("events", []))
            status[box] = {"estado": "ok", "n_events": n,
                           "segundos": round(time.perf_counter() - t0, 1)}
            print(f"  [{i}/{len(names)}] {box}: ok ({n} eventos, "
                  f"{status[box]['segundos']} s)", flush=True)
        except subprocess.TimeoutExpired:
            status[box] = {"estado": "timeout",
                           "segundos": round(time.perf_counter() - t0, 1)}
            print(f"  [{i}/{len(names)}] {box}: TIMEOUT "
                  f"({args.box_timeout} s) -> se salta", flush=True)
        except subprocess.CalledProcessError as exc:
            status[box] = {"estado": "error",
                           "detalle": (exc.stderr or "")[-400:]}
            print(f"  [{i}/{len(names)}] {box}: ERROR -> se salta", flush=True)

    parts: list[tuple[str, dict]] = []
    for box in names:
        idx_path = cache_dir / box / f"{cohort}_cases_index.json"
        if idx_path.exists():
            parts.append((box, json.loads(idx_path.read_text(encoding="utf-8"))))
    merged = merge_partial_indices(parts, cohort=cohort)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{cohort}_cases_index.json").write_text(
        json.dumps(merged, ensure_ascii=False), encoding="utf-8")

    ok = sum(1 for s in status.values() if s["estado"] in ("ok", "reutilizado"))
    rep = {
        "cohort": cohort, "n_boxes": len(names), "boxes_ok": ok,
        "boxes_timeout": sum(1 for s in status.values() if s["estado"] == "timeout"),
        "boxes_error": sum(1 for s in status.values() if s["estado"] == "error"),
        "total_events": merged["total_events"],
        "total_excluded_events": merged["total_excluded_events"],
        "index": str((out_dir / f"{cohort}_cases_index.json").relative_to(ROOT)),
        "status": status,
    }
    rep_dir = Path(args.report_dir)
    rep_dir.mkdir(parents=True, exist_ok=True)
    (rep_dir / f"resumible_{cohort}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# Reconstrucción resumible de {cohort} (Fase 1.6d, punto 5)", "",
             f"- Boxes: {len(names)} ({ok} correctos, "
             f"{rep['boxes_timeout']} con timeout, {rep['boxes_error']} con error)",
             f"- Eventos: {merged['total_events']} "
             f"(excluidos {merged['total_excluded_events']})",
             f"- Índice: `{rep['index']}`", "",
             "| Box | Estado | Eventos | Segundos |", "|---|---|---|---|"]
    for box in names:
        s = status[box]
        lines.append(f"| {box} | {s['estado']} | {s.get('n_events', '—')} | "
                     f"{s.get('segundos', '—')} |")
    (rep_dir / f"resumible_{cohort}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
