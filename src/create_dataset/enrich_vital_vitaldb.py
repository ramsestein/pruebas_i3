"""
enrich_vital_vitaldb.py

Enriquece los .vital del dataset VitalDB añadiendo curvas derivadas
como nuevos tracks dentro de cada archivo.

Fuente : datasets/vitaldb/vital_full_cases/*.vital
Output : datasets/vitaldb/vital_full_cases_enriched/*.vital

Diferencias clave respecto a enrich_vital.py (clinic):
  - Dispositivo ventilador: Primus (no Intellivue)
  - ART y PLETH a 500 Hz (vs 125 Hz)
  - AWP (presión vía aérea) disponible a 62.5 Hz → GROUP E
  - PPLAT siempre disponible → compliance estática siempre calculada
  - BIS/EEG waveforms a 128 Hz
  - Track names: Primus/, Solar8000/, SNUADC/, BIS/

Nuevos tracks añadidos
──────────────────────
GROUP A  Transformaciones de waveform (a srate nativo)
  Derived/CO2_d1/d2/filtered/residual/auc_cum        62.5 Hz
  Derived/AWP_d1/d2/filtered/residual/auc_cum        62.5 Hz
  Derived/ART_d1/d2/filtered/residual/auc_cum        500 Hz
  Derived/PLETH_d1/d2/filtered/residual/auc_cum      500 Hz
  Derived/EEG1_WAV_d1/d2/filtered/residual/auc_cum  128 Hz
  Derived/EEG2_WAV_d1/d2/filtered/residual/auc_cum  128 Hz

GROUP B  Breath-by-breath CO2 (numeric recs, 1/ciclo)
  Derived/bb_rr_waveform, bb_te, bb_ti, bb_ti_te_ratio
  Derived/bb_co2_peak, bb_co2_baseline, bb_co2_auc_exp
  Derived/bb_co2_slope_exp, bb_co2_phase3_slope, bb_co2_alpha_angle
  Derived/bb_dead_space_proxy, bb_co2_poly2_a/b/rmse

GROUP A2  Derivadas de señales numéricas (1 Hz)
  Para cada señal en NUMERIC_DERIVATIVE_TRACKS:
  Derived/{SHORT}_d1/d2/filtered/residual/auc_cum

GROUP C  Índices ventilatorios como curvas (1 Hz)
  Derived/driving_pressure      PIP - PEEP  [mbar]
  Derived/driving_pressure_stat PPLAT - PEEP [mbar]
  Derived/compliance_dyn        TV_mL / (PIP-PEEP)   [mL/mbar]
  Derived/compliance_stat       TV_mL / (PPLAT-PEEP) [mL/mbar]
  Derived/rsbi                  RR / (TV/1000)        [ciclos/L/min]
  Derived/mechanical_power      potencia mecánica Gattinoni [J/min]
  Derived/peep_pip_ratio        PEEP / PIP
  Derived/delta_pip/peep/tv     variación ciclo a ciclo
  Derived/ventilatory_ratio     (RR × etCO2_mmHg) / 400
  Derived/oxygenation_index     (FiO2/100) × Pmaw / SpO2  [proxy PF ratio]

GROUP D  Curvas de tendencia rolling 60 s
  Derived/trend_tv / trend_mv / trend_pip / trend_peep / trend_rr

GROUP E  Breath-by-breath AWP (presión en vía aérea, numeric recs 1/ciclo)
  Derived/bb_awp_pip            PIP desde waveform  [mbar]
  Derived/bb_awp_peep           PEEP desde waveform [mbar]
  Derived/bb_awp_driving        PIP_wv - PEEP_wv    [mbar]
  Derived/bb_awp_ptp            pressure-time product (integral inspiratorio)
  Derived/bb_awp_insp_slope     velocidad de subida de presión [mbar/s]
  Derived/bb_awp_ti             tiempo inspiratorio desde AWP [s]
  Derived/bb_awp_te             tiempo espiratorio desde AWP  [s]
  Derived/bb_awp_rr             RR desde AWP [rpm]
"""

