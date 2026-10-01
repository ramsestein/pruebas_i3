"""
Copy .vital files for filtered case IDs into vital_full_cases/
"""
import os
import shutil

vitaldb_dir = "datasets/vitaldb"
filtered_csv = os.path.join(vitaldb_dir, "caseids_filtered.csv")
dest_dir = os.path.join(vitaldb_dir, "vital_full_cases")

# Load filtered case IDs
with open(filtered_csv) as f:
    filtered_ids = {int(l.strip()) for l in f if l.strip()}

# Copy matching .vital files
os.makedirs(dest_dir, exist_ok=True)
copied = 0
for fname in os.listdir(vitaldb_dir):
    if not fname.endswith(".vital"):
        continue
    caseid = int(fname.replace(".vital", ""))
    if caseid in filtered_ids:
        src = os.path.join(vitaldb_dir, fname)
        dst = os.path.join(dest_dir, fname)
        shutil.copy2(src, dst)
        copied += 1

print(f"Copied {copied} .vital files to {dest_dir}")
