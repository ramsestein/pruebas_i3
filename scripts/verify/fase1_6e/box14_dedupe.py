#!/usr/bin/env python3
"""
scripts/verify/fase1_6e/box14_dedupe.py
=======================================
Fase 1.6e — **punto 2b / D-g**: verificación por SHA-256 de los homónimos de
`box14` (y comprobación de homónimos en el resto de boxes).

Contexto: `D:\\data\\clinic_vitals\\box14` está partido en dos carpetas, `14/` y
`14_2/`, con **578 nombres repetidos** (mismos tokens de dispositivo). Como
`source_files` guarda solo el nombre, la selección cogía las dos copias y 30
eventos listaban cada fichero dos veces.

D-g: se deduplica por nombre dentro del box **solo si el contenido es idéntico**
(SHA-256). Si algún par difiere → NO se deduplica, se reporta el par (tamaño,
`dtstart`/`dtend`, pistas) y hay que parar para que lo decida el usuario.

Uso:
    python scripts/verify/fase1_6e/box14_dedupe.py --limit 20     # muestra
    python scripts/verify/fase1_6e/box14_dedupe.py                # todos
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.vital_signals import probe_vital_file  # noqa: E402

REPORT = ROOT / "reports" / "fase1_6e"
CHUNK = 4 * 1024 * 1024


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            b = fh.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def homonyms(root: Path) -> dict[str, list[Path]]:
    by_name: dict[str, list[Path]] = defaultdict(list)
    for f in sorted(root.rglob("*.vital")):
        by_name[f.name].append(f)
    return {n: ps for n, ps in by_name.items() if len(ps) > 1}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=r"D:\data\clinic_vitals\box14")
    p.add_argument("--limit", type=int, default=0,
                   help="Procesa solo los primeros N pares (0 = todos).")
    p.add_argument("--state", default=str(REPORT / "box14_dedupe.jsonl"))
    args = p.parse_args()

    root = Path(args.root)
    dups = homonyms(root)
    total = sum(len(v) - 1 for v in dups.values())
    print(f"{root}: {len(dups)} nombres repetidos · {total} pares a comparar")

    state = Path(args.state)
    done: dict[str, dict] = {}
    if state.exists():
        for line in state.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["name"]] = r
    if done:
        print(f"  reanudando: {len(done)} pares ya comparados")

    names = sorted(dups)
    if args.limit:
        names = names[: args.limit]
    REPORT.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(names, start=1):
        if name in done:
            continue
        paths = dups[name]
        hashes = {}
        info = []
        for path in paths:
            h = sha256(path)
            hashes[str(path)] = h
            info.append({"path": str(path.relative_to(root)),
                         "size": path.stat().st_size, "sha256": h,
                         "dtstart": None, "dtend": None, "tracks": []})
        identical = len(set(hashes.values())) == 1
        if not identical:
            # Solo en los pares DISTINTOS hace falta la sonda (para el informe):
            # parsear el fichero dobla la lectura, así que no se hace si son
            # iguales (el caso esperado).
            for path, d in zip(paths, info):
                pr = probe_vital_file(path)
                d["dtstart"] = None if pr is None else pr.dtstart
                d["dtend"] = None if pr is None else pr.dtend
                d["tracks"] = sorted(pr.tracks) if pr else []
        sizes = {d["size"] for d in info}
        row = {"name": name, "n": len(info), "identical": identical,
               "same_size": len(sizes) == 1, "files": info}
        with state.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        done[name] = row
        if i % 25 == 0 or i == len(names):
            ident = sum(1 for r in done.values() if r["identical"])
            print(f"  {i}/{len(names)} · idénticos {ident} · distintos "
                  f"{len(done) - ident}", flush=True)

    rows = [done[n] for n in names if n in done]
    ident = [r for r in rows if r["identical"]]
    diff = [r for r in rows if not r["identical"]]
    out = {
        "root": str(root),
        "n_names": len(dups),
        "n_pairs_compared": len(rows),
        "n_identical": len(ident),
        "n_different": len(diff),
        "different": diff[:50],
        "verdict": ("deduplicar (todos idénticos)" if not diff
                    else "PARAR: hay pares con contenido distinto"),
    }
    (REPORT / "box14_dedupe.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Fase 1.6e, punto 2b / D-g — homónimos de `box14` (SHA-256)", "",
        f"- Nombres repetidos: **{len(dups)}** · pares comparados: "
        f"**{len(rows)}**",
        f"- Contenido **idéntico** (mismo SHA-256): **{len(ident)}**",
        f"- Contenido **distinto**: **{len(diff)}**",
        f"- Veredicto: **{out['verdict']}**", "",
    ]
    for r in diff[:10]:
        lines.append(f"  - `{r['name']}`: " + " · ".join(
            f"{d['path']} ({d['size']} B, {d['sha256'][:12]}…, "
            f"{d['dtstart']}→{d['dtend']}, {len(d['tracks'])} pistas)"
            for d in r["files"]))
    (REPORT / "box14_dedupe.md").write_text("\n".join(lines) + "\n",
                                            encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
