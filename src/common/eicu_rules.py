"""
common/eicu_rules.py
====================
Reglas de eICU compartidas por el builder de casos y el adaptador (Fase 1.4).

Aquí viven las decisiones que ANTES estaban duplicadas o mal implementadas:

- **t0**: inicio de la ventilación observada en UCI, en una ÚNICA función
  (``eicu_t0_minutes``) usada por el builder y por el adaptador.
- **MAP (D7)**: invasiva si existe; si no, no invasiva
  (``systemicmean`` -> ``vitalAperiodic.noninvasivemean``).
- **RR (D7)**: la FR total del VENTILADOR; NUNCA la FR de enfermería (la
  impedancia/cuidados del monitor). El builder debe descartar la de enfermería
  cuando hay FR de ventilador.
- **Vasopresores**: la infusión continua tiene prioridad sobre los bolos de
  ``medication``.
- **Regex de fármacos**: ``'epine'`` capturaba también ``norepinephrine``;
  se usan patrones que no se solapan.
- **Centinela de dosis desconocida**: ``-1.0`` documentado y centralizado.
- **Duraciones/huecos imposibles**: offsets fuera de la estancia se registran
  como anomalías y se recortan; nunca se silencian.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Centinela de "dosis desconocida/no numérica" en las pistas de vasopresores.
DOSE_UNKNOWN_SENTINEL: float = -1.0

# Ventana de desconexión de eICU (D1: 2 h = 120 min).
DISCONNECT_GAP_MIN: float = 120.0

# Patrones de fármaco SIN solapamiento. 'norepinephrine' contiene 'epinephrine',
# por eso 'epine' tal cual capturaba ambas.
VASOPRESSOR_PATTERNS: dict[str, str] = {
    "norepinephrine": r"norepinephrine|norepi\b|levophed",
    "epinephrine": r"(?<!nor)epinephrine\b",
    "dopamine": r"dopamine\b",
}
VASOPRESSOR_TRACKS: dict[str, str] = {
    "norepinephrine": "eICU/Norepinephrine",
    "epinephrine": "eICU/Epinephrine",
    "dopamine": "eICU/Dopamine",
}

# MAP: invasiva -> no invasiva (D7)
MAP_INVASIVE_TRACK = "eICU/ABP_M"       # vitalPeriodic.systemicmean
MAP_NONINVASIVE_TRACK = "eICU/NIBP_M"   # vitalAperiodic.noninvasivemean
MAP_PREFERENCE: tuple[str, ...] = (MAP_INVASIVE_TRACK, MAP_NONINVASIVE_TRACK)

# RR: ventilador (respiratoryCharting) NUNCA enfermería.
RR_VENT_TRACK = "eICU/RR_V"
RR_NURSE_TRACK = "eICU/RR_nurse"   # se conserva aparte, no se usa como RR


# ── t0 ───────────────────────────────────────────────────────────────────────

def merge_vent_episodes(
    pt_vent: pd.DataFrame,
    gap_tolerance_min: float = DISCONNECT_GAP_MIN,
) -> pd.DataFrame:
    """Fusiona episodios de ventilación separados por <= ``gap_tolerance_min``."""
    if pt_vent is None or pt_vent.empty:
        return pd.DataFrame(columns=["patientunitstayid", "ventstartoffset", "ventendoffset"])
    df = pt_vent.sort_values("ventstartoffset")
    merged: list[dict] = []
    cur_start = cur_end = None
    for _, row in df.iterrows():
        start, end = float(row["ventstartoffset"]), float(row["ventendoffset"])
        if cur_start is None:
            cur_start, cur_end = start, end
        elif start <= cur_end + gap_tolerance_min:
            cur_end = max(cur_end, end)
        else:
            merged.append({"ventstartoffset": cur_start, "ventendoffset": cur_end})
            cur_start, cur_end = start, end
    if cur_start is not None:
        merged.append({"ventstartoffset": cur_start, "ventendoffset": cur_end})
    return pd.DataFrame(merged)


def eicu_t0_minutes(vent_df: pd.DataFrame) -> Optional[float]:
    """t0 (min desde la admisión a UCI) = inicio del primer episodio de VM.

    Función ÚNICA compartida por builder y adaptador (Fase 1.4).
    """
    if vent_df is None or vent_df.empty:
        return None
    merged = merge_vent_episodes(vent_df)
    if merged.empty:
        return None
    return float(merged.iloc[0]["ventstartoffset"])


# ── Anomalías de duración/hueco ──────────────────────────────────────────────

@dataclass
class EicuAnomaly:
    patientunitstayid: int
    kind: str
    detail: str


@dataclass
class SanitizedVent:
    episodes: pd.DataFrame
    anomalies: list[EicuAnomaly] = field(default_factory=list)


def sanitize_vent_episodes(
    vent_df: pd.DataFrame,
    unit_discharge_offset_min: float,
) -> SanitizedVent:
    """Aplica reglas explícitas a duraciones y huecos imposibles.

    Reglas (se registran, no se silencian):
      - ``ventendoffset > unitdischargeoffset``: el fin se recorta al alta.
      - ``ventstartoffset < 0``: el inicio se recorta a 0.
      - ``ventstartoffset >= unitdischargeoffset``: episodio descartado.
      - ``ventendoffset - ventstartoffset`` > 60 días: descartado como
        imposible (colisión de camas / error de registro) y registrado.
    """
    anomalies: list[EicuAnomaly] = []
    if vent_df is None or vent_df.empty:
        return SanitizedVent(pd.DataFrame(
            columns=["patientunitstayid", "ventstartoffset", "ventendoffset"]), anomalies)

    rows: list[dict] = []
    for _, row in vent_df.iterrows():
        pid = int(row["patientunitstayid"])
        start = float(row["ventstartoffset"])
        end = float(row["ventendoffset"])

        if start < 0:
            anomalies.append(EicuAnomaly(pid, "vent_start_before_admission",
                                         f"ventstartoffset={start}"))
            start = 0.0
        if end > unit_discharge_offset_min:
            anomalies.append(EicuAnomaly(
                pid, "vent_end_after_discharge",
                f"ventendoffset={end} > unitdischargeoffset={unit_discharge_offset_min}"))
            end = float(unit_discharge_offset_min)
        if start >= unit_discharge_offset_min:
            anomalies.append(EicuAnomaly(
                pid, "vent_start_after_discharge",
                f"ventstartoffset={start} >= {unit_discharge_offset_min}"))
            continue
        if end - start > 60 * 24 * 60:
            anomalies.append(EicuAnomaly(
                pid, "vent_duration_impossible", f"duration_min={end - start}"))
            continue
        if end > start:
            rows.append({"patientunitstayid": pid,
                         "ventstartoffset": start, "ventendoffset": end})
        else:
            anomalies.append(EicuAnomaly(
                pid, "vent_zero_or_negative_duration", f"[{start}, {end}]"))
    return SanitizedVent(pd.DataFrame(rows), anomalies)


# ── Vasopresores ─────────────────────────────────────────────────────────────

def drug_kind(drugname: str) -> Optional[str]:
    """Tipo de vasopresor de un nombre de fármaco (o ``None``).

    Usa patrones sin solapamiento: 'norepinephrine' NO se mapea a epinephrine.
    """
    if not isinstance(drugname, str):
        return None
    import re
    name = drugname.lower()
    for kind, pattern in VASOPRESSOR_PATTERNS.items():
        if re.search(pattern, name):
            return kind
    return None


def add_vasopressor_series(
    store: dict[str, tuple[list[float], list[float]]],
    df: pd.DataFrame,
    *,
    offset_col: str,
    value_col: str,
    t0_minutes: float,
    overwrite: bool,
) -> None:
    """Acumula series de vasopresores en ``store`` (gestión de prioridad).

    ``overwrite=False`` (infusión continua ya presente) hace que los bolos de
    ``medication`` NO sobrescriban las infusiones.
    """
    if df is None or df.empty:
        return
    for _, row in df.iterrows():
        kind = drug_kind(row.get("drugname"))
        if kind is None:
            continue
        track = VASOPRESSOR_TRACKS[kind]
        if not overwrite and track in store:
            continue
        t = (float(row[offset_col]) - t0_minutes) * 60.0
        v = pd.to_numeric(pd.Series([row[value_col]]), errors="coerce").iloc[0]
        val = DOSE_UNKNOWN_SENTINEL if pd.isna(v) else float(v)
        times, vals = store.setdefault(track, ([], []))
        times.append(t)
        vals.append(val)


# ── MAP ──────────────────────────────────────────────────────────────────────

def pick_map_source(available_tracks: Iterable[str]) -> Optional[str]:
    """Devuelve la pista de MAP preferida (invasiva primero, D7)."""
    present = set(available_tracks)
    for track in MAP_PREFERENCE:
        if track in present:
            return track
    return None
