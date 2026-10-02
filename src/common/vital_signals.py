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

    Devuelve ``None`` si el fichero no se puede leer. Cada pista se reduce
    inmediatamente a (dt_min, dt_max, n_recs), de modo que las ondas de alta
    frecuencia no se mantienen en memoria.
    """
    path = Path(path)
    try:
        vf = vitaldb.VitalFile(str(path), track_names=list(track_names))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[vital_signals] no se pudo leer %s: %s", path.name, exc)
        return None

    if vf is None or not getattr(vf, "trks", None):
        return None

    probe = VitalProbe(path=path, dtstart=float(vf.dtstart), dtend=float(vf.dtend))

    for name, trk in vf.trks.items():
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

    return probe


# ── Derivación de intervalos (segundos epoch) ────────────────────────────────

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
