"""
Compara distribuciones de features entre clinic y MIMIC.
Genera histogramas lado a lado y estadisticas (media, std, KS-test).
"""
import json
import warnings
import numpy as np
import pandas as pd
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats

warnings.filterwarnings("ignore")

# ── Paths ─────────────────────────────────────────────────────────────────────
CLINIC_WIN = Path("datasets/clinic_vitals/windows_10min")
CLINIC_IDX = Path("datasets/clinic_vitals/windows_index.json")
MIMIC_WIN  = Path("datasets/mimic3wdb/windows_10min")
MIMIC_IDX  = Path("datasets/mimic3wdb/windows_index.json")
OUT_DIR    = Path("results/mimic_evaluation")
OUT_DIR.mkdir(parents=True, exist_ok=True)

RANDOM_STATE = 42
MAX_SAMPLES  = 5000  # muestras por dataset

ALL_FEATS = ["PEEP","PIP","TV","MV","RR_V","ABP_S","ABP_D","ABP_M","SpO2","FiO2","SAFI","CO2","ETCO2","Compliance","Drive_pressure","PIP_PEEP_ratio","MV_TV_ratio"]

CLINIC_MAP = {
    "PEEP":"Intellivue/PEEP_CMH2O","PIP":"Intellivue/PIP_CMH2O","TV":"Intellivue/TV_INSP",
    "MV":"Intellivue/MV_EXP","RR_V":"Intellivue/VENT_RR","ABP_S":"Intellivue/ART_SYS",
    "ABP_D":"Intellivue/ART_DIA","ABP_M":"Intellivue/ART_MEAN","SpO2":"Intellivue/PLETH_SAT_O2",
    "FiO2":"Intellivue/FIO2","CO2":"Intellivue/CO2","ETCO2":"Intellivue/AWAY_CO2_ET",
}
MIMIC_MAP = {
    "PEEP": "PEEP", "PIP": "PIP", "TV": "TV", "MV": "MV", "RR_V": "RR_V",
    "HR": "HR", "ABP_S": "ABP_S", "ABP_D": "ABP_D", "ABP_M": "ABPMean",
    "SpO2": "SpO2", "FiO2": "FiO2", "CVP": "CVP",
}


def extract_features_clinic(df_window):
    vals = {}
    for name, col in CLINIC_MAP.items():
        if col is not None and col in df_window.columns:
            s = df_window[col].dropna()
            vals[name] = float(s.mean()) if len(s) > 0 else np.nan
        else:
            vals[name] = np.nan
    feats = {k: vals.get(k, np.nan) for k in ALL_FEATS if k not in ["SAFI","Compliance","Drive_pressure","PIP_PEEP_ratio","MV_TV_ratio"]}
    fio2, spo2 = vals.get("FiO2", np.nan), vals.get("SpO2", np.nan)
    if pd.notna(fio2) and pd.notna(spo2) and fio2 > 0:
        feats["SAFI"] = spo2 / (fio2 / 100.0)
    else:
        feats["SAFI"] = np.nan
    peep, pip, tv = vals.get("PEEP", np.nan), vals.get("PIP", np.nan), vals.get("TV", np.nan)
    if pd.notna(peep) and pd.notna(pip) and pip > peep and pd.notna(tv) and tv > 0:
        feats["Compliance"] = tv / (pip - peep)
    else:
        feats["Compliance"] = np.nan
    feats["Drive_pressure"] = pip - peep if pd.notna(peep) and pd.notna(pip) else np.nan
    feats["PIP_PEEP_ratio"] = pip / peep if pd.notna(peep) and peep > 0 and pd.notna(pip) else np.nan
    mv = vals.get("MV", np.nan)
    feats["MV_TV_ratio"] = mv / tv if pd.notna(mv) and pd.notna(tv) and tv > 0 else np.nan
    return feats


def extract_features_mimic(df_window):
    vals = {}
    for name, col in MIMIC_MAP.items():
        if col is not None and col in df_window.columns:
            s = df_window[col].dropna()
            vals[name] = float(s.mean()) if len(s) > 0 else np.nan
        else:
            vals[name] = np.nan
    vals["CO2"] = np.nan
    vals["ETCO2"] = np.nan
    feats = {k: vals.get(k, np.nan) for k in ALL_FEATS if k not in ["SAFI","Compliance","Drive_pressure","PIP_PEEP_ratio","MV_TV_ratio"]}
    fio2, spo2 = vals.get("FiO2", np.nan), vals.get("SpO2", np.nan)
    if pd.notna(fio2) and pd.notna(spo2) and fio2 > 0:
        feats["SAFI"] = spo2 / (fio2 / 100.0)
    else:
        feats["SAFI"] = np.nan
    peep, pip, tv = vals.get("PEEP", np.nan), vals.get("PIP", np.nan), vals.get("TV", np.nan)
    if pd.notna(peep) and pd.notna(pip) and pip > peep and pd.notna(tv) and tv > 0:
        feats["Compliance"] = tv / (pip - peep)
    else:
        feats["Compliance"] = np.nan
    feats["Drive_pressure"] = pip - peep if pd.notna(peep) and pd.notna(pip) else np.nan
    feats["PIP_PEEP_ratio"] = pip / peep if pd.notna(peep) and peep > 0 and pd.notna(pip) else np.nan
    mv = vals.get("MV", np.nan)
    feats["MV_TV_ratio"] = mv / tv if pd.notna(mv) and pd.notna(tv) and tv > 0 else np.nan
    return feats