import os
import argparse
import numpy as np
import pandas as pd
import vitaldb
from scipy.signal import savgol_filter, find_peaks

import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.common.timeutils import to_epoch_utc  # noqa: E402

# ── Constantes ────────────────────────────────────────────────────────────────
CO2_SRATE   = 62.5
AWP_SRATE   = 62.5
ART_SRATE   = 500.0
PLETH_SRATE = 500.0
EEG_SRATE   = 128.0

WAVEFORM_TRACKS = {
    "Intellivue/CO2":      CO2_SRATE,
    "Intellivue/AWP_WAV":      AWP_SRATE,
    "Intellivue/ABP":      ART_SRATE,
    "Intellivue/PLETH":    PLETH_SRATE,
    "BIS/EEG1_WAV":    EEG_SRATE,
    "BIS/EEG2_WAV":    EEG_SRATE,
}

NUMERIC_TRACKS = [
    "Intellivue/TV_EXP",
    "Intellivue/MV_EXP",
    "Intellivue/VENT_RR",
    "Intellivue/PIP_CMH2O",
    "Intellivue/PEEP_CMH2O",
    "Intellivue/PPLAT_CMH2O",
    "Intellivue/AWAY_CO2_ET",
    "Intellivue/FIO2",
    "Primus/MAWP_MBAR",
    "Primus/COMPLIANCE",
    "Intellivue/ABP_SYS",
    "Intellivue/ABP_MEAN",
    "Intellivue/ABP_DIA",
    "Intellivue/PLETH_SAT_O2",
    "Intellivue/ECG_HR",
    "BIS/BIS",
    "BIS/SEF",
    "BIS/EMG",
]

NUMERIC_DERIVATIVE_TRACKS = [
    "Intellivue/TV_EXP",
    "Intellivue/MV_EXP",
    "Intellivue/VENT_RR",
    "Intellivue/PIP_CMH2O",
    "Intellivue/PEEP_CMH2O",
    "Intellivue/PPLAT_CMH2O",
    "Intellivue/AWAY_CO2_ET",
    "Intellivue/FIO2",
    "Primus/COMPLIANCE",
    "Intellivue/ABP_SYS",
    "Intellivue/ABP_MEAN",
    "Intellivue/ABP_DIA",
    "Intellivue/PLETH_SAT_O2",
    "Intellivue/ECG_HR",
    "BIS/BIS",
    "BIS/SEF",
    "BIS/EMG",
]

SAVGOL_WIN_1HZ = 11   # 11 muestras = 11 s, polyorder 3


# ── Helpers de conversión ─────────────────────────────────────────────────────

def to_array(vf, tname, srate):
    """Extrae un track a (timestamps_epoch, valores) con su srate nativo."""
    df = vf.to_pandas([tname], interval=1.0 / srate, return_datetime=True)
    if df is None or tname not in df.columns:
        return None, None
    times = df["Time"].apply(to_epoch_utc).values
    vals  = df[tname].values.astype(np.float32)
    return times, vals


def numeric_recs(times, values):
    """Arrays → vitaldb numeric recs, descarta NaN."""
    out = []
    for t, v in zip(times, values):
        if np.isfinite(v) and np.isfinite(t):
            out.append({"dt": float(t), "val": float(v)})
    return out


def wav_recs(start_dt, values, srate, chunk=16):
    """Array continuo → vitaldb waveform recs en bloques de `chunk` muestras."""
    arr = np.asarray(values, dtype=np.float32)
    recs = []
    for i in range(0, len(arr), chunk):
        recs.append({"dt": start_dt + i / srate, "val": arr[i : i + chunk]})
    return recs


def fill_nan(arr):
    """Interpolación lineal de NaN para operaciones continuas."""
    s = pd.Series(arr.astype(np.float32))
    return s.interpolate(method="linear").ffill().bfill().values.astype(np.float32)


