"""
R_base(t): tiempo restante esperado condicionado al tiempo transcurrido.

Estimado mediante bins adaptativos por cuantiles de log(t) (robusto a
la distribución ultra-sesgada de landmark_t) a partir SOLO del split
de entrenamiento. Incluye manejo de cola: extrapolación plana donde
el conjunto de riesgo < min_risk.
"""
import numpy as np
from statsmodels.nonparametric.smoothers_lowess import lowess


def compute_rbase(
    landmark_t: np.ndarray,
    log_time_remaining: np.ndarray,
    min_risk: int = 100,
    frac: float = 0.15,
    grid_step: float = 0.5,
    max_samples: int = 200_000,
    n_bins: int = 100,
    smooth: bool = True,
) -> dict:
    """
    Estima R_base(t) = exp(E[log(R) | t]).

    Usa bins adaptativos por cuantiles de log(t+1) + suavizado LOESS
    opcional sobre la media por bin. NO fuerza monótona: en ventilación
    mecánica, E[R|t] sube con t (pacientes que sobreviven más tiempo
    en VM tienen más tiempo restante — supervivencia condicional).

    Filtra landmark_t < 0 (datos corruptos).

    Args:
        landmark_t: tiempos de landmarks (horas)
        log_time_remaining: log(R(t)) observado
        min_risk: tamaño mínimo del conjunto de riesgo para fiarse
        frac: fracción de datos usada en LOESS (suavizado)
        grid_step: resolución de la grilla (horas)
        max_samples: máximo de puntos para LOESS (subsampling estratificado)
        n_bins: número de bins adaptativos
        smooth: aplicar LOESS sobre las medias por bin

    Returns:
        dict con:
        - t_grid: grilla de tiempos
        - log_r_base: log(R_base) en cada punto de la grilla
        - r_base: R_base en horas
        - risk_size: tamaño del conjunto de riesgo en cada punto
        - tail_start: índice donde empieza la cola extrapolada
        - metadata: info de diagnóstico
    """
    # Filtrar landmarks negativos (datos corruptos)
    valid = landmark_t >= 0
    n_dropped = (~valid).sum()
    if n_dropped > 0:
        print(f"  ⚠ Filtrados {n_dropped} landmarks con t < 0")
    landmark_t = landmark_t[valid]
    log_time_remaining = log_time_remaining[valid]

    # Trabajar en espacio log(t+1) para bins adaptativos
    log_t = np.log1p(landmark_t)  # log(t+1)

    # Bins por cuantiles de log(t) → densidad uniforme en espacio log
    bin_edges = np.percentile(log_t, np.linspace(0, 100, n_bins + 1))
    bin_edges[0] = 0  # t=0 siempre en el primer bin
    bin_edges[-1] = log_t.max() + 1e-6

    # Media de log(R) por bin
    bin_centers_log = (bin_edges[:-1] + bin_edges[1:]) / 2
    bin_centers_t = np.expm1(bin_centers_log)  # volver a horas
    bin_means = np.zeros(n_bins)
    bin_counts = np.zeros(n_bins, dtype=int)

    # Asignar cada punto a un bin
    bin_idx = np.digitize(log_t, bin_edges) - 1  # 0-based
    bin_idx = np.clip(bin_idx, 0, n_bins - 1)

    for i in range(n_bins):
        mask = bin_idx == i
        bin_counts[i] = mask.sum()
        if mask.sum() > 0:
            bin_means[i] = log_time_remaining[mask].mean()
        else:
            # Interpolar vecinos si el bin está vacío
            if i > 0:
                bin_means[i] = bin_means[i - 1]

    # Suavizar con LOESS sobre los centros de bin (pocos puntos, rápido)
    if smooth and n_bins > 10:
        loess_result = lowess(
            bin_means, bin_centers_t,
            frac=min(1.0, frac * 3),  # más suavizado sobre bins
            it=2,
        )
        smooth_means = loess_result[:, 1]
        smooth_t = loess_result[:, 0]
    else:
        smooth_means = bin_means
        smooth_t = bin_centers_t

    # NO forzar monótona: E[R|t] sube en supervivencia condicional
    # (pacientes que aguantan más en VM tienen más tiempo restante)

    # Interpolar a grilla regular
    t_grid = np.arange(0, landmark_t.max() + grid_step, grid_step)
    log_r_base = np.interp(t_grid, smooth_t, smooth_means)

    # Calcular conjunto de riesgo: cuántos landmarks tienen t >= t_grid[i]
    risk_size = np.array([(landmark_t >= tg).sum() for tg in t_grid])

    # Encontrar cola: primer punto donde risk < min_risk
    tail_mask = risk_size < min_risk
    if tail_mask.any():
        tail_start = int(np.argmax(tail_mask))
        # Extrapolar plano desde el último punto fiable
        last_reliable = tail_start - 1
        if last_reliable >= 0:
            log_r_base[tail_start:] = log_r_base[last_reliable]
    else:
        tail_start = len(t_grid)

    r_base_hours = np.exp(log_r_base)

    return {
        "t_grid": t_grid,
        "log_r_base": log_r_base,
        "r_base": r_base_hours,
        "risk_size": risk_size,
        "tail_start": tail_start,
        "t_max_reliable": t_grid[tail_start - 1] if tail_start > 0 else t_grid[-1],
        "n_samples": len(landmark_t),
        "min_risk": min_risk,
        "n_bins": n_bins,
        "bin_centers_t": bin_centers_t,
        "bin_means": bin_means,
        "bin_counts": bin_counts,
    }


def get_rbase_at(t_query: np.ndarray, rbase_dict: dict) -> np.ndarray:
    """
    Evalúa R_base(t) para tiempos de consulta, usando interpolación lineal.
    Más allá de la grilla, devuelve el último valor (extrapolación plana).
    """
    t_q = np.clip(t_query, 0, rbase_dict["t_grid"][-1])
    log_r = np.interp(t_q, rbase_dict["t_grid"], rbase_dict["log_r_base"])
    return np.exp(log_r)


def report_rbase(rb: dict):
    """Imprime diagnóstico de R_base."""
    print(f"R_base(t) — bins adaptativos (n_bins={rb['n_bins']}, min_risk={rb['min_risk']})")
    print(f"  N muestras train: {rb['n_samples']:,}")
    print(f"  t_max fiable: {rb['t_max_reliable']:.0f}h (risk >= {rb['min_risk']})")
    print(f"  t_max grilla: {rb['t_grid'][-1]:.0f}h")
    print(f"  R_base(0h) = {rb['r_base'][0]:.1f}h")
    print(f"  R_base(t_max_reliable) = {rb['r_base'][rb['tail_start']-1]:.1f}h")

    # Reportar medias por bin (los más representativos)
    print(f"  Medias por bin (top 10 por densidad):")
    counts = rb['bin_counts']
    top = np.argsort(counts)[-10:][::-1]
    for i in top:
        t = rb['bin_centers_t'][i]
        m = np.exp(rb['bin_means'][i])
        n = counts[i]
        print(f"    t={t:7.1f}h  R_base={m:7.1f}h  n={n:8,d}")

    # Reportar riesgo cada 50h
    print(f"  Tamaño conjunto de riesgo:")
    step = max(1, len(rb['t_grid']) // 15)
    for i in range(0, len(rb['t_grid']), step):
        t = rb['t_grid'][i]
        r = rb['risk_size'][i]
        marker = " ← cola" if i >= rb['tail_start'] else ""
        print(f"    t={t:6.0f}h  risk={r:6d}{marker}")