def build_dataset_clinic(max_n=MAX_SAMPLES):
    rng = np.random.RandomState(RANDOM_STATE)
    with open(CLINIC_IDX) as f:
        cidx = json.load(f)["windows"]
    if len(cidx) > max_n:
        cidx = rng.choice(cidx, size=max_n, replace=False).tolist()
    records = []
    for w in cidx:
        fpath = CLINIC_WIN / w["window_file"]
        if not fpath.exists():
            continue
        try:
            df = pd.read_parquet(fpath)
        except Exception:
            continue
        row = extract_features_clinic(df)
        row["label"] = 1 - int(w["label"])
        records.append(row)
    df = pd.DataFrame(records)
    X = df[ALL_FEATS].copy()
    for c in ["Compliance","SAFI","PIP_PEEP_ratio","MV_TV_ratio"]:
        if c in X.columns:
            X[c] = X[c].clip(lower=0, upper=1000)
    return X


def build_dataset_mimic(max_n=MAX_SAMPLES):
    rng = np.random.RandomState(RANDOM_STATE)
    with open(MIMIC_IDX) as f:
        idx = json.load(f)["windows"]
    extub = [w for w in idx if w["label"] == 0]
    intub = [w for w in idx if w["label"] == 1]
    if len(intub) > max_n - len(extub):
        intub = rng.choice(intub, size=max_n - len(extub), replace=False).tolist()
    selected = extub + intub
    rng.shuffle(selected)
    records = []
    errors = 0
    for w in selected:
        fpath = MIMIC_WIN / w["window_file"]
        if not fpath.exists():
            errors += 1; continue
        try:
            df = pd.read_parquet(fpath)
        except Exception:
            errors += 1; continue
        row = extract_features_mimic(df)
        row["label"] = 1 - int(w["label"])
        records.append(row)
    df = pd.DataFrame(records)
    X = df[ALL_FEATS].copy()
    for c in ["Compliance","SAFI","PIP_PEEP_ratio","MV_TV_ratio"]:
        if c in X.columns:
            X[c] = X[c].clip(lower=0, upper=1000)
    return X


def plot_distributions(clinic_vals, mimic_vals, feature, ax):
    """Plotea histogramas lado a lado con estadisticas."""
    # Remover NaNs
    c = clinic_vals.dropna().values
    m = mimic_vals.dropna().values

    # Limitar a percentiles 1-99 para evitar outliers extremos
    if len(c) > 0:
        c_low, c_high = np.percentile(c, [1, 99])
        c = c[(c >= c_low) & (c <= c_high)]
    if len(m) > 0:
        m_low, m_high = np.percentile(m, [1, 99])
        m = m[(m >= m_low) & (m <= m_high)]

    bins = 40
    ax.hist(c, bins=bins, alpha=0.6, label=f"Clinic (n={len(c)})", color="blue", density=True)
    ax.hist(m, bins=bins, alpha=0.6, label=f"MIMIC (n={len(m)})", color="orange", density=True)
    ax.set_title(feature, fontsize=10)
    ax.legend(fontsize=7)
    ax.tick_params(labelsize=7)

    # Estadisticas
    if len(c) > 0 and len(m) > 0:
        ks_stat, ks_p = stats.ks_2samp(c, m)
        text = f"C: μ={c.mean():.2f} σ={c.std():.2f}\nM: μ={m.mean():.2f} σ={m.std():.2f}\nKS={ks_stat:.3f} p={ks_p:.2e}"
        ax.text(0.98, 0.98, text, transform=ax.transAxes, fontsize=7,
                verticalalignment="top", horizontalalignment="right",
                bbox=dict(boxstyle="round", facecolor="white", alpha=0.7))


