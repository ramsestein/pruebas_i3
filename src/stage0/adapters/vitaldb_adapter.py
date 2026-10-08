"""
adapters/vitaldb_adapter.py
===========================
Adaptador para la cohorte VitalDB SICU.

Fuente de datos:
  - Archivos .vital en vitaldb_cases_dir (uno por episodio de VM fusionado)
  - Índice JSON en vitaldb_index

PENDIENTE (preguntas abiertas P1–P4):
  P1. Nombres exactos de tracks ECG, PPG, ABP en VitalDB
  P2. Cómo detectar t0 (inicio VM)
  P3. Si existen tablas con intentos de extubación/reintubación
  P4. Si el índice ya tiene marcado cuándo termina la VM

Por ahora, t0 = inicio del archivo y extubación = fin del archivo
(análogo a Clínic, donde la detección fue por presencia de tracks de ventilador).
Los nombres de canal se resuelven desde la config (null → unavailable).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import vitaldb

from .base import (
    CohortAdapter,
    ClinicalEvents,
    ExtubationAttempt,
    NumericsRecord,
    WaveformRecord,
)
from ...common.timeutils import to_epoch_utc

logger = logging.getLogger(__name__)


def _flatten_track(trk) -> tuple[np.ndarray, np.ndarray]:
    """Devuelve (times, values) 1D para una pista VitalFile.

    En esta versión de vitaldb, cada rec WAV tiene `val` como array de nsamp
    muestras; hay que concatenarlos y expandir los timestamps por muestra.
    Las pistas NUM tienen `val` escalar y se tratan como 1 muestra.
    """
    if trk is None or not trk.recs:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float32)

    srate = float(trk.srate) if trk.srate and trk.srate > 0 else 0.0
    times_list: list[np.ndarray] = []
    vals_list: list[np.ndarray] = []
    for r in trk.recs:
        v = np.asarray(r['val'], dtype=np.float32)
        if v.ndim == 0:
            v = v.reshape(1)
        n = len(v)
        if n == 0:
            continue
        dt = float(r['dt'])
        vals_list.append(v)
        if srate > 0:
            times_list.append(dt + np.arange(n, dtype=np.float64) / srate)
        else:
            times_list.append(np.full(n, dt, dtype=np.float64))

    if not vals_list:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float32)
    return np.concatenate(times_list), np.concatenate(vals_list)

# Alias de nombres de pista en VitalDB: algunas cajas miden la presión arterial
# de forma invasiva (ABP_*) y otras no invasiva (NIBP_*); RR vs VENT_RR;
# ECG_HR vs PLETH_HR vs HR. Orden = prioridad de resolución.
VITALDB_CHANNEL_ALIASES: dict[str, list[str]] = {
    "SBP": ["Intellivue/ABP_SYS", "Intellivue/NIBP_SYS"],
    "DBP": ["Intellivue/ABP_DIA", "Intellivue/NIBP_DIA"],
    "MAP": ["Intellivue/ABP_MEAN", "Intellivue/NIBP_MEAN"],
    "RR":  ["Intellivue/RR", "Intellivue/VENT_RR"],
    "HR":  ["Intellivue/ECG_HR", "Intellivue/PLETH_HR", "Intellivue/HR"],
}

STANDARD_NUMERIC_COLS = [
    "HR", "SBP", "DBP", "MAP", "SpO2", "RR",
    "FiO2", "PEEP", "TV", "MV", "PIP",
    "vasopressor_norepinephrine", "vasopressor_epinephrine",
    "vasopressor_dopamine", "lactate",
    "SOFA_respiratory", "SOFA_cardiovascular", "SOFA_total",
]


class VitalDBAdapter(CohortAdapter):
    """
    Adaptador para archivos .vital de vitaldb_full_cases.

    Los archivos son producidos por build_vitaldb_cases.py: un único .vital
    por episodio de VM, fusionando los archivos horarios del mismo box.
    """

    @property
    def cohort_name(self) -> str:
        return "vitaldb"

    def __init__(self, config: dict):
        super().__init__(config)
        self._cases_dir = Path(config["paths"]["vitaldb_cases_dir"])
        self._index_path = Path(config["paths"]["vitaldb_index"])
        self._index: Optional[dict] = None
        self._vf_cache: dict[str, vitaldb.VitalFile] = {}

    # ── Index ─────────────────────────────────────────────────────────────────

    def _load_index(self) -> dict:
        if self._index is None:
            if not self._index_path.exists():
                raise FileNotFoundError(
                    f"Índice VitalDB no encontrado: {self._index_path}\n"
                    "Ejecuta primero build_vitaldb_cases.py"
                )
            with open(self._index_path, encoding="utf-8") as f:
                self._index = json.load(f)
        return self._index

    def _get_event_by_patient(self, patient_id: str) -> dict:
        idx = self._load_index()
        for ev in idx.get("events", []):
            if ev.get("event_id") == patient_id:
                return ev
        raise KeyError(f"[vitaldb] patient_id '{patient_id}' no encontrado en índice")

    # ── VitalFile cache ───────────────────────────────────────────────────────

    def _open_vital(self, patient_id: str) -> vitaldb.VitalFile:
        if patient_id not in self._vf_cache:
            self._vf_cache.clear()  # Liberar memoria del paciente anterior
            ev = self._get_event_by_patient(patient_id)
            path = self._cases_dir / ev["file"]
            if not path.exists():
                raise FileNotFoundError(f"[vitaldb] Archivo no encontrado: {path}")
            logger.debug("[vitaldb] Abriendo %s", path)
            self._vf_cache[patient_id] = vitaldb.VitalFile(str(path))
        return self._vf_cache[patient_id]

    def _resolve_available_channel(
        self, canonical_name: str, vf: vitaldb.VitalFile
    ) -> Optional[str]:
        """Devuelve el nombre de pista realmente presente en el fichero.

        Prueba primero el canal configurado y luego los alias conocidos
        (ABP_* vs NIBP_*, RR vs VENT_RR...). Devuelve None si ninguno existe.
        """
        candidates: list[str] = []
        configured = self._resolve_channel(canonical_name)
        if configured:
            candidates.append(configured)
        for alt in VITALDB_CHANNEL_ALIASES.get(canonical_name, []):
            if alt not in candidates:
                candidates.append(alt)
        for c in candidates:
            trk = vf.trks.get(c)
            if trk is not None and trk.recs:
                return c
        return configured

    def _get_t0_unix(self, patient_id: str) -> float:
        """
        t0 = inicio del episodio de VM (epoch).
        Prioriza el campo `t0_unix` del índice (si el builder lo escribió).
        Si no existe, usa la lógica configurada (t0_source).
        """
        ev = self._get_event_by_patient(patient_id)
        if ev.get("t0_unix") is not None:
            return float(ev["t0_unix"])

        t0_source = self._event_source.get("t0_source", "vital_file_start")

        if t0_source == "vital_file_start" or t0_source is None:
            return to_epoch_utc(pd.Timestamp(ev["start_time"]))

        if t0_source == "track_threshold":
            # P2: detección de t0 por umbral de track (ej. PEEP > 3 cmH2O)
            track = self._event_source.get("t0_track_threshold", {}).get("track")
            threshold = self._event_source.get("t0_track_threshold", {}).get("value")
            if track and threshold is not None:
                return self._detect_t0_by_threshold(patient_id, track, float(threshold))
            else:
                logger.warning(
                    "[vitaldb] t0_source='track_threshold' pero track/value no configurados; "
                    "usando start_time del índice"
                )
                return to_epoch_utc(pd.Timestamp(ev["start_time"]))

        # Fallback
        return to_epoch_utc(pd.Timestamp(ev["start_time"]))

    def _detect_t0_by_threshold(
        self, patient_id: str, track: str, threshold: float
    ) -> float:
        """Busca el primer timestamp donde `track` supera `threshold`."""
        vf = self._open_vital(patient_id)
        try:
            trk = vf.trks.get(track)
            if not trk or not trk.recs:
                raise ValueError("track vacío")
            times = np.array([r['dt'] for r in trk.recs], dtype=np.float64)
            values = np.array([r['val'] for r in trk.recs], dtype=np.float32)
            mask = values > threshold
            if not mask.any():
                raise ValueError(f"nunca supera umbral {threshold}")
            return float(times[mask][0])
        except Exception as e:
            logger.warning(
                "[vitaldb] patient=%s: fallo detección t0 por umbral en '%s': %s; "
                "usando start_time del índice",
                patient_id, track, e,
            )
            ev = self._get_event_by_patient(patient_id)
            return to_epoch_utc(pd.Timestamp(ev["start_time"]))

    # ── list_patients ─────────────────────────────────────────────────────────

    def list_patients(self) -> list[str]:
        idx = self._load_index()
        return [ev["event_id"] for ev in idx.get("events", [])]

    # ── get_waveforms ─────────────────────────────────────────────────────────

    def get_waveforms(self, patient_id: str) -> dict[str, WaveformRecord]:
        t0_unix = self._get_t0_unix(patient_id)
        vf = self._open_vital(patient_id)
        waveforms: dict[str, WaveformRecord] = {}

        for signal_name in self.REQUIRED_WAVEFORMS:
            track_name = self._resolve_available_channel(signal_name, vf)

            if track_name is None:
                # P1: nombre no configurado aún → pendiente
                waveforms[signal_name] = self._unavailable_waveform(
                    patient_id, signal_name,
                    reason=f"canal no mapeado en config (pendiente P1)"
                )
                continue

            try:
                trk = vf.trks.get(track_name)
                if not trk or not trk.recs:
                    raise ValueError("track vacío")
                times, values = _flatten_track(trk)
            except Exception as e:
                logger.warning(
                    "[vitaldb] patient=%s canal=%s error: %s", patient_id, track_name, e
                )
                waveforms[signal_name] = self._unavailable_waveform(
                    patient_id, signal_name, reason=str(e)
                )
                continue

            if times is None or values is None or len(values) == 0:
                waveforms[signal_name] = self._unavailable_waveform(
                    patient_id, signal_name, reason="track vacío"
                )
                continue

            dt_arr = np.diff(times.astype(np.float64))
            dt_arr = dt_arr[dt_arr > 0]
            fs_native = 1.0 / float(np.median(dt_arr)) if len(dt_arr) > 0 else 0.0
            timestamps_rel = times.astype(np.float64) - t0_unix

            waveforms[signal_name] = WaveformRecord(
                patient_id=patient_id,
                signal_name=signal_name,
                fs_native=fs_native,
                timestamps_rel=timestamps_rel,
                values=values.astype(np.float32),
                available=True,
            )
            logger.debug(
                "[vitaldb] patient=%s %s fs=%.1f Hz n=%d",
                patient_id, signal_name, fs_native, len(values),
            )

        self._validate_waveforms(waveforms)
        return waveforms

    # ── get_numerics ──────────────────────────────────────────────────────────

    def get_numerics(self, patient_id: str) -> NumericsRecord:
        t0_unix = self._get_t0_unix(patient_id)
        vf = self._open_vital(patient_id)

        series_dict: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for canonical in STANDARD_NUMERIC_COLS:
            track = self._resolve_available_channel(canonical, vf)
            if track is None:
                continue
            try:
                trk = vf.trks.get(track)
                if trk and trk.recs:
                    times = np.array([r['dt'] for r in trk.recs], dtype=np.float64)
                    values = np.array([r['val'] for r in trk.recs], dtype=np.float32)
                    if len(times) > 0:
                        series_dict[canonical] = (
                            times - t0_unix,
                            values,
                        )
            except Exception as e:
                logger.debug("[vitaldb] patient=%s canal=%s error: %s",
                             patient_id, track, e)

        if not series_dict:
            return NumericsRecord(
                patient_id=patient_id,
                timestamps_rel=np.empty(0, dtype=np.float64),
                data=pd.DataFrame(columns=STANDARD_NUMERIC_COLS),
            )

        # Alinear a rejilla de 1 s (promediar duplicados por timestamp redondeado).
        grid: dict[str, pd.Series] = {}
        for canonical, (times, values) in series_dict.items():
            t = np.round(times).astype(np.float64)
            s = pd.Series(values, index=t)
            grid[canonical] = s.groupby(level=0).mean()
        df = pd.DataFrame(grid).sort_index()
        for col in STANDARD_NUMERIC_COLS:
            if col not in df.columns:
                df[col] = np.nan
        df = df[STANDARD_NUMERIC_COLS]
        timestamps_rel = df.index.to_numpy(dtype=np.float64)
        df = df.reset_index(drop=True)

        return NumericsRecord(
            patient_id=patient_id,
            timestamps_rel=timestamps_rel,
            data=df,
        )

    # ── get_clinical_events ───────────────────────────────────────────────────

    def get_clinical_events(self, patient_id: str) -> ClinicalEvents:
        ev = self._get_event_by_patient(patient_id)
        t0_unix = self._get_t0_unix(patient_id)
        if ev.get("tend_unix") is not None:
            t_end_unix = float(ev["tend_unix"])
        else:
            t_end_unix = to_epoch_utc(pd.Timestamp(ev["end_time"]))

        record_end_hours = (t_end_unix - t0_unix) / 3600.0
        extubation_hours = record_end_hours  # por construcción

        # P3: si hay fuente de reintubaciones, cargarla
        reintubation_source = self._event_source.get("reintubation_source")
        if reintubation_source is not None:
            logger.warning(
                "[vitaldb] patient=%s: carga de reintubaciones desde '%s' "
                "pendiente (P3)",
                patient_id, reintubation_source,
            )

        return ClinicalEvents(
            patient_id=patient_id,
            cohort="vitaldb",
            t0_unix=t0_unix,
            record_end_hours=record_end_hours,
            extubation_confirmed_hours=extubation_hours,
            extubation_confirmed=True,
            extubation_attempts=[
                ExtubationAttempt(
                    attempt_index=0,
                    time_rel_hours=extubation_hours,
                    outcome="success",
                )
            ],
            censored_no_extubation=False,
        )
