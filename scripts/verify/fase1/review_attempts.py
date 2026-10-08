#!/usr/bin/env python3
"""
scripts/verify/fase1/review_attempts.py
=======================================
Comprobación de la tasa de eventos con >= 1 fallo por cohorte (Fase 1,
ajuste 5) y revisión manual de 10 casos cuando la tasa es sospechosa.

Regla del informe
-----------------
Si una cohorte supera el **30 %** de eventos con al menos un intento fallido
(> 1 intento) o queda por debajo del **2 %**, se marca como posible artefacto
de segmentación y se revisan 10 casos a mano.

Uso:
    python scripts/verify/fase1/review_attempts.py            # solo el test
    python scripts/verify/fase1/review_attempts.py --review eicu
"""

from __future__ import annotations

import argparse
import glob
import json
import random
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.common.eicu_rules import (  # noqa: E402
    eicu_t0_minutes,
    merge_vent_episodes,
    sanitize_vent_episodes,
)
from src.common.paths import config_path  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

SEED = 20261003
LOW_PCT = 2.0
HIGH_PCT = 30.0


def verdict(pct: float, *, low: float = LOW_PCT, high: float = HIGH_PCT) -> str:
    """Veredicto de la tasa de eventos con ≥ 1 fallo (regla del informe).

    Una tasa > 30 % o < 2 % se marca como posible artefacto de segmentación.
    """
    if pct > high:
        return f"**posible artefacto** (> {high:.0f} %)"
    if pct < low:
        return f"**posible artefacto** (< {low:.0f} %)"
    return "ok"


def is_flagged(pct: float, *, low: float = LOW_PCT, high: float = HIGH_PCT) -> bool:
    return pct > high or pct < low


# ── Carga de cohortes ────────────────────────────────────────────────────────

def _index(cohort: str) -> dict:
    pats = [f"datasets/{cohort}/cases_*/{cohort}_cases_index.json"]
    if cohort == "mimic":
        pats.append("datasets/mimic3wdb/cases_*/mimic_cases_index.json")
    hits = [p for pat in pats for p in glob.glob(str(ROOT / pat))]
    if not hits:
        raise FileNotFoundError(f"sin índice de casos para {cohort}")
    return json.load(open(sorted(hits)[-1], encoding="utf-8"))


def _episodes_index(cohort: str) -> list[list[tuple[float, float, float | None]]]:
    """Lista de episodios ``[(vent_start_h, vent_end_h, reintub_h), ...]``."""
    idx = _index(cohort)
    out = []
    for ev in idx["events"]:
        out.append([
            (float(a["vent_start_h"]), float(a["vent_end_h"]),
             None if a.get("reintubation_h") is None else float(a["reintubation_h"]))
            for a in ev.get("attempts", [])
        ])
    return out


def _episodes_eicu() -> list[dict]:
    config = load_config(ROOT / "src/stage0/config/harmonize.yaml")
    eicu_dir = config_path(config, "paths", "eicu_dir")
    pat = pd.read_csv(eicu_dir / "patient.csv.gz", usecols=[
        "patientunitstayid", "unitdischargeoffset", "unitdischargestatus"])
    care = pd.read_csv(eicu_dir / "respiratoryCare.csv.gz", usecols=[
        "patientunitstayid", "ventstartoffset", "ventendoffset",
        "respcarestatusoffset", "airwaytype"], low_memory=False)
    care = care.dropna(subset=["ventstartoffset"])
    care["ventendoffset"] = care["ventendoffset"].fillna(0)
    max_status = care.groupby(["patientunitstayid", "ventstartoffset"])[
        "respcarestatusoffset"].transform("max")
    care.loc[care["ventendoffset"] <= 0, "ventendoffset"] = max_status
    care.loc[care["ventendoffset"] <= care["ventstartoffset"], "ventendoffset"] = (
        care.loc[care["ventendoffset"] <= care["ventstartoffset"], "ventstartoffset"] + 1)

    pat_by_id = pat.set_index("patientunitstayid")
    out: list[dict] = []
    for pid, g in care.groupby("patientunitstayid"):
        meta = pat_by_id.loc[pid]
        discharge = float(meta["unitdischargeoffset"])
        san = sanitize_vent_episodes(g, discharge)
        merged = merge_vent_episodes(san.episodes)
        t0 = eicu_t0_minutes(san.episodes)
        if t0 is None or merged.empty or discharge <= t0:
            continue
        straps = [
            (float(r["ventstartoffset"] - t0) / 60.0,
             float(r["ventendoffset"] - t0) / 60.0)
            for _, r in merged.iterrows()
        ]
        out.append({
            "id": int(pid),
            "t0_min": float(t0),
            "spans": straps,
            "discharge_h": (discharge - t0) / 60.0,
            "status": str(meta["unitdischargestatus"]).strip().lower(),
            "airwaytype": sorted({str(x) for x in g["airwaytype"].dropna().unique()}),
        })
    return out