def main():
    print("="*60)
    print("COMPARACION DE DISTRIBUCIONES: clinic vs MIMIC")
    print("="*60)

    print("\nCargando clinic...")
    X_clinic = build_dataset_clinic(MAX_SAMPLES)
    print(f"  {len(X_clinic)} muestras")

    print("\nCargando MIMIC...")
    X_mimic = build_dataset_mimic(MAX_SAMPLES)
    print(f"  {len(X_mimic)} muestras")

    # Estadisticas por feature
    print(f"\n{'='*60}")
    print("ESTADISTICAS POR FEATURE")
    print(f"{'='*60}")
    print(f"{'Feature':<20} {'Clinic μ':>10} {'MIMIC μ':>10} {'Clinic σ':>10} {'MIMIC σ':>10} {'KS':>8} {'p-val':>10}")
    print(f"{'-'*75}")

    stats_rows = []
    for feat in ALL_FEATS:
        c = X_clinic[feat].dropna()
        m = X_mimic[feat].dropna()
        if len(c) > 0 and len(m) > 0:
            ks_stat, ks_p = stats.ks_2samp(c, m)
        else:
            ks_stat, ks_p = np.nan, np.nan
        stats_rows.append({
            "feature": feat,
            "clinic_mean": c.mean() if len(c) > 0 else np.nan,
            "mimic_mean": m.mean() if len(m) > 0 else np.nan,
            "clinic_std": c.std() if len(c) > 0 else np.nan,
            "mimic_std": m.std() if len(m) > 0 else np.nan,
            "clinic_n": len(c),
            "mimic_n": len(m),
            "KS_stat": ks_stat,
            "KS_p": ks_p,
        })
        print(f"{feat:<20} {c.mean():>10.2f} {m.mean():>10.2f} {c.std():>10.2f} {m.std():>10.2f} {ks_stat:>8.3f} {ks_p:>10.2e}")

    stats_df = pd.DataFrame(stats_rows)
    stats_df.to_csv(OUT_DIR / "feature_distribution_stats.csv", index=False)
    print(f"\nStats guardado en: {OUT_DIR / 'feature_distribution_stats.csv'}")

    # Figuras
    print("\nGenerando figuras...")
    n_feats = len(ALL_FEATS)
    ncols = 3
    nrows = (n_feats + ncols - 1) // ncols

    fig, axes = plt.subplots(nrows, ncols, figsize=(15, 4 * nrows))
    axes = axes.flatten()

    for i, feat in enumerate(ALL_FEATS):
        plot_distributions(X_clinic[feat], X_mimic[feat], feat, axes[i])

    # Ocultar ejes sobrantes
    for j in range(i + 1, len(axes)):
        axes[j].axis("off")

    plt.tight_layout()
    fig.savefig(OUT_DIR / "feature_distributions_clinic_vs_mimic.png", dpi=150)
    print(f"Figura guardada en: {OUT_DIR / 'feature_distributions_clinic_vs_mimic.png'}")

    # Figura 2: Violin plots para las features mas problematicas (KS p < 0.001)
    sig_feats = stats_df[stats_df["KS_p"] < 0.001]["feature"].tolist()
    print(f"\nFeatures con shift significativo (p<0.001): {len(sig_feats)}")
    print(f"  {', '.join(sig_feats)}")

    if len(sig_feats) > 0:
        ncols2 = 3
        nrows2 = (len(sig_feats) + ncols2 - 1) // ncols2
        fig2, axes2 = plt.subplots(nrows2, ncols2, figsize=(15, 4 * nrows2))
        axes2 = axes2.flatten()

        for i, feat in enumerate(sig_feats):
            ax = axes2[i]
            c = X_clinic[feat].dropna()
            m = X_mimic[feat].dropna()
            # Limitar a percentiles para violin
            if len(c) > 0:
                c_low, c_high = np.percentile(c, [1, 99])
                c = c[(c >= c_low) & (c <= c_high)]
            if len(m) > 0:
                m_low, m_high = np.percentile(m, [1, 99])
                m = m[(m >= m_low) & (m <= m_high)]

            parts = ax.violinplot([c, m], positions=[1, 2], showmeans=True, showmedians=True)
            parts['bodies'][0].set_facecolor('blue')
            parts['bodies'][1].set_facecolor('orange')
            ax.set_xticks([1, 2])
            ax.set_xticklabels(["Clinic", "MIMIC"])
            ax.set_title(feat, fontsize=10)
            ax.tick_params(labelsize=7)

        for j in range(i + 1, len(axes2)):
            axes2[j].axis("off")

        plt.tight_layout()
        fig2.savefig(OUT_DIR / "feature_violins_shifted.png", dpi=150)
        print(f"Violin plot guardado en: {OUT_DIR / 'feature_violins_shifted.png'}")

    # Figura 3: Boxplots normalizados (z-score dentro de cada dataset)
    print("\nGenerando boxplots normalizados...")
    fig3, axes3 = plt.subplots(nrows, ncols, figsize=(15, 4 * nrows))
    axes3 = axes3.flatten()

    for i, feat in enumerate(ALL_FEATS):
        ax = axes3[i]
        c = X_clinic[feat].dropna()
        m = X_mimic[feat].dropna()
        if len(c) > 0 and len(m) > 0:
            c_z = (c - c.mean()) / c.std()
            m_z = (m - m.mean()) / m.std()
            # Clip a ±3 sigma para visualizacion
            c_z = c_z.clip(-3, 3)
            m_z = m_z.clip(-3, 3)
            ax.boxplot([c_z, m_z], labels=["Clinic", "MIMIC"], widths=0.6)
            ax.set_title(feat, fontsize=10)
            ax.tick_params(labelsize=7)
        else:
            ax.text(0.5, 0.5, "Sin datos", ha="center", va="center", transform=ax.transAxes)
            ax.set_title(feat, fontsize=10)

    for j in range(i + 1, len(axes3)):
        axes3[j].axis("off")

    plt.tight_layout()
    fig3.savefig(OUT_DIR / "feature_boxplots_zscore.png", dpi=150)
    print(f"Boxplot guardado en: {OUT_DIR / 'feature_boxplots_zscore.png'}")

    print("\nDone.")


if __name__ == "__main__":
    main()
