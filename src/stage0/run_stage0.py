"""
run_stage0.py
=============
Orquestador de la Etapa 0: armonización, etiquetado y QC.

Uso:
    python src/stage0/run_stage0.py
    python src/stage0/run_stage0.py --config src/stage0/config/harmonize.yaml
    python src/stage0/run_stage0.py --cohorts clinic vitaldb  (solo esas cohortes)
    python src/stage0/run_stage0.py --dry-run  (sin escribir archivos)

Flujo:
  1. Cargar config + calcular dataset_version
  2. Por cada cohorte activa:
     a. Listar pacientes
     b. Por cada paciente:
        i.  get_clinical_events → survival rows
        ii. get_numerics → NumericsRecord
        iii.get_waveforms → WaveformRecord × 3
        iv. Resamplear + filtrar waveforms
        v.  Generar landmarks
        vi. Por cada landmark:
              - Extraer ventana de waveform
              - Calcular SQI
              - Aplicar máscara
              - Escribir NPZ
        vii. Calcular disponibilidad de canal
  3. Construir tablas de supervivencia (48h, 72h)
  4. Escribir todas las tablas parquet
  5. Generar reporte de QC
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
from pathlib import Path

import numpy as np

# ── Setup de imports (manejo de imports relativos cuando se ejecuta directamente) ─
# Añadir raíz del proyecto al path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from src.stage0.adapters import get_adapter
from src.stage0.harmonize.availability import (
    build_availability_table,
    compute_availability_row,
)
from src.stage0.harmonize.filter import apply_filters_from_config
from src.stage0.harmonize.resample import resample_signal
from src.stage0.io.versioning import load_config, make_dataset_version, save_version_manifest
from src.stage0.io.writers import (
    write_all_tables,
    write_sqi_mask,
    write_waveform_window,
    write_parquet,
)
from src.stage0.labeling.landmarks import (
    build_landmarks_table,
    compute_numerics_completeness,
    extract_waveform_window,
    generate_landmark_grid,
)
from src.stage0.labeling.survival import build_survival_tables
from src.stage0.qc.sqi import compute_sqi

import pandas as pd

logger = logging.getLogger(__name__)

COHORTS = ["mimic", "vitaldb", "clinic", "eicu"]


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def fix_seeds(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)


def process_patient(
    patient_id: str,
    adapter,
    config: dict,
    dataset_version: str,
    output_dir: Path,
    dry_run: bool = False,
) -> tuple[dict | None, dict | None, list[dict]]:
    """
    Procesa un paciente: waveforms, numéricos, landmarks, SQI.

    Returns:
        (availability_row, clinical_events, landmark_rows)
        None en availability_row o clinical_events si hubo error.
    """
    harm_cfg = config["harmonize"]
    target_fs = float(harm_cfg["target_fs"])
    delta_min = float(harm_cfg["landmark_delta_min"])
    window_min = float(harm_cfg["window_length_min"])
    window_mode = str(harm_cfg.get("window_mode", "trailing"))
    sqi_cfg = config.get("sqi", {})
    filter_cfg = config.get("filters", {})

    # 1. Eventos clínicos
    try:
        events = adapter.get_clinical_events(patient_id)
    except Exception as e:
        logger.error("[run] patient=%s: error en get_clinical_events: %s", patient_id, e)
        return None, None, []

    # 2. Numéricos
    try:
        numerics = adapter.get_numerics(patient_id)
        # Timestamps de numéricos en horas para los cálculos de landmark
        num_times_h = numerics.timestamps_rel / 3600.0
    except Exception as e:
        logger.error("[run] patient=%s: error en get_numerics: %s", patient_id, e)
        numerics = None
        num_times_h = np.array([], dtype=np.float64)

    # 3. Waveforms: resamplear + filtrar
    try:
        raw_waveforms = adapter.get_waveforms(patient_id)
    except Exception as e:
        logger.error("[run] patient=%s: error en get_waveforms: %s", patient_id, e)
        raw_waveforms = {}

    # Resamplear y filtrar cada señal disponible
    waveforms_resampled: dict[str, tuple[np.ndarray, np.ndarray]] = {}  # name → (values, times_h)
    for sig_name, rec in raw_waveforms.items():
        if not rec.available or rec.n_samples == 0:
            continue
        try:
            values_res, times_res = resample_signal(
                rec.values, rec.fs_native, target_fs,
                timestamps_rel=rec.timestamps_rel,
            )
            # Filtrar
            filtered_dict = apply_filters_from_config(
                {sig_name: values_res}, target_fs, filter_cfg
            )
            values_filt = filtered_dict[sig_name]
            waveforms_resampled[sig_name] = (values_filt, times_res / 3600.0)
        except Exception as e:
            logger.warning("[run] patient=%s %s: error resampleo/filtrado: %s",
                           patient_id, sig_name, e)

    # 4. Disponibilidad
    availability_row = compute_availability_row(
        patient_id=patient_id,
        cohort=events.cohort,
        waveforms=raw_waveforms,
        numerics=numerics if numerics is not None else _empty_numerics(patient_id),
    )

    # 5. Generar rejilla de landmarks
    lm_rows = generate_landmark_grid(events, delta_min, window_min, window_mode)

    if not lm_rows:
        logger.warning("[run] patient=%s: sin landmarks generados", patient_id)
        return availability_row, events, []

    # 6. Por cada landmark: extraer ventana, calcular SQI, escribir NPZ
    version_dir = output_dir / dataset_version
    waveform_dir = version_dir / "waveform_windows"
    sqi_dir = version_dir / "sqi_masks"
    numerics_dir = version_dir / "numerics"

    if not dry_run and numerics is not None and not numerics.data.empty:
        df_num = numerics.data.copy()
        df_num['patient_id'] = patient_id
        df_num['time_rel_hours'] = num_times_h
        write_parquet(df_num, numerics_dir / f"{patient_id}.parquet", description=f"numerics {patient_id}")

    window_length_s = window_min * 60.0
    n_samples_window = round(window_length_s * target_fs)

    for lm in lm_rows:
        idx = lm["landmark_idx"]
        win_start_h = lm["window_start_hours"]
        win_end_h = lm["window_end_hours"]

        # Extraer y rellenar a longitud fija con NaN
        def _extract(sig_name: str) -> np.ndarray:
            if sig_name not in waveforms_resampled:
                return np.full(n_samples_window, np.nan, dtype=np.float32)
            vals, times_h = waveforms_resampled[sig_name]
            win = extract_waveform_window(vals, times_h, win_start_h, win_end_h)
            if len(win) == 0:
                return np.full(n_samples_window, np.nan, dtype=np.float32)
            # Ajustar longitud
            if len(win) > n_samples_window:
                return win[:n_samples_window]
            if len(win) < n_samples_window:
                pad = np.full(n_samples_window - len(win), np.nan, dtype=np.float32)
                return np.concatenate([pad, win])  # padding al inicio
            return win

        ecg_win = _extract("ECG")
        ppg_win = _extract("PPG")
        abp_win = _extract("ABP")

        # SQI
        ecg_sqi_vals = compute_sqi("ECG", ecg_win, target_fs, sqi_cfg)
        ppg_sqi_vals = compute_sqi("PPG", ppg_win, target_fs, sqi_cfg)
        abp_sqi_vals = compute_sqi("ABP", abp_win, target_fs, sqi_cfg)

        # Máscaras (accept si SQI > 0)
        ecg_mask = ecg_sqi_vals > 0 if len(ecg_sqi_vals) > 0 else np.array([], dtype=bool)
        ppg_mask = ppg_sqi_vals > 0 if len(ppg_sqi_vals) > 0 else np.array([], dtype=bool)
        abp_mask = abp_sqi_vals > 0 if len(abp_sqi_vals) > 0 else np.array([], dtype=bool)

        # Actualizar metadatos del landmark
        lm["ecg_available"] = bool("ECG" in waveforms_resampled and
                                   np.isfinite(ecg_win).any())
        lm["ppg_available"] = bool("PPG" in waveforms_resampled and
                                   np.isfinite(ppg_win).any())
        lm["abp_available"] = bool("ABP" in waveforms_resampled and
                                   np.isfinite(abp_win).any())
        lm["ecg_sqi_mean"] = float(np.nanmean(ecg_sqi_vals)) if len(ecg_sqi_vals) > 0 else np.nan
        lm["ppg_sqi_mean"] = float(np.nanmean(ppg_sqi_vals)) if len(ppg_sqi_vals) > 0 else np.nan
        lm["abp_sqi_mean"] = float(np.nanmean(abp_sqi_vals)) if len(abp_sqi_vals) > 0 else np.nan
        lm["dataset_version"] = dataset_version

        # Completeness numérica
        if numerics is not None and len(num_times_h) > 0:
            lm["numerics_completeness"] = compute_numerics_completeness(
                numerics.data, num_times_h, win_start_h, win_end_h,
            )

        if not dry_run:
            if waveforms_resampled:
                # Archivo NPZ de waveform
                wf_fname = f"{patient_id}_{idx:05d}.npz"
                wf_path = waveform_dir / wf_fname
                t_start_unix = events.t0_unix + win_start_h * 3600.0
                write_waveform_window(wf_path, ecg_win, ppg_win, abp_win,
                                      t_start_unix, target_fs)
                lm["waveform_file"] = str(wf_fname)

                # Archivo NPZ de máscara SQI
                sqi_path = sqi_dir / wf_fname
                write_sqi_mask(
                    sqi_path, ecg_mask, ppg_mask, abp_mask,
                    ecg_sqi_vals, ppg_sqi_vals, abp_sqi_vals, target_fs,
                )

    return availability_row, events, lm_rows


def _empty_numerics(patient_id: str):
    """NumericsRecord vacío para cuando falla la carga."""
    from src.stage0.adapters.base import NumericsRecord
    return NumericsRecord(
        patient_id=patient_id,
        timestamps_rel=np.array([], dtype=np.float64),
        data=pd.DataFrame(),
    )


def main(args=None) -> None:
    parser = argparse.ArgumentParser(
        description="Etapa 0 — Armonización y Preparación de Datos"
    )
    parser.add_argument(
        "--config",
        default="src/stage0/config/harmonize.yaml",
        help="Ruta al archivo YAML de configuración",
    )
    parser.add_argument(
        "--cohorts",
        nargs="+",
        choices=COHORTS,
        default=COHORTS,
        help="Cohortes a procesar (por defecto: todas)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ejecutar sin escribir archivos de salida",
    )
    parser.add_argument(
        "--max-patients",
        type=int,
        default=None,
        help="Límite de pacientes por cohorte (para pruebas rápidas)",
    )
    opts = parser.parse_args(args)

    # ── Setup ──────────────────────────────────────────────────────────────────
    config = load_config(opts.config)
    setup_logging(config.get("log_level", "INFO"))
    fix_seeds(config.get("random_seed", 42))

    dataset_version = make_dataset_version(config)
    output_dir = Path(config["paths"]["output_dir"])
    failure_windows = [float(h) for h in config["harmonize"]["failure_windows_hours"]]

    logger.info("=" * 60)
    logger.info("Etapa 0 — Dataset version: %s", dataset_version)
    logger.info("Cohortes: %s", opts.cohorts)
    logger.info("Dry-run: %s", opts.dry_run)
    logger.info("=" * 60)

    # ── Procesamiento por cohorte ──────────────────────────────────────────────
    all_clinical_events = []
    all_availability_rows = []
    all_landmark_rows = []
    cohort_counts: dict[str, int] = {}

    for cohort in opts.cohorts:
        logger.info("\n[cohort=%s] Iniciando procesamiento", cohort)
        try:
            adapter = get_adapter(cohort, config)
            patients = adapter.list_patients()
        except Exception as e:
            logger.error("[cohort=%s] Error al inicializar adaptador: %s", cohort, e)
            continue

        if opts.max_patients:
            patients = patients[: opts.max_patients]

        logger.info("[cohort=%s] %d pacientes a procesar", cohort, len(patients))
        cohort_counts[cohort] = len(patients)

        for i, pid in enumerate(patients, 1):
            logger.info("[cohort=%s] [%d/%d] patient=%s",
                        cohort, i, len(patients), pid)
            avail, events, lm_rows = process_patient(
                patient_id=pid,
                adapter=adapter,
                config=config,
                dataset_version=dataset_version,
                output_dir=output_dir,
                dry_run=opts.dry_run,
            )
            if avail is not None:
                all_availability_rows.append(avail)
            if events is not None:
                all_clinical_events.append(events)
            all_landmark_rows.extend(lm_rows)

    # ── Tablas de supervivencia ────────────────────────────────────────────────
    logger.info("\nConstruyendo tablas de supervivencia...")
    survival_tables = build_survival_tables(
        all_events=all_clinical_events,
        failure_windows_h=failure_windows,
        dataset_version=dataset_version,
    )

    # ── Tabla de disponibilidad de canal ──────────────────────────────────────
    from src.stage0.harmonize.availability import build_availability_table
    availability_df = build_availability_table(all_availability_rows)

    # ── Tabla de landmarks ────────────────────────────────────────────────────
    landmarks_df = build_landmarks_table(all_landmark_rows, dataset_version)

    # ── Outcome variables (placeholder: columnas vacías, se rellenará con P12) ─
    outcome_vars_df = pd.DataFrame()  # TODO: implementar tras P12

    # ── Escritura ──────────────────────────────────────────────────────────────
    if not opts.dry_run:
        write_all_tables(
            output_dir=output_dir,
            dataset_version=dataset_version,
            survival_tables=survival_tables,
            landmarks_df=landmarks_df,
            channel_availability_df=availability_df,
            outcome_variables_df=outcome_vars_df,
        )
        save_version_manifest(output_dir / dataset_version, dataset_version,
                              config, cohort_counts)
    else:
        logger.info("[dry-run] Sin escritura de archivos")
        for key, df in survival_tables.items():
            logger.info("[dry-run] %s: %d filas", key, len(df))
        logger.info("[dry-run] landmarks_index: %d filas", len(landmarks_df))
        logger.info("[dry-run] channel_availability: %d filas", len(availability_df))

    logger.info("\n%s", "=" * 60)
    logger.info("Etapa 0 completada. Version: %s", dataset_version)
    logger.info("Pacientes procesados: %s", cohort_counts)
    if not opts.dry_run:
        logger.info("Output en: %s/%s", output_dir, dataset_version)


if __name__ == "__main__":
    main()
