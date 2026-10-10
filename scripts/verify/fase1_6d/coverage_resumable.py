#!/usr/bin/env python3
"""
scripts/verify/fase1_6d/coverage_resumable.py
=============================================
Fase 1.6d — **punto 5**: cobertura por evento **tolerante a cuelgues**.

Igual que ``coverage_parallel.py`` (mide ``coverage`` / ``vars_ok`` / D13 por
evento) pero:

- procesa los eventos **por lotes en subprocesos con timeout**;
- si un lote se atasca, lo **biseca** para aislar el evento culpable
  (``src/common/timeout_batches.py``);
- escribe los resultados **incrementalmente** (jsonl) → **reanudable**;
- un evento que falla siempre tras ``--retries`` se deja **sin cobertura** y se
  **reporta** (no se inventa un 0 que lo excluiría de D13).

Uso:
    python scripts/verify/fase1_6d/coverage_resumable.py --cohort clinic \
        --workers 1 --batch-size 8 --per-event-timeout 180
    python scripts/verify/fase1_6d/coverage_resumable.py --cohort clinic --apply
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.common.timeout_batches import chunk, run_with_bisection  # noqa: E402
from src.create_dataset.build_signal_cases import SPECS, scan_source_files  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
D13_CORE = ("HR", "SpO2")


def _tasks(index: dict, by_box: dict[str, dict[str, Path]]) -> dict[str, dict]:
    tasks: dict[str, dict] = {}
    for e in index["events"]:
        names = e.get("source_files") or []
        paths = [str(by_box.get(e["box"], {}).get(n)) if n else None
                 for n in names]
        if not names or any(x is None for x in paths):
            continue
        size_gb = 0.0
        for pth in paths:
            try:
                size_gb += Path(pth).stat().st_size / 1e9
            except OSError:
                pass
        tasks[e["event_id"]] = {
            "event_id": e["event_id"], "paths": paths,
            "t0_unix": float(e["t0_unix"]),
            "spans": [(a["vent_start_h"], a["vent_end_h"])
                      for a in e["attempts"]],
            "size_gb": round(size_gb, 3),
        }
    return tasks


def worker_main(batch_path: Path, out_path: Path) -> int:
    """Mide la cobertura de los eventos del lote y añade una línea por evento."""
    from src.common.vital_signals import coverage_fractions, read_coverage_series

    tasks = json.loads(batch_path.read_text(encoding="utf-8"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as fh:
        for t in tasks:
            try:
                series = read_coverage_series(t["paths"], t["t0_unix"])
                fracs = coverage_fractions(series, [tuple(s) for s in t["spans"]])
                fracs = {k: round(float(v), 4) for k, v in fracs.items()}
                rec = {"event_id": t["event_id"], "coverage": fracs}
            except Exception as exc:  # noqa: BLE001
                rec = {"event_id": t["event_id"], "coverage": None,
                       "error": f"{type(exc).__name__}: {exc}"}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
    return 0


def _load_done(parts_dir: Path) -> dict[str, dict]:
    done: dict[str, dict] = {}
    for f in sorted(parts_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("coverage"):
                done[rec["event_id"]] = rec
    return done


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--worker", action="store_true")
    p.add_argument("--batch", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--cohort", required=False, default="clinic")
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--index", default=None)
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--per-event-timeout", type=float, default=120.0,
                   help="Timeout mínimo por evento (s)")
    p.add_argument("--timeout-per-gb", type=float, default=300.0,
                   help="Timeout por GB de los ficheros del lote (s/GB)")
    p.add_argument("--min-timeout", type=float, default=120.0)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--apply", action="store_true",
                   help="Escribe coverage/vars_ok/D13 en el índice")
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "fase1_6d"))
    args = p.parse_args()

    if args.worker:
        if not args.batch or not args.out:
            raise SystemExit("--worker requiere --batch y --out")
        raise SystemExit(worker_main(Path(args.batch), Path(args.out)))

    cohort = args.cohort
    config = load_config(args.config)
    cands = sorted((ROOT / "datasets" / cohort).glob(
        f"cases_v*/{cohort}_cases_index.json"))
    if not cands:
        raise SystemExit(f"no hay índice de {cohort}")
    index_path = Path(args.index) if args.index else cands[-1]
    index = json.loads(index_path.read_text(encoding="utf-8"))

    boxes = scan_source_files(config_path(config, "paths", f"{cohort}_raw_dir"),
                              SPECS[cohort])
    by_box = {b: {sf.path.name: sf.path for sf in files}
              for b, files in boxes.items()}
    tasks = _tasks(index, by_box)
    parts_dir = (Path(args.out_dir) / f"cobertura_{cohort}_parts")
    parts_dir.mkdir(parents=True, exist_ok=True)

    done = _load_done(parts_dir)
    remaining = [eid for eid in tasks if eid not in done]
    print(f"[{cohort}] {len(tasks)} eventos con ficheros; hechos {len(done)}; "
          f"pendientes {len(remaining)}", flush=True)

    def run_fn(batch: list[str]) -> bool:
        payload = [tasks[eid] for eid in batch]
        batch_file = parts_dir / "_batch.json"
        batch_file.write_text(json.dumps(payload), encoding="utf-8")
        out_file = parts_dir / "coverage.jsonl"
        gb = sum(t.get("size_gb", 0.0) for t in payload)
        timeout = max(args.min_timeout,
                      len(payload) * args.per_event_timeout,
                      gb * args.timeout_per_gb)
        cmd = [sys.executable, str(Path(__file__).resolve()),
               "--worker", "--batch", str(batch_file), "--out", str(out_file)]
        try:
            subprocess.run(cmd, cwd=str(ROOT), timeout=timeout, check=True,
                           capture_output=True, text=True)
            return True
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            return False

    t0 = time.perf_counter()
    total = len(chunk(remaining, args.batch_size))
    for i, batch in enumerate(chunk(remaining, args.batch_size), start=1):
        ok, failed = run_with_bisection(batch, run_fn, min_batch=1)
        for item in list(failed):
            for _ in range(args.retries):
                if run_fn([item]):
                    failed.remove(item)
                    break
        print(f"  lote {i}/{total}: ok {len(ok)}, fallidos {len(failed)}",
              flush=True)

    done = _load_done(parts_dir)
    pendientes = sorted(set(tasks) - set(done))
    for e in index["events"]:
        rec = done.get(e["event_id"])
        if rec is None:
            continue
        fracs = rec["coverage"]
        e["coverage"] = fracs
        e["vars_ok_50"] = bool(fracs) and all(v > 0.5 for v in fracs.values())
        e["vars_ok_80"] = bool(fracs) and all(v > 0.8 for v in fracs.values())

    measured = [e for e in index["events"] if e.get("coverage")]
    n50 = sum(1 for e in measured if e.get("vars_ok_50"))
    n80 = sum(1 for e in measured if e.get("vars_ok_80"))
    d13 = sum(1 for e in measured
              if all(float(e["coverage"].get(v, 0.0)) > 0.5 for v in D13_CORE))
    index["levels_coverage_measured"] = len(measured)
    index["vars_ok_50"] = n50
    index["vars_ok_80"] = n80
    index["d13_incluidos"] = d13
    per_var: dict[str, list[float]] = defaultdict(list)
    for e in measured:
        for k, v in e["coverage"].items():
            per_var[k].append(float(v))
    medians = {k: round(sorted(v)[len(v) // 2], 3) for k, v in sorted(per_var.items())}

    if args.apply:
        index_path.write_text(json.dumps(index, ensure_ascii=False),
                              encoding="utf-8")

    rep = {
        "cohort": cohort, "index": str(index_path.relative_to(ROOT)),
        "n_events": len(index["events"]), "n_measured": len(measured),
        "n_pendientes": len(pendientes), "pendientes": pendientes[:30],
        "vars_ok_50": n50, "vars_ok_80": n80, "d13_incluidos": d13,
        "d13_pct": round(100.0 * d13 / len(measured), 1) if measured else 0.0,
        "cobertura_mediana_por_variable": medians,
        "aplicado": bool(args.apply),
        "segundos": round(time.perf_counter() - t0, 1),
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"cobertura_{cohort}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k != "pendientes"},
                     ensure_ascii=False, indent=2))
    if pendientes:
        print(f"\n[FALLO] eventos sin cobertura (agotaron reintentos): "
              f"{len(pendientes)} -> {pendientes[:10]}")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