def _n_attempts(ep) -> int:
    """Nº de intentos, tanto en lista de tuplas (índices) como en dict (eICU)."""
    return len(ep["spans"] if isinstance(ep, dict) else ep)


def _n_with_failure(episodes) -> int:
    return sum(1 for e in episodes if _n_attempts(e) > 1)


# ── Informe ──────────────────────────────────────────────────────────────────

def report() -> dict[str, float]:
    print("## Eventos con >= 1 fallo (> 1 intento) por cohorte\n")
    print("| Cohorte | Eventos | Con >=1 fallo | % | Veredicto |")
    print("|---|---|---|---|---|")
    pcts: dict[str, float] = {}
    for cohort in ("clinic", "vitaldb", "mimic"):
        eps = _episodes_index(cohort)
        n = len(eps)
        k = _n_with_failure(eps)
        pct = 100.0 * k / n if n else 0.0
        pcts[cohort] = pct
        print(f"| {cohort} | {n} | {k} | {pct:.2f} % | {verdict(pct)} |")

    eps = _episodes_eicu()
    n = len(eps)
    k = _n_with_failure(eps)
    pct = 100.0 * k / n if n else 0.0
    pcts["eicu"] = pct
    print(f"| eicu | {n} | {k} | {pct:.2f} % | {verdict(pct)} |")
    return pcts


def review_eicu(n_cases: int = 10) -> None:
    """Detalle de 10 casos de eICU (5 con >= 2 intentos + 5 con 1 intento)."""
    eps = _episodes_eicu()
    multi = [e for e in eps if len(e["spans"]) > 1]
    single = [e for e in eps if len(e["spans"]) == 1]
    rnd = random.Random(SEED)
    # Los casos con más intentos son los más informativos: primero esos y,
    # si no llegan a 10, se completan con casos de un solo intento al azar.
    sample = sorted(multi, key=lambda e: -len(e["spans"]))[:n_cases]
    need = n_cases - len(sample)
    if need > 0:
        sample += rnd.sample(single, min(need, len(single)))
    sample = sorted(sample, key=lambda e: -len(e["spans"]))[:n_cases]

    print(f"\n### Revisión manual eICU ({len(sample)} casos)\n")
    print("| # | stay | t0 (min) | intentos (h desde t0) | huecos (h) | alta (h) | estado | airwaytype |")
    print("|---|---|---|---|---|---|---|---|")
    for i, e in enumerate(sample, 1):
        spans = e["spans"]
        txt = " → ".join(f"[{a:.1f}, {b:.1f}]" for a, b in spans)
        gaps = [spans[j + 1][0] - spans[j][1] for j in range(len(spans) - 1)]
        gaps_txt = ", ".join(f"{g:.1f}" for g in gaps) if gaps else "—"
        airway = ", ".join(e["airwaytype"][:3])
        print(f"| {i} | {e['id']} | {e['t0_min']:.0f} | {txt} | {gaps_txt} | "
              f"{e['discharge_h']:.1f} | {e['status']} | {airway} |")

    # Diagnóstico: ¿los "episodios" separados caen dentro o fuera de la ventana?
    gaps_all = [
        spans[j + 1][0] - spans[j][1]
        for e in eps for spans in [e["spans"]] for j in range(len(spans) - 1)
    ]
    if gaps_all:
        within = sum(1 for g in gaps_all if g <= 48.0)
        print(f"\n- Huecos entre episodios de VM: n={len(gaps_all)} | "
              f"dentro de 48 h (reintubación real): {within} | "
              f"fuera de 48 h (episodios distintos): {len(gaps_all) - within}")
        print(f"- Mediana del hueco: {pd.Series(gaps_all).median():.2f} h")
    print(f"- Eventos con >1 episodio: {len(multi)} | con 1 episodio: {len(single)}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--review", choices=["clinic", "vitaldb", "mimic", "eicu"],
                   default=None, help="cohorte a revisar en detalle (10 casos)")
    p.add_argument("--cases", type=int, default=10)
    args = p.parse_args()

    pcts = report()
    flagged = [c for c, v in pcts.items() if is_flagged(v)]
    print(f"\n**Cohortes marcadas:** {flagged or 'ninguna'}")
    target = args.review or (flagged[0] if flagged else None)
    if target == "eicu":
        review_eicu(args.cases)


if __name__ == "__main__":
    main()
