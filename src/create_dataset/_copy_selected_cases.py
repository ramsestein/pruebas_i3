"""
Copia los casos con gaps de ventilación a selected_cases/ para revisión manual.
"""
import os, json, shutil

# Cargar resultados del análisis de gaps
with open("datasets/vitaldb/vent_gaps_analysis.json") as f:
    data = json.load(f)

results = data if isinstance(data, list) else data.get("results", data)

src_dir = "datasets/vitaldb/vital_full_cases"
dst_dir = "datasets/vitaldb/selected_cases"
os.makedirs(dst_dir, exist_ok=True)

copied = []
for r in results:
    fname = r["file"]
    src = os.path.join(src_dir, fname)
    dst = os.path.join(dst_dir, fname)
    if os.path.exists(src):
        shutil.copy2(src, dst)
        copied.append(fname)

print(f"Copiados {len(copied)} archivos a {dst_dir}/")
print()
print("Archivos copiados:")
for f in sorted(copied):
    print(f"  {f}")