def gradient1(arr, srate):
    """1ª derivada (dX/dt) con numpy.gradient."""
    return np.gradient(fill_nan(arr), 1.0 / srate).astype(np.float32)


def savgol_win(srate, seconds=0.3, polyorder=3):
    """Calcula window_length impar para savgol_filter."""
    w = max(polyorder + 2, int(srate * seconds))
    return w if w % 2 == 1 else w + 1


# ── GROUP A: transformaciones de waveform ─────────────────────────────────────

def compute_waveform_derived(vf, tname, srate):
    """
    Calcula 5 curvas derivadas (d1, d2, filtered, residual, auc_cum)
    para un track de waveform. Devuelve dict {track_name: recs}.
    """
    times, vals = to_array(vf, tname, srate)
    if times is None or np.sum(np.isfinite(vals)) < int(srate * 5):
        return {}

    start_dt = times[0]
    short = tname.split("/")[-1]   # "CO2", "AWP", "ART", "PLETH", "EEG1_WAV", ...
    filled = fill_nan(vals)

    d1 = gradient1(filled, srate)
    d2 = gradient1(d1, srate)

    win = savgol_win(srate)
    try:
        filt     = savgol_filter(filled, window_length=win, polyorder=3).astype(np.float32)
        residual = (filled - filt).astype(np.float32)
    except Exception:
        filt     = filled.copy()
        residual = np.zeros_like(filled)

    auc_cum = (np.cumsum(np.abs(filled)) / np.arange(1, len(filled) + 1)).astype(np.float32)

    return {
        f"Derived/{short}_d1":       wav_recs(start_dt, d1,       srate),
        f"Derived/{short}_d2":       wav_recs(start_dt, d2,       srate),
        f"Derived/{short}_filtered": wav_recs(start_dt, filt,     srate),
        f"Derived/{short}_residual": wav_recs(start_dt, residual, srate),
        f"Derived/{short}_auc_cum":  wav_recs(start_dt, auc_cum,  srate),
    }


# ── GROUP B: breath-by-breath CO2 ─────────────────────────────────────────────

def detect_breaths_co2(co2_vals, srate=CO2_SRATE):
    """Detecta ciclos respiratorios desde capnografía."""
    arr = fill_nan(co2_vals)
    win = savgol_win(srate, seconds=0.2)
    try:
        smooth = savgol_filter(arr, window_length=win, polyorder=2)
    except Exception:
        smooth = arr

    min_dist_peak   = max(int(srate * 1.5), 1)
    min_dist_valley = max(int(srate * 0.5), 1)

    peaks,   _ = find_peaks( smooth, distance=min_dist_peak,   prominence=2.0)
    valleys, _ = find_peaks(-smooth, distance=min_dist_valley, prominence=1.0)
    return peaks, valleys


