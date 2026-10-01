"""
enrich_vital.py

Enriquece los .vital de eventos completos añadiendo curvas derivadas
como nuevos tracks dentro de cada archivo.

Fuente : datasets/clinic_vitals/clinic_full_cases/*.vital  (srate nativo)
Output : datasets/clinic_vitals/clinic_full_cases_enriched/*.vital

Nuevos tracks añadidos
──────────────────────
GROUP A  Transformaciones de waveform (a srate nativo)
  Derived/CO2_d1          1ª derivada CO2         62.5 Hz
  Derived/CO2_d2          2ª derivada CO2         62.5 Hz
  Derived/CO2_filtered    CO2 suavizado (savgol)  62.5 Hz
  Derived/CO2_residual    residuo filtrado        62.5 Hz
  Derived/CO2_auc_cum     AUC acumulada CO2       62.5 Hz
  Derived/ART_d1/d2/filtered/residual/auc_cum    125 Hz
  Derived/PLETH_d1/d2/filtered/residual/auc_cum  125 Hz

GROUP B  Breath-by-breath desde capnografía CO2 (numeric recs, 1/ciclo)
  Derived/bb_rr_waveform  RR calculado del waveform (rpm)
  Derived/bb_te           tiempo espiratorio (s)
  Derived/bb_ti           tiempo inspiratorio (s)
  Derived/bb_ti_te_ratio  Ti/Te ratio
  Derived/bb_co2_peak     etCO2 por respiración (mmHg)
  Derived/bb_co2_baseline CO2 basal por respiración
  Derived/bb_co2_auc_exp  AUC CO2 durante espiración
  Derived/bb_co2_slope_exp    pendiente media espiratoria CO2
  Derived/bb_co2_phase3_slope pendiente fase III alveolar
  Derived/bb_co2_alpha_angle  ángulo alfa (fase II vs III)
  Derived/bb_dead_space_proxy proxy Bohr-Enghoff = (etCO2-base)/etCO2
  Derived/bb_co2_poly2_a/b/rmse ajuste polinómico grado 2 por respiración

GROUP C  Índices ventilatorios como curvas (numeric recs 1 Hz)
  Derived/driving_pressure      PIP - PEEP
  Derived/driving_pressure_stat PPLAT - PEEP (si disponible)
  Derived/compliance_dyn        TV_EXP / (PIP - PEEP)   mL/cmH2O
  Derived/compliance_stat       TV_EXP / (PPLAT - PEEP) (si disponible)
  Derived/rsbi                  VENT_RR / (TV_EXP/1000)  ciclos/L/min
  Derived/tv_ratio              TV_EXP / TV_INSP
  Derived/mv_ratio              MV_EXP / MV_INSP
  Derived/mechanical_power      potencia mecánica Gattinoni [J/min]
  Derived/peep_pip_ratio        PEEP / PIP
  Derived/delta_pip             variación PIP ciclo a ciclo
  Derived/delta_peep            variación PEEP ciclo a ciclo
  Derived/delta_tv              variación TV_EXP ciclo a ciclo
  Derived/ventilatory_ratio     (RR × etCO2) / (10 × 40)

GROUP D  Curvas de tendencia (rolling 60 s) de señales numéricas
  Derived/trend_tv_exp / trend_mv_exp / trend_pip
  Derived/trend_peep   / trend_rr
"""

import os
import argparse
import numpy as np
import pandas as pd
import vitaldb
from scipy.signal import savgol_filter, find_peaks

# ── Constantes ────────────────────────────────────────────────────────────────
CO2_SRATE   = 62.5
ART_SRATE   = 125.0
PLETH_SRATE = 125.0

WAVEFORM_TRACKS = {
    "Intellivue/CO2":   CO2_SRATE,
    "Intellivue/ART":   ART_SRATE,
    "Intellivue/PLETH": PLETH_SRATE,
}

