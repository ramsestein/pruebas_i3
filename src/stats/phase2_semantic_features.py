"""
Fase 2: Feature engineering semantico + Fase 3: Kernel custom por grupos.
Crea features compuestas con significado clinico y entrena SVM con kernel multi-grupo.
"""
import os
import json
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier
from sklearn.model_selection import train_test_split, GridSearchCV, StratifiedKFold
from sklearn.metrics import accuracy_score, roc_auc_score, f1_score

warnings.filterwarnings("ignore")

# ── Paths ─────────────────────────────────────────────────────────────────────
CLINIC_WIN = Path("datasets/clinic_vitals/windows_10min")
CLINIC_IDX = Path("datasets/clinic_vitals/windows_index.json")
MIMIC_WIN  = Path("datasets/mimic3wdb/windows_10min")
MIMIC_IDX  = Path("datasets/mimic3wdb/windows_index.json")
OUT_DIR    = Path("results/models")
OUT_DIR.mkdir(parents=True, exist_ok=True)

RANDOM_STATE = 42

# ── Grupos fisiologicos ─────────────────────────────────────────────────────
# Nota: CVP no disponible en clinic; HR solo 0.5% en clinic
GROUPS = {
    "VENT":   ["PEEP", "PIP", "TV", "MV", "RR_V"],
    "CARDIO": ["ABP_S", "ABP_D", "ABP_M"],
    "OXIGEN": ["SpO2", "FiO2", "SAFI"],
    "META":   ["Compliance", "Drive_pressure", "PIP_PEEP_ratio", "MV_TV_ratio"],
}

# Mapeo clinic -> columnas parquet
CLINIC_MAP = {
    "PEEP":    "Intellivue/PEEP_CMH2O",
    "PIP":     "Intellivue/PIP_CMH2O",
    "TV":      "Intellivue/TV_INSP",  # usar TV_INSP como proxy
    "MV":      "Intellivue/MV_EXP",
    "RR_V":    "Intellivue/VENT_RR",
    "HR":      "Intellivue/HR",
    "ABP_S":   "Intellivue/ART_SYS",
    "ABP_D":   "Intellivue/ART_DIA",
    "ABP_M":   "Intellivue/ART_MEAN",
    "SpO2":    "Intellivue/PLETH_SAT_O2",
    "FiO2":    "Intellivue/FIO2",
    "CVP":     "Intellivue/CVP",  # puede no existir
}

# Mapeo MIMIC -> columnas parquet
MIMIC_MAP = {
    "PEEP":    "PEEP",
    "PIP":     "PIP",
    "TV":      "TV",
    "MV":      "MV",
    "RR_V":    "RR_V",
    "HR":      "HR",
    "ABP_S":   "ABP_S",
    "ABP_D":   "ABP_D",
    "ABP_M":   "ABPMean",
    "SpO2":    "SpO2",
    "FiO2":    "FiO2",
    "CVP":     "CVP",
}


def extract_semantic_features(df_window, col_map):
    """Extrae features semanticas de una ventana."""
    vals = {}
    for name, col in col_map.items():
        if col is not None and col in df_window.columns:
            s = df_window[col].dropna()
            vals[name] = float(s.mean()) if len(s) > 0 else np.nan
        else:
            vals[name] = np.nan
    
    # Features compuestas con significado clinico
    feats = {}
    
    # Basicas (HR y CVP no disponibles en clinic)
    for k in ["PEEP", "PIP", "TV", "MV", "RR_V", "ABP_S", "ABP_D", "ABP_M", "SpO2", "FiO2"]:
        feats[k] = vals.get(k, np.nan)
    
    # SAFI
    fio2 = vals.get("FiO2", np.nan)
    spo2 = vals.get("SpO2", np.nan)
    if pd.notna(fio2) and pd.notna(spo2) and fio2 > 0:
        feats["SAFI"] = spo2 / (fio2 / 100.0)
    else:
        feats["SAFI"] = np.nan
    
    # Compliance = TV / (PIP - PEEP)  [mL/cmH2O]
    peep = vals.get("PEEP", np.nan)
    pip  = vals.get("PIP", np.nan)
    tv   = vals.get("TV", np.nan)
    if pd.notna(peep) and pd.notna(pip) and pip > peep and pd.notna(tv) and tv > 0:
        feats["Compliance"] = tv / (pip - peep)
    else:
        feats["Compliance"] = np.nan
    
    # Drive pressure = PIP - PEEP
    if pd.notna(peep) and pd.notna(pip):
        feats["Drive_pressure"] = pip - peep
    else:
        feats["Drive_pressure"] = np.nan
    
    # PIP/PEEP ratio
    if pd.notna(peep) and peep > 0 and pd.notna(pip):
        feats["PIP_PEEP_ratio"] = pip / peep
    else:
        feats["PIP_PEEP_ratio"] = np.nan
    
    # MV/TV ratio (frecuencia respiratoria efectiva)
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
    
    # Feature matrix: todas las features semanticas
    all_feats = []
    for gname, gcols in GROUPS.items():
        all_feats.extend(gcols)
    all_feats = list(dict.fromkeys(all_feats))  # dedup mantener orden
    
    # Clamp valores extremos
    X = df[all_feats].copy()
    for c in ["Compliance", "SAFI", "PIP_PEEP_ratio", "MV_TV_ratio"]:
        if c in X.columns:
            X[c] = X[c].clip(lower=0, upper=1000)
    
    return X, y, all_feats


