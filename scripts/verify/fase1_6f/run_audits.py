#!/usr/bin/env python3
"""
fase1_6f/run_audits.py
======================
Ejecuta en orden la fase de auditoría de 1.6f (puntos 1 a 4) sobre el espejo:

1. ``obs_end_audit.py`` (punto 1): igualdades ``obs_end_h == vent_end_h`` y su
   motivo por muestras (con ``--probe`` y una submuestra acotada).
2. ``apagones_report.py`` (puntos 2 y 3) para Clínic y VitalDB.
3. ``fallos_audit.py`` (punto 4) para Clínic y VitalDB.

Comprueba antes que el espejo está marcado como COMPLETO (si no, se lee el crudo
y el punto 0 no sirve de nada). Cada paso deja su log en
``reports/fase1_6f/_audit_<paso>.log``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.raw_mirror import is_complete, mirror_dir, resolve_raw_dir  # noqa: E402
from src.stage0.io.versioning import load_config  # noqa: E402

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
OUT = ROOT / "reports" / "fase1_6f"
SCRIPTS = ROOT / "scripts" / "verify" / "fase1_6f"

# Índices de partida (el cohorte de análisis de 13/10 y sus equivalentes VitalDB).
INDICES = {
    "clinic": "datasets/clinic/cases_v0.5.0_eac1eb35/clinic_cases_index.json",
    "vitaldb": "datasets/vitaldb/cases_v0.4.0_aa142f35/vitaldb_cases_index.json",
}
# Índices de la cascada de 1.6e (parejas candidatas del corte).
CASCADAS = {
    "clinic": OUT / ".." / "fase1_6e" / "cascada_clinic.json",
    "vitaldb": OUT / ".." / "fase1_6e" / "cascada_vitaldb.json",
}
# Índice donde auditar obs_end_h (clínic: v0.6.0, el que tiene la cascada).
OBS_INDEX = {
    "clinic": "datasets/clinic/cases_v0.6.0_cbf8b634/clinic_cases_index.json",
    "vitaldb": "datasets/vitaldb/cases_v0.4.0_aa142f35/vitaldb_cases_index.json",
}


def run(name: str, cmd: list[str]) -> int:
    log = OUT / f"_audit_{name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"\n=== {name} ===\n{' '.join(cmd)}", flush=True)
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.run(cmd, cwd=str(ROOT), stdout=fh,
                              stderr=subprocess.STDOUT, text=True)
    tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    print("\n".join(tail[-12:]), flush=True)
    print(f"[{name}] exit={proc.returncode} -> {log.name}", flush=True)
    return proc.returncode


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cohorts", default="clinic,vitaldb")
    p.add_argument("--skip-obs-end", action="store_true")
    p.add_argument("--probe-limit", type=int, default=25)
    p.add_argument("--figs", type=int, default=5)
    p.add_argument("--allow-raw", action="store_true",
                   help="Seguir aunque el espejo no esté completo")
    p.add_argument("--config", default=str(CONFIG))
    args = p.parse_args()

    cfg = load_config(args.config)
    py = sys.executable
    fallos: list[str] = []

    for cohort in [c.strip() for c in args.cohorts.split(",") if c.strip()]:
        mirror = mirror_dir(cfg, cohort)
        raw, is_mirror = resolve_raw_dir(cfg, cohort)
        if not is_mirror:
            msg = (f"el espejo de {cohort} NO está completo "
                   f"({mirror}); se leería {raw}")
            if not args.allow_raw:
                print(f"[AVISO] {msg}. Se salta la cohorte.", flush=True)
                fallos.append(f"{cohort}: espejo incompleto")
                continue
            print(f"[AVISO] {msg}. Siguiendo por indicación.", flush=True)

        if not args.skip_obs_end:
            rc = run(f"obs_end_{cohort}", [
                py, str(SCRIPTS / "obs_end_audit.py"), "--cohort", cohort,
                "--index", OBS_INDEX[cohort], "--probe",
                "--probe-limit", str(args.probe_limit),
                "--out", str(OUT / f"obs_end_{cohort}.json"),
                "--md", str(OUT / f"obs_end_h_{cohort}.md")])
            if rc:
                fallos.append(f"obs_end_{cohort}")

        rc = run(f"apagones_{cohort}", [
            py, str(SCRIPTS / "apagones_report.py"), "--cohort", cohort,
            "--cascada", str(CASCADAS[cohort]),
            "--out-dir", str(OUT), "--figs", str(args.figs)])
        if rc:
            fallos.append(f"apagones_{cohort}")

        rc = run(f"fallos_{cohort}", [
            py, str(SCRIPTS / "fallos_audit.py"), "--cohort", cohort,
            "--index", INDICES[cohort], "--out-dir", str(OUT)])
        if rc:
            fallos.append(f"fallos_{cohort}")

    print(f"\n[run_audits] pasos con fallo: {fallos or 'ninguno'}", flush=True)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
