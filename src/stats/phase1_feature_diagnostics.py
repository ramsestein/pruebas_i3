"""
Fase 1: Diagnostico del espacio de features — clinic vs MIMIC.
- Distribuciones por feature (mediana, IQR, KS-test)
- Matriz de correlacion en clinic
- Detectar grupos naturales por correlacion
"""
import json
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.stats import ks_2samp

warnings.filterwarnings("ignore")

# ── Paths ─────────────────────────────────────────────────────────────────────
CLINIC_WIN = Path("datasets/clinic_vitals/windows_10min")
CLINIC_IDX = Path("datasets/clinic_vitals/windows_index.json")
MIMIC_WIN  = Path("datasets/mimic3wdb/windows_10min")
MIMIC_IDX  = Path("datasets/mimic3wdb/windows_index.json")
OUT_DIR    = Path("results/mimic_evaluation")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Config ─────────────────────────────────────────────────────────────────
CLINIC_SIG = {
    "PEEP":    "Intellivue/PEEP_CMH2O",
    "PIP":     "Intellivue/PIP_CMH2O",
    "ART_M":   "Intellivue/ART_MEAN",
    "CO2":     "Intellivue/CO2",
    "ETCO2":   "Intellivue/AWAY_CO2_ET",
    "TV_EXP":  "Intellivue/TV_EXP",
    "TV_INSP": "Intellivue/TV_INSP",
    "MV":      "Intellivue/MV_EXP",
    "RR_V":    "Intellivue/VENT_RR",
    "FiO2":    "Intellivue/FIO2",
    "HR":      "Intellivue/HR",
    "SpO2":    "Intellivue/PLETH_SAT_O2",
    "ABP_S":   "Intellivue/ART_SYS",
    "ABP_D":   "Intellivue/ART_DIA",
    "RR":      "Intellivue/RR",
}

MIMIC_SIG = {
    "PEEP":    "PEEP",
    "PIP":     "PIP",
    "ART_M":   "ABPMean",  # proxy
    "CO2":     None,
    "ETCO2":   None,
    "TV_EXP":  "TV",
    "TV_INSP": "TV",
    "MV":      "MV",
    "RR_V":    "RR_V",
    "FiO2":    "FiO2",
    "HR":      "HR",
    "SpO2":    "SpO2",
    "ABP_S":   "ABP_S",
    "ABP_D":   "ABP_D",
    "RR":      "RESP",
}


def extract_mean_features(windows_meta, win_dir, sig_map, max_n=3000):
    """Extrae la media de cada señal por ventana."""
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
        row = {}
        for name, col in sig_map.items():
            if col is None or col not in df.columns:
                row[name] = np.nan
            else:
                s = df[col].dropna()
                row[name] = float(s.mean()) if len(s) > 0 else np.nan
        records.append(row)
    return pd.DataFrame(records)


