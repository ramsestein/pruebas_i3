"""
Muestra la diferencia de varianza por feature entre clinic y MIMIC
despues de aplicar MinMaxScaler ajustado en clinic.

Esto revela que features cambian mas/menos de distribucion entre dominios
cuando preservamos la varianza natural (no forzamos a 1 como StandardScaler).
"""
import json
import warnings
import numpy as np
import pandas as pd
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sklearn.impute import SimpleImputer
from sklearn.preprocessing import MinMaxScaler

warnings.filterwarnings("ignore")

# ── Paths ─────────────────────────────────────────────────────────────────────
CLINIC_WIN = Path("datasets/clinic_vitals/windows_10min")
CLINIC_IDX = Path("datasets/clinic_vitals/windows_index.json")
MIMIC_WIN  = Path("datasets/mimic3wdb/windows_10min")
MIMIC_IDX  = Path("datasets/mimic3wdb/windows_index.json")
OUT_DIR    = Path("results/mimic_evaluation")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Grupos ────────────────────────────────────────────────────────────────────
GROUPS = {
    "VENT":   ["PEEP", "PIP", "TV", "MV", "RR_V"],
    "CARDIO": ["ABP_S", "ABP_D", "ABP_M"],
    "OXIGEN": ["SpO2", "FiO2", "SAFI"],
    "META":   ["Compliance", "Drive_pressure", "PIP_PEEP_ratio", "MV_TV_ratio"],
}

CLINIC_MAP = {
    "PEEP":    "Intellivue/PEEP_CMH2O",
    "PIP":     "Intellivue/PIP_CMH2O",
    "TV":      "Intellivue/TV_INSP",
    "MV":      "Intellivue/MV_EXP",
    "RR_V":    "Intellivue/VENT_RR",
    "ABP_S":   "Intellivue/ART_SYS",
    "ABP_D":   "Intellivue/ART_DIA",
    "ABP_M":   "Intellivue/ART_MEAN",
    "SpO2":    "Intellivue/PLETH_SAT_O2",
    "FiO2":    "Intellivue/FIO2",
}

MIMIC_MAP = {
    "PEEP":    "PEEP",
    "PIP":     "PIP",
    "TV":      "TV",
    "MV":      "MV",
    "RR_V":    "RR_V",
    "ABP_S":   "ABP_S",
    "ABP_D":   "ABP_D",
    "ABP_M":   "ABPMean",
    "SpO2":    "SpO2",
    "FiO2":    "FiO2",
}


def extract_semantic_features(df_window, col_map):
    vals = {}
    for name, col in col_map.items():
        if col is not None and col in df_window.columns:
            s = df_window[col].dropna()
            vals[name] = float(s.mean()) if len(s) > 0 else np.nan
        else:
            vals[name] = np.nan
    
    feats = {}
    for k in ["PEEP", "PIP", "TV", "MV", "RR_V", "ABP_S", "ABP_D", "ABP_M", "SpO2", "FiO2"]:
        feats[k] = vals.get(k, np.nan)
    
    fio2 = vals.get("FiO2", np.nan)
    spo2 = vals.get("SpO2", np.nan)
    if pd.notna(fio2) and pd.notna(spo2) and fio2 > 0:
        feats["SAFI"] = spo2 / (fio2 / 100.0)
    else:
        feats["SAFI"] = np.nan
    
    peep = vals.get("PEEP", np.nan)
    pip  = vals.get("PIP", np.nan)
    tv   = vals.get("TV", np.nan)
    if pd.notna(peep) and pd.notna(pip) and pip > peep and pd.notna(tv) and tv > 0:
        feats["Compliance"] = tv / (pip - peep)
    else:
        feats["Compliance"] = np.nan
    
    if pd.notna(peep) and pd.notna(pip):
        feats["Drive_pressure"] = pip - peep
    else:
        feats["Drive_pressure"] = np.nan
    
    if pd.notna(peep) and peep > 0 and pd.notna(pip):
        feats["PIP_PEEP_ratio"] = pip / peep
    else:
        feats["PIP_PEEP_ratio"] = np.nan
    
    mv = vals.get("MV", np.nan)
    if pd.notna(mv) and pd.notna(tv) and tv > 0:
        feats["MV_TV_ratio"] = mv / tv
    else:
        feats["MV_TV_ratio"] = np.nan
    
    return feats


