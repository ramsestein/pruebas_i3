"""
Filter vitaldb cases to keep only laparoscopic or thoracic surgeries.
Saves the filtered case IDs to caseids_filtered.csv
"""
import os
import vitaldb

# Paths
vitaldb_dir = "datasets/vitaldb"
input_csv = os.path.join(vitaldb_dir, "caseids_AND.csv")
output_csv = os.path.join(vitaldb_dir, "caseids_filtered.csv")

# Load all case IDs from CSV
with open(input_csv) as f:
    all_caseids = [int(l.strip()) for l in f if l.strip()]
print(f"Total case IDs in CSV: {len(all_caseids)}")

# Load clinical data
print("Loading clinical data from VitalDB API...")
df = vitaldb.load_clinical_data(
    caseids=all_caseids,
    params=["caseid", "optype", "opname", "approach", "department"]
)
print(f"Clinical data loaded: {len(df)} rows")

# Filter: EXCLUDE laparoscopic (Videoscopic), robotic, and thoracic surgery
mask_lapa = df["approach"] == "Videoscopic"
mask_robot = df["approach"] == "Robotic"
mask_torac = df["department"] == "Thoracic surgery"
mask_exclude = mask_lapa | mask_robot | mask_torac
filtered = df[~mask_exclude]

filtered_caseids = sorted(filtered["caseid"].tolist())
excluded_count = mask_exclude.sum()
print(f"\nExcluded (laparoscopic, robotic, or thoracic): {excluded_count}")
print(f"  - Laparoscopic (Videoscopic): {mask_lapa.sum()}")
print(f"  - Robotic: {mask_robot.sum()}")
print(f"  - Thoracic surgery: {mask_torac.sum()}")
print(f"Remaining cases: {len(filtered_caseids)}")

# Check how many have .vital files
vital_files = {
    int(f.replace(".vital", ""))
    for f in os.listdir(vitaldb_dir)
    if f.endswith(".vital")
}
with_vital = [c for c in filtered_caseids if c in vital_files]
print(f"  - With .vital file: {len(with_vital)}")

# Save filtered case IDs
with open(output_csv, "w") as f:
    for cid in filtered_caseids:
        f.write(f"{cid}\n")

print(f"\nFiltered case IDs saved to: {output_csv}")
