"""
Paso 2: Descarga los archivos WFDB (numerics) de los 150 candidatos
y extrae sus chartevents de ventilacion desde CHARTEVENTS.csv.gz.

Salidas:
  datasets/mimic3wdb/raw/<pXX>/<pXXNNNN>/   <- archivos .hea / .dat
  datasets/mimic3wdb/chartevents_vent.parquet
"""
import os
import csv
import gzip
import requests
import pandas as pd
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
MIMIC_DIR        = Path("datasets/mimic3wdb")
RAW_DIR          = MIMIC_DIR / "raw"
CLIN_DIR         = MIMIC_DIR / "clinical"
BASE_WFDB        = "https://physionet.org/files/mimic3wdb-matched/1.0"
MIMIC_CLIN_BASE  = "https://physionet.org/files/mimiciii/1.4"

# ── Chartevents itemids (CareVue + Metavision) ─────────────────────────────────
CHART_ITEMS = {
    "FiO2": {3420, 223835},
    "PEEP": {505,  224700},
    "TV":   {681,  224685},
    "PIP":  {507,  224696},
    "RR_V": {618,  220210},
    "MV":   {682,  224687},
}
ALL_VENT_ITEMIDS = {iid for ids in CHART_ITEMS.values() for iid in ids}


def _auth():
    return (os.getenv("WFDB_USERNAME", ""), os.getenv("WFDB_PASSWORD", ""))


def _dl_file(remote_path: str, local_path: Path, retries: int = 4) -> bool:
    """Download a single file from mimic3wdb-matched via HTTP auth, with retries."""
    import time
    if local_path.exists():
        return True
    url = f"{BASE_WFDB}/{remote_path}"
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, auth=_auth(), stream=True, timeout=60)
            if r.status_code == 404:
                return False          # file genuinely missing
            if r.status_code != 200:
                time.sleep(2 * attempt)
                continue
            local_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = local_path.with_suffix(".tmp")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            tmp.rename(local_path)
            return True
        except Exception:
            if attempt < retries:
                time.sleep(3 * attempt)
    return False


def _parse_dat_names(hea_path) -> list[str]:
    """
    Parse a WFDB .hea file and return the unique data file names referenced.
    Each signal line starts with the .dat filename.
    """
    dat_files = set()
    with open(hea_path, "r") as f:
        lines = f.readlines()
    for line in lines[1:]:                  # skip the record header line
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tok = line.split()[0]               # first token = data filename
        if tok.endswith(".dat"):
            dat_files.add(tok)
    return list(dat_files)


def download_numerics_record(numerics_rec: str) -> bool:
    """
    Descarga el registro numerics completo:
      1. Descarga el .hea maestro
      2. Parsea el .hea para encontrar el nombre real del .dat
      3. Descarga el .dat con su nombre correcto

    Ejemplo: 'p04/p044083/p044083-2112-05-04-19-50n'
    El .hea puede referenciar un .dat con nombre diferente (p.ej. '3202027n.dat')
    """
    parts     = numerics_rec.split("/")
    rec_base  = parts[-1]
    rel_dir   = "/".join(parts[:-1])        # e.g. 'p07/p078076'
    local_dir = RAW_DIR / rel_dir
    hea_local = local_dir / f"{rec_base}.hea"

    # 1. Download .hea
    if not _dl_file(f"{numerics_rec}.hea", hea_local):
        return False

    # 2. Parse .hea to find actual .dat name(s)
    dat_names = _parse_dat_names(hea_local)
    if not dat_names:
        # Fallback: try same-name .dat
        dat_names = [f"{rec_base}.dat"]

    # 3. Download each .dat file
    all_ok = True
    for dat_name in dat_names:
        ok = _dl_file(f"{rel_dir}/{dat_name}", local_dir / dat_name)
        if not ok:
            all_ok = False
    return all_ok


def _find_chartevents() -> Path | None:
    """Busca CHARTEVENTS.csv.gz en raiz, CLIN_DIR o MIMIC_DIR."""
    for loc in [Path("."), CLIN_DIR, MIMIC_DIR]:
        p = loc / "CHARTEVENTS.csv.gz"
        if p.exists():
            return p
    return None