def compute_bb_co2(co2_vals, peaks, valleys, times, srate=CO2_SRATE):
    """Métricas respiración a respiración desde el capnograma."""
    keys = [
        "bb_rr_waveform", "bb_te", "bb_ti", "bb_ti_te_ratio",
        "bb_co2_peak", "bb_co2_baseline", "bb_co2_auc_exp",
        "bb_co2_slope_exp", "bb_co2_phase3_slope", "bb_co2_alpha_angle",
        "bb_dead_space_proxy",
        "bb_co2_poly2_a", "bb_co2_poly2_b", "bb_co2_poly2_rmse",
    ]
    res = {k: ([], []) for k in keys}

    if len(peaks) < 2 or len(valleys) < 2:
        return res

    arr = fill_nan(co2_vals)

    for p0 in peaks[:-1]:
        t_peak = times[p0]

        prev_valleys = valleys[valleys < p0]
        if len(prev_valleys) == 0:
            continue
        v_prev = prev_valleys[-1]

        next_valleys = valleys[valleys > p0]
        if len(next_valleys) == 0:
            continue
        v_next = next_valleys[0]

        te = (p0 - v_prev) / srate
        ti = (v_next - p0) / srate
        breath_dur = te + ti
        rr_wv = 60.0 / breath_dur if breath_dur > 0 else np.nan

        exp_seg = arr[v_prev : p0 + 1]
        if len(exp_seg) < 4:
            continue

        co2_peak     = float(arr[p0])
        co2_baseline = float(np.nanmin(exp_seg))
        auc_exp      = float(np.trapezoid(exp_seg) / srate)

        x_exp = np.arange(len(exp_seg)) / srate
        c_exp = np.polyfit(x_exp, exp_seg, 1)
        slope_exp = float(c_exp[0])

        ph3_start = int(v_prev + 0.70 * (p0 - v_prev))
        ph3_seg   = arr[ph3_start : p0 + 1]
        if len(ph3_seg) > 5:
            x3 = np.arange(len(ph3_seg)) / srate
            c3 = np.polyfit(x3, ph3_seg, 1)
            phase3_slope = float(c3[0])

            ph2_start = int(v_prev + 0.20 * (p0 - v_prev))
            ph2_seg   = arr[ph2_start : ph3_start]
            if len(ph2_seg) > 3:
                x2 = np.arange(len(ph2_seg)) / srate
                c2 = np.polyfit(x2, ph2_seg, 1)
                denom = 1.0 + c2[0] * c3[0]
                alpha = float(np.degrees(np.arctan(abs(c2[0] - c3[0]) / denom))) if denom != 0 else np.nan
            else:
                alpha = np.nan
        else:
            phase3_slope = np.nan
            alpha        = np.nan

        dead_sp = (co2_peak - co2_baseline) / co2_peak if co2_peak > 0 else np.nan

        if len(exp_seg) > 4:
            x_p = np.linspace(0, 1, len(exp_seg))
            try:
                pc     = np.polyfit(x_p, exp_seg, 2)
                fitted = np.polyval(pc, x_p)
                poly_a    = float(pc[0])
                poly_b    = float(pc[1])
                poly_rmse = float(np.sqrt(np.mean((exp_seg - fitted) ** 2)))
            except Exception:
                poly_a = poly_b = poly_rmse = np.nan
        else:
            poly_a = poly_b = poly_rmse = np.nan

        values_map = {
            "bb_rr_waveform":       rr_wv,
            "bb_te":                te,
            "bb_ti":                ti,
            "bb_ti_te_ratio":       ti / te if te > 0 else np.nan,
            "bb_co2_peak":          co2_peak,
            "bb_co2_baseline":      co2_baseline,
            "bb_co2_auc_exp":       auc_exp,
            "bb_co2_slope_exp":     slope_exp,
            "bb_co2_phase3_slope":  phase3_slope,
            "bb_co2_alpha_angle":   alpha,
            "bb_dead_space_proxy":  dead_sp,
            "bb_co2_poly2_a":       poly_a,
            "bb_co2_poly2_b":       poly_b,
            "bb_co2_poly2_rmse":    poly_rmse,
        }
        for k, v in values_map.items():
            res[k][0].append(t_peak)
            res[k][1].append(v)

    return res


# ── GROUP A2: derivadas de señales numéricas (1 Hz) ───────────────────────────

