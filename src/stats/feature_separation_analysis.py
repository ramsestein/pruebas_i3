"""
Analisis de separabilidad feature por feature.

Para cada feature individual:
  - Calcula Cohen's d = (mean1 - mean0) / std_pooled
  - Mide que tan separadas estan las clases en esa dimension

Para cada grupo de features:
  - Misma metrica pero con vectores multidimensionales

Pregunta: hay features que separan bien individualmente pero se degradan en combinacion?
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


def cohens_d(X0, X1):
    """Cohen's d = (mean1 - mean0) / std_pooled. Mayor absoluto = mejor separacion."""
    n0, n1 = len(X0), len(X1)
    if n0 < 2 or n1 < 2:
        return np.nan
    m0, m1 = np.mean(X0), np.mean(X1)
    s0, s1 = np.std(X0, ddof=1), np.std(X1, ddof=1)
    pooled_std = np.sqrt(((n0-1)*s0**2 + (n1-1)*s1**2) / (n0+n1-2))
    if pooled_std == 0:
        return np.nan
    return (m1 - m0) / pooled_std


def separability_ratio(X0, X1):
    """Ratio: varianza intra-clase / varianza inter-clase. Menor = mejor separacion."""
    n0, n1 = len(X0), len(X1)
    if n0 < 2 or n1 < 2:
        return np.nan
    intra_var = (np.var(X0) * n0 + np.var(X1) * n1) / (n0 + n1)
    overall_mean = (np.mean(X0) * n0 + np.mean(X1) * n1) / (n0 + n1)
    inter_var = (n0 * (np.mean(X0) - overall_mean)**2 + n1 * (np.mean(X1) - overall_mean)**2) / (n0 + n1)
    if inter_var == 0:
        return np.inf
    return intra_var / inter_var


def analyze_feature(X, y, feat_name, dataset_name):
    X0 = X[y == 0][feat_name].dropna().values
    X1 = X[y == 1][feat_name].dropna().values
    
    d = cohens_d(X0, X1)
    ratio = separability_ratio(X0, X1)
    
    return {
        "feature": feat_name,
        "dataset": dataset_name,
        "cohens_d": d,
        "separability_ratio": ratio,
        "mean_0": np.mean(X0) if len(X0) > 0 else np.nan,
        "mean_1": np.mean(X1) if len(X1) > 0 else np.nan,
        "std_0": np.std(X0) if len(X0) > 1 else np.nan,
        "std_1": np.std(X1) if len(X1) > 1 else np.nan,
        "n_0": len(X0),
        "n_1": len(X1),
    }


