import pandas as pd, json
from pathlib import Path
with open("datasets/clinic_vitals/windows_index.json") as f:
    idx = json.load(f)["windows"]
print("n_windows:", len(idx))
df = pd.read_parquet(Path("datasets/clinic_vitals/windows_10min") / idx[0]["window_file"])
print("shape:", df.shape)
print("index:", df.index[:5])
print("cols:", list(df.columns)[:20])
print(df.head(3))