def compute_numeric_waveform_derived(vf):
    """
    Para cada señal numérica disponible calcula:
      _d1, _d2, _filtered, _residual, _auc_cum  (srate=0, numeric recs)
    """
    avail = set(vf.get_track_names())
    load  = [t for t in NUMERIC_DERIVATIVE_TRACKS if t in avail]
    if not load:
        return {}

    df = vf.to_pandas(load, interval=1.0, return_datetime=True)
    if df is None or len(df) == 0:
        return {}

    times   = df["Time"].apply(to_epoch_utc).values
    derived = {}

    for tname in load:
        if tname not in df.columns:
            continue
        short = tname.split("/")[-1]
        vals  = df[tname].values.astype(np.float32)

        if np.sum(np.isfinite(vals)) < 15:
            continue

        filled = fill_nan(vals)

        d1 = np.gradient(filled, 1.0).astype(np.float32)
        d2 = np.gradient(d1,     1.0).astype(np.float32)

        try:
            filt     = savgol_filter(filled, window_length=SAVGOL_WIN_1HZ, polyorder=3).astype(np.float32)
            residual = (filled - filt).astype(np.float32)
        except Exception:
            filt     = filled.copy()
            residual = np.zeros_like(filled)

        auc_cum = (np.cumsum(np.abs(filled)) / np.arange(1, len(filled) + 1)).astype(np.float32)

        for suffix, arr in [
            ("_d1",       d1),
            ("_d2",       d2),
            ("_filtered", filt),
            ("_residual", residual),
            ("_auc_cum",  auc_cum),
        ]:
            recs = numeric_recs(times, arr)
            if recs:
                derived[f"Derived/{short}{suffix}"] = recs

    return derived


# ── GROUP C: índices ventilatorios como curvas (1 Hz) ─────────────────────────

