"""
adapters/clinic_adapter.py
==========================
Adaptador para la cohorte del Hospital Clínic.

Fuente de datos:
  - Archivos .vital en clinic_cases_dir (uno por episodio de VM)
  - Índice JSON en clinic_index
  - Tabla clínica externa con reintubaciones (si disponible, config: event_sources.clinic)

Garantías por construcción:
  - Todos los casos terminan en extubación exitosa (CO2+TV_EXP desaparecen al final)
  - t0 = primer timestamp del archivo = inicio de VM
  - t_extubation = último timestamp del archivo
"""

from __future__ import annotations

import json
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

# Columnas numéricas estándar (salida de get_numerics)
STANDARD_NUMERIC_COLS = [
    "HR", "SBP", "DBP", "MAP", "SpO2", "RR",
    "FiO2", "PEEP", "TV", "MV", "PIP",
    "vasopressor_norepinephrine", "vasopressor_epinephrine",
    "vasopressor_dopamine", "lactate",
    "SOFA_respiratory", "SOFA_cardiovascular", "SOFA_total",
]


# Alias de nombres de pista por caja: distintas cajas de Clínic usan ART vs ABP,
# ECG_HR vs PLETH_HR vs HR, VENT_RR vs RR. Orden = prioridad de resolución.
CLINIC_CHANNEL_ALIASES: dict[str, list[str]] = {
    "ABP": ["Intellivue/ART", "Intellivue/ABP"],
    "SBP": ["Intellivue/ART_SYS", "Intellivue/ABP_SYS", "Intellivue/NIBP_SYS"],
    "DBP": ["Intellivue/ART_DIA", "Intellivue/ABP_DIA", "Intellivue/NIBP_DIA"],
    "MAP": ["Intellivue/ART_MEAN", "Intellivue/ABP_MEAN", "Intellivue/NIBP_MEAN"],
    "HR":  ["Intellivue/ECG_HR", "Intellivue/PLETH_HR", "Intellivue/HR"],
    "RR":  ["Intellivue/VENT_RR", "Intellivue/RR"],
}


