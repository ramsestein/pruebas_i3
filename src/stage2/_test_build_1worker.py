"""Minimal test of build_datasets with 1 worker."""
import sys
sys.path.insert(0, ".")
import traceback

print("Starting...", flush=True)

try:
    from src.stage2.dataset import build_datasets
    print("Imported build_datasets", flush=True)
    result = build_datasets(n_workers=1)
    print(f"Result: train={len(result[0])}, val={len(result[1])}, test={len(result[2]) if result[2] else 0}", flush=True)
except Exception as e:
    traceback.print_exc()
    print(f"ERROR: {e}", flush=True)