NUMERIC_TRACKS = [
    "Intellivue/TV_EXP",   "Intellivue/TV_INSP",
    "Intellivue/MV_EXP",   "Intellivue/MV_INSP",
    "Intellivue/VENT_RR",  "Intellivue/VENT_RR_SPONT",
    "Intellivue/PIP_CMH2O","Intellivue/PEEP_CMH2O",
    "Intellivue/PPLAT_CMH2O",
    "Intellivue/AWAY_CO2_ET", "Intellivue/AWAY_CO2_INSP_MIN",
    "Intellivue/FIO2",
]

# ── Helpers de conversión ─────────────────────────────────────────────────────

def to_array(vf, tname, srate):
    """Extrae un track a (timestamps_epoch, valores) con su srate nativo."""
    df = vf.to_pandas([tname], interval=1.0 / srate, return_datetime=True)
    if df is None or tname not in df.columns:
        return None, None
    times = df["Time"].apply(lambda x: x.timestamp()).values
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
    short = tname.split("/")[-1]   # "CO2", "ART", "PLETH"
    filled = fill_nan(vals)

    # 1ª y 2ª derivada
    d1 = gradient1(filled, srate)
    d2 = gradient1(d1, srate)

    # Savitzky-Golay
    win = savgol_win(srate)
    try:
        filt     = savgol_filter(filled, window_length=win, polyorder=3).astype(np.float32)
        residual = (filled - filt).astype(np.float32)
    except Exception:
        filt     = filled.copy()
        residual = np.zeros_like(filled)

    # AUC acumulada (normalizada por nº muestras)
    auc_cum = (np.cumsum(np.abs(filled)) / np.arange(1, len(filled) + 1)).astype(np.float32)

    return {
        f"Derived/{short}_d1":       wav_recs(start_dt, d1,       srate),
        f"Derived/{short}_d2":       wav_recs(start_dt, d2,       srate),
        f"Derived/{short}_filtered": wav_recs(start_dt, filt,     srate),
        f"Derived/{short}_residual": wav_recs(start_dt, residual, srate),
        f"Derived/{short}_auc_cum":  wav_recs(start_dt, auc_cum,  srate),
    }


# ── GROUP B: breath-by-breath desde CO2 ──────────────────────────────────────

def detect_breaths(co2_vals, srate=CO2_SRATE):
    """
    Detecta ciclos respiratorios desde la capnografía.

    En el capnograma:
      - CO2 ~ 0 durante INSPIRACIÓN (lavado de espacio muerto)
      - CO2 sube y llega a pico (etCO2) al final de la ESPIRACIÓN

    Returns
    -------
    peaks   : ndarray  índices de pico etCO2 (fin espiración)
    valleys : ndarray  índices de valle CO2 ~ 0 (inicio espiración)
    """
    arr = fill_nan(co2_vals)
    win = savgol_win(srate, seconds=0.2)
    try:
        smooth = savgol_filter(arr, window_length=win, polyorder=2)
    except Exception:
        smooth = arr

    min_dist_peak   = max(int(srate * 1.5), 1)   # >= 1.5 s entre picos
    min_dist_valley = max(int(srate * 0.5), 1)

    peaks,   _ = find_peaks( smooth, distance=min_dist_peak,   prominence=2.0)
    valleys, _ = find_peaks(-smooth, distance=min_dist_valley, prominence=1.0)

    return peaks, valleys


