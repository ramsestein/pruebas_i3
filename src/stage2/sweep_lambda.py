"""
Barrido de λ (coherencia): entrena con λ ∈ {0, 0.02, 0.05, 0.1, 0.2}
Reutiliza carga de datos, R_base, y transformación de targets.
"""
import os, sys, time, json
sys.path.insert(0, ".")

import numpy as np
import torch
from torch.utils.data import DataLoader

from src.stage2.config import (
    BATCH_SIZE, MAX_EPOCHS, LEARNING_RATE, WEIGHT_DECAY,
    EARLY_STOPPING_PATIENCE, RANDOM_SEED, CHECKPOINT_DIR,
    GRAD_CLIP_VALUE, LR_SCHEDULER_FACTOR, LR_SCHEDULER_PATIENCE, LR_MIN,
)
from src.stage2.dataset import build_datasets
from src.stage2.models import ExtubationModel
from src.stage2.losses import LogNormalNLL, CoherenceLoss
from src.stage2.rbase import compute_rbase, get_rbase_at, report_rbase
from src.stage2.train_v2 import (
    _collate, build_residual_targets, PatientBatchSampler, PreCollatedDataset,
)

torch.manual_seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Reducir paciencia para el barrido (ahorra ~40% tiempo)
SWEEP_PATIENCE = 10
SWEEP_EPOCHS = 50


def train_one_lambda(model, train_loader, val_loader, lambda_coh, loss_fn, coh_fn):
    """Entrena con un λ específico. Devuelve best_val_nll y métricas finales."""
    model = ExtubationModel().to(DEVICE)  # modelo fresco
    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE,
                                  weight_decay=WEIGHT_DECAY)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=LR_SCHEDULER_FACTOR,
        patience=LR_SCHEDULER_PATIENCE, min_lr=LR_MIN,
    )

    best_val_loss = float("inf")
    patience_counter = 0
    history = {"train_nll": [], "val_nll": [], "train_coh": []}

    for epoch in range(1, SWEEP_EPOCHS + 1):
        t0 = time.time()

        # --- Train ---
        model.train()
        train_nll_vals, train_coh_vals = [], []
        for batch in train_loader:
            x = batch["x"].to(DEVICE)
            dt = batch["delta_t"].to(DEVICE)
            y = batch["y"].to(DEVICE)
            lm = batch["landmark_t"].to(DEVICE)
            pids = batch["patient_id"]

            optimizer.zero_grad()
            mu, log_sigma = model(x, dt, lm)
            nll = loss_fn(mu, log_sigma, y)

            if torch.isnan(nll) or torch.isinf(nll):
                continue

            if coh_fn is not None:
                lm_raw = batch["landmark_t_raw"].to(DEVICE)
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
                x = batch["x"].to(DEVICE)
                dt = batch["delta_t"].to(DEVICE)
                y = batch["y"].to(DEVICE)
                lm = batch["landmark_t"].to(DEVICE)
                mu, log_sigma = model(x, dt, lm)
                nll = loss_fn(mu, log_sigma, y)
                if torch.isnan(nll) or torch.isinf(nll):
                    nan_batches += 1
                    continue
                val_nll_vals.append(nll.item())

        avg_train = np.mean(train_nll_vals) if train_nll_vals else float("nan")
        avg_train_coh = np.mean(train_coh_vals) if train_coh_vals else 0.0
        avg_val = np.mean(val_nll_vals) if val_nll_vals else float("nan")
        elapsed = time.time() - t0

        history["train_nll"].append(avg_train)
        history["val_nll"].append(avg_val)
        history["train_coh"].append(avg_train_coh)

        coh_str = f" | Coh: {avg_train_coh:.4f}" if coh_fn else ""
        nan_str = f" | NaN batches: {nan_batches}" if nan_batches else ""
        print(f"  λ={lambda_coh:.3f} Epoch {epoch:2d} | "
              f"Train: {avg_train:.4f}{coh_str} | Val: {avg_val:.4f} | "
              f"{elapsed:.0f}s{nan_str}")

        if avg_val < best_val_loss - 1e-4:
            best_val_loss = avg_val
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= SWEEP_PATIENCE:
                print(f"  λ={lambda_coh:.3f} ⏹ Early stop (epoch {epoch})")
                break

        scheduler.step(avg_val)

    return best_val_loss, history


