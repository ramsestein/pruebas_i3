"""
adapters/eicu_adapter.py
========================
Adaptador para la cohorte eICU Collaborative.

Fuente de datos:
  - datasets/eicu_collaborative/*.csv.gz

Particularidades de eICU:
  - Identificador de paciente/ingreso: `patientunitstayid`.
  - Tiempos: Se miden en `offset` (minutos desde la admisión a la UCI).
  - T0: Definido por `ventstartoffset` y `ventendoffset` de `respiratoryCare.csv.gz`.
    - Si el paciente tiene múltiples episodios, los evaluaremos todos o tomaremos el primero/último.
    - Opcionalmente también se puede mirar `treatment.csv.gz`.
  - No tiene waveforms (disponibilidad = False).
  - Constantes numéricas en `vitalPeriodic.csv.gz` (resolución 5 minutos) y `respiratoryCharting.csv.gz`.
"""

from __future__ import annotations

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
from ...common.eicu_rules import (
    eicu_t0_minutes,
    merge_vent_episodes,
    pick_map_source,
    sanitize_vent_episodes,
)

logger = logging.getLogger(__name__)


class EicuAdapter(CohortAdapter):
    """
    Adaptador para archivos de la cohorte eICU Collaborative.
    """

    @property
    def cohort_name(self) -> str:
        return "eicu"

    def __init__(self, config: dict):
        super().__init__(config)
        self._data_dir = Path(config["paths"].get("eicu_dir", "datasets/eicu_collaborative"))
        self._vital_dir = self._data_dir / "eicu_full_cases"
        
        # Caches de datos
        self._patients_df: Optional[pd.DataFrame] = None
        self._respcare_df: Optional[pd.DataFrame] = None
        
        self._vf_cache: dict[str, vitaldb.VitalFile] = {}
        self._patient_ids: list[str] = []

    def _load_patients(self):
        if self._patients_df is None:
            path = self._data_dir / "patient.csv.gz"
            if not path.exists():
                logger.warning(f"[eicu] No se encontró {path}")
                self._patients_df = pd.DataFrame()
                return
            
            self._patients_df = pd.read_csv(
                path, compression='gzip',
                usecols=['patientunitstayid', 'hospitaldischargeoffset', 'unitdischargeoffset', 'unitdischargestatus', 'hospitaldischargestatus']
            )
            # Normalizamos el patient_id a eicu_{id}
            self._patients_df['patient_id'] = 'eicu_' + self._patients_df['patientunitstayid'].astype(str)
            self._patients_df.set_index('patient_id', inplace=True)
            self._patient_ids = self._patients_df.index.tolist()

    def _load_respcare(self):
        if self._respcare_df is None:
            path = self._data_dir / "respiratoryCare.csv.gz"
            if not path.exists():
                logger.warning(f"[eicu] No se encontró {path}")
                self._respcare_df = pd.DataFrame()
                return
                
            self._respcare_df = pd.read_csv(
                path, compression='gzip',
                usecols=['patientunitstayid', 'ventstartoffset', 'ventendoffset', 'respcarestatusoffset']
            )
            self._respcare_df.dropna(subset=['ventstartoffset'], inplace=True)
            self._respcare_df['patient_id'] = 'eicu_' + self._respcare_df['patientunitstayid'].astype(str)
            
            # eICU quirk: ventendoffset a menudo viene como 0 o nulo.
            # Aproximamos el final del episodio usando el último respcarestatusoffset registrado.
            df = self._respcare_df
            df['ventendoffset'] = df['ventendoffset'].fillna(0)
            
            max_status = df.groupby(['patient_id', 'ventstartoffset'])['respcarestatusoffset'].transform('max')
            
            mask = df['ventendoffset'] <= 0
            df.loc[mask, 'ventendoffset'] = max_status[mask]
            
            # Garantizar que el final sea mayor al inicio para no perder el episodio
            mask_equal = df['ventendoffset'] <= df['ventstartoffset']
            df.loc[mask_equal, 'ventendoffset'] = df.loc[mask_equal, 'ventstartoffset'] + 1
            
            self._respcare_df = df

    def _open_vital(self, patient_id: str) -> vitaldb.VitalFile:
        if patient_id not in self._vf_cache:
            self._vf_cache.clear()  # Liberar memoria del paciente anterior
            path = self._vital_dir / f"{patient_id}.vital"
            logger.debug("[eicu] Abriendo %s", path)
            self._vf_cache[patient_id] = vitaldb.VitalFile(str(path))
        return self._vf_cache[patient_id]

    def list_patients(self) -> list[str]:
        self._load_patients()
        # Filtramos para incluir solo aquellos que están en respcare (que tuvieron ventilación)
        self._load_respcare()
        if not self._respcare_df.empty:
            vent_patients = set(self._respcare_df['patient_id'].unique())
            return [p for p in self._patient_ids if p in vent_patients]
        return self._patient_ids

    def get_waveforms(self, patient_id: str) -> dict[str, WaveformRecord]:
        # eICU no tiene waveforms de alta frecuencia
        wfs = {}
        for req in self.REQUIRED_WAVEFORMS:
            wfs[req] = self._unavailable_waveform(patient_id, req, "eICU no provee waveforms de alta frecuencia")
        return wfs

    def get_numerics(self, patient_id: str) -> NumericsRecord:
        events = self.get_clinical_events(patient_id)
        # eICU guarda los vitals ya en segundos desde T0 internamente si usamos la logica pre-T0?
        # NO, en eicu_full_cases guardamos desde T0 como 0.0 segundos usando t0_sec=0.0.
        # Por lo tanto, no hace falta restar t0_unix a las marcas de tiempo extraidas del .vital.
        
        try:
            vf = self._open_vital(patient_id)
        except Exception as e:
            logger.warning("[eicu] Error leyendo vital %s: %s", patient_id, e)
            return NumericsRecord(patient_id=patient_id, timestamps_rel=np.array([]), data=pd.DataFrame())
            
        series_dict: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        
        for canon_name, source_track in self._channel_map.items():
            if source_track is None:
                continue

            track_name = source_track
            if isinstance(source_track, list):
                track_name = source_track[0]

            # D7: MAP invasiva -> no invasiva, según las pistas presentes.
            if canon_name == "MAP":
                chosen = pick_map_source(vf.trks.keys())
                if chosen is not None:
                    track_name = chosen

            try:
                trk = vf.trks.get(track_name)
                if trk and trk.recs:
                    times = np.array([r['dt'] for r in trk.recs], dtype=np.float64)
                    vals = np.array([r['val'] for r in trk.recs], dtype=np.float32)
                    if len(times) > 0:
                        series_dict[canon_name] = (times, vals)
            except Exception as e:
                logger.debug("[eicu] patient=%s canal=%s error al extraer recs: %s", patient_id, track_name, e)
                
        if not series_dict:
            return NumericsRecord(patient_id=patient_id, timestamps_rel=np.array([]), data=pd.DataFrame())
            
        # Fusionar cada serie respetando sus marcas de tiempo individuales para alinear
        dfs = [
            pd.DataFrame(
                {canon_name: vals},
                index=pd.Index(times, name="time_rel_s"),
            )
            for canon_name, (times, vals) in series_dict.items()
        ]
        
        # Eliminar duplicados en el índice de tiempo si existieran antes de concatenar
        dfs = [df[~df.index.duplicated(keep='last')] for df in dfs]
        
        df_merged = pd.concat(dfs, axis=1).sort_index()
        
        return NumericsRecord(
            patient_id=patient_id,
            timestamps_rel=df_merged.index.to_numpy(dtype=np.float64),
            data=df_merged.reset_index(drop=True)
        )

    def get_clinical_events(self, patient_id: str) -> ClinicalEvents:
        self._load_patients()
        self._load_respcare()
        
        t0_unix = 0.0
        record_end_hours = 0.0
        extub_confirmed_hours = None
        extubation_confirmed = False
        censored_no_extubation = False
        censored_reason = None
        attempts = []
        
        if patient_id in self._patients_df.index:
            pt_meta = self._patients_df.loc[patient_id]
            # En eICU usamos los "offset" (minutos) como pseudo-timestamps.
            # El alta a la unidad será nuestro fin de registro.
            record_end_hours = pt_meta['unitdischargeoffset'] / 60.0
            
            # Buscar episodios de ventilación
            if not self._respcare_df.empty:
                pt_vent = self._respcare_df[self._respcare_df['patient_id'] == patient_id].copy()
                pt_vent.sort_values('ventstartoffset', inplace=True)

                if not pt_vent.empty:
                    # Reglas explícitas para offsets fuera de la estancia (Fase 1.4):
                    # se recortan y se registran, nunca se silencian.
                    san = sanitize_vent_episodes(
                        pt_vent[['patientunitstayid', 'ventstartoffset', 'ventendoffset']],
                        float(pt_meta['unitdischargeoffset']),
                    )
                    for a in san.anomalies:
                        logger.warning(
                            "[eicu] patient=%s anomalía %s: %s", patient_id, a.kind, a.detail
                        )
                    merged = merge_vent_episodes(san.episodes)

                    # t0 = inicio de la ventilación (función compartida con el builder).
                    t0_minutes = eicu_t0_minutes(san.episodes)

                    if t0_minutes is not None and len(merged) > 0:
                        t0_unix = t0_minutes * 60.0  # pseudo-epoch (segundos)

                        record_end_hours = (pt_meta['unitdischargeoffset'] - t0_minutes) / 60.0
                        
                        # Cada episodio subsecuente es una reintubación
                        for i in range(len(merged)):
                            episode = merged.iloc[i]
                            # Extubación: end of this episode
                            extub_time = (episode['ventendoffset'] - t0_minutes) / 60.0
                            
                            # Si es la primera extubación (índice 0), time_rel_hours es 0
                            # Si hay un episodio siguiente, entonces falló
                            outcome = 'success'
                            reintub_time = None
                            
                            if i + 1 < len(merged):
                                next_episode = merged.iloc[i+1]
                                reintub_time = (next_episode['ventstartoffset'] - t0_minutes) / 60.0

                                # NO se aplica aquí la ventana de fallo (D3): la
                                # clasificación por ventanas la hace `classify_attempts`.
                                outcome = 'failure'

                            attempts.append(ExtubationAttempt(
                                attempt_index=i,
                                time_rel_hours=extub_time,
                                outcome=outcome,
                                reintubation_time_rel_hours=reintub_time
                            ))
                            
                        # Si el último episodio termina en muerte o alta, es censored
                        if pt_meta['unitdischargestatus'] == 'Expired':
                            # Ver si vent_end coincide con unitdischarge
                            last_end = merged.iloc[-1]['ventendoffset']
                            if abs(last_end - pt_meta['unitdischargeoffset']) < 120:
                                censored_no_extubation = True
                                censored_reason = "death_at_vent_end"
                                
                        if attempts:
                            last_att = attempts[-1]
                            if last_att.outcome == 'success' and not censored_no_extubation:
                                extubation_confirmed = True
                                extub_confirmed_hours = last_att.time_rel_hours

        return ClinicalEvents(
            patient_id=patient_id,
            cohort=self.cohort_name,
            t0_unix=t0_unix,
            record_end_hours=record_end_hours,
            extubation_confirmed_hours=extub_confirmed_hours,
            extubation_confirmed=extubation_confirmed,
            extubation_attempts=attempts,
            censored_no_extubation=censored_no_extubation,
            censored_reason=censored_reason
        )

    def _merge_vent_episodes(self, df: pd.DataFrame, gap_tolerance_mins: float = 120.0) -> pd.DataFrame:
        """Compatibilidad: delega en ``src.common.eicu_rules.merge_vent_episodes``."""
        if df.empty:
            return df
        return merge_vent_episodes(df, gap_tolerance_min=gap_tolerance_mins)
