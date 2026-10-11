#!/usr/bin/env python3
"""
scripts/verify/fase1_6e/track_inventory.py
==========================================
Fase 1.6e — **punto 1d**: inventario de pistas de Clínic y VitalDB.

Lista TODAS las pistas presentes en los ``.vital`` de origen (no solo las del
catálogo) con su cobertura por cohorte. Sirve para decidir el punto 3: ¿existe
alguna pista de **modo ventilatorio**, **fuga (leak %)**, **TV inspirado** o
**interfaz**?

Usa ``vitaldb.vital_trks`` (lee solo la cabecera), por lotes en subprocesos con
timeout y **bisección** (`src/common/timeout_batches.py`), con estado
incremental → **reanudable** (el disco `D:` tiene rachas de bloqueo).

Salida: ``reports/fase1_6e/inventario_pistas_{cohort}.json`` / ``.md``

Uso:
    python scripts/verify/fase1_6e/track_inventory.py --cohort vitaldb
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.common.timeout_batches import chunk, run_with_bisection  # noqa: E402
from src.create_dataset.build_signal_cases import SPECS, scan_source_files  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"

# Términos que interesan para el punto 3 (modo, fuga, TV inspirado, interfaz).
INTEREST_KEYWORDS = ("mode", "leak", "tv_insp", "vti", "insp", "interface",
                     "circuit", "niv", "cpap", "peep", "fio2", "compl", "resist")


def _cohort_files(cohort: str, config: dict,
                  limit_per_box: int | None = None) -> list[str]:
    raw = config_path(config, "paths", f"{cohort}_raw_dir")
    boxes = scan_source_files(raw, SPECS[cohort])
    out: list[str] = []
    for b in sorted(boxes):
        files = boxes[b]
        if limit_per_box:
            # Muestreo equiespaciado dentro del box (las pistas son propiedad
            # de la caja: unas pocas por box bastan para el inventario).
            n = len(files)
            k = min(limit_per_box, n)
            idx = sorted({int(round(i * (n - 1) / max(1, k - 1)))
                          for i in range(k)}) if k > 1 else [0]
            files = [files[i] for i in idx]
        out += [str(Path(sf.path)) for sf in files]
    return out


def worker_main(batch_path: Path, out_path: Path) -> int:
    import vitaldb

    paths = json.loads(batch_path.read_text(encoding="utf-8"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as fh:
        for p in paths:
            try:
                trks = sorted(str(t) for t in vitaldb.vital_trks(p))
            except Exception as exc:  # noqa: BLE001
                trks = None
                err = f"{type(exc).__name__}: {exc}"
            else:
                err = None
            fh.write(json.dumps({"path": p, "tracks": trks, "error": err}) + "\n")
            fh.flush()
    return 0


def _load_done(parts_dir: Path) -> dict[str, list[str]]:
    done: dict[str, list[str]] = {}
    for f in sorted(parts_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("tracks"):
                done[rec["path"]] = rec["tracks"]
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
    p.add_argument("--cohort", default="vitaldb")
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--batch-size", type=int, default=100)
    p.add_argument("--limit-per-box", type=int, default=None,
                   help="Muestrear N ficheros por box (las pistas son "
                        "propiedad de la caja)")
    p.add_argument("--per-file-timeout", type=float, default=3.0)
    p.add_argument("--min-timeout", type=float, default=30.0)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--report-dir", default=str(ROOT / "reports" / "fase1_6e"))
    args = p.parse_args()

    if args.worker:
        if not args.batch or not args.out:
            raise SystemExit("--worker requiere --batch y --out")
        raise SystemExit(worker_main(Path(args.batch), Path(args.out)))

    config = load_config(args.config)
    cohort = args.cohort
    rep_dir = Path(args.report_dir)
    parts_dir = rep_dir / f"inventario_{cohort}_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    paths = _cohort_files(cohort, config, args.limit_per_box)
    print(f"[{cohort}] {len(paths)} ficheros"
          f"{' (muestra de ' + str(args.limit_per_box) + '/box)' if args.limit_per_box else ''}",
          flush=True)

    def run_fn(batch: list[str]) -> bool:
        as_list = list(batch)
        bf = parts_dir / "_batch.json"
        bf.write_text(json.dumps(as_list), encoding="utf-8")
        timeout = max(args.min_timeout, len(as_list) * args.per_file_timeout)
        cmd = [sys.executable, str(Path(__file__).resolve()),
               "--worker", "--batch", str(bf),
               "--out", str(parts_dir / "tracks.jsonl")]
        try:
            subprocess.run(cmd, cwd=str(ROOT), timeout=timeout, check=True,
                           capture_output=True, text=True)
            return True
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            return False

    done = _load_done(parts_dir)
    remaining = [p for p in paths if p not in done]
    print(f"  hechos {len(done)}; pendientes {len(remaining)}", flush=True)
    batches = chunk(remaining, args.batch_size)
    for i, batch in enumerate(batches, start=1):
        ok, failed = run_with_bisection(batch, run_fn, min_batch=1)
        for item in list(failed):
            for _ in range(args.retries):
                if run_fn([item]):
                    failed.remove(item)
                    break
        print(f"  lote {i}/{len(batches)}: ok {len(ok)}, ilegibles "
              f"{len(failed)}", flush=True)

    done = _load_done(parts_dir)
    counts: Counter = Counter()
    for trks in done.values():
        counts.update(trks)
    n_files = len(paths)
    inventory = {
        "n_files": n_files, "n_ok": len(done),
        "n_ilegibles": n_files - len(done),
        "tracks": {t: {"n": c, "pct": round(100.0 * c / max(1, len(done)), 2)}
                   for t, c in counts.most_common()},
    }
    interesantes = {t: v for t, v in inventory["tracks"].items()
                    if any(k in t.lower() for k in INTEREST_KEYWORDS)}
    rep_dir.mkdir(parents=True, exist_ok=True)
    (rep_dir / f"inventario_pistas_{cohort}.json").write_text(
        json.dumps({"inventario": inventory, "interesantes": interesantes},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# Inventario de pistas — {cohort} (Fase 1.6e, punto 1d)", "",
             f"- Ficheros: {n_files} (legibles {len(done)}, ilegibles "
             f"{n_files - len(done)})",
             f"- Pistas distintas: {len(counts)}", "",
             "## Pistas presentes (todas)", "",
             "| Pista | Ficheros | % |", "|---|---|---|"]
    for t, v in inventory["tracks"].items():
        lines.append(f"| `{t}` | {v['n']} | {v['pct']} % |")
    lines += ["", "## Pistas de interés (modo / fuga / TV inspirado / interfaz)", "",
              "| Pista | Ficheros | % |", "|---|---|---|"]
    for t, v in interesantes.items():
        lines.append(f"| `{t}` | {v['n']} | {v['pct']} % |")
    if not interesantes:
        lines.append("| — | 0 | 0 % |")
    (rep_dir / f"inventario_pistas_{cohort}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:40]))


if __name__ == "__main__":
    main()
