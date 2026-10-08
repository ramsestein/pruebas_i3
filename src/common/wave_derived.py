"""
common/wave_derived.py
======================
Fase 1.6d — **punto 4**: variables derivadas de las ondas del ventilador.

Hay cohortes (Clínic y, sobre todo, VitalDB) en las que el respirador **no
publica** sus numéricos (``PEEP_CMH2O``, ``PIP_CMH2O``, ``VENT_RR``, ``TV_EXP``)
pero **sí** graba las ondas de presión y flujo de la vía aérea
(``Intellivue/AWP_WAV`` y ``Intellivue/FLOW_WAV``). Este módulo reconstruye, a
partir de esas ondas, cuatro variables respiración a respiración:

- **PIP** = máximo de presión inspiratoria (cmH2O).
- **PEEP** = presión al final de la espiración (cmH2O).
- **RR** del ventilador = respiraciones detectadas por minuto (rpm).
- **TV** = integral del flujo inspiratorio con la deriva corregida (mL).

Reglas (punto 4 del enunciado)
------------------------------
1. **Detección por respiración.** Se detectan las inspiraciones como tramos en
   los que la presión supera ``baseline + umbral``. Una respiración es el par
   (inicio de inspiración, fin de inspiración) y su espiración llega hasta el
   inicio de la siguiente inspiración.
2. **Rechazo de artefactos.** Se descartan las respiraciones:
   - **incompletas** (con la inspiración cortada en el borde del registro);
   - de **desconexión** (la presión del tramo es ≈ 0: no hay paciente);
   - **aspiraciones** y **tos** (espigas demasiado cortas o amplitudes fuera de
     rango fisiológico).
3. **Agregación por minuto con la mediana.** Cada variable se resume por minuto
   con la mediana de las respiraciones válidas de ese minuto.
4. **Validación obligatoria.** Antes de usar una variable derivada se compara
   con su numérica donde coexisten: sesgo y límites de acuerdo (Bland–Altman) y
   fracción de minutos dentro del margen. Solo se acepta si ``|sesgo|`` no pasa
   del máximo y la fracción dentro del margen es ``>= 80 %``.
5. **Precedencia.** El numérico del ventilador **manda**; el derivado solo
   rellena huecos. El origen del valor se marca en la observación con
   ``source_variable`` = ``AWP_WAV_derived`` / ``FLOW_WAV_derived``.

El módulo es puro (numpy + dataclasses) y no lee disco: los builders le pasan
las series ya muestreadas ``(t_sec, valor)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

# ── Marcas de origen (van en ``observations.source_variable``) ────────────────
AWP_SOURCE = "AWP_WAV_derived"
FLOW_SOURCE = "FLOW_WAV_derived"

# ── Parámetros de detección (valores por defecto; configurables) ──────────────
DEFAULT_FS_HZ: float = 125.0
# Percentil de la presión que define la línea de base espiratoria (PEEP).
PEEP_BASELINE_PERCENTILE: float = 10.0
# Cuánto tiene que subir la presión sobre la base para contar como inspiración.
INSP_THRESHOLD_CMH2O: float = 2.0
# Ventana (s) al final de la espiración para medir la PEEP.
EXP_WINDOW_S: float = 0.30
# Límites de duración de una respiración (rechaza tos/aspiraciones e incompletas).
MIN_BREATH_S: float = 0.30
MAX_BREATH_S: float = 12.0
# Amplitud mínima (PIP − base) para considerar que hay respiración.
MIN_AMPLITUDE_CMH2O: float = 2.0
# Presión máxima plausible (por encima es artefacto).
MAX_PIP_CMH2O: float = 80.0
# Por debajo de esta presión (absoluta) el circuito está desconectado.
FLAT_PRESSURE_CMH2O: float = 1.0

# ── Parámetros de la integral de flujo (TV) ───────────────────────────────────
FLOW_DRIFT_WINDOW_S: float = 30.0
MIN_TV_ML: float = 50.0
MAX_TV_ML: float = 2000.0

# ── Umbrales de validación (Bland–Altman contra el numérico) ──────────────────
# Margen de acuerdo por variable: ±2 cmH2O (PEEP/PIP), ±2 rpm (RR), ±10 % (TV).
AGREEMENT_MARGIN: dict[str, float] = {
    "PIP": 2.0, "PEEP": 2.0, "RR": 2.0, "TV": 0.10,
}
# Sesgo máximo admitido: 1 cmH2O / 1 rpm / 5 %.
MAX_ABS_BIAS: dict[str, float] = {
    "PIP": 1.0, "PEEP": 1.0, "RR": 1.0, "TV": 0.05,
}
# Fracción mínima de minutos dentro del margen.
MIN_FRACTION_WITHIN: float = 0.80


# ── Tipos ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Breath:
    """Una respiración detectada en la onda de presión."""
    insp_start_s: float
    insp_end_s: float
    pip_cmh2o: float
    peep_cmh2o: Optional[float]
    rejected: bool = False
    reject_reason: Optional[str] = None

    @property
    def duration_s(self) -> float:
        return float(self.insp_end_s - self.insp_start_s)


@dataclass
class DerivedSeries:
    """Variables derivadas, agregadas por minuto (mediana de las válidas).

    Las claves de los diccionarios son el índice de minuto (entero, respecto al
    inicio de la serie). ``None`` cuando el minuto no tiene ninguna respiración
    válida.
    """
    pip_cmh2o: dict[int, float] = field(default_factory=dict)
    peep_cmh2o: dict[int, float] = field(default_factory=dict)
    rr_rpm: dict[int, float] = field(default_factory=dict)
    tv_ml: dict[int, float] = field(default_factory=dict)
    n_breaths: int = 0
    n_rejected: int = 0
    disconnected: bool = False

    def coverage_minutes(self) -> int:
        return len(self.pip_cmh2o)


# ── Detección de respiraciones (AWP_WAV) ──────────────────────────────────────

def _rising_falling(pressure: np.ndarray, threshold: float) -> tuple[list[int], list[int]]:
    """Índices de flanco de subida y de bajada de ``pressure > threshold``."""
    above = pressure > threshold
    if above.size < 2:
        return [], []
    diff = np.diff(above.astype(np.int8))
    rises = list(np.flatnonzero(diff == 1) + 1)
    falls = list(np.flatnonzero(diff == -1) + 1)
    return rises, falls


def detect_breaths(
    t_sec: Sequence[float] | np.ndarray,
    pressure: Sequence[float] | np.ndarray,
    *,
    fs: float = DEFAULT_FS_HZ,
    peep_percentile: float = PEEP_BASELINE_PERCENTILE,
    insp_threshold: float = INSP_THRESHOLD_CMH2O,
    min_breath_s: float = MIN_BREATH_S,
    max_breath_s: float = MAX_BREATH_S,
    min_amplitude: float = MIN_AMPLITUDE_CMH2O,
    max_pip: float = MAX_PIP_CMH2O,
    flat_pressure: float = FLAT_PRESSURE_CMH2O,
    exp_window_s: float = EXP_WINDOW_S,
) -> list[Breath]:
    """Detecta respiraciones en la onda de presión de la vía aérea.

    Devuelve la lista **completa** (válidas y rechazadas): cada respiración lleva
    ``rejected``/``reject_reason`` para poder auditar el descarte.
    """
    t = np.asarray(t_sec, dtype=np.float64)
    p = np.asarray(pressure, dtype=np.float64)
    if t.size != p.size:
        raise ValueError("t_sec y pressure deben tener la misma longitud")
    ok = np.isfinite(t) & np.isfinite(p)
    t, p = t[ok], p[ok]
    if p.size < 2:
        return []
    # Sin paciente: todo el tramo con presión ≈ 0 es una desconexión.
    if float(np.nanmax(p)) < flat_pressure:
        return [Breath(float(t[0]), float(t[0]), 0.0, None,
                       True, "disconnection")]

    baseline = float(np.percentile(p, peep_percentile))
    threshold = baseline + insp_threshold
    rises, falls = _rising_falling(p, threshold)

    breaths: list[Breath] = []
    n_win = max(1, int(round(exp_window_s * fs)))
    for j, r0 in enumerate(rises):
        # Fin de inspiración = primera bajada posterior al inicio.
        falls_after = [f for f in falls if f > r0]
        if not falls_after:
            # Inspiración cortada en el borde del registro -> incompleta.
            breaths.append(Breath(float(t[r0]), float(t[-1]), float(p[r0:].max()),
                                  None, True, "incomplete"))
            continue
        r1 = falls_after[0]
        seg = p[r0:r1 + 1]
        pip = float(seg.max())
        # Fin de la espiración = inicio de la siguiente inspiración (o final).
        next_start = rises[j + 1] if j + 1 < len(rises) else p.size - 1
        last = max(r1, next_start - 1)
        lo = max(r1, last - n_win)
        peep = float(np.median(p[lo:last + 1])) if last > lo else float(p[last])

        duration = float(t[r1] - t[r0])
        reason: Optional[str] = None
        if duration < min_breath_s:
            reason = "too_short"          # tos / aspiración
        elif duration > max_breath_s:
            reason = "too_long"           # incompleta / fuga
        elif (pip - baseline) < min_amplitude:
            reason = "low_amplitude"
        elif pip > max_pip:
            reason = "high_pressure"
        breaths.append(Breath(float(t[r0]), float(t[r1]), pip, peep,
                              reason is not None, reason))
    return breaths


# ── Volumen tidal por integración del flujo (FLOW_WAV) ────────────────────────

def _drift_corrected_flow(
    t_sec: np.ndarray, flow: np.ndarray, *, window_s: float = FLOW_DRIFT_WINDOW_S
) -> np.ndarray:
    """Corrige la deriva del flujo restando una **mediana móvil** por bloques.

    Sobre una respiración completa el flujo neto es 0: la línea de base lenta
    (deriva del sensor, fuga) se estima con la mediana de bloques de
    ``window_s`` y se resta. La mediana es robusta frente a espigas de tos o
    aspiraciones, que de otro modo sesgarían la base.
    """
    if t_sec.size < 3:
        return flow - float(np.median(flow))
    span = float(t_sec[-1] - t_sec[0])
    if span <= window_s:
        return flow - float(np.median(flow))
    # Centros de bloque equiespaciados + mediana de cada bloque.
    edges = np.arange(t_sec[0], t_sec[-1] + 1e-9, window_s)
    if edges.size < 2:
        return flow - float(np.median(flow))
    centers: list[float] = []
    medians: list[float] = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (t_sec >= a) & (t_sec < b)
        if not m.any():
            continue
        centers.append(0.5 * (a + b))
        medians.append(float(np.median(flow[m])))
    if len(centers) < 2:
        return flow - float(np.median(flow))
    base = np.interp(t_sec, np.asarray(centers), np.asarray(medians))
    return flow - base


def tidal_volumes(
    t_sec: Sequence[float] | np.ndarray,
    flow_lpm: Sequence[float] | np.ndarray,
    *,
    min_tv_ml: float = MIN_TV_ML,
    max_tv_ml: float = MAX_TV_ML,
    min_insp_s: float = MIN_BREATH_S,
    max_insp_s: float = MAX_BREATH_S,
    drift_window_s: float = FLOW_DRIFT_WINDOW_S,
) -> list[tuple[float, float]]:
    """TV (mL) por inspiración a partir de la onda de flujo.

    La inspiración se define por el propio flujo: un tramo continuo de flujo
    **positivo** tras corregir la deriva. El flujo debe venir en L/min;
    ``∫ flujo dt`` en L/min·s se pasa a mL con el factor ``1000/60``.

    Devuelve ``[(instante_insp_s, tv_ml), ...]`` descartando las inspiraciones
    fuera de los límites de duración o de TV (artefactos).
    """
    t = np.asarray(t_sec, dtype=np.float64)
    f = np.asarray(flow_lpm, dtype=np.float64)
    if t.size != f.size:
        raise ValueError("t_sec y flow deben tener la misma longitud")
    ok = np.isfinite(t) & np.isfinite(f)
    t, f = t[ok], f[ok]
    out: list[tuple[float, float]] = []
    if t.size < 3:
        return out
    order = np.argsort(t, kind="stable")
    t, f = t[order], f[order]
    fc = _drift_corrected_flow(t, f, window_s=drift_window_s)

    positive = fc > 0.0
    if not positive.any():
        return out
    diff = np.diff(positive.astype(np.int8))
    starts = list(np.flatnonzero(diff == 1) + 1)
    ends = list(np.flatnonzero(diff == -1) + 1)
    if positive[0]:
        starts = [0] + starts
    if positive[-1]:
        ends = ends + [t.size - 1]
    for s0, s1 in zip(starts, ends):
        if s1 <= s0:
            continue
        duration = float(t[s1] - t[s0])
        if duration < min_insp_s or duration > max_insp_s:
            continue
        tv_ml = float(np.trapezoid(fc[s0:s1 + 1], t[s0:s1 + 1])) * (1000.0 / 60.0)
        if min_tv_ml <= tv_ml <= max_tv_ml:
            out.append((float(t[s0]), tv_ml))
    return out


# ── Agregación por minuto (mediana) ───────────────────────────────────────────

def _median(values: Sequence[float]) -> float:
    return float(np.median(np.asarray(values, dtype=np.float64)))


def median_by_minute(
    samples_s: Sequence[float],
    values: Sequence[float],
    *,
    minute_offset_s: float = 0.0,
) -> dict[int, float]:
    """Mediana de ``values`` por minuto (clave = índice de minuto)."""
    buckets: dict[int, list[float]] = {}
    for t0, val in zip(samples_s, values):
        if val is None or not np.isfinite(float(val)):
            continue
        key = int(np.floor((float(t0) - minute_offset_s) / 60.0))
        buckets.setdefault(key, []).append(float(val))
    return {k: _median(v) for k, v in sorted(buckets.items()) if v}


def aggregate_by_minute(
    breaths_dt_s: Sequence[float],
    *,
    breath_values: dict[str, Sequence[float]],
    minute_offset_s: float = 0.0,
    min_breaths_for_rr: int = 1,
) -> DerivedSeries:
    """Agrupa por minuto (mediana) las variables de las respiraciones válidas.

    ``breaths_dt_s``: instante (s) del inicio de inspiración de cada respiración
    (incluidas las rechazadas). ``breath_values``: listas paralelas por variable
    (``PIP``, ``PEEP``); las entradas rechazadas deben valer ``None``.
    """
    out = DerivedSeries()
    buckets: dict[int, dict[str, list[float]]] = {}
    for i, t0 in enumerate(breaths_dt_s):
        key = int(np.floor((float(t0) - minute_offset_s) / 60.0))
        b = buckets.setdefault(key, {"PIP": [], "PEEP": []})
        for var in ("PIP", "PEEP"):
            vals = breath_values.get(var)
            if vals is None:
                continue
            val = vals[i]
            if val is not None and np.isfinite(float(val)):
                b[var].append(float(val))
    for minute, b in sorted(buckets.items()):
        if b["PIP"]:
            out.pip_cmh2o[minute] = _median(b["PIP"])
        if b["PEEP"]:
            out.peep_cmh2o[minute] = _median(b["PEEP"])
        n_rr = max(len(b["PIP"]), len(b["PEEP"]))
        if n_rr >= min_breaths_for_rr:
            out.rr_rpm[minute] = float(n_rr)
    return out


def derive_from_waves(
    t_sec: Sequence[float] | np.ndarray,
    *,
    awp: Optional[Sequence[float] | np.ndarray] = None,
    flow: Optional[Sequence[float] | np.ndarray] = None,
    fs: float = DEFAULT_FS_HZ,
    minute_offset_s: float = 0.0,
    **detect_kwargs,
) -> DerivedSeries:
    """Deriva PIP/PEEP/RR (de AWP) y TV (de FLOW) de las ondas del ventilador."""
    if awp is None:
        return DerivedSeries()
    breaths = detect_breaths(t_sec, awp, fs=fs, **detect_kwargs)
    out = DerivedSeries(n_breaths=sum(1 for b in breaths if not b.rejected),
                        n_rejected=sum(1 for b in breaths if b.rejected),
                        disconnected=any(b.reject_reason == "disconnection"
                                         for b in breaths))
    agg = aggregate_by_minute(
        [b.insp_start_s for b in breaths],
        breath_values={
            "PIP": [None if b.rejected else b.pip_cmh2o for b in breaths],
            "PEEP": [None if b.rejected else b.peep_cmh2o for b in breaths],
        },
        minute_offset_s=minute_offset_s,
    )
    out.pip_cmh2o = agg.pip_cmh2o
    out.peep_cmh2o = agg.peep_cmh2o
    out.rr_rpm = agg.rr_rpm
    if flow is not None:
        segs = tidal_volumes(t_sec, flow)
        out.tv_ml = median_by_minute([s[0] for s in segs],
                                     [s[1] for s in segs],
                                     minute_offset_s=minute_offset_s)
    return out


# ── Validación contra el numérico (Bland–Altman) ──────────────────────────────

def bland_altman(pairs: Sequence[tuple[float, float]]) -> dict:
    """Sesgo y límites de acuerdo (media ± 1.96·SD de la diferencia der−num)."""
    if not pairs:
        return {"n": 0, "bias": None, "sd": None, "loa_low": None,
                "loa_high": None}
    d = np.asarray([float(a) - float(b) for a, b in pairs], dtype=np.float64)
    bias = float(d.mean())
    sd = float(d.std(ddof=1)) if d.size > 1 else 0.0
    return {"n": int(d.size), "bias": bias, "sd": sd,
            "loa_low": bias - 1.96 * sd, "loa_high": bias + 1.96 * sd}


def fraction_within(
    pairs: Sequence[tuple[float, float]],
    margin: float,
    *,
    relative: bool = False,
) -> float:
    """Fracción de pares con ``|der − num|`` dentro del margen.

    ``relative=True`` usa el margen como fracción del numérico (p. ej. 0.10 =
    ±10 %). Se ignoran los pares con numérico 0 en modo relativo.
    """
    if not pairs:
        return 0.0
    good = total = 0
    for a, b in pairs:
        a, b = float(a), float(b)
        if relative:
            if b == 0.0:
                continue
            total += 1
            if abs(a - b) / abs(b) <= margin:
                good += 1
        else:
            total += 1
            if abs(a - b) <= margin:
                good += 1
    return float(good / total) if total else 0.0


def variable_is_usable(
    variable: str,
    pairs: Sequence[tuple[float, float]],
) -> dict:
    """¿Se puede usar la variable derivada? (sesgo y fracción dentro del margen).

    Criterio del punto 4: ``|sesgo| <= MAX_ABS_BIAS`` **y** fracción dentro del
    margen ``>= MIN_FRACTION_WITHIN`` (``TV`` en relativo).
    """
    relative = variable == "TV"
    margin = AGREEMENT_MARGIN[variable]
    max_bias = MAX_ABS_BIAS[variable]
    ba = bland_altman(pairs)
    frac = fraction_within(pairs, margin, relative=relative)
    bias = ba["bias"]
    usable = bool(
        pairs and bias is not None and abs(bias) <= max_bias
        and frac >= MIN_FRACTION_WITHIN
    )
    return {
        "variable": variable, "n": ba["n"], "bias": bias,
        "loa_low": ba["loa_low"], "loa_high": ba["loa_high"],
        "margin": margin, "relative": relative,
        "fraction_within": frac, "max_abs_bias": max_bias,
        "usable": usable,
    }


def validation_report(
    derived: dict[str, dict[int, float]],
    numeric: dict[str, dict[int, float]],
) -> dict:
    """Valida varias variables: ``{var: {minuto: valor}}`` derivado vs numérico."""
    out: dict[str, dict] = {}
    for var in sorted(derived):
        num = numeric.get(var, {})
        pairs = [(derived[var][m], num[m]) for m in sorted(derived[var])
                 if m in num and np.isfinite(num[m])]
        out[var] = variable_is_usable(var, pairs)
    return out
