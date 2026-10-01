"""
adapters/mimic_adapter.py
=========================
Adaptador para la cohorte MIMIC-III Waveform Database.

Fuente de datos:
  - Archivos .vital en mimic_cases_dir (enriquecidos con chartevents)
  - Índice JSON en mimic_index
  - Tablas clínicas en mimic_clinical_dir:
      ICUSTAYS.csv.gz, PROCEDUREEVENTS_MV.csv.gz,
      ADMISSIONS.csv.gz, PATIENTS.csv.gz  (P11: confirmar disponibilidad)

Particularidades de MIMIC:
  - NO tiene waveforms ECG ni PPG → WaveformRecord(available=False)
  - ABP waveform disponible solo en el matched-waveform subset (algunos casos)
  - Parámetros ventilatorios (PEEP, FiO2, TV…) ya fusionados como tracks
    MIMIC/PEEP, MIMIC/FiO2, etc. en los archivos _enriched
  - Edge case: si vent_end coincide con DEATHTIME → caso no extubado
    → censored_no_extubation=True

Lógica de extubación:
  - t0 = PROCEDUREEVENTS_MV.starttime (itemid 225792)
  - extubation_time = PROCEDUREEVENTS_MV.endtime (mismo registro)
  - Verificación: si ADMISSIONS.DEATHTIME <= endtime → censored
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
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

# Tolerancia temporal para comparar vent_end con DEATHTIME (5 minutos)
DEATH_TOLERANCE_S = 5 * 60


def _parse_mimic_filename(fname: str):
    """
    Extrae (subject_id, start_dt, end_dt) del nombre del archivo:
    mimic_{sid}_{YYYYMMDD}_{HHMMSS}_to_{YYYYMMDD}_{HHMMSS}.vital
    """
    m = re.search(
        r"mimic_(\d+)_(\d{8})_(\d{6})_to_(\d{8})_(\d{6})\.vital", fname
    )
    if not m:
        return None, None, None
    sid = int(m.group(1))
    start_dt = datetime.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M%S")
    end_dt = datetime.strptime(m.group(4) + m.group(5), "%Y%m%d%H%M%S")
    return sid, start_dt, end_dt


class MimicAdapter(CohortAdapter):
    """
    Adaptador para archivos .vital de mimic_full_cases_enriched.
    """

    @property
    def cohort_name(self) -> str:
        return "mimic"

    def __init__(self, config: dict):
        super().__init__(config)
        self._cases_dir = Path(config["paths"]["mimic_cases_dir"])
        self._clinical_dir = Path(config["paths"]["mimic_clinical_dir"])
        self._vf_cache: dict[str, vitaldb.VitalFile] = {}

        # Caches de tablas clínicas (carga perezosa)
        self._admissions: Optional[pd.DataFrame] = None
        self._procedureevents: Optional[pd.DataFrame] = None
        self._patients: Optional[pd.DataFrame] = None

        # Mapa patient_id → metadatos construido al escanear el directorio
        self._patient_meta: Optional[dict[str, dict]] = None

    # ── Escaneo de archivos ───────────────────────────────────────────────────

    def _build_patient_meta(self) -> dict[str, dict]:
        """Escanea casos_dir y construye mapa patient_id → metadatos."""
        meta: dict[str, dict] = {}
        for path in self._cases_dir.glob("mimic_*.vital"):
            sid, start_dt, end_dt = _parse_mimic_filename(path.name)
            if sid is None:
                continue
            patient_id = f"mimic_{sid}"
            # Si hay múltiples archivos para el mismo paciente, queda el último
            # procesado. En la práctica debería haber solo uno por paciente.
            if patient_id in meta:
                logger.warning(
                    "[mimic] Múltiples archivos para %s; usando %s",
                    patient_id, path.name,
                )
            meta[patient_id] = {
                "subject_id": sid,
                "file": path.name,
                "path": path,
                "start_dt": start_dt,
                "end_dt": end_dt,
                "t0_unix": start_dt.timestamp(),
                "tend_unix": end_dt.timestamp(),
            }
        return meta

    def _get_patient_meta(self, patient_id: str) -> dict:
        if self._patient_meta is None:
            self._patient_meta = self._build_patient_meta()
        if patient_id not in self._patient_meta:
            raise KeyError(f"[mimic] patient_id '{patient_id}' no encontrado")
        return self._patient_meta[patient_id]

    # ── Tablas clínicas (carga perezosa) ─────────────────────────────────────

    def _load_admissions(self) -> pd.DataFrame:
        if self._admissions is None:
            path = self._find_clinical_file("ADMISSIONS.csv.gz")
            if path is None:
                logger.warning("[mimic] ADMISSIONS.csv.gz no encontrado; "
                               "verificación de DEATHTIME deshabilitada")
                self._admissions = pd.DataFrame()
            else:
                df = pd.read_csv(
                    path, compression="gzip",
                    usecols=["SUBJECT_ID", "HADM_ID", "ADMITTIME", "DISCHTIME",
                             "DEATHTIME"],
                    parse_dates=["ADMITTIME", "DISCHTIME", "DEATHTIME"],
                )
                df.columns = df.columns.str.lower()
                self._admissions = df
        return self._admissions

    def _load_procedureevents(self) -> pd.DataFrame:
        if self._procedureevents is None:
            path = self._find_clinical_file("PROCEDUREEVENTS_MV.csv.gz")
            if path is None:
                logger.warning("[mimic] PROCEDUREEVENTS_MV.csv.gz no encontrado")
                self._procedureevents = pd.DataFrame()
            else:
                t0_itemid = self._event_source.get("t0_itemid", 225792)
                df = pd.read_csv(
                    path, compression="gzip",
                    usecols=["SUBJECT_ID", "HADM_ID", "ICUSTAY_ID",
                             "ITEMID", "STARTTIME", "ENDTIME"],
                    parse_dates=["STARTTIME", "ENDTIME"],
                )
                df.columns = df.columns.str.lower()
                # Solo eventos de VM invasiva
                df = df[df["itemid"] == t0_itemid].copy()
                self._procedureevents = df
        return self._procedureevents

    def _find_clinical_file(self, fname: str) -> Optional[Path]:
        for loc in [self._clinical_dir, Path(".")]:
            p = loc / fname
            if p.exists():
                return p
        return None

    # ── VitalFile cache ───────────────────────────────────────────────────────

    def _open_vital(self, patient_id: str) -> vitaldb.VitalFile:
        if patient_id not in self._vf_cache:
            self._vf_cache.clear()  # Liberar memoria del paciente anterior
            meta = self._get_patient_meta(patient_id)
            path = meta["path"]
            logger.debug("[mimic] Abriendo %s", path)
            self._vf_cache[patient_id] = vitaldb.VitalFile(str(path))
        return self._vf_cache[patient_id]

    # ── list_patients ─────────────────────────────────────────────────────────

    def list_patients(self) -> list[str]:
        if self._patient_meta is None:
            self._patient_meta = self._build_patient_meta()
        return sorted(self._patient_meta.keys())

    # ── get_waveforms ─────────────────────────────────────────────────────────

    def get_waveforms(self, patient_id: str) -> dict[str, WaveformRecord]:
        t0_unix = self._get_patient_meta(patient_id)["t0_unix"]
        vf = self._open_vital(patient_id)
        waveforms: dict[str, WaveformRecord] = {}

        for signal_name in self.REQUIRED_WAVEFORMS:
            # ECG y PPG no disponibles en MIMIC (por diseño)
            if signal_name in ("ECG", "PPG"):
                waveforms[signal_name] = self._unavailable_waveform(
                    patient_id, signal_name,
                    reason="MIMIC no tiene waveforms ECG/PPG en .vital"
                )
                continue

            # ABP: solo disponible en el matched-waveform subset
            track_name = self._resolve_channel(signal_name)
            if track_name is None:
                waveforms[signal_name] = self._unavailable_waveform(
                    patient_id, signal_name, reason="no mapeado en config"
                )
                continue

            try:
                trk = vf.trks.get(track_name)
                if not trk or not trk.recs:
                    raise ValueError("track vacío o no encontrado")
                times = np.array([r['dt'] for r in trk.recs], dtype=np.float64)
                values = np.array([r['val'] for r in trk.recs], dtype=np.float32)
            except Exception as e:
                logger.warning("[mimic] patient=%s canal=%s error: %s",
                               patient_id, track_name, e)
                waveforms[signal_name] = self._unavailable_waveform(
                    patient_id, signal_name, reason=str(e)
                )
                continue

            if times is None or values is None or len(values) == 0:
                waveforms[signal_name] = self._unavailable_waveform(
                    patient_id, signal_name, reason="track vacío o inexistente"
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
            logger.debug("[mimic] patient=%s ABP fs=%.1f Hz n=%d",
                         patient_id, fs_native, len(values))

        self._validate_waveforms(waveforms)
        return waveforms

    # ── get_numerics ──────────────────────────────────────────────────────────

    def get_numerics(self, patient_id: str) -> NumericsRecord:
        t0_unix = self._get_patient_meta(patient_id)["t0_unix"]
        vf = self._open_vital(patient_id)

        series_dict: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for canonical in STANDARD_NUMERIC_COLS:
            # outcome variables no van aquí (se extraen aparte)
            if canonical.startswith(("vasopressor", "lactate", "SOFA")):
                continue
            track = self._resolve_channel(canonical)
            if track is None:
                continue
            try:
                trk = vf.trks.get(track)
                if not trk or not trk.recs:
                    continue
                times = np.array([r['dt'] for r in trk.recs], dtype=np.float64)
                values = np.array([r['val'] for r in trk.recs], dtype=np.float32)
                if len(times) > 0:
                    series_dict[canonical] = (
                        times - t0_unix,
                        values,
                    )
            except Exception as e:
                logger.debug("[mimic] patient=%s canal=%s error: %s",
                             patient_id, track, e)

        if not series_dict:
            return NumericsRecord(
                patient_id=patient_id,
                timestamps_rel=np.empty(0, dtype=np.float64),
                data=pd.DataFrame(columns=STANDARD_NUMERIC_COLS),
            )

        dfs = [
            pd.DataFrame(
                {canonical: values},
                index=pd.Index(times, name="time_rel_s"),
            )
            for canonical, (times, values) in series_dict.items()
        ]
        df = pd.concat(dfs, axis=1).sort_index()
        for col in STANDARD_NUMERIC_COLS:
            if col not in df.columns:
                df[col] = np.nan
        df = df[STANDARD_NUMERIC_COLS]
        timestamps_rel = df.index.to_numpy(dtype=np.float64)

        return NumericsRecord(
            patient_id=patient_id,
            timestamps_rel=timestamps_rel,
            data=df.reset_index(drop=True),
        )

    # ── get_clinical_events ───────────────────────────────────────────────────

    def get_clinical_events(self, patient_id: str) -> ClinicalEvents:
        meta = self._get_patient_meta(patient_id)
        sid = meta["subject_id"]
        t0_unix = meta["t0_unix"]
        tend_unix = meta["tend_unix"]
        record_end_hours = (tend_unix - t0_unix) / 3600.0

        # Verificar si vent_end coincide con DEATHTIME (edge case)
        censored, censor_reason = self._check_death_at_vent_end(sid, tend_unix)

        if censored:
            logger.info(
                "[mimic] patient=%s censored_no_extubation: %s",
                patient_id, censor_reason,
            )
            return ClinicalEvents(
                patient_id=patient_id,
                cohort="mimic",
                t0_unix=t0_unix,
                record_end_hours=record_end_hours,
                extubation_confirmed=False,
                extubation_confirmed_hours=None,
                extubation_attempts=[],
                censored_no_extubation=True,
                censored_reason=censor_reason,
            )

        # Caso normal: extubación confirmada al fin del episodio
        extubation_hours = record_end_hours

        # Cargar reintubaciones adicionales desde PROCEDUREEVENTS_MV si existen
        extubation_attempts = self._load_extubation_attempts(
            sid=sid,
            t0_unix=t0_unix,
            default_extubation_hours=extubation_hours,
        )

        return ClinicalEvents(
            patient_id=patient_id,
            cohort="mimic",
            t0_unix=t0_unix,
            record_end_hours=record_end_hours,
            extubation_confirmed=True,
            extubation_confirmed_hours=extubation_hours,
            extubation_attempts=extubation_attempts,
            censored_no_extubation=False,
        )

    def _check_death_at_vent_end(
        self, subject_id: int, tend_unix: float
    ) -> tuple[bool, Optional[str]]:
        """
        Comprueba si la fecha de fin de VM coincide con DEATHTIME en ADMISSIONS.
        Si DEATHTIME está dentro de ±5 min de vent_end → caso no extubado.
        """
        admissions = self._load_admissions()
        if admissions.empty:
            return False, None

        pat_adm = admissions[admissions["subject_id"] == subject_id]
        for _, row in pat_adm.iterrows():
            if pd.isna(row.get("deathtime")):
                continue
            death_unix = pd.Timestamp(row["deathtime"]).timestamp()
            if abs(death_unix - tend_unix) <= DEATH_TOLERANCE_S:
                return True, "death_at_vent_end"

        return False, None

    def _load_extubation_attempts(
        self,
        sid: int,
        t0_unix: float,
        default_extubation_hours: float,
    ) -> list[ExtubationAttempt]:
        """
        Intenta detectar reintubaciones como múltiples episodios consecutivos
        de PROCEDUREEVENTS_MV (itemid 225792) para el mismo sujeto.
        Si no hay tabla clínica, devuelve un único intento exitoso.
        """
        proc = self._load_procedureevents()
        if proc.empty:
            return [
                ExtubationAttempt(
                    attempt_index=0,
                    time_rel_hours=default_extubation_hours,
                    outcome="success",
                )
            ]

        pat = proc[proc["subject_id"] == sid].sort_values("starttime").reset_index(drop=True)
        if len(pat) <= 1:
            # Un solo episodio → un intento exitoso
            return [
                ExtubationAttempt(
                    attempt_index=0,
                    time_rel_hours=default_extubation_hours,
                    outcome="success",
                )
            ]

        # Múltiples episodios: cada fin de episodio → intento de extubación
        # El siguiente inicio es la reintubación → fallo
        attempts: list[ExtubationAttempt] = []
        for i in range(len(pat)):
            end_unix = pat.loc[i, "endtime"].timestamp()
            attempt_hours = (end_unix - t0_unix) / 3600.0

            if i < len(pat) - 1:
                # Hay un episodio siguiente → este intento fue un fallo
                next_start_unix = pat.loc[i + 1, "starttime"].timestamp()
                reintub_hours = (next_start_unix - t0_unix) / 3600.0
                attempts.append(ExtubationAttempt(
                    attempt_index=i,
                    time_rel_hours=attempt_hours,
                    outcome="failure",
                    reintubation_time_rel_hours=reintub_hours,
                ))
            else:
                # Último episodio → extubación exitosa
                attempts.append(ExtubationAttempt(
                    attempt_index=i,
                    time_rel_hours=attempt_hours,
                    outcome="success",
                ))

        return attempts
