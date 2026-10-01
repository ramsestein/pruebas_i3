"""
Muestra la varianza de cada dimension (feature) en el espacio escalado del SVM,
comparando clinic vs MIMIC.

Esto revela que features cambian mas/menos de distribucion entre dominios.
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
from sklearn.preprocessing import StandardScaler

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
    print("VARIANZA POR DIMENSION — Espacio escalado SVM")
    print("=" * 60)
    
    with open(CLINIC_IDX) as f:
        cidx = json.load(f)["windows"]
    with open(MIMIC_IDX) as f:
        midx = json.load(f)["windows"]
    
    X_c, y_c, feat_names = build_dataset(cidx, CLINIC_WIN, CLINIC_MAP, max_n=5000)
    X_m, y_m, _ = build_dataset(midx, MIMIC_WIN, MIMIC_MAP, max_n=12000)
    
    print(f"\nClinic: {len(y_c):,}  MIMIC: {len(y_m):,}")
    print(f"Features: {feat_names}")
    
    # Escalar como en SVM (fit en clinic, transform en ambos)
    imp = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    
    X_c_i = imp.fit_transform(X_c)
    X_m_i = imp.transform(X_m)
    
    X_c_s = scaler.fit_transform(X_c_i)
    X_m_s = scaler.transform(X_m_i)
    
    # Varianza por dimension
    var_c = np.var(X_c_s, axis=0)
    var_m = np.var(X_m_s, axis=0)
    
    print(f"\n{'='*60}")
    print("VARIANZA POR DIMENSION (despues de StandardScaler)")
    print(f"{'='*60}")
    print(f"{'Feature':<18} {'Clinic var':>12} {'MIMIC var':>12} {'Ratio M/C':>10} {'Grupo':>8}")
    print(f"{'-'*62}")
    
    # Determinar grupo de cada feature
    feat_to_group = {}
    for g, cols in GROUPS.items():
        for c in cols:
            feat_to_group[c] = g
    
    for i, feat in enumerate(feat_names):
        ratio = var_m[i] / var_c[i] if var_c[i] > 0 else np.nan
        g = feat_to_group.get(feat, "?")
        print(f"{feat:<18} {var_c[i]:>12.4f} {var_m[i]:>12.4f} {ratio:>10.2f} {g:>8}")
    
    # Varianza total
    print(f"\n{'-'*62}")
    print(f"{'TOTAL (suma)':<18} {var_c.sum():>12.4f} {var_m.sum():>12.4f} {var_m.sum()/var_c.sum():>10.2f}")
    
    # ── Plot comparativo ─────────────────────────────────────────────
    x = np.arange(len(feat_names))
    width = 0.35
    
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    
    # Bar chart varianzas
    ax = axes[0]
    bars1 = ax.bar(x - width/2, var_c, width, label="Clinic", color="#1f77b4", alpha=0.8)
    bars2 = ax.bar(x + width/2, var_m, width, label="MIMIC", color="#ff7f0e", alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(feat_names, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Varianza", fontsize=10)
    ax.set_title("Varianza por dimension (espacio escalado)", fontsize=11)
    ax.legend()
    ax.set_ylim(0, max(var_c.max(), var_m.max()) * 1.15)
    ax.axhline(1.0, color="grey", ls="--", lw=0.8, alpha=0.5)
    
    # Ratio MIMIC/Clinic
    ax = axes[1]
    ratios = [var_m[i]/var_c[i] if var_c[i] > 0 else 0 for i in range(len(feat_names))]
    colors = ["#2ca02c" if r < 1.5 else "#d62728" for r in ratios]
    ax.bar(x, ratios, color=colors, alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(feat_names, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Ratio MIMIC/Clinic", fontsize=10)
    ax.set_title("Ratio de varianzas (MIMIC / Clinic)", fontsize=11)
    ax.axhline(1.0, color="black", ls="--", lw=1)
    ax.axhline(2.0, color="red", ls=":", lw=0.8, alpha=0.5)
    ax.set_ylim(0, max(ratios) * 1.1)
    
    plt.tight_layout()
    out = OUT_DIR / "variance_per_dimension.png"
    fig.savefig(out, dpi=120)
    print(f"\nFigura guardada: {out}")
    
    # ── Varianza por grupo ─────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("VARIANZA POR GRUPO (suma de varianzas intra-grupo)")
    print(f"{'='*60}")
    print(f"{'Grupo':<12} {'Clinic sum':>12} {'MIMIC sum':>12} {'Ratio M/C':>10}")
    print(f"{'-'*48}")
    
    for gname, gcols in GROUPS.items():
        gidx = [feat_names.index(c) for c in gcols if c in feat_names]
        if gidx:
            gc = var_c[gidx].sum()
            gm = var_m[gidx].sum()
            ratio = gm / gc if gc > 0 else np.nan
            print(f"{gname:<12} {gc:>12.4f} {gm:>12.4f} {ratio:>10.2f}")


if __name__ == "__main__":
    main()