def compute_numeric_derived(vf):
    """
    Índices ventilatorios y hemodinámicos derivados de las señales numéricas.
    Devuelve dict {track_name: recs_list}.
    """
    avail = set(vf.get_track_names())
    load  = [t for t in NUMERIC_TRACKS if t in avail]
    if not load:
        return {}

    df = vf.to_pandas(load, interval=1.0, return_datetime=True)
    if df is None or len(df) == 0:
        return {}

    times = df["Time"].apply(to_epoch_utc).values

    def col(name):
        return df[name].values.astype(np.float32) if name in df.columns else None

    tv       = col("Intellivue/TV_EXP")           # mL
    mv       = col("Intellivue/MV_EXP")           # L/min
    rr       = col("Intellivue/VENT_RR")       # breaths/min
    pip      = col("Intellivue/PIP_CMH2O")     # mbar
    peep     = col("Intellivue/PEEP_CMH2O")    # mbar
    pplat    = col("Intellivue/PPLAT_CMH2O")   # mbar
    etco2    = col("Intellivue/AWAY_CO2_ET")        # % (Primus)
    fio2     = col("Intellivue/FIO2")         # %
    mawp     = col("Primus/MAWP_MBAR")    # mean airway pressure [mbar]
    spo2     = col("Intellivue/PLETH_SAT_O2")# %

    derived = {}

    # ── Driving pressure ─────────────────────────────────────────────────────
    if pip is not None and peep is not None:
        dp = (pip - peep).astype(np.float32)
        derived["Derived/driving_pressure"] = numeric_recs(times, dp)

    # Driving pressure estática (PPLAT siempre disponible en vitaldb)
    if pplat is not None and peep is not None:
        dp_s = (pplat - peep).astype(np.float32)
        derived["Derived/driving_pressure_stat"] = numeric_recs(times, dp_s)

    # ── Compliance ───────────────────────────────────────────────────────────
    if tv is not None and pip is not None and peep is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            cdyn = np.where((pip - peep) > 0, tv / (pip - peep), np.nan).astype(np.float32)
        derived["Derived/compliance_dyn"] = numeric_recs(times, cdyn)

    if tv is not None and pplat is not None and peep is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            cstat = np.where((pplat - peep) > 0, tv / (pplat - peep), np.nan).astype(np.float32)
        derived["Derived/compliance_stat"] = numeric_recs(times, cstat)

    # ── RSBI ─────────────────────────────────────────────────────────────────
    if rr is not None and tv is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            rsbi = np.where(tv > 0, rr / (tv / 1000.0), np.nan).astype(np.float32)
        derived["Derived/rsbi"] = numeric_recs(times, rsbi)

    # ── Mechanical Power (Gattinoni 2016) ─────────────────────────────────────
    # MP [J/min] = 0.098 × RR × TV_L × (PIP − ΔP/2)
    # mbar ≈ cmH2O × 0.98 → 0.098 × 0.98 ≈ 0.096 J/mbar·L; usar 0.098 es aprox
    if rr is not None and tv is not None and pip is not None and peep is not None:
        tv_l = tv / 1000.0
        dp   = pip - peep
        with np.errstate(divide="ignore", invalid="ignore"):
            mp = (0.098 * rr * tv_l * (pip - dp / 2.0)).astype(np.float32)
            mp = np.where(np.isfinite(mp), mp, np.nan).astype(np.float32)
        derived["Derived/mechanical_power"] = numeric_recs(times, mp)

    # ── PEEP/PIP ratio ────────────────────────────────────────────────────────
    if pip is not None and peep is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            pp_ratio = np.where(pip > 0, peep / pip, np.nan).astype(np.float32)
        derived["Derived/peep_pip_ratio"] = numeric_recs(times, pp_ratio)

    # ── Variaciones ciclo a ciclo ─────────────────────────────────────────────
    for arr_s, name in [
        (pip, "Derived/delta_pip"),
        (peep, "Derived/delta_peep"),
        (tv,   "Derived/delta_tv"),
    ]:
        if arr_s is not None:
            delta = np.diff(arr_s, prepend=arr_s[0]).astype(np.float32)
            derived[name] = numeric_recs(times, delta)

    # ── Ventilatory ratio ─────────────────────────────────────────────────────
    # Intellivue/AWAY_CO2_ET en % → convertir a mmHg: × 7.6 (1 atm ≈ 760 mmHg, 1% = 7.6 mmHg)
    if rr is not None and etco2 is not None:
        etco2_mmhg = etco2 * 7.6
        with np.errstate(divide="ignore", invalid="ignore"):
            vr = (rr * etco2_mmhg / 400.0).astype(np.float32)
            vr = np.where(np.isfinite(vr), vr, np.nan).astype(np.float32)
        derived["Derived/ventilatory_ratio"] = numeric_recs(times, vr)

    # ── Oxygenation index (proxy) ─────────────────────────────────────────────
    # OI = (FiO2% / 100) × Pmaw / SpO2 × 100
    if fio2 is not None and mawp is not None and spo2 is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            oi = np.where(
                spo2 > 0,
                (fio2 / 100.0) * mawp / spo2 * 100.0,
                np.nan
            ).astype(np.float32)
            oi = np.where(np.isfinite(oi), oi, np.nan).astype(np.float32)
        derived["Derived/oxygenation_index"] = numeric_recs(times, oi)

    # ── GROUP D: curvas de tendencia rolling 60 s ─────────────────────────────
    roll_map = {
        "Intellivue/TV_EXP":        "Derived/trend_tv",
        "Intellivue/MV_EXP":        "Derived/trend_mv",
        "Intellivue/PIP_CMH2O":  "Derived/trend_pip",
        "Intellivue/PEEP_CMH2O": "Derived/trend_peep",
        "Intellivue/VENT_RR":    "Derived/trend_rr",
    }
    for src, dst in roll_map.items():
        if src in df.columns:
            trend = (df[src]
                     .rolling(60, min_periods=1, center=True)
                     .median()
                     .values.astype(np.float32))
            derived[dst] = numeric_recs(times, trend)

    return derived


# ── GROUP E: breath-by-breath desde AWP (presión vía aérea) ───────────────────

def detect_breaths_awp(awp_vals, srate=AWP_SRATE):
    """
    Detecta ciclos respiratorios en la curva de presión de vía aérea.
    En ventilación con presión positiva:
      - Picos   = final de inspiración (PIP)
      - Valles  = final de espiración  (PEEP)
    """
    arr = fill_nan(awp_vals)
    win = savgol_win(srate, seconds=0.3)
    try:
        smooth = savgol_filter(arr, window_length=win, polyorder=2)
    except Exception:
        smooth = arr

    min_dist_peak  = max(int(srate * 1.5), 1)
    min_dist_valley = max(int(srate * 0.5), 1)

    peaks,  _ = find_peaks( smooth, distance=min_dist_peak,  prominence=2.0)
    valleys, _ = find_peaks(-smooth, distance=min_dist_valley, prominence=1.0)
    return peaks, valleys


