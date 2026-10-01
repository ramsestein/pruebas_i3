"""
Entrenamiento V2: target residual + coherencia + batching por paciente.

Cambios respecto a V1:
- Target: residuo fisiológico y = log(R(t) / R_base(t))
- R_base(t) estimada con LOESS solo del split train
- Batching agrupado por paciente (landmarks consecutivos juntos)
- Pérdida: NLL + λ * coherencia (suavidad sobre residuo)
- Logging separado de componentes NLL y coherencia
"""
import os, sys, time
sys.path.insert(0, ".")

import numpy as np
import torch
from torch.utils.data import DataLoader

from src.stage2.config import (
    BATCH_SIZE, MAX_EPOCHS, LEARNING_RATE, WEIGHT_DECAY,
    EARLY_STOPPING_PATIENCE, RANDOM_SEED, CHECKPOINT_DIR,
    GRAD_CLIP_VALUE, LR_SCHEDULER_FACTOR, LR_SCHEDULER_PATIENCE, LR_MIN,
    LANDMARK_T_LOG,
)
from src.stage2.dataset import build_datasets
from src.stage2.models import ExtubationModel
from src.stage2.losses import LogNormalNLL, CoherenceLoss
from src.stage2.rbase import compute_rbase, get_rbase_at, report_rbase

torch.manual_seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)


# ── Helpers ────────────────────────────────────────────────────────────

def _collate(indices, dataset):
    """Convierte una lista de índices en un batch (dict de tensores + metadatos)."""
    feats_list, missing_list, dts_list = [], [], []
    targets, landmarks, pids = [], [], []
    for i in indices:
        s = dataset.sequences[i]
        feats_list.append(s["window_feats"])
        missing_list.append(s["window_missing"])
        dts_list.append(s["delta_ts"])
        targets.append(s["log_time_remaining"])
        landmarks.append(s["landmark_t"])
        pids.append(s["patient_id"])
    x = np.concatenate([np.stack(feats_list), np.stack(missing_list)], axis=-1)
    # Normalizar landmark_t: log(t+1). Clip a >= 0 por si hay valores negativos.
    lm_raw = np.array(landmarks, dtype=np.float32)
    lm_raw = np.clip(lm_raw, 0.0, None)
    lm_norm = np.log(lm_raw + 1.0).astype(np.float32)
    return {
        "x":          torch.from_numpy(x.astype(np.float32)),
        "delta_t":    torch.from_numpy(np.stack(dts_list).astype(np.float32)),
        "y":          torch.tensor(np.array(targets, dtype=np.float32)),
        "landmark_t": torch.from_numpy(lm_norm),       # normalizado para el modelo
        "landmark_t_raw": torch.tensor(lm_raw),         # bruto (horas) para coherencia
        "patient_id": pids,
    }


def build_residual_targets(sequences, rbase_dict):
    """Transforma targets de log(R) absoluto a residuo log(R/R_base)."""
    for s in sequences:
        t = s["landmark_t"]
        r_base = get_rbase_at(np.array([t]), rbase_dict)[0]
        s["r_base"] = float(r_base)
        s["log_time_remaining"] = np.float32(
            s["log_time_remaining"] - np.log(max(r_base, 1e-6))
        )


class PatientBatchSampler:
    """
    Agrupa landmarks del MISMO paciente en batches consecutivos.
    Ideal para el término de coherencia temporal (solo train).
    """
    def __init__(self, dataset, batch_size, shuffle=True):
        self.dataset = dataset
        self.batch_size = batch_size
        self.shuffle = shuffle
        self._build()

    def _build(self):
        seqs = self.dataset.sequences
        groups = {}
        for i, s in enumerate(seqs):
            pid = s["patient_id"]
            groups.setdefault(pid, []).append((s["landmark_t"], i))
        self.batches = []
        for items in groups.values():
            items.sort()  # por landmark_t
            indices = [idx for _, idx in items]
            for start in range(0, len(indices), self.batch_size):
                self.batches.append(indices[start:start + self.batch_size])
        if self.shuffle:
            rng = np.random.RandomState(RANDOM_SEED)
            rng.shuffle(self.batches)

    def __iter__(self):
        return iter(self.batches)

    def __len__(self):
        return len(self.batches)


