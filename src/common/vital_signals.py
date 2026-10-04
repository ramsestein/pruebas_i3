"""
common/vital_signals.py
=======================
Lectura de ficheros ``.vital`` (Clínic y VitalDB) usando el parser de
``vitaldb`` — NUNCA búsqueda de cadenas sobre los bytes del gzip (regla Fase 1.2).

El objetivo no es cargar las señales completas, sino **probar** cada fichero de
origen (típicamente 1 h) y devolver, en segundos epoch:

- el intervalo de actividad del ventilador (presencia real de pistas de
  ventilador, no la mera aparición del nombre en el fichero);
- los intervalos de presencia de monitor (HR / SpO2 / ECG / PLETH);
- las marcas de muestra de HR y SpO2 (para D4).

Los nombres de pista son los del exportador Philips IntelliVue usado por
VitalDB / Clínic. La etiqueta oficial de cada pista va comentada al lado.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np
import vitaldb

from .episodes import Span

logger = logging.getLogger(__name__)


# ── Catálogo de pistas (etiqueta oficial entre comillas) ──────────────────────

# Pistas de ventilador (D6/Fase 1.2). Si CUALQUIERA tiene registros, el
# paciente estaba ventilado en ese intervalo.
VENT_TRACKS: tuple[str, ...] = (
    "Intellivue/VENT_RR",      # "Ventilator Respiratory Rate" (rpm)
    "Intellivue/FIO2",         # "FiO2" (%)
    "Intellivue/PEEP_CMH2O",   # "PEEP" (cmH2O)
    "Intellivue/TV_EXP",       # "Tidal Volume Expired" (mL)
    "Intellivue/MV_EXP",       # "Minute Volume Expired" (L/min)
    "Intellivue/PIP_CMH2O",    # "Peak Inspiratory Pressure" (cmH2O)
    "Intellivue/AWP_WAV",      # "Airway Pressure" (cmH2O)
    "Intellivue/FLOW_WAV",     # "Airway Flow" (L/min)
)

# Pistas de monitor numéricas: HR y SpO2 (para D2 y D4).
MONITOR_NUM_TRACKS: tuple[str, ...] = (
    "Intellivue/ECG_HR",       # "ECG Heart Rate" (bpm)
    "Intellivue/HR",           # "Heart Rate" (bpm)
    "Intellivue/PLETH_HR",     # "Pleth Heart Rate" (bpm)
    "Intellivue/PLETH_SAT_O2", # "SpO2" (%)
)

# HR y SpO2 (conjuntos concretos para D4).
HR_TRACKS: tuple[str, ...] = (
    "Intellivue/ECG_HR", "Intellivue/HR", "Intellivue/PLETH_HR",
)
SPO2_TRACKS: tuple[str, ...] = ("Intellivue/PLETH_SAT_O2",)

# Pistas de monitor de onda (su presencia indica monitor activo; D2).
MONITOR_WAVE_TRACKS: tuple[str, ...] = (
    "Intellivue/ECG_II",       # "ECG II" waveform
    "Intellivue/PLETH",        # "Plethysmogram" waveform
)

# Pistas que se conservan al fusionar un evento (.vital de salida).
MERGE_TRACK_NAMES: tuple[str, ...] = (
    # Ondas
    "Intellivue/ECG_II",
    "Intellivue/PLETH",
    "Intellivue/ART",          # "Arterial Blood Pressure" waveform
    "Intellivue/ABP",          # idem (alias de caja)
    "Intellivue/AWP_WAV",      # "Airway Pressure" waveform
    "Intellivue/FLOW_WAV",     # "Airway Flow" waveform
    # Constantes y ventilador
    "Intellivue/HR",
    "Intellivue/ECG_HR",
    "Intellivue/PLETH_HR",
    "Intellivue/ART_SYS", "Intellivue/ART_DIA", "Intellivue/ART_MEAN",
    "Intellivue/ABP_SYS", "Intellivue/ABP_DIA", "Intellivue/ABP_MEAN",
    "Intellivue/NIBP_SYS", "Intellivue/NIBP_DIA", "Intellivue/NIBP_MEAN",
    "Intellivue/PLETH_SAT_O2",
    "Intellivue/RR",
    "Intellivue/VENT_RR",
    "Intellivue/FIO2",
    "Intellivue/PEEP_CMH2O",
    "Intellivue/TV_EXP",
    "Intellivue/MV_EXP",
    "Intellivue/PIP_CMH2O",
)

_PROBE_TRACKS: tuple[str, ...] = tuple(dict.fromkeys(
    VENT_TRACKS + MONITOR_NUM_TRACKS + MONITOR_WAVE_TRACKS
))

# Pistas para la detección de muerte por señales (Fase 1, ajuste 2).
# Las de onda se resumen a su amplitud (max-min) por registro.
_DEATH_TRACKS: dict[str, tuple[str, ...]] = {
    "HR": ("Intellivue/ECG_HR", "Intellivue/HR", "Intellivue/PLETH_HR"),
    "SpO2": ("Intellivue/PLETH_SAT_O2",),
    "MAP": ("Intellivue/ART_MEAN", "Intellivue/ABP_MEAN", "Intellivue/NIBP_MEAN"),
    "ABP_amp": ("Intellivue/ART", "Intellivue/ABP"),
    "PPG_amp": ("Intellivue/PLETH",),
}
_DEATH_WAVE_KEYS = ("ABP_amp", "PPG_amp")


@dataclass
class TrackProbe:
    """Resumen de una pista presente en un fichero .vital."""
    name: str
    dt_min: float          # primer 'dt' (segundos epoch)
    dt_max: float          # último 'dt' (segundos epoch)
    n_recs: int
    srate: float
    is_wave: bool


@dataclass
class VitalProbe:
    """Resultado de probar un .vital de origen."""
    path: Path
    dtstart: float
    dtend: float
    tracks: dict[str, TrackProbe] = field(default_factory=dict)

    def has_any(self, names: Iterable[str]) -> bool:
        return any(self.tracks.get(n) for n in names)


# ── Lectura ──────────────────────────────────────────────────────────────────

def probe_vital_file(path: str | Path, track_names: Sequence[str] = _PROBE_TRACKS) -> Optional[VitalProbe]:
    """Abre un .vital y resume las pistas de interés sin conservar sus valores.

    Devuelve ``None`` SOLO si el fichero no se puede leer (corrupto/ilegible).
    Un fichero legible sin ninguna pista reconocida devuelve un ``VitalProbe``
    con ``tracks`` vacío: eso es "sin señal", no "sin dato".

    Usa ``probe_vital_file_detail`` si además se necesita el mensaje de error.
    """
    probe, err = probe_vital_file_detail(path, track_names)
    if probe is None:
        logger.warning("[vital_signals] no se pudo leer %s: %s",
                       Path(path).name, err)
    return probe


def probe_vital_file_detail(
    path: str | Path,
    track_names: Sequence[str] = _PROBE_TRACKS,
) -> tuple[Optional[VitalProbe], Optional[str]]:
    """Igual que ``probe_vital_file`` pero devuelve ``(probe, error)``.

    ``error`` es ``None`` si el fichero se abrió (aunque no tuviera pistas).
    """
    path = Path(path)
    try:
        vf = vitaldb.VitalFile(str(path), track_names=list(track_names))
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"

    if vf is None:
        return None, "VitalFile devolvió None"

    probe = VitalProbe(path=path, dtstart=float(vf.dtstart), dtend=float(vf.dtend))

    for name, trk in (getattr(vf, "trks", None) or {}).items():
        recs = getattr(trk, "recs", None)
        if not recs:
            continue
        dts = [float(r["dt"]) for r in recs if "dt" in r]
        if not dts:
            continue
        probe.tracks[name] = TrackProbe(
            name=name,
            dt_min=min(dts),
            dt_max=max(dts),
            n_recs=len(dts),
            srate=float(trk.srate or 0.0),
            is_wave=bool(trk.type == 1),
        )
        # Liberar memoria de la pista cuanto antes.
        trk.recs = None

    return probe, None


# ── Derivación de intervalos (segundos epoch) ────────────────────────────────

# ── Serialización de sondas (caché del escaneo; Fase 1.5 punto 2) ─────────────

def probe_to_dict(probe: Optional[VitalProbe]) -> Optional[dict]:
    """Serializa un ``VitalProbe`` (o ``None`` si el fichero es ilegible)."""
    if probe is None:
        return None
    return {
        "dtstart": probe.dtstart,
        "dtend": probe.dtend,
        "tracks": {
            name: [t.dt_min, t.dt_max, t.n_recs, t.srate, t.is_wave]
            for name, t in probe.tracks.items()
        },
    }


def probe_from_dict(path: str | Path, data: Optional[dict]) -> Optional[VitalProbe]:
    """Reconstruye un ``VitalProbe`` desde ``probe_to_dict`` (``None``→ilegible)."""
    if data is None:
        return None
    probe = VitalProbe(path=Path(path), dtstart=float(data["dtstart"]),
                       dtend=float(data["dtend"]))
    for name, (dt_min, dt_max, n_recs, srate, is_wave) in data["tracks"].items():
        probe.tracks[name] = TrackProbe(
            name=name, dt_min=float(dt_min), dt_max=float(dt_max),
            n_recs=int(n_recs), srate=float(srate), is_wave=bool(is_wave),
        )
    return probe


def probe_fn_from_cache(cache: dict[str, Optional[dict]]):
    """Devuelve un ``probe_fn`` que sirve sondas desde la caché (sin leer disco)."""
    norm: dict[str, Optional[dict]] = {}
    for k, v in cache.items():
        try:
            norm[str(Path(k))] = v
        except Exception:  # noqa: BLE001
            pass

    def _fn(path: str | Path):
        key = str(Path(path))
        if key in norm:
            return probe_from_dict(path, norm[key])
        return probe_vital_file(path)
    return _fn


def _union_extent(probe: VitalProbe, names: Iterable[str]) -> Optional[Span]:
    present = [probe.tracks[n] for n in names if n in probe.tracks]
    if not present:
        return None
    return Span(
        min(t.dt_min for t in present),
        max(t.dt_max for t in present),
    )


def vent_span_from_probe(probe: VitalProbe) -> Optional[Span]:
    """Intervalo con actividad de ventilador, o ``None`` si no la hay."""
    return _union_extent(probe, VENT_TRACKS)


def monitor_span_from_probe(probe: VitalProbe) -> Optional[Span]:
    """Intervalo con presencia de monitor (HR/SpO2/ECG/PLETH), o ``None``."""
    return _union_extent(
        probe, MONITOR_NUM_TRACKS + MONITOR_WAVE_TRACKS
    )


def hr_span_from_probe(probe: VitalProbe) -> Optional[Span]:
    return _union_extent(probe, HR_TRACKS)


def spo2_span_from_probe(probe: VitalProbe) -> Optional[Span]:
    return _union_extent(probe, SPO2_TRACKS)


def read_death_series(
    paths: Sequence[str | Path],
    t0_unix: float,
) -> dict[str, list[tuple[float, float]]]:
    """Series para la detección de muerte por señales, en horas desde t0.

    Devuelve ``{"HR": [(t, v), ...], "SpO2": ..., "MAP": ...,
    "ABP_amp": ..., "PPG_amp": ...}``. Las pistas de onda se resumen a su
    amplitud (max-min) de cada registro, que es lo que delata la pérdida de
    pulsatilidad.
    """
    track_names = [n for names in _DEATH_TRACKS.values() for n in names]
    out: dict[str, list[tuple[float, float]]] = {k: [] for k in _DEATH_TRACKS}
    for p in paths:
        try:
            vf = vitaldb.VitalFile(str(p), track_names=track_names)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[vital_signals] no se pudo leer %s: %s", p, exc)
            continue
        if vf is None or not getattr(vf, "trks", None):
            continue
        for canonical, names in _DEATH_TRACKS.items():
            for n in names:
                trk = vf.trks.get(n)
                if not trk or not trk.recs:
                    continue
                for r in trk.recs:
                    dt = float(r["dt"])
                    t_h = (dt - t0_unix) / 3600.0
                    if canonical in _DEATH_WAVE_KEYS:
                        arr = np.asarray(r["val"], dtype=np.float64).ravel()
                        if arr.size == 0:
                            continue
                        out[canonical].append((t_h, float(np.nanmax(arr) - np.nanmin(arr))))
                    else:
                        v = float(r["val"])
                        if np.isfinite(v):
                            out[canonical].append((t_h, v))
                break  # primera pista disponible de ese canal
    return out


# Pistas para la cobertura de variables (Fase 1.5, punto 2).
COVERAGE_TRACKS: dict[str, tuple[str, ...]] = {
    "HR": ("Intellivue/ECG_HR", "Intellivue/HR", "Intellivue/PLETH_HR"),
    "SpO2": ("Intellivue/PLETH_SAT_O2",),
    "MAP": ("Intellivue/ART_MEAN", "Intellivue/ABP_MEAN", "Intellivue/NIBP_MEAN"),
    "RR": ("Intellivue/VENT_RR",),
    "FiO2": ("Intellivue/FIO2",),
    "PEEP": ("Intellivue/PEEP_CMH2O",),
}


def read_coverage_series(
    paths: Sequence[str | Path],
    t0_unix: float,
) -> dict[str, tuple[list[float], list[float]]]:
    """Series numéricas (horas desde t0) de las variables obligatorias.

    Lee SOLO pistas numéricas (no ondas), por lo que es barato frente a la
    fusión completa. Devuelve ``{var: (times_h, values)}``; de cada variable se
    usa la primera pista disponible.
    """
    track_names = [n for names in COVERAGE_TRACKS.values() for n in names]
    out: dict[str, tuple[list[float], list[float]]] = {
        k: ([], []) for k in COVERAGE_TRACKS
    }
    for p in paths:
        try:
            vf = vitaldb.VitalFile(str(p), track_names=track_names)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[vital_signals] cobertura: no se pudo leer %s: %s", p, exc)
            continue
        if vf is None:
            continue
        for var, names in COVERAGE_TRACKS.items():
            for n in names:
                trk = (getattr(vf, "trks", None) or {}).get(n)
                if not trk or not trk.recs:
                    continue
                times, vals = out[var]
                for r in trk.recs:
                    v = float(r["val"])
                    if np.isfinite(v):
                        times.append((float(r["dt"]) - t0_unix) / 3600.0)
                        vals.append(v)
                break
    return out


def merge_event_files(
    files: Sequence[str | Path],
    out_path: str | Path,
    track_names: Sequence[str] = MERGE_TRACK_NAMES,
) -> tuple[Path, float, float]:
    """Fusiona los .vital de un evento con ``vitaldb.VitalFile([...])``.

    Usa el parser de vitaldb (no concatenación de bytes) e incluye todas las
    pistas de ventilador y NIBP. Devuelve (ruta, dtstart, dtend).

    Si el fichero de salida ya existe, aborta: las versiones son inmutables
    (nunca se reutilizan salidas antiguas).
    """
    out_path = Path(out_path)
    if out_path.exists():
        raise FileExistsError(
            f"La salida ya existe, no se sobrescribe: {out_path}"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    vf = vitaldb.VitalFile([str(p) for p in files], track_names=list(track_names))
    vf.to_vital(str(out_path))
    return out_path, float(vf.dtstart), float(vf.dtend)