def compute_bb_awp(awp_vals, peaks, valleys, times, srate=AWP_SRATE):
    """
    Métricas respiración a respiración desde la curva AWP.

    Convención:
      valley_prev → peak  : fase inspiratoria (Ti) — presión sube PEEP→PIP
      peak        → valley_next : fase espiratoria (Te) — presión baja PIP→PEEP

    Devuelve dict {nombre: (list_dt, list_val)}.
    """
    keys = [
        "bb_awp_pip", "bb_awp_peep", "bb_awp_driving",
        "bb_awp_ptp", "bb_awp_insp_slope",
        "bb_awp_ti", "bb_awp_te", "bb_awp_rr",
    ]
    res = {k: ([], []) for k in keys}

    if len(peaks) < 2 or len(valleys) < 2:
        return res

    arr = fill_nan(awp_vals)

    for p0 in peaks[:-1]:
        t_peak = times[p0]

        # Valley ANTES del pico = inicio de inspiración (PEEP)
        prev_valleys = valleys[valleys < p0]
        if len(prev_valleys) == 0:
            continue
        v_prev = prev_valleys[-1]

        # Valley DESPUÉS del pico = inicio de siguiente inspiración (PEEP)
        next_valleys = valleys[valleys > p0]
        if len(next_valleys) == 0:
            continue
        v_next = next_valleys[0]

        ti = (p0 - v_prev) / srate
        te = (v_next - p0) / srate
        breath_dur = ti + te
        rr_awp = 60.0 / breath_dur if breath_dur > 0 else np.nan

        # PIP desde waveform = valor en pico
        pip_wv  = float(arr[p0])
        # PEEP desde waveform = mediana del último 20 % del segmento espiratorio
        exp_seg = arr[p0 : v_next + 1]
        if len(exp_seg) > 2:
            peep_wv = float(np.median(exp_seg[int(0.8 * len(exp_seg)):]))
        else:
            peep_wv = float(arr[v_prev])

        driving = pip_wv - peep_wv

        # Pressure-time product (PTP) = integral(AWP - PEEP) durante inspiración
        insp_seg = arr[v_prev : p0 + 1]
        ptp = float(np.trapezoid(np.maximum(insp_seg - peep_wv, 0)) / srate)

        # Velocidad de subida de presión (slope lineal en la primera mitad inspiratoria)
        half = max(2, len(insp_seg) // 2)
        x_r  = np.arange(half) / srate
        try:
            c_r = np.polyfit(x_r, insp_seg[:half], 1)
            insp_slope = float(c_r[0])
        except Exception:
            insp_slope = np.nan

        for k, v in {
            "bb_awp_pip":        pip_wv,
            "bb_awp_peep":       peep_wv,
            "bb_awp_driving":    driving,
            "bb_awp_ptp":        ptp,
            "bb_awp_insp_slope": insp_slope,
            "bb_awp_ti":         ti,
            "bb_awp_te":         te,
            "bb_awp_rr":         rr_awp,
        }.items():
            res[k][0].append(t_peak)
            res[k][1].append(v)

    return res


# ── Función principal por archivo ─────────────────────────────────────────────

def enrich_one(src_path, out_path):
    print(f"  Cargando {os.path.basename(src_path)} ...")
    vf     = vitaldb.VitalFile(src_path)
    tracks = set(vf.get_track_names())
    added  = 0

    # GROUP A — derivadas de waveform
    for tname, srate in WAVEFORM_TRACKS.items():
        if tname not in tracks:
            continue
        short = tname.split("/")[-1]
        print(f"    Waveform derivatives: {short} @ {srate} Hz ...")
        wav_derived = compute_waveform_derived(vf, tname, srate)
        for dname, recs in wav_derived.items():
            if recs:
                vf.add_track(dname, recs, srate=srate)
                added += 1

    # GROUP B — breath-by-breath CO2
    if "Intellivue/CO2" in tracks:
        print(f"    Breath-by-breath CO2 ...")
        times, co2 = to_array(vf, "Intellivue/CO2", CO2_SRATE)
        if times is not None and np.sum(np.isfinite(co2)) > int(CO2_SRATE * 10):
            peaks, valleys = detect_breaths_co2(co2)
            print(f"      Detectados {len(peaks)} picos respiratorios (CO2)")
            bb = compute_bb_co2(co2, peaks, valleys, times)
            for key, (ts, vs) in bb.items():
                recs = numeric_recs(ts, vs)
                if recs:
                    vf.add_track(f"Derived/{key}", recs, srate=0)
                    added += 1

    # GROUP A2 — derivadas de señales numéricas
    print(f"    Derivadas de señales numéricas (1 Hz) ...")
    num_wav_derived = compute_numeric_waveform_derived(vf)
    for dname, recs in num_wav_derived.items():
        if recs:
            vf.add_track(dname, recs, srate=0)
            added += 1

    # GROUP C + D — índices ventilatorios y tendencias
    print(f"    Índices ventilatorios y tendencias ...")
    num_derived = compute_numeric_derived(vf)
    for dname, recs in num_derived.items():
        if recs:
            vf.add_track(dname, recs, srate=0)
            added += 1

    # GROUP E — breath-by-breath AWP
    if "Intellivue/AWP_WAV" in tracks:
        print(f"    Breath-by-breath AWP ...")
        times_awp, awp = to_array(vf, "Intellivue/AWP_WAV", AWP_SRATE)
        if times_awp is not None and np.sum(np.isfinite(awp)) > int(AWP_SRATE * 10):
            peaks_awp, valleys_awp = detect_breaths_awp(awp)
            print(f"      Detectados {len(peaks_awp)} picos en AWP")
            bb_awp = compute_bb_awp(awp, peaks_awp, valleys_awp, times_awp)
            for key, (ts, vs) in bb_awp.items():
                recs = numeric_recs(ts, vs)
                if recs:
                    vf.add_track(f"Derived/{key}", recs, srate=0)
                    added += 1

    print(f"    Guardando -> {os.path.basename(out_path)} ({added} tracks nuevos) ...")
    vf.save_vital(out_path)
    print(f"    ✓ Listo")
    return added


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Enriquecer .vital VitalDB"
    )
    p.add_argument(
        "--input_dir",
        default="datasets/vitaldb_sicu/vitaldb_full_cases",
    )
    p.add_argument(
        "--output_dir",
        default="datasets/vitaldb_sicu/vitaldb_full_cases_enriched",
    )
    p.add_argument(
        "--file",
        default=None,
        help="Procesar solo un archivo (para pruebas). Ej: 0017.vital",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Reescribir archivos ya procesados",
    )
    args = p.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    if args.file:
        files = [args.file]
    else:
        files = sorted(
            f for f in os.listdir(args.input_dir)
            if f.endswith(".vital")
        )

    print(f"Procesando {len(files)} archivos .vital ...")
    errors = []

    for i, fname in enumerate(files, 1):
        src = os.path.join(args.input_dir, fname)
        out = os.path.join(args.output_dir, fname)

        if os.path.exists(out) and not args.overwrite:
            print(f"[{i}/{len(files)}] Saltar (ya existe): {fname}")
            continue

        print(f"[{i}/{len(files)}] {fname}")
        try:
            enrich_one(src, out)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            errors.append((fname, str(exc)))

    print(f"\nFinalizado. Errores: {len(errors)}")
    for fname, err in errors:
        print(f"  {fname}: {err}")


if __name__ == "__main__":
    main()