def compute_bb_co2(co2_vals, peaks, valleys, times, srate=CO2_SRATE):
    """
    Métricas respiración a respiración desde el capnograma.

    Convención:
      valley_prev → peak  : fase espiratoria (Te) — CO2 sube 0→etCO2
      peak        → valley_next : fase inspiratoria (Ti) — CO2 baja etCO2→0

    Devuelve dict {nombre: (list_dt, list_val)}.
    """
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

    for i, p0 in enumerate(peaks[:-1]):
        t_peak = times[p0]

        # Valley ANTES del pico = inicio de espiración
        prev_valleys = valleys[valleys < p0]
        if len(prev_valleys) == 0:
            continue
        v_prev = prev_valleys[-1]

        # Valley DESPUÉS del pico = inicio de inspiración → fin espiración
        next_valleys = valleys[valleys > p0]
        if len(next_valleys) == 0:
            continue
        v_next = next_valleys[0]

        # ── Tiempos ──────────────────────────────────────────────────────────
        te = (p0 - v_prev) / srate          # valley→peak  = espiración
        ti = (v_next - p0) / srate          # peak→valley  = inspiración
        breath_dur = te + ti
        rr_wv = 60.0 / breath_dur if breath_dur > 0 else np.nan

        # ── Segmento espiratorio ──────────────────────────────────────────────
        exp_seg = arr[v_prev : p0 + 1]
        if len(exp_seg) < 4:
            continue

        co2_peak     = float(arr[p0])
        co2_baseline = float(np.nanmin(exp_seg))
        auc_exp      = float(np.trapezoid(exp_seg) / srate)

        # Pendiente media espiratorio (lineal)
        x_exp = np.arange(len(exp_seg)) / srate
        c_exp = np.polyfit(x_exp, exp_seg, 1)
        slope_exp = float(c_exp[0])

        # Fase III (último 30 %) — pendiente alveolar
        ph3_start = int(v_prev + 0.70 * (p0 - v_prev))
        ph3_seg   = arr[ph3_start : p0 + 1]
        if len(ph3_seg) > 5:
            x3 = np.arange(len(ph3_seg)) / srate
            c3 = np.polyfit(x3, ph3_seg, 1)
            phase3_slope = float(c3[0])

            # Fase II (20–70 %) — ángulo alfa entre fases II y III
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

        # Dead space proxy Bohr-Enghoff
        dead_sp = (co2_peak - co2_baseline) / co2_peak if co2_peak > 0 else np.nan

        # Ajuste polinómico grado 2 del segmento espiratorio
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

        # ── Almacenar en el timestamp del pico ───────────────────────────────
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


# ── GROUP A2: derivadas de señales numéricas (1 Hz) ─────────────────────────────

# Señales 1 Hz sobre las que calcular derivadas / filtrado / AUC / residuo
NUMERIC_DERIVATIVE_TRACKS = [
    "Intellivue/TV_EXP",    "Intellivue/TV_INSP",
    "Intellivue/MV_EXP",    "Intellivue/MV_INSP",
    "Intellivue/VENT_RR",   "Intellivue/VENT_RR_SPONT",
    "Intellivue/PIP_CMH2O", "Intellivue/PEEP_CMH2O",
    "Intellivue/PPLAT_CMH2O",
    "Intellivue/AWAY_CO2_ET", "Intellivue/AWAY_CO2_INSP_MIN",
    "Intellivue/FIO2",
    "Intellivue/ART_MEAN", "Intellivue/ART_SYS", "Intellivue/ART_DIA",
    "Intellivue/PLETH_SAT_O2",
]

SAVGOL_WIN_1HZ = 11   # 11 muestras = 11 s, polyorder 3

