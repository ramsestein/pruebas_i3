#!/usr/bin/env python3
"""
scripts/verify/fase1_6d/probe_cohort_resumable.py
=================================================
Fase 1.6d — **punto 5**: caché de **sondas por fichero** tolerante a cuelgues.

Problema: en Clínic el disco `D:` puede **bloquear una lectura durante minutos o
indefinidamente**. Con el builder normal, un solo fichero atascado impide
terminar el box.

Solución: **probar los ficheros por lotes en subprocesos con timeout** y, cuando
un lote se atasca, **bisecarlo** (`src/common/timeout_batches.py`) hasta aislar
el fichero culpable. El resto del lote se salva. Los resultados se escriben
**incrementalmente** (jsonl), así que el proceso es **reanudable**, y al final se
genera el JSON que consume el builder con ``--probe-cache`` (la segmentación ya
no toca el disco).

Un fichero que falla siempre tras ``--retries`` se marca como **ilegible**
(``null``): el builder lo trata como «sin dato» (no crea huecos) y se **reporta
de forma prominente**.

Uso (padre):
    python scripts/verify/fase1_6d/probe_cohort_resumable.py --cohort clinic \
        --cache reports/fase1_6d/probe_clinic.json --batch-size 25

Uso (hijo, interno):
    python scripts/verify/fase1_6d/probe_cohort_resumable.py --worker \
        --batch <batch.json> --out <part.jsonl>
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
from src.common.timeout_batches import chunk, run_with_bisection  # noqa: E402
from src.create_dataset.build_signal_cases import SPECS, scan_source_files  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"


# ── Modo hijo ────────────────────────────────────────────────────────────────

def worker_main(batch_path: Path, out_path: Path) -> int:
    """Prueba los ficheros del lote y añade una línea jsonl por fichero."""
    from src.common.vital_signals import probe_to_dict, probe_vital_file_detail

    paths = json.loads(batch_path.read_text(encoding="utf-8"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as fh:
        for p in paths:
            probe, err = probe_vital_file_detail(p)
            fh.write(json.dumps({
                "path": str(Path(p)), "probe": probe_to_dict(probe),
                "error": err,
            }, ensure_ascii=False) + "\n")
            fh.flush()
    return 0


# ── Modo padre ───────────────────────────────────────────────────────────────

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
                continue  # línea truncada por un hijo matado a medias
            done[rec["path"]] = rec
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
    p.add_argument("--cohort", default="clinic")
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--boxes", default=None)
    p.add_argument("--cache", default=None)
    p.add_argument("--batch-size", type=int, default=25)
    p.add_argument("--per-file-timeout", type=float, default=20.0)
    p.add_argument("--min-timeout", type=float, default=30.0)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--report-dir", default=str(ROOT / "reports" / "fase1_6d"))
    args = p.parse_args()

    if args.worker:
        if not args.batch or not args.out:
            raise SystemExit("--worker requiere --batch y --out")
        raise SystemExit(worker_main(Path(args.batch), Path(args.out)))

    config = load_config(args.config)
    cohort = args.cohort
    raw_dir = config_path(config, "paths", f"{cohort}_raw_dir")
    cache_path = (Path(args.cache) if args.cache
                  else ROOT / "reports" / "fase1_6d" / f"probe_{cohort}.json")
    parts_dir = cache_path.with_suffix("").with_name(
        cache_path.stem + "_parts")
    parts_dir.mkdir(parents=True, exist_ok=True)

    boxes = scan_source_files(raw_dir, SPECS[cohort])
    if args.boxes:
        want = {b.strip() for b in args.boxes.split(",") if b.strip()}
        boxes = {b: f for b, f in boxes.items() if b in want}
    paths = [str(Path(sf.path)) for b in sorted(boxes) for sf in boxes[b]]
    print(f"[{cohort}] {len(paths)} ficheros a sondar en {len(boxes)} boxes",
          flush=True)

    done = _load_done(parts_dir)
    remaining = [p for p in paths if p not in done]
    print(f"  ya sondados: {len(done)}; pendientes: {len(remaining)}",
          flush=True)

    def run_fn(batch: list[str]) -> bool:
        as_list = list(batch)
        batch_file = parts_dir / "_batch.json"
        batch_file.write_text(json.dumps(as_list), encoding="utf-8")
        out_file = parts_dir / "probes.jsonl"
        timeout = max(args.min_timeout, len(as_list) * args.per_file_timeout)
        cmd = [sys.executable, str(Path(__file__).resolve()),
               "--worker", "--batch", str(batch_file), "--out", str(out_file)]
        try:
            subprocess.run(cmd, cwd=str(ROOT), timeout=timeout, check=True,
                           capture_output=True, text=True)
            return True
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            return False

    t0 = time.perf_counter()
    total_batches = len(chunk(remaining, args.batch_size))
    for i, batch in enumerate(chunk(remaining, args.batch_size), start=1):
        ok, failed = run_with_bisection(batch, run_fn, min_batch=1)
        # Reintentos de los elementos aislados que fallaron.
        for item in list(failed):
            for _ in range(args.retries):
                if run_fn([item]):
                    failed.remove(item)
                    ok.append(item)
                    break
        print(f"  lote {i}/{total_batches}: ok {len(ok)}, fallidos "
              f"{len(failed)}", flush=True)

    done = _load_done(parts_dir)
    ilegibles = sorted(set(paths) - set(done))
    cache = {}
    for pth in paths:
        rec = done.get(pth)
        cache[pth] = None if rec is None else rec.get("probe")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    rep = {
        "cohort": cohort, "n_files": len(paths), "n_sondados": len(done),
        "n_ilegibles": len(ilegibles), "ilegibles": ilegibles,
        "cache": str(cache_path.relative_to(ROOT)),
        "segundos": round(time.perf_counter() - t0, 1),
    }
    rep_dir = Path(args.report_dir)
    rep_dir.mkdir(parents=True, exist_ok=True)
    (rep_dir / f"sondas_{cohort}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{cohort}] sondados {len(done)}/{len(paths)}; ilegibles "
          f"{len(ilegibles)}; caché -> {cache_path}")
    if ilegibles:
        print(f"[AVISO] ficheros marcados ILEGIBLES (se reintentaron "
              f"{args.retries} veces): {ilegibles[:10]}"
              f"{' ...' if len(ilegibles) > 10 else ''}")


if __name__ == "__main__":
    main()
