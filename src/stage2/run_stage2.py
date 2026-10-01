"""Script principal del Escalón 2: entrena, infiere y evalúa."""
import faulthandler; faulthandler.enable()  # Captura segfaults del nightly PyTorch
import sys
sys.path.insert(0, ".")

# ── torch debe importarse ANTES que pandas/pyarrow ──
# El nightly de PyTorch 2.12+cu128 carga DLLs de CUDA que
# colisionan con pyarrow si este se carga primero.
import torch
import os

from src.stage2.train import train
from src.stage2.inference import run_inference, infer
from src.stage2.evaluate import evaluate

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["train", "infer", "evaluate", "all"], default="all")
    args = parser.parse_args()

    if args.mode in ("train", "all"):
        print("\n" + "=" * 60)
        print("FASE 1: ENTRENAMIENTO")
        print("=" * 60)
        model, train_ds, val_ds, test_ds = train()

    if args.mode in ("infer", "all"):
        print("\n" + "=" * 60)
        print("FASE 2: INFERENCIA")
        print("=" * 60)
        infer()

    if args.mode in ("evaluate", "all"):
        print("\n" + "=" * 60)
        print("FASE 3: EVALUACIÓN")
        print("=" * 60)
        evaluate()
