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
    p.add_argument("--box-timeout", type=float, default=1200.0,
                   help="Timeout MÍNIMO por box (s)")
    p.add_argument("--timeout-per-gb", type=float, default=240.0,
                   help="Timeout adicional por GB del box (s/GB): el timeout "
                        "efectivo es max(box-timeout, GB * timeout-per-gb)")
    p.add_argument("--retries", type=int, default=3,
                   help="Reintentos por box si se agota el tiempo (nunca se "
                        "salta: solo se marca 'pendiente' tras agotarlos)")
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

    def _box_timeout(box: str) -> float:
        gb = sum(f.path.stat().st_size for f in boxes[box]) / 1e9
        return max(args.box_timeout, gb * args.timeout_per_gb)

    status: dict[str, dict] = {}
    pendientes: list[str] = []
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
        tmo = _box_timeout(box)
        gb = sum(f.path.stat().st_size for f in boxes[box]) / 1e9
        ok = False
        last = ""
        for attempt in range(1, args.retries + 2):
            t0 = time.perf_counter()
            try:
                subprocess.run(cmd, cwd=str(ROOT), timeout=tmo,
                               check=True, capture_output=True, text=True)
                n = len(json.loads(index_path.read_text(
                    encoding="utf-8")).get("events", []))
                status[box] = {
                    "estado": "ok" if attempt == 1 else "recuperado",
                    "n_events": n, "intentos": attempt,
                    "segundos": round(time.perf_counter() - t0, 1),
                    "gb": round(gb, 1), "timeout_s": round(tmo, 0),
                }
                print(f"  [{i}/{len(names)}] {box}: {status[box]['estado']} "
                      f"({n} eventos, {status[box]['segundos']} s, intento "
                      f"{attempt})", flush=True)
                ok = True
                break
            except subprocess.TimeoutExpired:
                last = (f"timeout tras {tmo:.0f} s (intento {attempt}/"
                        f"{args.retries + 1})")
                print(f"  [{i}/{len(names)}] {box}: TIMEOUT ({tmo:.0f} s, "
                      f"intento {attempt}) -> REINTENTO", flush=True)
            except subprocess.CalledProcessError as exc:
                last = f"error: {(exc.stderr or '')[-300:]}"
                print(f"  [{i}/{len(names)}] {box}: ERROR -> REINTENTO",
                      flush=True)
        if not ok:
            status[box] = {"estado": "pendiente", "detalle": last,
                           "gb": round(gb, 1), "timeout_s": round(tmo, 0)}
            pendientes.append(box)
            print(f"  [{i}/{len(names)}] {box}: PENDIENTE tras "
                  f"{args.retries + 1} intentos ({last})", flush=True)

    parts: list[tuple[str, dict]] = []
    for box in names:
        idx_path = cache_dir / box / f"{cohort}_cases_index.json"
        if idx_path.exists():
            parts.append((box, json.loads(idx_path.read_text(encoding="utf-8"))))
    merged = merge_partial_indices(parts, cohort=cohort)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Un índice INCOMPLETO no debe parecer el definitivo: si queda algún box
    # pendiente se escribe con nombre explícito y se sale con código 2.
    if pendientes:
        destino = out_dir / f"{cohort}_cases_index_INCOMPLETO.json"
    else:
        destino = out_dir / f"{cohort}_cases_index.json"
    destino.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")

    ok = sum(1 for s in status.values() if s["estado"] in ("ok", "reutilizado",
                                                          "recuperado"))
    rep = {
        "cohort": cohort, "n_boxes": len(names), "boxes_ok": ok,
        "boxes_recuperados": sum(1 for s in status.values()
                                 if s["estado"] == "recuperado"),
        "boxes_pendientes": pendientes,
        "total_events": merged["total_events"],
        "total_excluded_events": merged["total_excluded_events"],
        "index": str(destino.relative_to(ROOT)),
        "status": status,
    }
    rep_dir = Path(args.report_dir)
    rep_dir.mkdir(parents=True, exist_ok=True)
    (rep_dir / f"resumible_{cohort}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# Reconstrucción resumible de {cohort} (Fase 1.6d, punto 5)", "",
             f"- Boxes: {len(names)} ({ok} correctos, "
             f"{rep['boxes_recuperados']} recuperados, "
             f"{len(pendientes)} pendientes)",
             f"- Eventos: {merged['total_events']} "
             f"(excluidos {merged['total_excluded_events']})",
             f"- Índice: `{rep['index']}`", "",
             "| Box | Estado | GB | Timeout (s) | Eventos | Segundos | Intento |",
             "|---|---|---|---|---|---|---|"]
    for box in names:
        s = status[box]
        lines.append(f"| {box} | {s['estado']} | {s.get('gb', '—')} | "
                     f"{s.get('timeout_s', '—')} | {s.get('n_events', '—')} | "
                     f"{s.get('segundos', '—')} | {s.get('intentos', '—')} |")
    (rep_dir / f"resumible_{cohort}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    if pendientes:
        print(f"\n[FALLO] boxes pendientes: {pendientes}. El índice escrito es "
              f"`{destino.name}` (INCOMPLETO). Vuelve a lanzar para reanudar.")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