def main():
    print("=" * 60)
    print("ANALISIS DE SEPARABILIDAD FEATURE POR FEATURE")
    print("=" * 60)
    
    with open(CLINIC_IDX) as f:
        cidx = json.load(f)["windows"]
    with open(MIMIC_IDX) as f:
        midx = json.load(f)["windows"]
    
    X_c, y_c, feat_names = build_dataset(cidx, CLINIC_WIN, CLINIC_MAP, max_n=5000)
    X_m, y_m, _ = build_dataset(midx, MIMIC_WIN, MIMIC_MAP, max_n=12000)
    
    print(f"\nClinic: {len(y_c):,}  MIMIC: {len(y_m):,}")
    
    # Analisis feature por feature (datos raw, no escalados)
    results = []
    for feat in feat_names:
        r_c = analyze_feature(X_c, y_c, feat, "clinic")
        r_m = analyze_feature(X_m, y_m, feat, "mimic")
        results.extend([r_c, r_m])
    
    df_results = pd.DataFrame(results)
    
    # Tabla comparativa
    print(f"\n{'='*90}")
    print("SEPARABILIDAD POR FEATURE (datos raw)")
    print(f"{'='*90}")
    print(f"{'Feature':<18} {'Grupo':<8} {'Clinic d':>10} {'MIMIC d':>10} {'Clinic ratio':>12} {'MIMIC ratio':>12} {'Arrastrada?':>12}")
    print(f"{'-'*90}")
    
    feat_to_group = {}
    for g, cols in GROUPS.items():
        for c in cols:
            feat_to_group[c] = g
    
    dragged_features = []
    good_individually = []
    
    for feat in feat_names:
        c_row = df_results[(df_results["feature"] == feat) & (df_results["dataset"] == "clinic")].iloc[0]
        m_row = df_results[(df_results["feature"] == feat) & (df_results["dataset"] == "mimic")].iloc[0]
        
        d_c = c_row["cohens_d"]
        d_m = m_row["cohens_d"]
        r_c = c_row["separability_ratio"]
        r_m = m_row["separability_ratio"]
        g = feat_to_group.get(feat, "?")
        
        # Criterio: Cohen's d > 0.5 = separacion moderada, > 0.8 = buena
        clinic_good = abs(d_c) > 0.5 if not np.isnan(d_c) else False
        mimic_good = abs(d_m) > 0.5 if not np.isnan(d_m) else False
        
        arrastrada = ""
        if clinic_good and not mimic_good:
            arrastrada = "SI (clinic OK)"
            dragged_features.append(feat)
        elif not clinic_good and mimic_good:
            arrastrada = "SI (mimic OK)"
        elif clinic_good and mimic_good:
            good_individually.append(feat)
            arrastrada = "NO (ambas OK)"
        else:
            arrastrada = "NO (ambas mal)"
        
        print(f"{feat:<18} {g:<8} {d_c:>+10.3f} {d_m:>+10.3f} {r_c:>12.4f} {r_m:>12.4f} {arrastrada:>12}")
    
    # ── Analisis por grupo (multidimensional) ──────────────────────────
    print(f"\n{'='*90}")
    print("SEPARABILIDAD MULTIDIMENSIONAL POR GRUPO (Cohen's d multivariado)")
    print(f"{'='*90}")
    print(f"{'Grupo':<12} {'Clinic d':>10} {'MIMIC d':>10} {'Clinic ratio':>12} {'MIMIC ratio':>12}")
    print(f"{'-'*60}")
    
    for gname, gcols in GROUPS.items():
        gidx = [feat_names.index(c) for c in gcols if c in feat_names]
        if not gidx:
            continue
        
        # Multidimensional: usar norma del vector de diferencias de medias / traza covarianza
        for dataset, X, y in [("clinic", X_c, y_c), ("mimic", X_m, y_m)]:
            X0 = X.iloc[y == 0, gidx].dropna().values
            X1 = X.iloc[y == 1, gidx].dropna().values
            
            if len(X0) < 2 or len(X1) < 2:
                continue
            
            m0, m1 = X0.mean(axis=0), X1.mean(axis=0)
            mean_diff_norm = np.linalg.norm(m1 - m0)
            
            cov0 = np.cov(X0.T)
            cov1 = np.cov(X1.T)
            pooled_cov = (cov0 * len(X0) + cov1 * len(X1)) / (len(X0) + len(X1))
            pooled_trace = np.trace(pooled_cov)
            
            d_multi = mean_diff_norm / np.sqrt(pooled_trace) if pooled_trace > 0 else np.nan
            
            intra_var = pooled_trace
            inter_var = np.linalg.norm(m1 - m0) ** 2
            ratio = intra_var / inter_var if inter_var > 0 else np.inf
            
            if dataset == "clinic":
                print(f"{gname:<12} {d_multi:>10.3f}", end="")
            else:
                print(f" {d_multi:>10.3f} {ratio:>12.4f} {ratio:>12.4f}")
    
    # ── Plot ──────────────────────────────────────────────────────────
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    
    # Cohen's d por feature
    ax = axes[0]
    x = np.arange(len(feat_names))
    width = 0.35
    d_c_vals = [abs(df_results[(df_results["feature"] == f) & (df_results["dataset"] == "clinic")]["cohens_d"].values[0]) for f in feat_names]
    d_m_vals = [abs(df_results[(df_results["feature"] == f) & (df_results["dataset"] == "mimic")]["cohens_d"].values[0]) for f in feat_names]
    
    ax.bar(x - width/2, d_c_vals, width, label="Clinic", color="#1f77b4", alpha=0.8)
    ax.bar(x + width/2, d_m_vals, width, label="MIMIC", color="#ff7f0e", alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(feat_names, rotation=45, ha="right", fontsize=8)
    ax.axhline(0.5, color="green", ls="--", lw=1, label="d=0.5 (moderado)")
    ax.axhline(0.8, color="red", ls="--", lw=1, label="d=0.8 (fuerte)")
    ax.set_ylabel("|Cohen's d|", fontsize=10)
    ax.set_title("Separabilidad por feature", fontsize=11)
    ax.legend(fontsize=8)
    
    # Ratio intra/inter por feature
    ax = axes[1]
    r_c_vals = [df_results[(df_results["feature"] == f) & (df_results["dataset"] == "clinic")]["separability_ratio"].values[0] for f in feat_names]
    r_m_vals = [df_results[(df_results["feature"] == f) & (df_results["dataset"] == "mimic")]["separability_ratio"].values[0] for f in feat_names]
    # Clip para visualizacion
    r_c_vals = np.clip(r_c_vals, 0, 5)
    r_m_vals = np.clip(r_m_vals, 0, 5)
    
    ax.bar(x - width/2, r_c_vals, width, label="Clinic", color="#1f77b4", alpha=0.8)
    ax.bar(x + width/2, r_m_vals, width, label="MIMIC", color="#ff7f0e", alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(feat_names, rotation=45, ha="right", fontsize=8)
    ax.axhline(1.0, color="red", ls="--", lw=1, label="ratio=1 (sin separacion)")
    ax.set_ylabel("Intra-var / Inter-var", fontsize=10)
    ax.set_title("Ratio de varianzas (menor = mejor)", fontsize=11)
    ax.legend(fontsize=8)
    ax.set_ylim(0, 5)
    
    plt.tight_layout()
    out = OUT_DIR / "feature_separability.png"
    fig.savefig(out, dpi=120)
    print(f"\nFigura guardada: {out}")
    
    print(f"\n{'='*60}")
    print("RESUMEN")
    print(f"{'='*60}")
    print(f"Features que separan bien en AMBOS: {good_individually}")
    print(f"Features arrastradas (clinic OK, mimic mal): {dragged_features}")


if __name__ == "__main__":
    main()
