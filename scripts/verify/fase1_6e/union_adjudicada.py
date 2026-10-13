"""Aplica la adjudicacion visual ciega: de las candidatas variante d, une solo
las que el usuario marco `mismo` en `hoja.csv`.

Es el paso que sigue al paquete de `adjudicacion.py`. NO vuelve a mirar la
senal: solo cruza la hoja rellenada con la clave (que vive FUERA del repo) y
comprueba que la adjudicacion es plausible.

Salidas:
  - FUERA del repo (junto a la clave): `uniones_confirmadas_<cohort>.json` con
    las parejas confirmadas y su prueba (box, epochs, ficheros y su SHA-256 del
    contenido). Es el fichero que consumira el builder.
  - Dentro del repo: `reports/fase1_6e/adjudicacion/<cohort>/
    resultado_adjudicacion.md` — SOLO agregados (cuantos `mismo`/`distinto`/
    `dudoso` en candidatas y en controles, y la tasa de falsos positivos del
    adjudicador). Nada de datos de pacientes.

Regla dura: si alguna pareja marcada `mismo` resulta ser un CONTROL (pacientes
distintos por construccion), el script ABORTA (exit 2) y no une nada: seria un
falso positivo del adjudicador y hay que revisarlo antes de seguir.

Uso:
    python scripts/verify/fase1_6e/union_adjudicada.py --cohort clinic
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
REPORT = ROOT / "reports" / "fase1_6e"
CLAVE_DEF = r"D:\data\iprove3_work\adjudicacion_clave.json"
DECISIONES = ("mismo", "distinto", "dudoso")


def _carga_hoja(path: Path) -> tuple[dict[str, str], list[str]]:
    """Devuelve (decision por codigo, problemas). Vacio = dudoso."""
    decisiones: dict[str, str] = {}
    problemas: list[str] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for fila in csv.DictReader(fh):
            cod = (fila.get("codigo") or "").strip()
            dec = (fila.get("decision") or "").strip().lower()
            if not cod:
                continue
            if dec and dec not in DECISIONES:
                problemas.append(f"{cod}: decision '{dec}' no reconocida "
                                 f"(se trata como dudoso)")
                dec = "dudoso"
            if not dec:
                problemas.append(f"{cod}: sin decision (se trata como dudoso)")
                dec = "dudoso"
            decisiones[cod] = dec
    return decisiones, problemas


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cohort", required=True)
    p.add_argument("--clave", default=CLAVE_DEF)
    p.add_argument("--hoja", default=None)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--salida", default=None,
                   help="JSON de uniones confirmadas (por defecto, fuera del repo).")
    p.add_argument("--permitir-falso-positivo", action="store_true",
                   help="No abortar si un CONTROL se marco `mismo`.")
    args = p.parse_args()

    out_dir = (Path(args.out_dir) if args.out_dir
               else REPORT / "adjudicacion" / args.cohort)
    hoja = Path(args.hoja) if args.hoja else out_dir / "hoja.csv"
    if not hoja.exists():
        print(f"[error] no existe la hoja adjudicada: {hoja}")
        sys.exit(1)
    clave_path = Path(args.clave)
    if not clave_path.exists():
        print(f"[error] no existe la clave: {clave_path}")
        sys.exit(1)

    clave_doc = json.loads(clave_path.read_text(encoding="utf-8"))
    clave = clave_doc.get("clave") or {}
    decisiones, problemas = _carga_hoja(hoja)
    for aviso in problemas:
        print(f"[aviso] {aviso}")

    faltan = sorted(set(clave) - set(decisiones))
    sobran = sorted(set(decisiones) - set(clave))
    if faltan:
        print(f"[error] {len(faltan)} codigos de la clave no estan en la hoja: "
              f"{faltan[:8]}…")
        sys.exit(1)
    if sobran:
        print(f"[aviso] {len(sobran)} codigos de la hoja no estan en la clave "
              f"(se ignoran): {sobran[:8]}…")

    confirmadas, fp_controles = [], []
    cuenta = {"candidata": Counter(), "control": Counter()}
    for cod, meta in clave.items():
        tipo = meta.get("tipo", "candidata")
        dec = decisiones.get(cod, "dudoso")
        cuenta.setdefault(tipo, Counter())[dec] += 1
        if dec != "mismo":
            continue
        if tipo == "control":
            fp_controles.append(cod)
            continue
        confirmadas.append({
            "codigo": cod,
            "box": meta.get("box"),
            "prev_end_epoch": meta.get("prev_end_epoch"),
            "next_start_epoch": meta.get("next_start_epoch"),
            "prev_file": meta.get("prev_file"),
            "prev_file_sha256_content": meta.get("prev_file_sha256_content"),
            "next_file": meta.get("next_file"),
            "next_file_sha256_content": meta.get("next_file_sha256_content"),
            "gap_h": meta.get("gap_h"),
            "deltas": {k: meta.get(k) for k in
                       ("d_peep", "d_fio2", "d_tv", "d_rr")},
        })

    n_ctrl = sum(cuenta.get("control", Counter()).values())
    n_cand = sum(cuenta.get("candidata", Counter()).values())
    print(f"- Candidatas adjudicadas: {n_cand} "
          f"({dict(cuenta.get('candidata', Counter()))})")
    print(f"- Controles adjudicados: {n_ctrl} "
          f"({dict(cuenta.get('control', Counter()))})")
    print(f"- Confirmadas para unir: {len(confirmadas)}")
    if n_ctrl:
        print(f"- Falsos positivos del adjudicador (controles 'mismo'): "
              f"{len(fp_controles)}/{n_ctrl}")

    if fp_controles and not args.permitir_falso_positivo:
        print("\n[ABORTA] Hay controles marcados `mismo` (parejas que son "
              "pacientes distintos POR CONSTRUCCION).")
        print(f"  codigos: {fp_controles}")
        print("  No se une nada. Revisa esos PNG: si de verdad parecen el mismo "
              "paciente, el criterio de 'control' (hueco > 24 h) no separa "
              "pacientes; si te has equivocado al rellenar, corrige la hoja.")
        sys.exit(2)

    salida = Path(args.salida) if args.salida else (
        Path(str(args.clave)).with_name(f"uniones_confirmadas_{args.cohort}.json"))
    contenido = json.dumps({
        "cohort": args.cohort,
        "semilla": clave_doc.get("semilla"),
        "n_candidatas": n_cand, "n_controles": n_ctrl,
        "n_confirmadas": len(confirmadas),
        "falsos_positivos_control": len(fp_controles),
        "uniones": sorted(confirmadas, key=lambda c: (c["box"],
                                                      c["prev_end_epoch"])),
    }, ensure_ascii=False, indent=2)
    salida.write_text(contenido, encoding="utf-8")
    sha = hashlib.sha256(contenido.encode("utf-8")).hexdigest()

    md = [f"# Resultado de la adjudicacion — {args.cohort}", "",
          f"- Candidatas: **{n_cand}** "
          f"({', '.join(f'{k} {v}' for k, v in sorted(cuenta.get('candidata', Counter()).items()))})",
          f"- Controles: **{n_ctrl}** "
          f"({', '.join(f'{k} {v}' for k, v in sorted(cuenta.get('control', Counter()).items()))})",
          f"- Confirmadas para unir: **{len(confirmadas)}** de {n_cand}",
          f"- Falsos positivos del adjudicador: **{len(fp_controles)}/{n_ctrl}** "
          f"(controles marcados `mismo`; el 0 es lo esperado)",
          f"- Uniones confirmadas (FUERA del repo): `{salida}`",
          f"- SHA-256 del fichero de uniones: `{sha}`", "",
          "Solo se uniran estas parejas. Las marcadas `distinto` o `dudoso` no "
          "se unen (la duda se trata como no unir).", ""]
    if problemas:
        md += ["## Avisos al leer la hoja", ""] + [f"- {a}" for a in problemas] + [""]
    (out_dir / "resultado_adjudicacion.md").write_text("\n".join(md),
                                                      encoding="utf-8")
    print(f"Uniones confirmadas: {salida}")
    print(f"SHA-256: {sha}")
    print(f"Informe: {out_dir / 'resultado_adjudicacion.md'}")


if __name__ == "__main__":
    main()
