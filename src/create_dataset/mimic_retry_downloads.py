"""
Reintenta descargas de registros numerics WFDB fallidos de MIMIC-III.
Descarga solo archivos faltantes (.hea y .dat) usando menos workers para evitar throttling.

Uso:
    python src/create_dataset/mimic_retry_downloads.py
"""
import os
import time
import requests
import pandas as pd
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
MIMIC_DIR = Path("datasets/mimic3wdb")
RAW_DIR   = MIMIC_DIR / "raw"
BASE_WFDB = "https://physionet.org/files/mimic3wdb-matched/1.0"

# ── Parameters ────────────────────────────────────────────────────────────────
MAX_WORKERS = 3   # reducido para evitar throttling de PhysioNet
RETRIES     = 5


def _auth():
    return (os.getenv("WFDB_USERNAME", ""), os.getenv("WFDB_PASSWORD", ""))


def _dl_file(remote_path: str, local_path: Path) -> tuple[bool, int]:
    """Download a single file. Returns (success, http_status)."""
    if local_path.exists():
        return True, 200
    url = f"{BASE_WFDB}/{remote_path}"
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, auth=_auth(), stream=True, timeout=60)
            if r.status_code == 404:
                return False, 404
            if r.status_code == 429:
                time.sleep(10 * attempt)
                continue
            if r.status_code != 200:
                time.sleep(3 * attempt)
                continue
            local_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = local_path.with_suffix(".tmp")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            tmp.rename(local_path)
            return True, 200
        except Exception:
            if attempt < RETRIES:
                time.sleep(5 * attempt)
    return False, -1


def _parse_dat_names(hea_path: Path) -> list[str]:
    """Parse .hea to find referenced .dat files."""
    dat_files = set()
    with open(hea_path, "r") as f:
        lines = f.readlines()
    for line in lines[1:]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tok = line.split()[0]
        if tok.endswith(".dat"):
            dat_files.add(tok)
    if not dat_files:
        rec_base = hea_path.stem
        dat_files.add(f"{rec_base}.dat")
    return list(dat_files)


def download_record(rec: str) -> tuple[str, bool, list[str]]:
    """Download a numerics record (.hea + .dat files). Returns (rec, ok, missing)."""
    parts    = rec.split("/")
    rec_base = parts[-1]
    rel_dir  = "/".join(parts[:-1])
    local_dir = RAW_DIR / rel_dir
    hea_local = local_dir / f"{rec_base}.hea"

    # 1. Download .hea
    if not hea_local.exists():
        ok, status = _dl_file(f"{rec}.hea", hea_local)
        if not ok:
            return rec, False, [f"{rec}.hea (status={status})"]

    # 2. Parse .hea for .dat names
    try:
        dat_names = _parse_dat_names(hea_local)
    except Exception as e:
        return rec, False, [f"parse_hea_error: {e}"]

    # 3. Download each .dat
    missing = []
    for dat_name in dat_names:
        dat_ok, status = _dl_file(f"{rel_dir}/{dat_name}", local_dir / dat_name)
        if not dat_ok:
            missing.append(f"{rel_dir}/{dat_name} (status={status})")

    return rec, len(missing) == 0, missing


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"WFDB user: {os.getenv('WFDB_USERNAME', '(no configurado)')}")

    candidates = pd.read_csv(MIMIC_DIR / "candidate_list.csv")
    print(f"Candidatos: {len(candidates)}")

    # Collect ALL records that need downloading
    all_recs = []
    for row in candidates.itertuples():
        recs = [r.strip() for r in str(row.numerics_records_all).split("|") if r.strip()]
        for rec in recs:
            all_recs.append((rec, int(row.subject_id)))

    # Deduplicate while preserving order
    seen = set()
    unique_jobs = []
    for rec, sid in all_recs:
        if rec not in seen:
            seen.add(rec)
            unique_jobs.append((rec, sid))

    total = len(unique_jobs)
    print(f"Registros unicos a verificar/descargar: {total}")

    # Check which are actually missing
    missing_jobs = []
    for rec, sid in unique_jobs:
        parts    = rec.split("/")
        rec_base = parts[-1]
        rel_dir  = "/".join(parts[:-1])
        local_dir = RAW_DIR / rel_dir
        hea_local = local_dir / f"{rec_base}.hea"

        if not hea_local.exists():
            missing_jobs.append((rec, sid))
            continue

        # Check .dat files
        try:
            dat_names = _parse_dat_names(hea_local)
        except Exception:
            missing_jobs.append((rec, sid))
            continue

        for dat_name in dat_names:
            if not (local_dir / dat_name).exists():
                missing_jobs.append((rec, sid))
                break

    print(f"Registros con archivos faltantes: {len(missing_jobs)}")
    if not missing_jobs:
        print("Nada que descargar.")
        return

    # Download missing
    ok_recs   = 0
    fail_recs = 0
    failed_details = []

    def _worker(args):
        idx, (rec, sid) = args
        rec, ok, missing = download_record(rec)
        status = "OK  " if ok else "FAIL"
        print(f"  [{idx:>4}/{len(missing_jobs)}] {status}  {rec.split('/')[-1]}")
        return rec, sid, ok, missing

    args = [(i, job) for i, job in enumerate(missing_jobs, 1)]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_worker, a): a for a in args}
        for fut in as_completed(futures):
            rec, sid, ok, missing = fut.result()
            if ok:
                ok_recs += 1
            else:
                fail_recs += 1
                failed_details.append({"subject_id": sid, "record": rec, "missing": "; ".join(missing)})

    print(f"\nDescarga: OK={ok_recs}  FAIL={fail_recs}")

    if failed_details:
        df_fail = pd.DataFrame(failed_details)
        df_fail.to_csv(MIMIC_DIR / "download_failures_retry.csv", index=False)
        print(f"Detalles de fallos guardados en: {MIMIC_DIR / 'download_failures_retry.csv'}")
        # Unique subject IDs that still fail
        still_failed_sids = sorted(df_fail["subject_id"].unique())
        print(f"Pacientes que aun fallan: {len(still_failed_sids)}")
    else:
        print("Todas las descargas exitosas!")

    # Final summary of patients with complete data
    complete_sids = set()
    partial_sids = set()
    zero_sids = set()
    for row in candidates.itertuples():
        sid = int(row.subject_id)
        recs = [r.strip() for r in str(row.numerics_records_all).split("|") if r.strip()]
        ok_count = 0
        for rec in recs:
            parts = rec.split("/")
            rec_base = parts[-1]
            rel_dir = "/".join(parts[:-1])
            local_dir = RAW_DIR / rel_dir
            hea = local_dir / f"{rec_base}.hea"
            if not hea.exists():
                continue
            try:
                dat_names = _parse_dat_names(hea)
            except Exception:
                continue
            if all((local_dir / dn).exists() for dn in dat_names):
                ok_count += 1
        if ok_count == len(recs) and len(recs) > 0:
            complete_sids.add(sid)
        elif ok_count > 0:
            partial_sids.add(sid)
        else:
            zero_sids.add(sid)

    print(f"\nResumen final de pacientes:")
    print(f"  Completos (todos los registros): {len(complete_sids)}")
    print(f"  Parciales (algunos registros): {len(partial_sids)}")
    print(f"  Sin datos: {len(zero_sids)}")


if __name__ == "__main__":
    main()
