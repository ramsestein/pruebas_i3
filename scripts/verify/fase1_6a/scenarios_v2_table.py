"""Escenarios del censo v2 + análisis de sensibilidad del filtro de plausibilidad.

Lee ``reports/fase1_6a/eicu_hospitales_v2.csv`` (ya calculado) y reproduce los
escenarios a–g de la Fase 1.6a-bis, añadiendo variantes e'/e2'/f' en las que el
filtro ``rc_invasive/icu <= 0.6`` se sustituye por el veredicto de plausibilidad
(se admiten los hospitales "concentrado_concordante").
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
CSV = ROOT / "reports" / "fase1_6a" / "eicu_hospitales_v2.csv"
pd.set_option("display.width", 220)

d = pd.read_csv(CSV)


def stats(mask: pd.Series) -> dict:
    sub = d[mask]
    u = sub.usable_stays.to_numpy(dtype=float)
    total = u.sum()
    q1, med, q3 = np.percentile(u, [25, 50, 75]) if u.size else (0, 0, 0)
    return {
        "hosp": int(u.size),
        "usable": int(total),
        "min": int(u.min()) if u.size else 0,
        "p25": round(float(q1)),
        "med": round(float(med)),
        "p75": round(float(q3)),
        "max": int(u.max()) if u.size else 0,
        "top%": round(100.0 * u.max() / total, 1) if total else 0.0,
        "enseñ": int((sub.teaching.astype(str) == "t").sum()),
    }


def dense_ok(m: pd.DataFrame) -> pd.Series:
    return m.rc_over_icu.fillna(9) <= 0.6


def plausible(m: pd.DataFrame) -> pd.Series:
    return m.plausibility != "implausible_permeable"


def base(m: pd.DataFrame) -> pd.Series:
    return ((m.vent_stays >= 50) & (m.frac_rc_without_intub.notna()) &
            (m.rc_invasive_stays / m.vent_stays >= 0.80) &
            m.inter_adj_median_min.notna())


S: dict[str, pd.Series] = {}
# a se toma del JSON (incluye el criterio de presión pico en 24 h, no presente
# como columna en el CSV).
import json  # noqa: E402

SCEN = json.loads((ROOT / "reports" / "fase1_6a" / "eicu_scenarios_v2.json")
                  .read_text(encoding="utf-8"))
S["a_mechanical_power"] = d.hospital_id.isin(SCEN["scenario_hospitals"]["a_mechanical_power"])
S["b_50_80_med4h"] = base(d) & (d.inter_adj_median_min <= 240)
S["c_50_80_med2h"] = base(d) & (d.inter_adj_median_min <= 120)
S["d_b_plus_varsok70"] = S["b_50_80_med4h"] & (d.usable_stays / d.vent_stays.clip(lower=1) >= 0.70)
S["e_plaus_med4h_30us"] = S["b_50_80_med4h"] & dense_ok(d) & (d.usable_stays >= 30)
S["e2_plaus_med4h_50us"] = S["b_50_80_med4h"] & dense_ok(d) & (d.usable_stays >= 50)
S["f_plaus_med2h_30us"] = S["c_50_80_med2h"] & dense_ok(d) & (d.usable_stays >= 30)
S["g_plaus_30us"] = dense_ok(d) & (d.usable_stays >= 30)
# Sensibilidad: veredicto en lugar del umbral duro
S["e'_veredicto_30us"] = S["b_50_80_med4h"] & plausible(d) & (d.usable_stays >= 30)
S["e2'_veredicto_50us"] = S["b_50_80_med4h"] & plausible(d) & (d.usable_stays >= 50)
S["g'_veredicto_30us"] = plausible(d) & (d.usable_stays >= 30)

rows = [{"escenario": k, **stats(v)} for k, v in S.items()]
print(pd.DataFrame(rows).to_string(index=False))

print("\n== hospitales excluidos por el umbral 0.6 pero NO implausibles ==")
for hard_k, soft_k in (("e_plaus_med4h_30us", "e'_veredicto_30us"),
                       ("g_plaus_30us", "g'_veredicto_30us")):
    add = sorted(set(d[S[soft_k]].hospital_id) - set(d[S[hard_k]].hospital_id))
    sub = d[d.hospital_id.isin(add)]
    print(f"{hard_k} -> +{len(add)} hospitales, +{int(sub.usable_stays.sum())} estancias: "
          f"{add} (rc_over_icu={[round(x, 3) for x in sub.rc_over_icu]})")

print("\n== hospitales de cada escenario (id:usable) ==")
for k, v in S.items():
    sub = d[v].sort_values("usable_stays", ascending=False)
    print(f"{k} ({len(sub)}):",
          ", ".join(f"{int(r.hospital_id)}:{int(r.usable_stays)}" for r in sub.itertuples()))