def stream_chartevents(subject_ids: set, out_path: Path):
    """
    Filtra CHARTEVENTS.csv.gz para los subject_ids e itemids de ventilacion
    sin cargar el archivo completo (~30 GB) en memoria.
    Columnas MIMIC-III son UPPERCASE; se normalizan a lowercase.
    """
    chartevents_gz = _find_chartevents()
    if chartevents_gz is None:
        print(
            "CHARTEVENTS.csv.gz no encontrado.\n"
            f"  Descargalo de {MIMIC_CLIN_BASE}/CHARTEVENTS.csv.gz\n"
            "  y ponlo en la raiz del proyecto o en datasets/mimic3wdb/clinical/"
        )
        return

    print(f"Filtrando chartevents para {len(subject_ids)} pacientes "
          f"({len(ALL_VENT_ITEMIDS)} itemids)...")

    rows   = []
    total  = 0
    kept   = 0

    with gzip.open(chartevents_gz, "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # Normalize header keys to lowercase
        reader.fieldnames = [k.lower() for k in reader.fieldnames] if reader.fieldnames else None
        for row in reader:
            total += 1
            if total % 5_000_000 == 0:
                print(f"  {total // 1_000_000}M filas procesadas, {kept:,} guardadas...")
            try:
                sid = int(row["subject_id"])
                iid = int(row["itemid"])
            except (ValueError, KeyError):
                continue
            if sid in subject_ids and iid in ALL_VENT_ITEMIDS:
                rows.append({
                    "subject_id": sid,
                    "icustay_id": row.get("icustay_id", ""),
                    "charttime":  row.get("charttime", ""),
                    "itemid":     iid,
                    "valuenum":   row.get("valuenum", ""),
                    "valueuom":   row.get("valueuom", ""),
                })
                kept += 1

    print(f"  Total procesadas: {total:,}  |  Guardadas: {kept:,}")

    df = pd.DataFrame(rows)
    if not df.empty:
        df["charttime"] = pd.to_datetime(df["charttime"])
        df["valuenum"]  = pd.to_numeric(df["valuenum"], errors="coerce")
        df["subject_id"]  = df["subject_id"].astype("int32")
        df["icustay_id"]  = pd.to_numeric(df["icustay_id"], errors="coerce").astype("Int32")
        df["itemid"]      = df["itemid"].astype("int32")

    df.to_parquet(out_path, index=False)
    print(f"Chartevents guardados: {out_path}  ({len(df):,} filas)")


MAX_WORKERS = 12   # parallel downloads; adjust down if PhysioNet throttles


def _worker(args):
    """Thread worker: download one numerics record. Returns (rec, ok)."""
    rec, idx, total = args
    ok = download_numerics_record(rec)
    status = "OK  " if ok else "FAIL"
    print(f"  [{idx:>4}/{total}] {status}  {rec.split('/')[-1]}")
    return rec, ok


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"WFDB user: {os.getenv('WFDB_USERNAME', '(no configurado)')}")

    candidates = pd.read_csv(MIMIC_DIR / "candidate_list.csv")
    print(f"Candidatos a descargar: {len(candidates)}")

    # Collect all records to download
    all_jobs: list[tuple[str, int, int]] = []
    for row in candidates.itertuples():
        recs = [r.strip() for r in str(row.numerics_records_all).split("|") if r.strip()]
        all_jobs.extend(recs)

    # De-duplicate (same .dat may be referenced by multiple records)
    unique_jobs = list(dict.fromkeys(all_jobs))   # preserve order
    total = len(unique_jobs)
    print(f"Total registros numerics a descargar: {total}  "
          f"(workers paralelos: {MAX_WORKERS})")

    ok_recs   = 0
    fail_recs = 0
    failed_sids: set[int] = set()
    sid_of: dict[str, int] = {}
    for row in candidates.itertuples():
        for rec in str(row.numerics_records_all).split("|"):
            sid_of[rec.strip()] = int(row.subject_id)

    args = [(rec, i, total) for i, rec in enumerate(unique_jobs, 1)]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_worker, a): a[0] for a in args}
        for fut in as_completed(futures):
            rec, ok = fut.result()
            if ok:
                ok_recs += 1
            else:
                fail_recs += 1
                sid = sid_of.get(rec)
                if sid:
                    failed_sids.add(sid)

    print(f"\nDescarga: OK={ok_recs}  FAIL={fail_recs}  "
          f"pacientes con fallos: {len(failed_sids)}")
    if failed_sids:
        pd.Series(sorted(failed_sids)).to_csv(
            MIMIC_DIR / "download_failures.csv", index=False, header=["subject_id"]
        )

    # Chartevents
    chart_out = MIMIC_DIR / "chartevents_vent.parquet"
    if not chart_out.exists():
        subject_ids = set(candidates["subject_id"].dropna().astype(int))
        stream_chartevents(subject_ids, chart_out)
    else:
        existing = pd.read_parquet(chart_out)
        print(f"Chartevents ya filtrados: {chart_out} ({len(existing):,} filas)")


if __name__ == "__main__":
    main()
