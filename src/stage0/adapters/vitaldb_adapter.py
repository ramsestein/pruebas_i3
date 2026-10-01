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

logger = logging.getLogger(__name__)

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

    def _get_t0_unix(self, patient_id: str) -> float:
        """
        t0 = inicio del episodio de VM.
        Por defecto: start_time del evento en el índice.
        Si la config especifica t0_source = "track_threshold", se usa
        el primer timestamp donde el track supera el umbral configurado.
        (P2: a confirmar con el usuario)
        """
        ev = self._get_event_by_patient(patient_id)
        t0_source = self._event_source.get("t0_source", "vital_file_start")

        if t0_source == "vital_file_start" or t0_source is None:
            return pd.Timestamp(ev["start_time"]).timestamp()

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
                return pd.Timestamp(ev["start_time"]).timestamp()

        # Fallback
        return pd.Timestamp(ev["start_time"]).timestamp()

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
            return pd.Timestamp(ev["start_time"]).timestamp()

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
            track_name = self._resolve_channel(signal_name)

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
                times = np.array([r['dt'] for r in trk.recs], dtype=np.float64)
                values = np.array([r['val'] for r in trk.recs], dtype=np.float32)
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
            track = self._resolve_channel(canonical)
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

        dfs = []
        for canonical, (times, values) in series_dict.items():
            dfs.append(pd.DataFrame(
                {canonical: values},
                index=pd.Index(times, name="time_rel_s"),
            ))
        df = pd.concat(dfs, axis=1).sort_index()
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
        t_end_unix = pd.Timestamp(ev["end_time"]).timestamp()

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
