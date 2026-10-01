"""
Entrenamiento del modelo del Escalón 2.

Split por paciente, early stopping, logging en consola.
"""

import os
import numpy as np
import torch
from torch.utils.data import DataLoader
from torch.optim import AdamW
import time

from src.stage2.config import (
    BATCH_SIZE, MAX_EPOCHS, LEARNING_RATE, WEIGHT_DECAY,
    EARLY_STOPPING_PATIENCE, RANDOM_SEED, CHECKPOINT_DIR,
    OUTPUT_DIR, GRAD_CLIP_VALUE,
    LR_SCHEDULER_FACTOR, LR_SCHEDULER_PATIENCE, LR_MIN,
)
from src.stage2.dataset import build_datasets
from src.stage2.models import ExtubationModel
from src.stage2.losses import LogNormalNLL

# ── Reproducibilidad ────────────────────────────────────────────────────
torch.manual_seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)


def _median_from_params(mu, log_sigma):
    """Mediana de la log-normal: exp(mu)."""
    return torch.exp(mu)


def train():
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Dispositivo: {device}")

    # ── Datos ──────────────────────────────────────────────────────────
    try:
        train_ds, val_ds, test_ds = build_datasets()
    except Exception as e:
        print(f"ERROR en build_datasets: {e}")
        import traceback; traceback.print_exc()
        return None, None, None, None

    if train_ds is None or len(train_ds) == 0:
        print("ERROR: Dataset de entrenamiento vacío. Abortando.")
        return None, None, None, None

    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE, shuffle=False) if val_ds and len(val_ds) > 0 else None

    if val_loader is None:
        print("ERROR: Dataset de validación vacío. Abortando.")
        return None, None, None, None

    # ── Modelo ─────────────────────────────────────────────────────────
    model = ExtubationModel().to(device)
    loss_fn = LogNormalNLL()
    optimizer = AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=LR_SCHEDULER_FACTOR,
        patience=LR_SCHEDULER_PATIENCE, min_lr=LR_MIN,
    )

    n_params = sum(p.numel() for p in model.parameters())
    print(f"\nParámetros: {n_params:,}")
    print(f"Train: {len(train_loader)} batches × {BATCH_SIZE} ≈ {len(train_loader)*BATCH_SIZE} samples")
    print(f"Val:   {len(val_loader)} batches × {BATCH_SIZE} ≈ {len(val_loader)*BATCH_SIZE} samples")
    sample_y = next(iter(train_loader))["y"]
    print(f"Target (log-hours): min={sample_y.min():.2f}, max={sample_y.max():.2f}, "
          f"mean={sample_y.mean():.2f}")
    if device.type == "cuda":
        print(f"GPU: {torch.cuda.get_device_name(0)}")
        print(f"VRAM asignada: {torch.cuda.memory_allocated()/1024**2:.0f} MB / "
              f"total: {torch.cuda.get_device_properties(0).total_memory/1024**3:.1f} GB")

    # ── Entrenamiento ──────────────────────────────────────────────────
    best_val_loss = float("inf")
    patience_counter = 0
    history = {"train_loss": [], "val_loss": [], "val_mae": [], "val_rmse": []}

    print("\nEntrenando...\n")
    for epoch in range(1, MAX_EPOCHS + 1):
        t0 = time.time()

        # --- Train ---
        model.train()
        train_losses = []
        for batch_idx, batch in enumerate(train_loader):
            x = batch["x"].to(device)
            dt = batch["delta_t"].to(device)
            y = batch["y"].to(device)
            lm = batch["landmark_t"].to(device)

            optimizer.zero_grad()
            mu, log_sigma = model(x, dt, lm)
            loss = loss_fn(mu, log_sigma, y)
            if torch.isnan(loss) or torch.isinf(loss):
                continue
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            torch.nn.utils.clip_grad_value_(model.parameters(), clip_value=GRAD_CLIP_VALUE)
            optimizer.step()

            train_losses.append(loss.item())

            # Progress dentro de la epoch para epochs largos (cada 25% de batches)
            if (batch_idx + 1) % max(1, len(train_loader) // 4) == 0:
                avg_so_far = np.mean(train_losses)
                print(f"  [{epoch}/{MAX_EPOCHS}] train {batch_idx+1}/{len(train_loader)} "
                      f"| NLL: {avg_so_far:.4f}")

        # --- Val ---
        model.eval()
        val_losses = []
        val_maes = []
        val_rmses = []
        with torch.no_grad():
            for batch in val_loader:
                x = batch["x"].to(device)
                dt = batch["delta_t"].to(device)
                y = batch["y"].to(device)
                lm = batch["landmark_t"].to(device)

                mu, log_sigma = model(x, dt, lm)
                loss = loss_fn(mu, log_sigma, y)
                val_losses.append(loss.item())

                median = _median_from_params(mu, log_sigma)
                val_maes.append(torch.abs(median - y).mean().item())
                val_rmses.append(torch.sqrt(((median - y) ** 2).mean()).item())

        avg_train = np.mean(train_losses)
        avg_val = np.mean(val_losses)
        avg_mae = np.mean(val_maes)
        avg_rmse = np.mean(val_rmses)
        elapsed = time.time() - t0

        history["train_loss"].append(avg_train)
        history["val_loss"].append(avg_val)
        history["val_mae"].append(avg_mae)
        history["val_rmse"].append(avg_rmse)

        # Muestra estadísticas de las predicciones
        print(
            f"── Epoch {epoch:3d} ───────────────────────────────"
            f" {elapsed:.1f}s ──"
        )
        print(f"  Train NLL: {avg_train:.4f}  |  "
              f"Val NLL: {avg_val:.4f}  |  "
              f"Val MAE: {avg_mae:6.1f}h  |  "
              f"Val RMSE: {avg_rmse:6.1f}h")

        # --- Early stopping + scheduler ---
        if avg_val < best_val_loss - 1e-4:
            best_val_loss = avg_val
            patience_counter = 0
            torch.save(model.state_dict(), os.path.join(CHECKPOINT_DIR, "best_model.pt"))
            print(f"  ✓ Checkpoint guardado (val NLL={best_val_loss:.4f})")
        else:
            patience_counter += 1
            if patience_counter >= EARLY_STOPPING_PATIENCE:
                print(f"\n⏹ Early stopping en epoch {epoch}. Best val NLL = {best_val_loss:.4f}")
                break
        scheduler.step(avg_val)
        if optimizer.param_groups[0]["lr"] < LEARNING_RATE:
            print(f"  🔻 LR: {optimizer.param_groups[0]['lr']:.2e}")
        print()

    # ── Cargar mejor modelo ────────────────────────────────────────────
    model.load_state_dict(torch.load(os.path.join(CHECKPOINT_DIR, "best_model.pt")))
    print(f"\nMejor modelo cargado. val NLL={best_val_loss:.4f}")

    return model, train_ds, val_ds, test_ds


if __name__ == "__main__":
    train()
