"""
Paso 1: Identificar 150 pacientes de MIMIC-III Waveform Database Matched Subset
con ventilacion mecanica documentada.

Requiere (descargar manualmente de physionet.org/files/mimiciii/1.4/ si no existen):
  - ICUSTAYS.csv.gz
  - PROCEDUREEVENTS_MV.csv.gz

Estos archivos se buscan en (por orden):
  1. Raiz del proyecto
  2. datasets/mimic3wdb/clinical/

Salida: datasets/mimic3wdb/candidate_list.csv
"""
import os
import re
import json
import requests
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
MIMIC_DIR = Path("datasets/mimic3wdb")
CLIN_DIR  = MIMIC_DIR / "clinical"
BASE_WFDB = "https://physionet.org/files/mimic3wdb-matched/1.0"

# ── Parameters ────────────────────────────────────────────────────────────────
MIN_VENT_HOURS = 12
TARGET_ICU     = {"MICU", "SICU", "CSRU", "CCU", "TSICU"}
N_CANDIDATES   = 150
ITEMID_MV_INV  = 225792   # Invasive Mechanical Ventilation


# ── HTTP helper ───────────────────────────────────────────────────────────────
def _auth():
    return (os.getenv("WFDB_USERNAME", ""), os.getenv("WFDB_PASSWORD", ""))


# ── Clinical tables ───────────────────────────────────────────────────────────
def _find_file(fname: str) -> Path | None:
    """Search for a file in root and CLIN_DIR."""
    for loc in [Path("."), CLIN_DIR, MIMIC_DIR]:
        p = loc / fname
        if p.exists():
            return p
    return None


def load_clinical_tables():
    """Load ICUSTAYS and PROCEDUREEVENTS_MV; columns are UPPERCASE in MIMIC-III."""
    CLIN_DIR.mkdir(parents=True, exist_ok=True)

    icu_path  = _find_file("ICUSTAYS.csv.gz")
    proc_path = _find_file("PROCEDUREEVENTS_MV.csv.gz")

    if icu_path is None:
        raise FileNotFoundError(
            "ICUSTAYS.csv.gz no encontrado. "
            "Descargalo de physionet.org/files/mimiciii/1.4/ y ponlo en la raiz del proyecto."
        )
    if proc_path is None:
        raise FileNotFoundError(
            "PROCEDUREEVENTS_MV.csv.gz no encontrado. "
            "Descargalo de physionet.org/files/mimiciii/1.4/ y ponlo en la raiz del proyecto."
        )

    print(f"ICUSTAYS:          {icu_path}")
    print(f"PROCEDUREEVENTS:   {proc_path}")

    icu = pd.read_csv(
        icu_path, compression="gzip",
        parse_dates=["INTIME", "OUTTIME"],
        usecols=["SUBJECT_ID", "HADM_ID", "ICUSTAY_ID", "FIRST_CAREUNIT", "INTIME", "OUTTIME"],
    )
    proc = pd.read_csv(
        proc_path, compression="gzip",
        parse_dates=["STARTTIME", "ENDTIME"],
        usecols=["SUBJECT_ID", "HADM_ID", "ICUSTAY_ID", "ITEMID", "STARTTIME", "ENDTIME"],
    )
    # Normalize to lowercase for consistency downstream
    icu.columns  = icu.columns.str.lower()
    proc.columns = proc.columns.str.lower()

    print(f"  ICU stays: {len(icu):,}  |  Procedures: {len(proc):,}")
    return icu, proc


# ── Matched-subset RECORDS ────────────────────────────────────────────────────
def get_matched_directories() -> dict[int, str]:
    """
    Downloads RECORDS from mimic3wdb-matched/1.0.
    Returns dict { subject_id -> directory_path }
    e.g. { 44083: 'p04/p044083' }
    """
    records_cache = CLIN_DIR / "RECORDS_matched.txt"
    if records_cache.exists():
        text = records_cache.read_text(encoding="utf-8")
    else:
        print("Descargando RECORDS de mimic3wdb-matched/1.0...")
        r = requests.get(f"{BASE_WFDB}/RECORDS", auth=_auth(), timeout=30)
        r.raise_for_status()
        text = r.text
        records_cache.write_text(text, encoding="utf-8")

    dirs: dict[int, str] = {}
    for line in text.splitlines():
        line = line.strip().rstrip("/")
        if not line:
            continue
        # Expected format: 'p04/p044083'
        m = re.search(r"(p\d{2}/(p0*(\d+)))$", line)
        if m:
            sid = int(m.group(3))
            dirs[sid] = m.group(1)

    print(f"  Pacientes en matched subset: {len(dirs):,}")
    return dirs