class GroupRBFKernel:
    """
    Kernel RBF ponderado por grupos fisiologicos.
    K(x,y) = sum_g(w_g * exp(-gamma_g * ||x_g - y_g||^2))
    """
    def __init__(self, groups, gammas, weights):
        self.groups = groups  # dict: gname -> list of feature indices
        self.gammas = gammas  # dict: gname -> gamma
        self.weights = weights  # dict: gname -> weight
    
    def __call__(self, X, Y):
        # X, Y are numpy arrays
        K = np.zeros((X.shape[0], Y.shape[0]))
        for gname, idxs in self.groups.items():
            gamma = self.gammas[gname]
            w = self.weights[gname]
            Xg = X[:, idxs]
            Yg = Y[:, idxs]
            # pairwise squared Euclidean distance
            dist = np.sum((Xg[:, None, :] - Yg[None, :, :]) ** 2, axis=2)
            K += w * np.exp(-gamma * dist)
        return K


def build_group_indices(feature_names, groups):
    """Mapea cada grupo a indices de columnas en X."""
    feat_to_idx = {f: i for i, f in enumerate(feature_names)}
    group_idx = {}
    for gname, gcols in groups.items():
        idxs = [feat_to_idx[c] for c in gcols if c in feat_to_idx]
        if idxs:
            group_idx[gname] = idxs
    print(f"  build_group_indices: {len(feature_names)} features, max_idx={max([max(v) for v in group_idx.values()]) if group_idx else 'N/A'}")
    return group_idx


def train_group_svm(X, y, group_idx, random_state=42):
    """Entrena SVM con kernel custom por grupos usando GridSearchCV."""
    from sklearn.model_selection import StratifiedKFold
    
    print(f"  train_group_svm: X.shape={X.shape}")
    
    # Valores por defecto de gammas (1 / (2 * varianza media del grupo))
    gammas = {}
    for gname, idxs in group_idx.items():
        max_idx = max(idxs)
        if max_idx >= X.shape[1]:
            print(f"    WARNING: grupo {gname} tiene indice {max_idx} pero X solo tiene {X.shape[1]} columnas. Saltando.")
            continue
        Xg = X[:, idxs]
        # imputar temporalmente para calcular varianza
        from sklearn.impute import SimpleImputer
        imp = SimpleImputer(strategy="median")
        Xg_i = imp.fit_transform(Xg)
        var = np.mean(np.var(Xg_i, axis=0))
        gammas[gname] = 1.0 / (2.0 * max(var, 1e-5))
    
    # Pesos iguales por defecto
    weights = {g: 1.0 for g in group_idx}
    
    kernel = GroupRBFKernel(group_idx, gammas, weights)
    
    # SVM con kernel precomputed
    # Necesitamos calcular la matriz de kernel completa
    print("  Calculando matriz de kernel custom...")
    K = kernel(X, X)
    
    # CV para C
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=random_state)
    
    best_C = 1.0
    best_auc = 0.0
    for C in [0.1, 1, 10, 100, 200, 500]:
        svm = SVC(C=C, kernel="precomputed", probability=True,
                  class_weight="balanced", random_state=random_state)
        from sklearn.model_selection import cross_val_score
        scores = cross_val_score(svm, K, y, cv=cv, scoring="roc_auc")
        auc_mean = scores.mean()
        print(f"    C={C:>6}  CV AUC={auc_mean:.4f}")
        if auc_mean > best_auc:
            best_auc = auc_mean
            best_C = C
    
    print(f"  Mejor C={best_C} (CV AUC={best_auc:.4f})")
    
    # Entrenar modelo final
    svm_final = SVC(C=best_C, kernel="precomputed", probability=True,
                    class_weight="balanced", random_state=random_state)
    svm_final.fit(K, y)
    
    return svm_final, kernel, best_C, best_auc, gammas, weights