def build_dataset(windows_meta, win_dir, col_map, max_n=5000):
    rng = np.random.RandomState(42)
    if len(windows_meta) > max_n:
        windows_meta = rng.choice(windows_meta, size=max_n, replace=False).tolist()
    
    records = []
    for w in windows_meta:
        fpath = win_dir / w["window_file"]
        if not fpath.exists():
            continue
        try:
            df = pd.read_parquet(fpath)
        except Exception:
            continue
        row = extract_semantic_features(df, col_map)
        row["label"] = 1 - int(w["label"])
        records.append(row)
    
    df = pd.DataFrame(records)
    y = df["label"].values.astype(int)
    
    all_feats = []
    for gname, gcols in GROUPS.items():
        all_feats.extend(gcols)
    all_feats = list(dict.fromkeys(all_feats))
    
    X = df[all_feats].copy()
    for c in ["Compliance", "SAFI", "PIP_PEEP_ratio", "MV_TV_ratio"]:
        if c in X.columns:
            X[c] = X[c].clip(lower=0, upper=1000)
    
    return X, y, all_feats


def main():
    print("=" * 60)
    print("DIFERENCIA DE VARIANZA: Clinic vs MIMIC (MinMaxScaler)")
    print("=" * 60)
    
    with open(CLINIC_IDX) as f:
        cidx = json.load(f)["windows"]
    with open(MIMIC_IDX) as f:
        midx = json.load(f)["windows"]
    
    X_c, y_c, feat_names = build_dataset(cidx, CLINIC_WIN, CLINIC_MAP, max_n=5000)
    X_m, y_m, _ = build_dataset(midx, MIMIC_WIN, MIMIC_MAP, max_n=12000)
    
    print(f"\nClinic: {len(y_c):,}  MIMIC: {len(y_m):,}")
    
    # Imputar y MinMaxScaler ajustado en clinic
    imp = SimpleImputer(strategy="median")
    scaler = MinMaxScaler()
    
    X_c_i = imp.fit_transform(X_c)
    X_m_i = imp.transform(X_m)
    
    X_c_s = scaler.fit_transform(X_c_i)
    X_m_s = scaler.transform(X_m_i)
    
    # Varianzas despues de MinMaxScaler (fit en clinic)
    var_c = np.var(X_c_s, axis=0)
    var_m = np.var(X_m_s, axis=0)
    
    # Diferencia absoluta y relativa
    diff_abs = var_m - var_c
    diff_rel = np.zeros_like(diff_abs)
    for i in range(len(var_c)):
        if var_c[i] > 0:
            diff_rel[i] = (var_m[i] - var_c[i]) / var_c[i]
        else:
            diff_rel[i] = np.nan
    
    print(f"\n{'='*70}")
    print("VARIANZAS Y DIFERENCIAS (MinMaxScaler fit en clinic)")
    print(f"{'='*70}")
    print(f"{'Feature':<18} {'Clinic':>10} {'MIMIC':>10} {'Diff abs':>10} {'Diff rel':>10} {'Grupo':>8}")
    print(f"{'-'*70}")
    
    feat_to_group = {}
    for g, cols in GROUPS.items():
        for c in cols:
            feat_to_group[c] = g
    
    for i, feat in enumerate(feat_names):
        g = feat_to_group.get(feat, "?")
        rel_str = f"{diff_rel[i]:>+9.1%}" if not np.isnan(diff_rel[i]) else "    N/A"
        print(f"{feat:<18} {var_c[i]:>10.5f} {var_m[i]:>10.5f} {diff_abs[i]:>+10.5f} {rel_str} {g:>8}")
    
    # Totales
    print(f"{'-'*70}")
    print(f"{'TOTAL':<18} {var_c.sum():>10.5f} {var_m.sum():>10.5f} {diff_abs.sum():>+10.5f}")
    
    # ── Por grupo ─────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("VARIANZA Y DIFERENCIA POR GRUPO")
    print(f"{'='*70}")
    print(f"{'Grupo':<12} {'Clinic sum':>12} {'MIMIC sum':>12} {'Diff abs':>12} {'Diff rel':>10}")
    print(f"{'-'*58}")
    
    for gname, gcols in GROUPS.items():
        gidx = [feat_names.index(c) for c in gcols if c in feat_names]
        if gidx:
            gc = var_c[gidx].sum()
            gm = var_m[gidx].sum()
            da = gm - gc
            dr = da / gc if gc > 0 else np.nan
            dr_str = f"{dr:>+9.1%}" if not np.isnan(dr) else "    N/A"
            print(f"{gname:<12} {gc:>12.5f} {gm:>12.5f} {da:>+12.5f} {dr_str}")
    
    # ── Plot ──────────────────────────────────────────────────────────
    x = np.arange(len(feat_names))
    
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    # Bar chart varianzas
    ax = axes[0, 0]
    width = 0.35
    ax.bar(x - width/2, var_c, width, label="Clinic", color="#1f77b4", alpha=0.8)
    ax.bar(x + width/2, var_m, width, label="MIMIC", color="#ff7f0e", alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(feat_names, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Varianza", fontsize=10)
    ax.set_title("Varianza por feature (MinMaxScaler)", fontsize=11)
    ax.legend()
    
    # Diferencia absoluta
    ax = axes[0, 1]
    colors = ["#2ca02c" if d < 0 else "#d62728" for d in diff_abs]
    ax.bar(x, diff_abs, color=colors, alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(feat_names, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("MIMIC - Clinic", fontsize=10)
    ax.set_title("Diferencia de varianza (abs)", fontsize=11)
    ax.axhline(0, color="black", lw=1)
    
    # Diferencia relativa
    ax = axes[1, 0]
    valid = ~np.isnan(diff_rel)
    colors_rel = ["#2ca02c" if d < 0 else "#d62728" for d in diff_rel[valid]]
    ax.bar(x[valid], diff_rel[valid] * 100, color=colors_rel, alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(feat_names, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("% cambio", fontsize=10)
    ax.set_title("Diferencia relativa de varianza (%)", fontsize=11)
    ax.axhline(0, color="black", lw=1)
    
    # Varianza por grupo (stacked bar)
    ax = axes[1, 1]
    group_names = list(GROUPS.keys())
    clinic_group_vars = []
    mimic_group_vars = []
    for gname, gcols in GROUPS.items():
        gidx = [feat_names.index(c) for c in gcols if c in feat_names]
        clinic_group_vars.append(var_c[gidx].sum() if gidx else 0)
        mimic_group_vars.append(var_m[gidx].sum() if gidx else 0)
    
    xg = np.arange(len(group_names))
    width = 0.35
    ax.bar(xg - width/2, clinic_group_vars, width, label="Clinic", color="#1f77b4", alpha=0.8)
    ax.bar(xg + width/2, mimic_group_vars, width, label="MIMIC", color="#ff7f0e", alpha=0.8)
    ax.set_xticks(xg)
    ax.set_xticklabels(group_names)
    ax.set_ylabel("Varianza total", fontsize=10)
    ax.set_title("Varianza por grupo", fontsize=11)
    ax.legend()
    
    plt.tight_layout()
    out = OUT_DIR / "variance_diff_minmax.png"
    fig.savefig(out, dpi=120)
    print(f"\nFigura guardada: {out}")


if __name__ == "__main__":
    main()