def compute_numeric_waveform_derived(vf):
    """
    Para cada señal numérica de 1 Hz disponible, calcula:
      _d1       1ª derivada (dX/dt)
      _d2       2ª derivada
      _filtered Savitzky-Golay suavizado (ventana 11 s)
      _residual diferencia original – filtrado
      _auc_cum  AUC acumulada (media móvil del área)

    Devuelve dict {derived_track_name: recs_list} con srate=0 (numeric).
    """
    avail = set(vf.get_track_names())
    load  = [t for t in NUMERIC_DERIVATIVE_TRACKS if t in avail]
    if not load:
        return {}

    df = vf.to_pandas(load, interval=1.0, return_datetime=True)
    if df is None or len(df) == 0:
        return {}

    times = df["Time"].apply(lambda x: x.timestamp()).values
    srate = 1.0
    derived = {}

    for tname in load:
        if tname not in df.columns:
            continue
        short  = tname.split("/")[-1]   # e.g. "TV_EXP"
        vals   = df[tname].values.astype(np.float32)

        # Necesitamos al menos 15 muestras finitas
        if np.sum(np.isfinite(vals)) < 15:
            continue

        filled = fill_nan(vals)

        # 1ª y 2ª derivada
        d1 = np.gradient(filled, 1.0 / srate).astype(np.float32)   # unidades/s
        d2 = np.gradient(d1,     1.0 / srate).astype(np.float32)

        # Savitzky-Golay (ventana fija de 11 muestras, polyorder=3)
        win = SAVGOL_WIN_1HZ
        try:
            filt     = savgol_filter(filled, window_length=win, polyorder=3).astype(np.float32)
            residual = (filled - filt).astype(np.float32)
        except Exception:
            filt     = filled.copy()
            residual = np.zeros_like(filled)

        # AUC acumulada (media corriente de abs(valores))
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
    Calcula curvas derivadas a partir de señales numéricas (1 Hz).
    Devuelve dict {track_name: recs_list}.
    """
    avail  = set(vf.get_track_names())
    load   = [t for t in NUMERIC_TRACKS if t in avail]
    if not load:
        return {}

    df = vf.to_pandas(load, interval=1.0, return_datetime=True)
    if df is None or len(df) == 0:
        return {}

    times = df["Time"].apply(lambda x: x.timestamp()).values

    def col(name):
        return df[name].values.astype(np.float32) if name in df.columns else None

    tv_exp  = col("Intellivue/TV_EXP")
    tv_insp = col("Intellivue/TV_INSP")
    mv_exp  = col("Intellivue/MV_EXP")
    mv_insp = col("Intellivue/MV_INSP")
    rr      = col("Intellivue/VENT_RR")
    pip     = col("Intellivue/PIP_CMH2O")
    peep    = col("Intellivue/PEEP_CMH2O")
    pplat   = col("Intellivue/PPLAT_CMH2O")
    co2_et  = col("Intellivue/AWAY_CO2_ET")

    derived = {}

    # ── Driving pressure ─────────────────────────────────────────────────────
    if pip is not None and peep is not None:
        dp = (pip - peep).astype(np.float32)
        derived["Derived/driving_pressure"] = numeric_recs(times, dp)
        if pplat is not None:
            dp_s = (pplat - peep).astype(np.float32)
            derived["Derived/driving_pressure_stat"] = numeric_recs(times, dp_s)

    # ── Compliance dinámica y estática ────────────────────────────────────────
    if tv_exp is not None and pip is not None and peep is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            cdyn = np.where((pip - peep) > 0, tv_exp / (pip - peep), np.nan).astype(np.float32)
        derived["Derived/compliance_dyn"] = numeric_recs(times, cdyn)
    if tv_exp is not None and pplat is not None and peep is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            cstat = np.where((pplat - peep) > 0, tv_exp / (pplat - peep), np.nan).astype(np.float32)
        derived["Derived/compliance_stat"] = numeric_recs(times, cstat)

    # ── RSBI ─────────────────────────────────────────────────────────────────
    if rr is not None and tv_exp is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            rsbi = np.where(tv_exp > 0, rr / (tv_exp / 1000.0), np.nan).astype(np.float32)
        derived["Derived/rsbi"] = numeric_recs(times, rsbi)

    # ── TV y MV ratios ────────────────────────────────────────────────────────
    if tv_exp is not None and tv_insp is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            tv_ratio = np.where(tv_insp > 0, tv_exp / tv_insp, np.nan).astype(np.float32)
        derived["Derived/tv_ratio"] = numeric_recs(times, tv_ratio)
    if mv_exp is not None and mv_insp is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            mv_ratio = np.where(mv_insp > 0, mv_exp / mv_insp, np.nan).astype(np.float32)
        derived["Derived/mv_ratio"] = numeric_recs(times, mv_ratio)

    # ── Mechanical Power (Gattinoni 2016, simplificada) ───────────────────────
    # MP [J/min] = 0.098 × RR × TV_L × (PIP − ΔP/2)
    if rr is not None and tv_exp is not None and pip is not None and peep is not None:
        tv_l = tv_exp / 1000.0
        dp   = pip - peep
        with np.errstate(divide="ignore", invalid="ignore"):
            mp = (0.098 * rr * tv_l * (pip - dp / 2.0)).astype(np.float32)
            mp = np.where(np.isfinite(mp), mp, np.nan).astype(np.float32)
        derived["Derived/mechanical_power"] = numeric_recs(times, mp)

    # ── PEEP / PIP ratio ──────────────────────────────────────────────────────
    if pip is not None and peep is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            pp_ratio = np.where(pip > 0, peep / pip, np.nan).astype(np.float32)
        derived["Derived/peep_pip_ratio"] = numeric_recs(times, pp_ratio)

    # ── Variaciones ciclo a ciclo ─────────────────────────────────────────────
    for arr_s, name in [
        (pip,    "Derived/delta_pip"),
        (peep,   "Derived/delta_peep"),
        (tv_exp, "Derived/delta_tv"),
    ]:
        if arr_s is not None:
            delta = np.diff(arr_s, prepend=arr_s[0]).astype(np.float32)
            derived[name] = numeric_recs(times, delta)

    # ── Ventilatory ratio ─────────────────────────────────────────────────────
    # (RR × etCO2) / (10 × 40) — proxy de inadecuación ventilatoria
    if rr is not None and co2_et is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            vr = (rr * co2_et / 400.0).astype(np.float32)
            vr = np.where(np.isfinite(vr), vr, np.nan).astype(np.float32)
        derived["Derived/ventilatory_ratio"] = numeric_recs(times, vr)

    # ── GROUP D: curvas de tendencia (rolling 60 s) ───────────────────────────
    roll_map = {
        "Intellivue/TV_EXP":    "Derived/trend_tv_exp",
        "Intellivue/MV_EXP":    "Derived/trend_mv_exp",
        "Intellivue/PIP_CMH2O": "Derived/trend_pip",
        "Intellivue/PEEP_CMH2O":"Derived/trend_peep",
        "Intellivue/VENT_RR":   "Derived/trend_rr",
    }
    for src, dst in roll_map.items():
        if src in df.columns:
            trend = (df[src]
                     .rolling(60, min_periods=1, center=True)
                     .median()
                     .values.astype(np.float32))
            derived[dst] = numeric_recs(times, trend)

    return derived


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
            peaks, valleys = detect_breaths(co2)
            print(f"      Detectados {len(peaks)} picos respiratorios")
            bb = compute_bb_co2(co2, peaks, valleys, times)
            for key, (ts, vs) in bb.items():
                recs = numeric_recs(ts, vs)
                if recs:
                    vf.add_track(f"Derived/{key}", recs, srate=0)
                    added += 1

    # GROUP A2 — derivadas de curvas numéricas (1 Hz)
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

    print(f"    Guardando → {os.path.basename(out_path)} ({added} tracks nuevos) ...")
    vf.save_vital(out_path)
    print(f"    ✓ Listo")
    return added


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Enriquecer .vital con curvas derivadas"
    )
    p.add_argument(
        "--input-dir",
        default="datasets/clinic_vitals/clinic_full_cases",
    )
    p.add_argument(
        "--output-dir",
        default="datasets/clinic_vitals/clinic_full_cases_enriched",
    )
    p.add_argument(
        "--file",
        default=None,
        help="Procesar solo un archivo (para pruebas). Ej: box10_251126_...vital",
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