def main():
    print("=" * 60)
    print("FASE 2+3: FEATURES SEMANTICAS + KERNEL CUSTOM")
    print("=" * 60)
    
    # Cargar datos
    with open(CLINIC_IDX) as f:
        cidx = json.load(f)["windows"]
    with open(MIMIC_IDX) as f:
        midx = json.load(f)["windows"]
    
    print(f"\nClinic: {len(cidx):,} ventanas")
    print(f"MIMIC:  {len(midx):,} ventanas")
    
    # Build datasets
    print("\nExtrayendo features semanticas clinic...")
    X_clinic, y_clinic, feat_names = build_dataset(cidx, CLINIC_WIN, CLINIC_MAP, max_n=5000)
    print(f"  {len(y_clinic):,} ventanas  |  extub={sum(y_clinic)}  intub={len(y_clinic)-sum(y_clinic)}")
    print(f"  Features: {feat_names}")
    
    print("\nExtrayendo features semanticas MIMIC...")
    X_mimic, y_mimic, _ = build_dataset(midx, MIMIC_WIN, MIMIC_MAP, max_n=12000)
    print(f"  {len(y_mimic):,} ventanas  |  extub={sum(y_mimic)}  intub={len(y_mimic)-sum(y_mimic)}")
    
    # Imputar NaN
    imp = SimpleImputer(strategy="median")
    X_clinic_i = imp.fit_transform(X_clinic)
    X_mimic_i = imp.transform(X_mimic)
    
    # Escalar
    scaler = StandardScaler()
    X_clinic_s = scaler.fit_transform(X_clinic_i)
    X_mimic_s = scaler.transform(X_mimic_i)
    
    # ── Entrenar baseline SVM RBF (kernel global) ─────────────────────────
    print(f"\n{'='*60}")
    print("BASELINE: SVM RBF kernel global")
    print(f"{'='*60}")
    
    X_tr, X_te, y_tr, y_te = train_test_split(
        X_clinic_s, y_clinic, test_size=0.2, stratify=y_clinic, random_state=RANDOM_STATE
    )
    
    svm_base = Pipeline([
        ("clf", SVC(kernel="rbf", C=200, gamma=0.2, probability=True,
                    class_weight="balanced", random_state=RANDOM_STATE)),
    ])
    svm_base.fit(X_tr, y_tr)
    y_prob_base = svm_base.predict_proba(X_te)[:, 1]
    auc_base = roc_auc_score(y_te, y_prob_base)
    acc_base = accuracy_score(y_te, (y_prob_base >= 0.5).astype(int))
    f1_base = f1_score(y_te, (y_prob_base >= 0.5).astype(int))
    print(f"  Test: Acc={acc_base:.4f}  AUC={auc_base:.4f}  F1={f1_base:.4f}")
    
    # ── Entrenar SVM con kernel custom por grupos ────────────────────────
    print(f"\n{'='*60}")
    print("CUSTOM: SVM con kernel multi-grupo")
    print(f"{'='*60}")
    
    group_idx = build_group_indices(feat_names, GROUPS)
    print(f"  Grupos: {list(group_idx.keys())}")
    for g, idxs in group_idx.items():
        print(f"    {g}: {[feat_names[i] for i in idxs]}")
    
    svm_custom, kernel_obj, best_C, cv_auc, gammas, weights = train_group_svm(
        X_clinic_s, y_clinic, group_idx, random_state=RANDOM_STATE
    )
    
    # Evaluar custom en test clinic
    K_te = kernel_obj(X_te, X_tr)  # Nota: SVM con kernel precomputed espera K(test, train)
    # SVC con precomputed espera K(X_test, X_train)
    # Re-entrenar con todo train para evaluar test clinic
    K_tr = kernel_obj(X_tr, X_tr)
    svm_eval = SVC(C=best_C, kernel="precomputed", probability=True,
                   class_weight="balanced", random_state=RANDOM_STATE)
    svm_eval.fit(K_tr, y_tr)
    y_prob_custom = svm_eval.predict_proba(K_te)[:, 1]
    auc_custom = roc_auc_score(y_te, y_prob_custom)
    acc_custom = accuracy_score(y_te, (y_prob_custom >= 0.5).astype(int))
    f1_custom = f1_score(y_te, (y_prob_custom >= 0.5).astype(int))
    print(f"  Test: Acc={acc_custom:.4f}  AUC={auc_custom:.4f}  F1={f1_custom:.4f}")
    
    # ── Evaluar ambos en MIMIC ─────────────────────────────────────────
    print(f"\n{'='*60}")
    print("EVALUACION EN MIMIC")
    print(f"{'='*60}")
    
    # Baseline en MIMIC
    y_prob_mimic_base = svm_base.predict_proba(X_mimic_s)[:, 1]
    auc_m_base = roc_auc_score(y_mimic, y_prob_mimic_base)
    acc_m_base = accuracy_score(y_mimic, (y_prob_mimic_base >= 0.5).astype(int))
    f1_m_base = f1_score(y_mimic, (y_prob_mimic_base >= 0.5).astype(int))
    print(f"  Baseline SVM:  Acc={acc_m_base:.4f}  AUC={auc_m_base:.4f}  F1={f1_m_base:.4f}")
    
    # Custom en MIMIC: reentrenar con TODO clinic para evaluar MIMIC
    K_full = kernel_obj(X_clinic_s, X_clinic_s)
    svm_full = SVC(C=best_C, kernel="precomputed", probability=True,
                   class_weight="balanced", random_state=RANDOM_STATE)
    svm_full.fit(K_full, y_clinic)
    K_mimic = kernel_obj(X_mimic_s, X_clinic_s)  # K(MIMIC, clinic_full)
    y_prob_mimic_custom = svm_full.predict_proba(K_mimic)[:, 1]
    auc_m_custom = roc_auc_score(y_mimic, y_prob_mimic_custom)
    acc_m_custom = accuracy_score(y_mimic, (y_prob_mimic_custom >= 0.5).astype(int))
    f1_m_custom = f1_score(y_mimic, (y_prob_mimic_custom >= 0.5).astype(int))
    print(f"  Custom SVM:    Acc={acc_m_custom:.4f}  AUC={auc_m_custom:.4f}  F1={f1_m_custom:.4f}")
    
    # ── Guardar resultados ─────────────────────────────────────────────
    results = {
        "evaluated_at": datetime.now().isoformat(),
        "features": feat_names,
        "groups": {k: [feat_names[i] for i in v] for k, v in group_idx.items()},
        "gammas": gammas,
        "weights": weights,
        "clinic": {
            "baseline": {"acc": acc_base, "auc": auc_base, "f1": f1_base},
            "custom": {"acc": acc_custom, "auc": auc_custom, "f1": f1_custom, "C": best_C, "cv_auc": cv_auc},
        },
        "mimic": {
            "baseline": {"acc": acc_m_base, "auc": auc_m_base, "f1": f1_m_base},
            "custom": {"acc": acc_m_custom, "auc": auc_m_custom, "f1": f1_m_custom},
        },
    }
    
    with open(OUT_DIR / "semantic_kernel_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nResultados guardados en: {OUT_DIR / 'semantic_kernel_results.json'}")
    
    # ── Resumen ──────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("RESUMEN COMPARATIVO")
    print(f"{'='*60}")
    print(f"  {'Dataset':<10} {'Modelo':<12} {'Acc':>8} {'AUC':>8} {'F1':>8}")
    print(f"  {'-'*42}")
    print(f"  {'Clinic':<10} {'Baseline':<12} {acc_base:>8.4f} {auc_base:>8.4f} {f1_base:>8.4f}")
    print(f"  {'Clinic':<10} {'Custom':<12} {acc_custom:>8.4f} {auc_custom:>8.4f} {f1_custom:>8.4f}")
    print(f"  {'MIMIC':<10} {'Baseline':<12} {acc_m_base:>8.4f} {auc_m_base:>8.4f} {f1_m_base:>8.4f}")
    print(f"  {'MIMIC':<10} {'Custom':<12} {acc_m_custom:>8.4f} {auc_m_custom:>8.4f} {f1_m_custom:>8.4f}")


if __name__ == "__main__":
    main()
