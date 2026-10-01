"""
Análisis descriptivo comparativo: ventanas label=0 (extubado) vs label=1 (intubado).
Calcula medias por ventana y compara poblaciones con tests estadísticos.
"""
import os
import json
import numpy as np
import pandas as pd
from scipy import stats

# Cargar índice
with open("datasets/clinic_vitals/windows_index.json") as f:
    index = json.load(f)

windows_meta = index["windows"]
print(f"Total ventanas en índice: {len(windows_meta)}")
print(f"  Label 0 (extubado): {index['windows_extubado']}")
print(f"  Label 1 (intubado): {index['windows_intubado']}")

win_dir = "datasets/clinic_vitals/windows_10min"

# Señales de interés para el análisis
signals_of_interest = [
    "Intellivue/PEEP_CMH2O",
    "Intellivue/PIP_CMH2O",
    "Intellivue/ART_MEAN",
    "Intellivue/CO2",
    "Intellivue/AWAY_CO2_ET",
    "Intellivue/TV_EXP",
    "Intellivue/TV_INSP",
    "Intellivue/MV_EXP",
    "Intellivue/VENT_RR",
    "Intellivue/FIO2",
    "Intellivue/HR",
    "Intellivue/PLETH_SAT_O2",
    "Intellivue/ART_SYS",
    "Intellivue/ART_DIA",
    "Intellivue/RR",
]

# Recolectar medias por ventana
records = []
errors = 0
for w in windows_meta:
    fpath = os.path.join(win_dir, w["window_file"])
    if not os.path.exists(fpath):
        errors += 1
        continue
    try:
        df = pd.read_parquet(fpath)
    except Exception:
        errors += 1
        continue

    row = {"label": w["label"], "window_file": w["window_file"], "box": w["box"]}
    for sig in signals_of_interest:
        if sig in df.columns:
            vals = df[sig].dropna()
            if len(vals) > 0:
                row[sig] = float(vals.mean())
            else:
                row[sig] = np.nan
        else:
            row[sig] = np.nan
    records.append(row)

print(f"\nVentanas procesadas: {len(records)}, errores: {errors}")

df_all = pd.DataFrame(records)

# Separar por label
df_0 = df_all[df_all["label"] == 0]
df_1 = df_all[df_all["label"] == 1]

print(f"\n{'='*80}")
print(f"{'SEÑAL':<30} {'Label 0 (extubado)':>25} {'Label 1 (intubado)':>25}")
print(f"{'':<30} {'mean ± std (n)':>25} {'mean ± std (n)':>25}")
print(f"{'='*80}")

results = []
for sig in signals_of_interest:
    v0 = df_0[sig].dropna()
    v1 = df_1[sig].dropna()

    if len(v0) < 2 or len(v1) < 2:
        continue

    m0, s0 = v0.mean(), v0.std()
    m1, s1 = v1.mean(), v1.std()

    # Test estadístico: t-test si normales, Mann-Whitney si no
    # Usamos Shapiro-Wilk para normalidad (solo en muestra < 5000)
    normal0 = True
    normal1 = True
    if len(v0) < 5000:
        _, p_norm0 = stats.shapiro(v0.sample(min(1000, len(v0))))
        normal0 = p_norm0 > 0.05
    if len(v1) < 5000:
        _, p_norm1 = stats.shapiro(v1.sample(min(1000, len(v1))))
        normal1 = p_norm1 > 0.05

    if normal0 and normal1:
        stat, p_val = stats.ttest_ind(v0, v1, equal_var=False)
        test_name = "t-test Welch"
    else:
        stat, p_val = stats.mannwhitneyu(v0, v1, alternative="two-sided")
        test_name = "Mann-Whitney U"

    # Tamaño del efecto (Cohen's d o r)
    if normal0 and normal1:
        # Cohen's d
        pooled_std = np.sqrt((s0**2 + s1**2) / 2)
        effect_size = (m1 - m0) / pooled_std if pooled_std > 0 else 0
        effect_name = "Cohen's d"
    else:
        # r = Z / sqrt(N)
        n_total = len(v0) + len(v1)
        z = (stat - len(v0) * len(v1) / 2) / np.sqrt(len(v0) * len(v1) * (n_total + 1) / 12)
        effect_size = z / np.sqrt(n_total)
        effect_name = "r"

    sig_stars = ""
    if p_val < 0.001:
        sig_stars = "***"
    elif p_val < 0.01:
        sig_stars = "**"
    elif p_val < 0.05:
        sig_stars = "*"

    # Nombre corto para la señal
    short_name = sig.replace("Intellivue/", "")

    print(f"{short_name:<30} {m0:>8.2f} ± {s0:>6.2f} ({len(v0):>4d})  {m1:>8.2f} ± {s1:>6.2f} ({len(v1):>4d})  p={p_val:.4f} {sig_stars}")

    results.append({
        "signal": short_name,
        "signal_full": sig,
        "n_0": len(v0),
        "mean_0": round(m0, 2),
        "std_0": round(s0, 2),
        "n_1": len(v1),
        "mean_1": round(m1, 2),
        "std_1": round(s1, 2),
        "test": test_name,
        "statistic": round(stat, 4),
        "p_value": round(p_val, 6),
        "significant": bool(p_val < 0.05),
        "effect_size": round(effect_size, 4),
        "effect_name": effect_name,
    })

print(f"\n{'='*80}")
print("\nNOTAS:")
print("  * p<0.05, ** p<0.01, *** p<0.001")
print("  t-test Welch para datos normales, Mann-Whitney U para no normales")
print("  Cohen's d: 0.2=pequeño, 0.5=medio, 0.8=grande")
print("  r: 0.1=pequeño, 0.3=medio, 0.5=grande")

# Guardar resultados
out = {
    "total_windows_0": len(df_0),
    "total_windows_1": len(df_1),
    "results": results,
}
with open("datasets/clinic_vitals/descriptive_analysis.json", "w") as f:
    json.dump(out, f, indent=2, ensure_ascii=False)
print(f"\nResultados guardados en: datasets/clinic_vitals/descriptive_analysis.json")
