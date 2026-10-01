"""
Inferencia del Escalón 2 sobre eICU-test y MIMIC.
Genera trayectorias completas por paciente y landmark.
"""

import os
import numpy as np
import torch
from torch.utils.data import DataLoader

from src.stage2.config import (
    BATCH_SIZE, RANDOM_SEED, CHECKPOINT_DIR, TRAJECTORY_DIR,
)
from src.stage2.dataset import build_datasets
from src.stage2.models import ExtubationModel
from src.stage2.trajectory_logger import log_trajectory, save_trajectories


def run_inference(model, dataset, cohort_name, device):
    """
    Infiere sobre un dataset y guarda trayectorias.

    Returns:
        DataFrame con todas las trayectorias.
    """
    if dataset is None or len(dataset) == 0:
        print(f"  [{cohort_name}] Sin datos para inferir.")
        return None

    loader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=False)
    model.eval()

    records = []
    with torch.no_grad():
        for batch in loader:
            x = batch["x"].to(device)
            dt = batch["delta_t"].to(device)
            y = batch["y"].numpy()
            pids = batch["patient_id"]
            landmarks = batch["landmark_t"].numpy()

            mu, log_sigma = model(x, dt, batch["landmark_t"].to(device))
            mu_np = mu.cpu().numpy()
            ls_np = log_sigma.cpu().numpy()

            for i in range(len(mu_np)):
                log_y = float(y[i])
                rec = log_trajectory(
                    patient_id=pids[i],
                    landmark_t=float(landmarks[i]),
                    mu=float(mu_np[i]),
                    log_sigma=float(ls_np[i]),
                    log_time_remaining_true=log_y,
                    total_duration=float(np.exp(log_y) + landmarks[i]),
                )
                records.append(rec)

    return save_trajectories(records, cohort_name)


def infer():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Dispositivo: {device}")

    # Cargar modelo
    model = ExtubationModel().to(device)
    ckpt = os.path.join(CHECKPOINT_DIR, "best_model.pt")
    if not os.path.exists(ckpt):
        print(f"ERROR: No se encontró {ckpt}. Ejecuta train.py primero.")
        return
    model.load_state_dict(torch.load(ckpt, map_location=device))
    print(f"Modelo cargado desde {ckpt}")

    # Datos
    _, _, test_ds = build_datasets()
    _, val_ds, _ = build_datasets()  # val es eICU test interno

    # Reconstruir test interno de eICU (el val split)
    print("\n" + "=" * 60)
    print("Inferencia sobre eICU-test (validación interna)...")
    df_eicu = run_inference(model, val_ds, "eicu_test", device)

    print("\n" + "=" * 60)
    print("Inferencia sobre MIMIC (validación externa)...")
    df_mimic = run_inference(model, test_ds, "mimic", device)

    return df_eicu, df_mimic


if __name__ == "__main__":
    infer()
