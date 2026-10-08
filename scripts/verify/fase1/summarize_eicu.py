#!/usr/bin/env python3
"""
scripts/verify/fase1/summarize_eicu.py
======================================
Tablas del informe de Fase 1 para eICU, calculadas directamente con las reglas
compartidas (`src/common/eicu_rules.py` + `src/common/labels.py`), ya que eICU
no genera índice de casos en esta fase (su ruta es el adaptador de la Etapa 0).

Uso:
    python scripts/verify/fase1/summarize_eicu.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.common.d5_events import is_trach_text, trach_time_from_offset_rows  # noqa: E402
from src.common.eicu_rules import (  # noqa: E402
    eicu_t0_minutes,
    merge_vent_episodes,
    sanitize_vent_episodes,
)
from src.common.labels import (  # noqa: E402
    FAILURE_WINDOWS_H,
    assign_label,
    attempts_from_pairs,
)
from src.common.paths import config_path  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402


def main() -> None:
    config = load_config(ROOT / "src/stage0/config/harmonize.yaml")
    eicu_dir = config_path(config, "paths", "eicu_dir")

    pat = pd.read_csv(eicu_dir / "patient.csv.gz", usecols=[
        "patientunitstayid", "unitdischargeoffset", "unitdischargestatus"])
    care = pd.read_csv(eicu_dir / "respiratoryCare.csv.gz", usecols=[
        "patientunitstayid", "ventstartoffset", "ventendoffset",
        "respcarestatusoffset", "airwaytype"], low_memory=False)
    care = care.dropna(subset=["ventstartoffset"])
    # Normalización de eICU (igual que el adaptador): ventendoffset suele ser
    # 0/nulo; se aproxima con el último respcarestatusoffset del episodio.
    care["ventendoffset"] = care["ventendoffset"].fillna(0)
    max_status = care.groupby(["patientunitstayid", "ventstartoffset"])[
        "respcarestatusoffset"].transform("max")
    care.loc[care["ventendoffset"] <= 0, "ventendoffset"] = max_status
    care.loc[care["ventendoffset"] <= care["ventstartoffset"], "ventendoffset"] = (
        care.loc[care["ventendoffset"] <= care["ventstartoffset"], "ventstartoffset"] + 1
    )

    pat_by_id = pat.set_index("patientunitstayid")
    care_groups = {pid: g for pid, g in care.groupby("patientunitstayid")}

    n_events = n_excluded = 0
    excluded_preexisting = 0
    attempts_hist: Counter = Counter()
    reasons: Counter = Counter()
    by_window = {w: Counter() for w in FAILURE_WINDOWS_H}
    causes = {w: Counter() for w in FAILURE_WINDOWS_H}
    dur_h: list[float] = []
    anomalies = Counter()
    disconn_to_death: list[float] = []

    for pid, g in care_groups.items():
        meta = pat_by_id.loc[pid]
        discharge = float(meta["unitdischargeoffset"])
        san = sanitize_vent_episodes(g, discharge)
        for a in san.anomalies:
            anomalies[a.kind] += 1
        merged = merge_vent_episodes(san.episodes)
        t0 = eicu_t0_minutes(san.episodes)
        if t0 is None or merged.empty:
            continue
        if discharge <= t0:
            n_excluded += 1
            continue
        n_events += 1

        # Traqueostomía (antes de contar: la previa a t0 excluye el evento).
        trach_off = None
        if "airwaytype" in g.columns:
            mask = g["airwaytype"].astype(str).map(is_trach_text)
            trach_off = trach_time_from_offset_rows(g.loc[mask, "respcarestatusoffset"])
        trach_h = None if trach_off is None else (trach_off - t0) / 60.0
        if trach_h is not None and trach_h < 0:
            excluded_preexisting += 1
            n_events -= 1
            continue

        pairs: list[tuple[float, float | None]] = []
        for i in range(len(merged)):
            extub = (float(merged.iloc[i]["ventendoffset"]) - t0) / 60.0
            reintub = (
                (float(merged.iloc[i + 1]["ventstartoffset"]) - t0) / 60.0
                if i + 1 < len(merged) else None
            )
            pairs.append((extub, reintub))
        attempts_hist[len(pairs)] += 1
        dur_h.append((float(merged.iloc[-1]["ventendoffset"]) - t0) / 60.0)

        died = str(meta["unitdischargestatus"]).strip().lower() == "expired"
        death_h = ((discharge - t0) / 60.0) if died else None
        last_disconnect = pairs[-1][0] if pairs else None
        died_vent = bool(
            died and last_disconnect is not None
            and death_h is not None and death_h <= last_disconnect + 1e-6
        )

        from src.common.d5_events import d5_censor_for_window
        if (died and not died_vent and death_h is not None
                and last_disconnect is not None and death_h >= last_disconnect):
            disconn_to_death.append(death_h - last_disconnect)
        for w in FAILURE_WINDOWS_H:
            dec = d5_censor_for_window(
                failure_window_h=float(w), last_disconnect_h=last_disconnect,
                trach_time_h=trach_h, death_time_h=death_h, died_ventilated=died_vent,
            )
            lab = assign_label(
                attempts_from_pairs(pairs), obs_end_h=(discharge - t0) / 60.0,
                failure_window_h=float(w),
                censor_cause=dec.censor_cause, censor_time_h=dec.censor_time_h,
            )
            by_window[w][lab.event_type] += 1
            if lab.censor_cause:
                causes[w][lab.censor_cause] += 1
        reasons["expired" if died else "alive"] += 1

    print("## eICU\n")
    print(f"- Estancias con VM (tras sanea): **{n_events}** | descartadas (alta <= t0): {n_excluded}")
    print(f"- Excluidas por traqueostomía previa a t0: **{excluded_preexisting}**")
    print(f"- Intentos por evento: {dict(sorted(attempts_hist.items()))}")
    if dur_h:
        d = sorted(dur_h)
        print(f"- Duración (h): min {d[0]:.1f} | Q1 {d[len(d)//4]:.1f} | "
              f"mediana {d[len(d)//2]:.1f} | Q3 {d[3*len(d)//4]:.1f} | máx {d[-1]:.1f}")
    print(f"- Estado al alta: {dict(reasons)}")
    for w in FAILURE_WINDOWS_H:
        n = sum(by_window[w].values())
        succ = by_window[w].get("successful_extubation", 0)
        print(f"- **Ventana {int(w)}h**: éxito {succ} ({100*succ/max(n,1):.1f}%) | "
              f"censura {n-succ} | eventos: {dict(by_window[w])}")
        print(f"  - causas de censura: {dict(causes[w])}")
    print(f"- Anomalías de duración/hueco (registradas): {dict(anomalies)}")
    if disconn_to_death:
        dd = sorted(disconn_to_death)
        print(f"- Desconexión→muerte (h), n={len(dd)}: min {dd[0]:.2f} | "
              f"Q1 {dd[len(dd)//4]:.2f} | mediana {dd[len(dd)//2]:.2f} | "
              f"Q3 {dd[3*len(dd)//4]:.2f} | máx {dd[-1]:.2f}")


if __name__ == "__main__":
    main()