class PreCollatedDataset(torch.utils.data.Dataset):
    """Dataset que ya tiene los batches pre-colados en memoria."""
    def __init__(self, batches):
        self.batches = batches

    def __len__(self):
        return len(self.batches)

    def __getitem__(self, idx):
        return self.batches[idx]


# ── Train Loop ─────────────────────────────────────────────────────────

def train_v2(lambda_coh: float = 0.0):
    """Entrena modelo V2 con target residual + coherencia opcional."""
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tag = f"lambda{lambda_coh:.3f}".replace(".", "_")
    ckpt_path = os.path.join(CHECKPOINT_DIR, f"v2_{tag}.pt")

    print("=" * 60)
    print(f"ENTRENAMIENTO V2 — Target residual + coherencia λ={lambda_coh}")
    print(f"Dispositivo: {device}")
    print("=" * 60)

    # ── 1. Datos ──────────────────────────────────────────────────
    train_ds, val_ds, test_ds = build_datasets()

    # ── 2. R_base desde train ─────────────────────────────────────
    print("\n[1] Calculando R_base(t) desde train...")
    train_landmarks = np.array([s["landmark_t"] for s in train_ds.sequences])
    train_log_r = np.array([s["log_time_remaining"] for s in train_ds.sequences])
    rbase = compute_rbase(train_landmarks, train_log_r, min_risk=100, frac=0.15)
    report_rbase(rbase)

    # ── 3. Transformar targets a residual ─────────────────────────
    print("\n[2] Transformando targets a residual...")
    build_residual_targets(train_ds.sequences, rbase)
    build_residual_targets(val_ds.sequences, rbase)
    if test_ds:
        build_residual_targets(test_ds.sequences, rbase)

    train_res = np.array([s["log_time_remaining"] for s in train_ds.sequences])
    print(f"  Train residuo: μ={train_res.mean():.3f}, σ={train_res.std():.3f}, "
          f"min={train_res.min():.2f}, max={train_res.max():.2f}")
    print(f"  → σ_residuo es la señal fisiológica extraíble (sin offset paciente)")

    # ── 4. DataLoaders ────────────────────────────────────────────
    batch_size = 128
    use_patient_batches = (lambda_coh > 0)

    if use_patient_batches:
        # Train: PatientBatchSampler → batches pre-colados (agrupados por paciente)
        sampler = PatientBatchSampler(train_ds, batch_size, shuffle=True)
        train_batches = [_collate(b, train_ds) for b in sampler.batches]
        train_loader = DataLoader(
            PreCollatedDataset(train_batches), batch_size=1, shuffle=True,
            collate_fn=lambda x: x[0],
        )
    else:
        # Train: batches aleatorios normales (mejor sin coherencia)
        n_train = len(train_ds)
        train_indices = np.arange(n_train)
        rng_tr = np.random.RandomState(RANDOM_SEED)
        rng_tr.shuffle(train_indices)
        train_batches_raw = [train_indices[i:i + batch_size] for i in range(0, n_train, batch_size)]
        train_batches = [_collate(b, train_ds) for b in train_batches_raw]
        train_loader = DataLoader(
            PreCollatedDataset(train_batches), batch_size=1, shuffle=True,
            collate_fn=lambda x: x[0],
        )

    # Val: batches aleatorios normales (no agrupados por paciente, más rápido)
    n_val = len(val_ds)
    val_indices = np.arange(n_val)
    rng = np.random.RandomState(42)
    rng.shuffle(val_indices)
    val_batches_raw = [val_indices[i:i + batch_size] for i in range(0, n_val, batch_size)]
    val_batches = [_collate(b, val_ds) for b in val_batches_raw]
    val_loader = DataLoader(
        PreCollatedDataset(val_batches), batch_size=1, shuffle=False,
        collate_fn=lambda x: x[0],
    )

    print(f"\n[3] Train: {len(train_loader)} batches (~{batch_size} landmarks c/u)")
    print(f"    Val:   {len(val_loader)} batches (~{batch_size} landmarks c/u)")

    # ── 5. Modelo ─────────────────────────────────────────────────
    model = ExtubationModel().to(device)
    loss_fn = LogNormalNLL()
    coh_fn = CoherenceLoss() if lambda_coh > 0 else None
    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE,
                                  weight_decay=WEIGHT_DECAY)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=LR_SCHEDULER_FACTOR,
        patience=LR_SCHEDULER_PATIENCE, min_lr=LR_MIN,
    )
    n_params = sum(p.numel() for p in model.parameters())
    print(f"\nParámetros: {n_params:,}")

    # ── 6. Entrenamiento ──────────────────────────────────────────
    best_val_loss = float("inf")
    patience_counter = 0

    for epoch in range(1, MAX_EPOCHS + 1):
        t0 = time.time()

        # --- Train ---
        model.train()
        train_nll_vals, train_coh_vals = [], []
        for batch in train_loader:
            x = batch["x"].to(device)
            dt = batch["delta_t"].to(device)
            y = batch["y"].to(device)
            lm = batch["landmark_t"].to(device)
            pids = batch["patient_id"]

            optimizer.zero_grad()
            mu, log_sigma = model(x, dt, lm)
            nll = loss_fn(mu, log_sigma, y)

            if torch.isnan(nll) or torch.isinf(nll):
                continue

            if coh_fn is not None:
                lm_raw = batch["landmark_t_raw"].to(device)
                coh = coh_fn(mu, lm_raw, pids)
                loss = nll + lambda_coh * coh
                train_coh_vals.append(coh.item())
            else:
                loss = nll

            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            torch.nn.utils.clip_grad_value_(model.parameters(),
                                            clip_value=GRAD_CLIP_VALUE)
            optimizer.step()
            train_nll_vals.append(nll.item())

        # --- Val ---
        model.eval()
        val_nll_vals = []
        nan_batches = 0
        with torch.no_grad():
            for batch in val_loader:
                x = batch["x"].to(device)
                dt = batch["delta_t"].to(device)
                y = batch["y"].to(device)
                lm = batch["landmark_t"].to(device)
                mu, log_sigma = model(x, dt, lm)
                nll = loss_fn(mu, log_sigma, y)
                if torch.isnan(nll) or torch.isinf(nll):
                    nan_batches += 1
                    if nan_batches == 1 and epoch == 1:
                        print(f"  ⚠ NaN en val batch: mu=[{mu.min():.2f},{mu.max():.2f}], "
                              f"sigma=[{log_sigma.min():.2f},{log_sigma.max():.2f}], "
                              f"y=[{y.min():.2f},{y.max():.2f}], "
                              f"lm=[{lm.min():.2f},{lm.max():.2f}]")
                    continue
                val_nll_vals.append(nll.item())

        avg_train_nll = np.mean(train_nll_vals) if train_nll_vals else float("nan")
        avg_train_coh = np.mean(train_coh_vals) if train_coh_vals else 0.0
        avg_val = np.mean(val_nll_vals) if val_nll_vals else float("nan")
        elapsed = time.time() - t0

        coh_str = f" | Coh: {avg_train_coh:.4f}" if coh_fn else ""
        print(f"Epoch {epoch:3d} | Train NLL: {avg_train_nll:.4f}{coh_str} | "
              f"Val NLL: {avg_val:.4f} | {elapsed:.0f}s")

        if avg_val < best_val_loss - 1e-4:
            best_val_loss = avg_val
            patience_counter = 0
            os.makedirs(CHECKPOINT_DIR, exist_ok=True)
            torch.save({
                "model": model.state_dict(),
                "rbase": rbase,
                "lambda_coh": lambda_coh,
                "val_nll": avg_val,
            }, ckpt_path)
            print(f"  ✓ Checkpoint ({tag})")
        else:
            patience_counter += 1
            if patience_counter >= EARLY_STOPPING_PATIENCE:
                print(f"  ⏹ Early stop (epoch {epoch})")
                break

        scheduler.step(avg_val)

    print(f"\nBest Val NLL: {best_val_loss:.4f} (λ={lambda_coh})")
    return model, rbase


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--lambda_coh", type=float, default=0.0,
                        help="Peso del término de coherencia")
    args = parser.parse_args()
    train_v2(lambda_coh=args.lambda_coh)
