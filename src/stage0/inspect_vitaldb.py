import vitaldb
import json
from pathlib import Path
from collections import Counter

index_path = Path("datasets/vitaldb_sicu/vitaldb_full_cases_index.json")
cases_dir = Path("datasets/vitaldb_sicu/vitaldb_full_cases")

if not index_path.exists():
    print(f"Index not found: {index_path}")
else:
    with open(index_path) as f:
        idx = json.load(f)
    
    events = idx.get("events", [])
    print(f"Total files in index: {len(events)}")
    
    track_counts = Counter()
    
    # Inspeccionar los primeros 10 archivos para ver los nombres de los canales
    files_checked = 0
    for ev in events:
        file_path = cases_dir / ev["file"]
        if file_path.exists():
            try:
                vf = vitaldb.VitalFile(str(file_path))
                tracks = vf.get_track_names()
                for t in tracks:
                    track_counts[t] += 1
                files_checked += 1
                if files_checked >= 10:
                    break
            except Exception as e:
                print(f"Error reading {file_path}: {e}")
                
    print(f"\nCanales encontrados en {files_checked} archivos de VitalDB evaluados:")
    for t, c in track_counts.most_common():
        print(f"  {t} (presente en {c} archivos)")