class ClinicAdapter(CohortAdapter):
    """
    Adaptador para archivos .vital de clinic_full_cases.

    Los archivos .vital son producidos por build_clinical_cases.py y contienen
    un único episodio de VM por archivo, con señales Intellivue a ~500 Hz
    (waveforms) y 1 Hz (numéricos).
    """

    @property
    def cohort_name(self) -> str:
        return "clinic"

    def __init__(self, config: dict):
        super().__init__(config)
        self._cases_dir = Path(config["paths"]["clinic_cases_dir"])
        self._index_path = Path(config["paths"]["clinic_index"])
        self._index: Optional[dict] = None
        # Cache de archivos .vital abiertos (evita re-parseo)
        self._vf_cache: dict[str, vitaldb.VitalFile] = {}

    # ── Index ─────────────────────────────────────────────────────────────────

    def _load_index(self) -> dict:
        if self._index is None:
            if not self._index_path.exists():
                logger.warning(
                    f"Índice de Clínic no encontrado: {self._index_path}. "
                    "Auto-descubriendo casos desde archivos .vital..."
                )
                self._index = self._build_index_from_files()
            else:
                with open(self._index_path, encoding="utf-8") as f:
                    self._index = json.load(f)
        return self._index

    def _build_index_from_files(self) -> dict:
        events = []
        for path in self._cases_dir.glob("*.vital"):
            # fname format: box10_250414_121109_to_250417_031058.vital
            m = re.search(r"(box\d+)_(\d{6})_(\d{6})_to_(\d{6})_(\d{6})\.vital", path.name)
            if not m:
                continue
            
            box, start_date, start_time, end_date, end_time = m.groups()
            s_dt = datetime.strptime(start_date + start_time, "%y%m%d%H%M%S")
            e_dt = datetime.strptime(end_date + end_time, "%y%m%d%H%M%S")
            
            events.append({
                "event_id": path.name.replace(".vital", ""),
                "file": path.name,
                "start_time": s_dt.isoformat(),
                "end_time": e_dt.isoformat(),
            })
        return {"events": events}

    def _get_event_by_patient(self, patient_id: str) -> dict:
        idx = self._load_index()
        for ev in idx.get("events", []):
            if ev.get("event_id") == patient_id:
                return ev
        raise KeyError(f"[clinic] patient_id '{patient_id}' no encontrado en índice")

    # ── VitalFile cache ───────────────────────────────────────────────────────

    def _open_vital(self, patient_id: str) -> vitaldb.VitalFile:
        if patient_id not in self._vf_cache:
            self._vf_cache.clear()  # Liberar memoria del paciente anterior
            ev = self._get_event_by_patient(patient_id)
            path = self._cases_dir / ev["file"]
            if not path.exists():
                raise FileNotFoundError(f"[clinic] Archivo no encontrado: {path}")
            logger.debug("[clinic] Abriendo %s", path)
            self._vf_cache[patient_id] = vitaldb.VitalFile(str(path))
        return self._vf_cache[patient_id]

    def _resolve_available_channel(
        self, canonical_name: str, vf: vitaldb.VitalFile
    ) -> Optional[str]:
        """Devuelve el nombre de pista realmente presente en el fichero.

        Prueba primero el canal configurado y luego los alias conocidos de la
        caja (ART vs ABP, ECG_HR vs HR...). Devuelve None si ninguno existe.
        """
        candidates: list[str] = []
        configured = self._resolve_channel(canonical_name)
        if configured:
            candidates.append(configured)
        for alt in CLINIC_CHANNEL_ALIASES.get(canonical_name, []):
            if alt not in candidates:
                candidates.append(alt)
        for c in candidates:
            trk = vf.trks.get(c)
            if trk is not None and trk.recs:
                return c
        return configured

    def _get_t0_unix(self, patient_id: str) -> float:
        """t0 = timestamp epoch absoluto del evento (prefiere t0_unix del índice)."""
        ev = self._get_event_by_patient(patient_id)
        if ev.get("t0_unix") is not None:
            return float(ev["t0_unix"])
        return to_epoch_utc(pd.Timestamp(ev["start_time"]))

    def _get_tend_unix(self, patient_id: str) -> float:
        """Fin del evento en epoch (prefiere tend_unix del índice)."""
        ev = self._get_event_by_patient(patient_id)
        if ev.get("tend_unix") is not None:
            return float(ev["tend_unix"])
        return to_epoch_utc(pd.Timestamp(ev["end_time"]))

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
                waveforms[signal_name] = self._unavailable_waveform(
                    patient_id, signal_name, reason="no mapeado en config"
                )
                continue

            try:
                # vitaldb devuelve (times, values) arrays numpy
                trk = vf.trks.get(track_name)
                if not trk or not trk.recs:
                    raise ValueError("track vacío o no encontrado")
                times, values = _flatten_track(trk)
            except Exception as e:
                logger.warning(
                    "[clinic] patient=%s canal=%s error al leer: %s",
                    patient_id, track_name, e,
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

            # Inferir fs desde diferencias de timestamps
            dt_arr = np.diff(times.astype(np.float64))
            dt_arr = dt_arr[dt_arr > 0]
            fs_native = 1.0 / float(np.median(dt_arr)) if len(dt_arr) > 0 else 0.0

            # Convertir timestamps a segundos desde t0
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
                "[clinic] patient=%s %s fs=%.1f Hz n=%d dur=%.1fh",
                patient_id, signal_name, fs_native, len(values),
                (timestamps_rel[-1] - timestamps_rel[0]) / 3600,
            )

        self._validate_waveforms(waveforms)
        return waveforms

    # ── get_numerics ──────────────────────────────────────────────────────────

    def get_numerics(self, patient_id: str) -> NumericsRecord:
        t0_unix = self._get_t0_unix(patient_id)
        vf = self._open_vital(patient_id)

        # Resolver cada canal numérico al nombre de pista presente en el fichero
        series_dict: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for canonical in STANDARD_NUMERIC_COLS:
            track = self._resolve_available_channel(canonical, vf)
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
                logger.debug("[clinic] patient=%s canal=%s error numérico: %s",
                             patient_id, track, e)

        if not series_dict:
            return NumericsRecord(
                patient_id=patient_id,
                timestamps_rel=np.empty(0, dtype=np.float64),
                data=pd.DataFrame(columns=STANDARD_NUMERIC_COLS),
            )

        # Unificar en un DataFrame alineado por tiempo: redondear timestamps a
        # una rejilla de 1 s y promediar duplicados (cada canal tiene su propio
        # jitter a ~1 Hz, lo que rompía el outer-join por índice no único).
        grid: dict[str, pd.Series] = {}
        for canonical, (times, values) in series_dict.items():
            t = np.round(times).astype(np.float64)
            s = pd.Series(values, index=t)
            grid[canonical] = s.groupby(level=0).mean()
        df = pd.DataFrame(grid).sort_index()

        # Asegurar que todas las columnas estándar existen (NaN si faltan)
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
        t_end_unix = self._get_tend_unix(patient_id)

        record_end_hours = (t_end_unix - t0_unix) / 3600.0
        extubation_hours = record_end_hours  # por construcción

        # Cargar reintubaciones desde tabla externa (si configurada)
        reintubation_source = self._event_source.get("reintubation_source")
        extubation_attempts = self._load_extubation_attempts(
            patient_id=patient_id,
            t0_unix=t0_unix,
            default_extubation_hours=extubation_hours,
            reintubation_source=reintubation_source,
        )

        return ClinicalEvents(
            patient_id=patient_id,
            cohort="clinic",
            t0_unix=t0_unix,
            record_end_hours=record_end_hours,
            extubation_confirmed_hours=extubation_hours,
            extubation_confirmed=True,   # garantía por construcción
            extubation_attempts=extubation_attempts,
            censored_no_extubation=False,
        )

    def _load_extubation_attempts(
        self,
        patient_id: str,
        t0_unix: float,
        default_extubation_hours: float,
        reintubation_source: Optional[str],
    ) -> list[ExtubationAttempt]:
        """
        Carga intentos de extubación desde tabla clínica externa.
        Si no hay fuente configurada, devuelve un único intento exitoso al final.
        """
        if reintubation_source is None:
            # Sin tabla de reintubaciones: único intento, exitoso
            logger.debug(
                "[clinic] patient=%s: sin fuente de reintubaciones; "
                "asumiendo único intento exitoso a %.1fh",
                patient_id, default_extubation_hours,
            )
            return [
                ExtubationAttempt(
                    attempt_index=0,
                    time_rel_hours=default_extubation_hours,
                    outcome="success",
                )
            ]

        # TODO: implementar cuando el usuario proporcione la tabla clínica (P6)
        # La tabla debería tener columnas:
        #   patient_join_key, event_type, event_time (datetime)
        # donde event_type ∈ {'extubation', 'reintubation'}
        logger.warning(
            "[clinic] patient=%s: carga de reintubaciones desde '%s' "
            "aún no implementada (pendiente P6)",
            patient_id, reintubation_source,
        )
        return [
            ExtubationAttempt(
                attempt_index=0,
                time_rel_hours=default_extubation_hours,
                outcome="success",
            )
        ]