def list_numerics_records(dir_path: str) -> list[str]:
    """
    Lists the numerics record base names for a patient directory by
    parsing the HTML directory listing from PhysioNet.
    Returns list of full relative paths like 'p04/p044083/p044083-2112-05-04-19-50n'.
    """
    url = f"{BASE_WFDB}/{dir_path}/"
    r   = requests.get(url, auth=_auth(), timeout=20)
    if r.status_code != 200:
        return []
    hea_files = re.findall(r'href="([^"/]+\.hea)"', r.text)
    return [
        f"{dir_path}/{f[:-4]}"          # strip .hea
        for f in hea_files
        if f.endswith("n.hea")          # numerics only
    ]


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    MIMIC_DIR.mkdir(parents=True, exist_ok=True)
    CLIN_DIR.mkdir(parents=True, exist_ok=True)
    print(f"WFDB user: {os.getenv('WFDB_USERNAME', '(no configurado)')}")

    # 1. Clinical tables
    icu, proc = load_clinical_tables()

    # 2. Filter invasive MV events with duration >= MIN_VENT_HOURS
    mv = proc[proc["itemid"] == ITEMID_MV_INV].copy()
    mv["duration_h"] = (mv["endtime"] - mv["starttime"]).dt.total_seconds() / 3600
    mv = mv[mv["duration_h"] >= MIN_VENT_HOURS]
    mv = mv.merge(
        icu[["subject_id", "icustay_id", "first_careunit", "intime", "outtime"]],
        on=["subject_id", "icustay_id"],
        how="left",
    )
    mv = mv[mv["first_careunit"].isin(TARGET_ICU)].copy()
    print(f"Eventos MV invasiva >= {MIN_VENT_HOURS}h en {TARGET_ICU}: "
          f"{len(mv):,} ({mv['subject_id'].nunique():,} pacientes)")

    # 3. Matched-subset directories
    matched_dirs = get_matched_directories()

    # 4. Cross-reference: MV patients that have waveform records
    mv["has_waveform"] = mv["subject_id"].isin(matched_dirs)
    mv_matched = mv[mv["has_waveform"]].copy()
    print(f"  Con waveform disponible: {mv_matched['subject_id'].nunique():,} pacientes")

    # 5. Best MV event per patient (longest)
    mv_best = (
        mv_matched
        .sort_values("duration_h", ascending=False)
        .drop_duplicates("subject_id", keep="first")
    )
    print(f"  Candidatos totales (1 evento por paciente): {len(mv_best):,}")

    # 6. Select top N_CANDIDATES
    selected = mv_best.head(N_CANDIDATES).copy()
    print(f"  Seleccionados: {len(selected)}")

    # 7. Discover numerics record names by listing patient directories
    print("Listando directorios de pacientes para encontrar registros numerics...")
    numerics_first: dict[int, str] = {}
    numerics_all:   dict[int, str] = {}
    for i, (_, row) in enumerate(selected.iterrows(), 1):
        sid      = int(row["subject_id"])
        dir_path = matched_dirs[sid]
        recs     = list_numerics_records(dir_path)
        numerics_first[sid] = recs[0] if recs else ""
        numerics_all[sid]   = "|".join(recs)
        print(f"  [{i:>3}/{len(selected)}] sid={sid}  numerics={len(recs)}", end="\r")
    print()

    selected = selected.copy()
    selected["numerics_record"]      = selected["subject_id"].map(numerics_first)
    selected["numerics_records_all"] = selected["subject_id"].map(numerics_all)

    # Drop rows where no numerics record was found
    before = len(selected)
    selected = selected[selected["numerics_record"] != ""].copy()
    print(f"  Con al menos un registro numerics: {len(selected)} "
          f"(descartados sin numerics: {before - len(selected)})")

    # 8. Save
    out = selected[[
        "subject_id", "icustay_id", "first_careunit",
        "intime", "outtime", "starttime", "endtime", "duration_h",
        "numerics_record", "numerics_records_all",
    ]].rename(columns={"starttime": "vent_start", "endtime": "vent_end"})
    out.to_csv(MIMIC_DIR / "candidate_list.csv", index=False)

    # 9. Summary
    print(f"\n{'='*60}")
    print(f"Candidatos finales: {len(out)}")
    print(out["first_careunit"].value_counts().to_string())
    print(f"Duracion MV  media={out['duration_h'].mean():.1f}h  "
          f"mediana={out['duration_h'].median():.1f}h  "
          f"max={out['duration_h'].max():.1f}h")
    print(f"Guardado en: {MIMIC_DIR / 'candidate_list.csv'}")

    summary = {
        "n_selected":       len(out),
        "icu_distribution": out["first_careunit"].value_counts().to_dict(),
        "vent_duration_h": {
            "mean":   round(float(out["duration_h"].mean()), 1),
            "median": round(float(out["duration_h"].median()), 1),
            "max":    round(float(out["duration_h"].max()), 1),
        },
    }
    with open(MIMIC_DIR / "candidate_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Resumen en:  {MIMIC_DIR / 'candidate_summary.json'}")


if __name__ == "__main__":
    main()
