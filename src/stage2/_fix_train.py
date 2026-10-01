import sys
path = r"c:\Users\Ramsés\Desktop\Proyectos\iprove3\src\stage2\train.py"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

# Fix: replace the merged lines
old1 = 'samples")    sample_y = next(iter(train_loader))["y"]\n    print(f"Target (log-hours): min={sample_y.min():.2f}, max={sample_y.max():.2f}, "\n          f"mean={sample_y.mean():.2f}")    if device.type == "cuda":'
new1 = 'samples")\n    sample_y = next(iter(train_loader))["y"]\n    print(f"Target (log-hours): min={sample_y.min():.2f}, max={sample_y.max():.2f}, "\n          f"mean={sample_y.mean():.2f}")\n    if device.type == "cuda":'
c = c.replace(old1, new1)

with open(path, "w", encoding="utf-8") as f:
    f.write(c)
print("Fixed train.py")
