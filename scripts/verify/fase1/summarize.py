#!/usr/bin/env python3
"""
scripts/verify/fase1/summarize.py
=================================
Genera las tablas del informe de Fase 1 a partir de los índices de casos
(`clinic_cases_index.json`, `vitaldb_cases_index.json`, `mimic_cases_index.json`).

Uso:
    python scripts/verify/fase1/summarize.py
    python scripts/verify/fase1/summarize.py --clinic <ruta.json> --vitaldb ... --mimic ...
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# La consola de Windows puede no ser UTF-8: forzarlo para no romper el informe.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.common.paths import config_path  # noqa: E402
from src.create_dataset.build_mimic_cases import output_root as mimic_out  # noqa: E402
from src.create_dataset.build_signal_cases import output_root as signal_out  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"


def _load(path: Path) -> dict | None:
    if not path or not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _pct(n: int, total: int) -> str:
    return f"{n} ({100.0 * n / total:.1f}%)" if total else "0"


def _duration_stats(events: list[dict]) -> str:
    dur = sorted(e["duration_seconds"] / 3600.0 for e in events)
    if not dur:
        return "—"
    q1 = dur[len(dur) // 4]
    med = statistics.median(dur)
    q3 = dur[(3 * len(dur)) // 4]
    return (f"min {dur[0]:.1f} h | Q1 {q1:.1f} h | mediana {med:.1f} h | "
            f"Q3 {q3:.1f} h | máx {dur[-1]:.1f} h")


def summarize(cohort: str, idx: dict | None) -> None:
    print(f"\n## {cohort.capitalize()}\n")
    if idx is None:
        print("_(índice no disponible)_")
        return
    events = idx.get("events", [])
    excluded = idx.get("excluded_events", [])
    total = len(events)
    print(f"- Eventos: **{total}** | excluidos (sin paciente): **{len(excluded)}**")

    print(f"- Intentos por evento: {dict(sorted(Counter(e['n_attempts'] for e in events).items()))}")
    print(f"- Duración: {_duration_stats(events)}")
    print(f"- `t0_source`: {dict(Counter(e.get('t0_source') for e in events))}")
    print(f"- `arrived_ventilated`: {dict(Counter(bool(e.get('arrived_ventilated')) for e in events))}")
    print(f"- `end_reason`: {dict(Counter(e.get('end_reason') for e in events))}")

    for win in ("48h", "72h"):
        types = Counter(e["labels"][win]["event_type"] for e in events if win in e.get("labels", {}))
        causes = Counter(
            e["labels"][win].get("censor_cause")
            for e in events
            if win in e.get("labels", {}) and e["labels"][win].get("censor_cause")
        )
        succ = types.get("successful_extubation", 0)
        print(f"- **Ventana {win}**: éxito {_pct(succ, total)} | "
              f"censura {_pct(total - succ, total)} {dict(causes)}")
        failed = Counter(
            "con_fallos" if e["labels"][win].get("n_failed_attempts", 0) > 0 else "sin_fallos"
            for e in events if win in e.get("labels", {})
        )
        print(f"  - intentos fallidos por evento: {dict(failed)}")

    if "n_signal_loss_at_end" in idx:
        print(f"- Pérdida de constantes al final (D5): **{idx['n_signal_loss_at_end']}**")
    if "n_simultaneous_shutdown" in idx:
        print(f"- Apagado simultáneo VM+monitor (≤15 min): **{idx['n_simultaneous_shutdown']}**")
    if "n_trach_time_unknown" in idx:
        print(f"- Traqueostomía ICD-9 sin hora (censura en último fin de VM): **{idx['n_trach_time_unknown']}**")
    if "total_stays_with_vent" in idx:
        print(f"- Estancias con VM: **{idx['total_stays_with_vent']}**")

    ex = Counter(e.get("exclusion_reason") for e in excluded)
    if ex:
        print(f"- Motivos de exclusión: {dict(ex)}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--clinic")
    p.add_argument("--vitaldb")
    p.add_argument("--mimic")
    args = p.parse_args()

    config = load_config(args.config)

    def _default(cohort: str) -> Path:
        if cohort == "mimic":
            return Path(mimic_out(config)[0]) / "mimic_cases_index.json"
        return Path(signal_out(config, cohort)[0]) / f"{cohort}_cases_index.json"

    paths = {
        "clinic": Path(args.clinic) if args.clinic else _default("clinic"),
        "vitaldb": Path(args.vitaldb) if args.vitaldb else _default("vitaldb"),
        "mimic": Path(args.mimic) if args.mimic else _default("mimic"),
    }
    for cohort in ("clinic", "vitaldb", "mimic"):
        summarize(cohort, _load(paths[cohort]))


if __name__ == "__main__":
    main()