def main():
    lambdas = [0.0, 0.02, 0.05, 0.1, 0.2]
    results = {}

    # ── 1. Cargar datos (UNA SOLA VEZ) ─────────────────────────
    print("=" * 60)
    print("BARIDO DE λ — Cargando datos...")
    print("=" * 60)
    train_ds, val_ds, test_ds = build_datasets()

    # ── 2. R_base (UNA SOLA VEZ) ───────────────────────────────
    print("\n[1/3] R_base(t)...")
    train_landmarks = np.array([s["landmark_t"] for s in train_ds.sequences])
    train_log_r = np.array([s["log_time_remaining"] for s in train_ds.sequences])
    rbase = compute_rbase(train_landmarks, train_log_r, min_risk=100, frac=0.15)
    report_rbase(rbase)

    # ── 3. Targets residuales (UNA SOLA VEZ) ───────────────────
    print("\n[2/3] Transformando targets...")
    build_residual_targets(train_ds.sequences, rbase)
    build_residual_targets(val_ds.sequences, rbase)

    train_res = np.array([s["log_time_remaining"] for s in train_ds.sequences])
    print(f"  Train residuo: μ={train_res.mean():.3f}, σ={train_res.std():.3f}")

    # ── 4. Pre-colar batches ────────────────────────────────────
    print("\n[3/3] Pre-colando batches...")
    batch_size = 128

    # Random batches (para λ=0 y val)
    n_train = len(train_ds)
    train_indices = np.arange(n_train)
    rng_tr = np.random.RandomState(RANDOM_SEED)
    rng_tr.shuffle(train_indices)
    train_random_raw = [train_indices[i:i + batch_size] for i in range(0, n_train, batch_size)]
    train_random = [_collate(b, train_ds) for b in train_random_raw]
    train_random_loader = DataLoader(
        PreCollatedDataset(train_random), batch_size=1, shuffle=True,
        collate_fn=lambda x: x[0],
    )

    # Patient-grouped batches (para λ>0, coherencia)
    sampler = PatientBatchSampler(train_ds, batch_size, shuffle=True)
    train_grouped = [_collate(b, train_ds) for b in sampler.batches]
    train_grouped_loader = DataLoader(
        PreCollatedDataset(train_grouped), batch_size=1, shuffle=True,
        collate_fn=lambda x: x[0],
    )

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

    print(f"  Train random: {len(train_random_loader)} batches")
    print(f"  Train grouped: {len(train_grouped_loader)} batches")
    print(f"  Val: {len(val_loader)} batches")

    # ── 5. Barrido ─────────────────────────────────────────────
    loss_fn = LogNormalNLL()
    print("\n" + "=" * 60)
    print("BARIDO DE λ")
    print("=" * 60)

    for lam in lambdas:
        print(f"\n{'─'*40}")
        print(f"λ = {lam:.3f}")
        print(f"{'─'*40}")
        # λ=0 → random batches, λ>0 → patient-grouped (necesario para coherencia)
        train_loader = train_random_loader if lam == 0 else train_grouped_loader
        coh_fn = CoherenceLoss() if lam > 0 else None
        best_val, history = train_one_lambda(
            model=None,  # se crea dentro
            train_loader=train_loader,
            val_loader=val_loader,
            lambda_coh=lam,
            loss_fn=loss_fn,
            coh_fn=coh_fn,
        )
        results[lam] = {
            "best_val_nll": float(best_val),
            "final_train_nll": float(history["train_nll"][-1]) if history["train_nll"] else float("nan"),
            "epochs": len(history["train_nll"]),
            "history": {k: [float(x) for x in v] for k, v in history.items()},
        }
        print(f"  → λ={lam:.3f} Best Val NLL: {best_val:.4f} ({results[lam]['epochs']} epochs)")

    # ── 6. Reporte final ───────────────────────────────────────
    print("\n" + "=" * 60)
    print("RESULTADOS DEL BARIDO")
    print("=" * 60)
    print(f"{'λ':>8}  {'Best Val NLL':>14}  {'Epochs':>7}  {'Δ vs λ=0':>10}")
    print("-" * 45)
    baseline = results[0.0]["best_val_nll"]
    for lam in lambdas:
        r = results[lam]
        delta = r["best_val_nll"] - baseline
        print(f"{lam:8.3f}  {r['best_val_nll']:14.4f}  {r['epochs']:7d}  {delta:+10.4f}")

    # Guardar
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    with open(os.path.join(CHECKPOINT_DIR, "sweep_results.json"), "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResultados guardados en {CHECKPOINT_DIR}/sweep_results.json")


if __name__ == "__main__":
    main()