def main():
    print("=" * 60)
    print("FASE 1: DIAGNOSTICO DE FEATURES — CLINIC vs MIMIC")
    print("=" * 60)
    
    # Cargar indices
    with open(CLINIC_IDX) as f:
        cidx = json.load(f)["windows"]
    with open(MIMIC_IDX) as f:
        midx = json.load(f)["windows"]
    
    print(f"\nCargando clinic: {len(cidx):,} ventanas")
    print(f"Cargando MIMIC:  {len(midx):,} ventanas")
    
    df_clinic = extract_mean_features(cidx, CLINIC_WIN, CLINIC_SIG, max_n=3000)
    df_mimic  = extract_mean_features(midx, MIMIC_WIN, MIMIC_SIG, max_n=3000)
    
    print(f"  Clinic sample: {len(df_clinic):,} ventanas procesadas")
    print(f"  MIMIC sample:  {len(df_mimic):,} ventanas procesadas")
    
    # ── Comparacion de distribuciones ────────────────────────────────────
    print(f"\n{'='*60}")
    print("COMPARACION DE DISTRIBUCIONES (Clinic vs MIMIC)")
    print(f"{'='*60}")
    print(f"{'Feature':<10} {'Clinic med':>10} {'MIMIC med':>10} {'Clinic IQR':>12} {'MIMIC IQR':>12} {'KS p-val':>10}")
    print(f"{'-'*68}")
    
    shared_cols = [c for c in df_clinic.columns if c in df_mimic.columns]
    ks_results = {}
    for col in shared_cols:
        c_vals = df_clinic[col].dropna()
        m_vals = df_mimic[col].dropna()
        if len(c_vals) < 10 or len(m_vals) < 10:
            continue
        ks_stat, ks_p = ks_2samp(c_vals, m_vals)
        ks_results[col] = ks_p
        print(f"{col:<10} {c_vals.median():>10.2f} {m_vals.median():>10.2f} "
              f"{(c_vals.quantile(0.75)-c_vals.quantile(0.25)):>12.2f} "
              f"{(m_vals.quantile(0.75)-m_vals.quantile(0.25)):>12.2f} "
              f"{ks_p:>10.2e}")
    
    # ── Matriz de correlacion clinic ───────────────────────────────────
    print(f"\n{'='*60}")
    print("MATRIZ DE CORRELACION — CLINIC (abs >= 0.5)")
    print(f"{'='*60}")
    corr = df_clinic.corr()
    # Mostrar pares con alta correlacion
    high_corr = []
    for i in range(len(corr.columns)):
        for j in range(i+1, len(corr.columns)):
            c = abs(corr.iloc[i, j])
            if c >= 0.5:
                high_corr.append((corr.columns[i], corr.columns[j], c))
    high_corr.sort(key=lambda x: -x[2])
    for a, b, c in high_corr:
        print(f"  {a:<10} vs {b:<10}: {c:.3f}")
    
    # ── Detectar grupos naturales ──────────────────────────────────────
    print(f"\n{'='*60}")
    print("GRUPOS NATURALES POR CORRELACION")
    print(f"{'='*60}")
    
    # Grupos tentativos basados en correlacion y significado clinico
    groups = {
        "VENTILATORIO": ["PEEP", "PIP", "TV_EXP", "TV_INSP", "MV", "RR_V"],
        "CARDIOVASC":   ["HR", "ABP_S", "ABP_D", "ART_M"],
        "OXIGENACION":  ["SpO2", "FiO2", "CO2", "ETCO2"],
        "RESPIRATORIO": ["RR", "RR_V"],
    }
    
    for gname, gcols in groups.items():
        available = [c for c in gcols if c in df_clinic.columns]
        if len(available) < 2:
            continue
        sub_corr = df_clinic[available].corr().abs()
        # Media de correlaciones off-diagonal
        vals = []
        for i in range(len(available)):
            for j in range(i+1, len(available)):
                vals.append(sub_corr.iloc[i, j])
        avg_corr = np.mean(vals) if vals else 0
        print(f"  {gname:<15} ({', '.join(available)})")
        print(f"    Correlacion media intra-grupo: {avg_corr:.3f}")
    
    # ── Features con equivalente fiable en MIMIC ────────────────────────
    print(f"\n{'='*60}")
    print("EQUIVALENCIA CLINIC -> MIMIC")
    print(f"{'='*60}")
    print(f"{'Feature':<12} {'Clinic disp':>10} {'MIMIC disp':>10} {'KS p-val':>12} {'Fiable?':>8}")
    print(f"{'-'*56}")
    for col in shared_cols:
        c_disp = 100 * df_clinic[col].notna().mean()
        m_disp = 100 * df_mimic[col].notna().mean()
        ks_p = ks_results.get(col, 1.0)
        # Fiable si KS no rechaza (p > 0.01) y disponibilidad >= 50% en ambos
        fiable = "SI" if ks_p > 0.01 and c_disp >= 50 and m_disp >= 50 else "NO"
        print(f"{col:<12} {c_disp:>10.1f}% {m_disp:>10.1f}% {ks_p:>12.2e} {fiable:>8}")
    
    print(f"\n{'='*60}")
    print("DIAGNOSTICO COMPLETADO")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
