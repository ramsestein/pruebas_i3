"""
Evaluación del Escalón 2.

Métricas:
- Regresión: MAE, RMSE (global y por tramos del ingreso)
- Calibración: cobertura de intervalos al 80%/90%
- Comparación eICU vs MIMIC (transferencia)
- Comparación vs baseline Escalón 1
"""

import os
import numpy as np
import pandas as pd

from src.stage2.config import TRAJECTORY_DIR, OUTPUT_DIR


def _load_trajectories(cohort_name: str) -> pd.DataFrame:
    path = os.path.join(TRAJECTORY_DIR, f"trajectories_{cohort_name}.parquet")
    if not os.path.exists(path):
        print(f"  [!] No se encontró {path}")
        return None
    return pd.read_parquet(path)


def _mae(y_true, y_pred):
    return np.mean(np.abs(y_true - y_pred))


def _rmse(y_true, y_pred):
    return np.sqrt(np.mean((y_true - y_pred) ** 2))


def _interval_coverage(y_true, lower, upper):
    """Fracción de y_true dentro de [lower, upper]."""
    return np.mean((y_true >= lower) & (y_true <= upper))


def evaluate_cohort(df: pd.DataFrame, name: str) -> dict:
    """Evalúa un cohorte completo."""
    if df is None or len(df) == 0:
        return None

    y_true = df["time_remaining_true"].values
    y_pred_median = df["median_R_t"].values

    # Métricas en horas
    mae = _mae(y_true, y_pred_median)
    rmse_val = _rmse(y_true, y_pred_median)

    # Métricas en log-space (más comparables entre escalas)
    y_true_log = np.log(np.maximum(y_true, 1e-6))
    y_pred_log = df["mu"].values  # mu ya está en log-space
    mae_log = _mae(y_true_log, y_pred_log)

    # Predicción naive: siempre la media (en log-space)
    baseline_naive_log = _mae(y_true_log, np.full_like(y_true_log, y_true_log.mean()))
    r2_log = 1 - mae_log / (baseline_naive_log + 1e-6)

    # Calibración de intervalos
    cov_80 = _interval_coverage(
        y_true, df["interval_80_lower"].values, df["interval_80_upper"].values
    )
    cov_90 = _interval_coverage(
        y_true, df["interval_90_lower"].values, df["interval_90_upper"].values
    )

    # Métricas por tramo del ingreso (basado en % de duración total)
    total_dur = df["total_duration"].values
    frac_elapsed = df["landmark_t"].values / (total_dur + 1e-6)

    metrics_by_bin = {}
    for bin_name, (lo, hi) in [
        ("inicio (0-25%)", (0.0, 0.25)),
        ("medio-temprano (25-50%)", (0.25, 0.50)),
        ("medio-tardío (50-75%)", (0.50, 0.75)),
        ("pre-extubación (75-100%)", (0.75, 1.0)),
    ]:
        mask = (frac_elapsed >= lo) & (frac_elapsed < hi)
        if mask.sum() > 10:
            metrics_by_bin[f"MAE_{bin_name}"] = _mae(y_true[mask], y_pred_median[mask])
            metrics_by_bin[f"RMSE_{bin_name}"] = _rmse(y_true[mask], y_pred_median[mask])
            metrics_by_bin[f"N_{bin_name}"] = mask.sum()

    results = {
        "cohort": name,
        "n_patients": df["patient_id"].nunique(),
        "n_landmarks": len(df),
        "MAE_h": round(mae, 2),
        "RMSE_h": round(rmse_val, 2),
        "MAE_log": round(mae_log, 4),
        "R2_log_vs_naive": round(r2_log, 4),
        "coverage_80": round(cov_80, 4),
        "coverage_90": round(cov_90, 4),
        **{k: round(v, 2) if "N_" not in k else v for k, v in metrics_by_bin.items()},
    }

    return results


def evaluate():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=" * 60)
    print("Evaluación Escalón 2")
    print("=" * 60)

    df_eicu = _load_trajectories("eicu_test")
    df_mimic = _load_trajectories("mimic")

    results = {}

    # eICU (interno)
    r_eicu = evaluate_cohort(df_eicu, "eICU (test interno)")
    if r_eicu:
        results["eicu"] = r_eicu
        print(f"\n─── eICU (test interno) ───")
        for k, v in r_eicu.items():
            print(f"  {k}: {v}")

    # MIMIC (externo)
    r_mimic = evaluate_cohort(df_mimic, "MIMIC (test externo)")
    if r_mimic:
        results["mimic"] = r_mimic
        print(f"\n─── MIMIC (test externo) ───")
        for k, v in r_mimic.items():
            print(f"  {k}: {v}")

    # Comparación de transferencia
    if r_eicu and r_mimic:
        print(f"\n─── Transferencia eICU → MIMIC ───")
        mae_drop = (r_mimic["MAE_h"] - r_eicu["MAE_h"]) / (r_eicu["MAE_h"] + 1e-6)
        rmse_drop = (r_mimic["RMSE_h"] - r_eicu["RMSE_h"]) / (r_eicu["RMSE_h"] + 1e-6)
        print(f"  Δ MAE:  +{mae_drop*100:.1f}%")
        print(f"  Δ RMSE: +{rmse_drop*100:.1f}%")
        print(f"  Cobertura 80%: {r_eicu['coverage_80']:.3f} → {r_mimic['coverage_80']:.3f}")
        print(f"  Cobertura 90%: {r_eicu['coverage_90']:.3f} → {r_mimic['coverage_90']:.3f}")

        results["transfer"] = {
            "mae_drop_pct": round(mae_drop * 100, 1),
            "rmse_drop_pct": round(rmse_drop * 100, 1),
        }

    # Guardar resultados
    results_flat = {}
    for k, v in results.items():
        if v:
            for kk, vv in v.items():
                results_flat[f"{k}/{kk}"] = vv

    df_results = pd.DataFrame([results_flat])
    path = os.path.join(OUTPUT_DIR, "metrics_summary.csv")
    df_results.to_csv(path, index=False)
    print(f"\nResultados guardados en {path}")

    return results


if __name__ == "__main__":
    evaluate()
