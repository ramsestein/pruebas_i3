"""
Baseline: solo landmark_t (tiempo desde intubación). Sin fisiología.

Predice tiempo restante hasta extubación usando solo el tiempo transcurrido.
Responde: ¿aportan algo las constantes vitales?
"""
import os, sys, time
sys.path.insert(0, ".")
import numpy as np, torch, torch.nn as nn
from torch.utils.data import DataLoader, Dataset
from src.stage2.config import (
    CHECKPOINT_DIR, OUTPUT_DIR, RANDOM_SEED, T_EPS,
    MU_CLAMP, LOG_SIGMA_MIN, LOG_SIGMA_MAX, GRAD_CLIP_VALUE,
)
from src.stage2.dataset import build_datasets

torch.manual_seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# ── Dataset minimalista ────────────────────────────────────────────────
class LandmarkOnlyDataset(Dataset):
    def __init__(self, ext_dataset):
        self.landmarks = []
        self.targets = []
        for i in range(len(ext_dataset)):
            s = ext_dataset[i]
            self.landmarks.append(s["landmark_t"].item())
            self.targets.append(s["y"].item())

    def __len__(self):
        return len(self.landmarks)

    def __getitem__(self, idx):
        return (
            torch.tensor(self.landmarks[idx], dtype=torch.float32),
            torch.tensor(self.targets[idx], dtype=torch.float32),
        )


# ── Modelo baseline ────────────────────────────────────────────────────
class TimeOnlyModel(nn.Module):
    """Predice mu, log_sigma solo con landmark_t."""
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(1, 32), nn.ReLU(),
            nn.Linear(32, 16), nn.ReLU(),
            nn.Linear(16, 2),
        )

    def forward(self, t):
        out = self.net(t.unsqueeze(-1))
        mu = torch.tanh(out[:, 0]) * MU_CLAMP
        log_sigma = torch.clamp(out[:, 1], LOG_SIGMA_MIN, LOG_SIGMA_MAX)
        return mu, log_sigma


class LogNormalNLL(nn.Module):
    def __init__(self):
        super().__init__()
        self.eps = T_EPS
        self.log_2pi = np.log(2 * np.pi)

    def forward(self, mu, log_sigma, y_true):
        sigma = torch.exp(log_sigma) + self.eps
        diff = y_true - mu
        nll = log_sigma + 0.5 * (diff / sigma).pow(2) + 0.5 * self.log_2pi
        return nll.mean()


# ── Entrenamiento ──────────────────────────────────────────────────────
def train_baseline():
    print("=" * 60)
    print("BASELINE: Solo landmark_t (sin fisiología)")
    print(f"Dispositivo: {device}")
    print("=" * 60)

    print("Cargando dataset (caché)...")
    train_ds, val_ds, _ = build_datasets()
    train_ds = LandmarkOnlyDataset(train_ds)
    val_ds = LandmarkOnlyDataset(val_ds)

    train_loader = DataLoader(train_ds, batch_size=256, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=256, shuffle=False)

    model = TimeOnlyModel().to(device)
    loss_fn = LogNormalNLL()
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=5, min_lr=1e-6,
    )

    n_params = sum(p.numel() for p in model.parameters())
    print(f"Parámetros: {n_params:,}")
    print(f"Train: {len(train_ds):,} samples, Val: {len(val_ds):,}")

    best_val = float("inf")
    patience = 0

    for epoch in range(1, 101):
        t0 = time.time()

        # Train
        model.train()
        train_losses = []
        for t_batch, y_batch in train_loader:
            t_batch, y_batch = t_batch.to(device), y_batch.to(device)
            optimizer.zero_grad()
            mu, log_sigma = model(t_batch)
            loss = loss_fn(mu, log_sigma, y_batch)
            if torch.isnan(loss): continue
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            torch.nn.utils.clip_grad_value_(model.parameters(), GRAD_CLIP_VALUE)
            optimizer.step()
            train_losses.append(loss.item())

        # Val
        model.eval()
        val_losses = []
        with torch.no_grad():
            for t_batch, y_batch in val_loader:
                t_batch, y_batch = t_batch.to(device), y_batch.to(device)
                mu, log_sigma = model(t_batch)
                val_losses.append(loss_fn(mu, log_sigma, y_batch).item())

        avg_train = np.mean(train_losses)
        avg_val = np.mean(val_losses)
        elapsed = time.time() - t0
        print(f"Epoch {epoch:3d} | Train: {avg_train:.4f} | Val: {avg_val:.4f} | {elapsed:.0f}s")

        if avg_val < best_val - 1e-4:
            best_val = avg_val
            patience = 0
            os.makedirs(CHECKPOINT_DIR, exist_ok=True)
            torch.save(model.state_dict(), os.path.join(CHECKPOINT_DIR, "baseline.pt"))
            print(f"  ✓ Best: {best_val:.4f}")
        else:
            patience += 1
            if patience >= 25:
                print(f"  ⏹ Early stop")
                break
        scheduler.step(avg_val)

    print(f"\n=== RESULTADO BASELINE ===")
    print(f"Best Val NLL: {best_val:.4f}")
    print(f"Parámetros: {n_params:,}")
    print(f"Si tu modelo saca NLL < {best_val:.4f}, las constantes SÍ aportan.")
    return best_val


if __name__ == "__main__":
    train_baseline()
