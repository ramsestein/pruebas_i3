#!/usr/bin/env python3
"""
fase1_6f/make_mirror.py
=======================
Punto 0 (D-m): copia local de SOLO LECTURA de los crudos de ``D:\\data`` con
verificación SHA-256 fichero a fichero.

Qué hace
--------
1. Recorre el origen (p. ej. ``D:\\data\\clinic_vitals``) y copia al espejo
   conservando la estructura relativa.
2. Por cada fichero calcula el SHA-256 **mientras copia** y vuelve a leer el
   destino para verificarlo (dos lecturas independientes del hash).
3. Escribe un manifiesto ``_manifest.jsonl`` (ruta relativa, tamaño, sha256,
   mtime) en el espejo, con un resumen agregado opcional en el repo.
4. Marca los ficheros del espejo como solo lectura.

Reanudable: si un fichero ya está en el manifiesto con el mismo tamaño y hash y
el destino existe, se salta. Reejecutar tras un corte no recopia lo ya validado.

``D:`` sigue siendo la fuente de verdad: el espejo NUNCA se modifica ni se
escribe encima; si un fichero del espejo no coincide con su hash se vuelve a
copiar desde el origen.

Uso:
    python scripts/verify/fase1_6f/make_mirror.py \\
        --source D:/data/clinic_vitals --dest D:/iprove3_raw_mirror/clinic_vitals \\
        --exclude dataset_clinic --summary reports/fase1_6f/mirror_clinic.json

    python scripts/verify/fase1_6f/make_mirror.py \\
        --source D:/data/vitaldb_sicu --dest D:/iprove3_raw_mirror/vitaldb_sicu \\
        --pattern "*.vital" --summary reports/fase1_6f/mirror_vitaldb.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_CHUNK = 4 * 1024 * 1024  # 4 MiB


@dataclass
class Entry:
    rel: str
    size: int
    sha256: str
    mtime: float


def _sha256_file(path: Path, chunk: int = _CHUNK) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _copy_and_hash(src: Path, dst: Path, *, intentos: int = 3) -> tuple[str, int]:
    """Copia ``src`` -> ``dst`` calculando el SHA-256 del contenido copiado.

    Reintenta unas pocas veces: en ``D:`` se han visto fallos de lectura
    transitorios (``OSError [Errno 22]``) que mataban una copia de 40 minutos.
    """
    last: Exception | None = None
    for i in range(intentos):
        h = hashlib.sha256()
        total = 0
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(dst.suffix + ".part")
        try:
            with open(src, "rb") as fin, open(tmp, "wb") as fout:
                for block in iter(lambda: fin.read(_CHUNK), b""):
                    h.update(block)
                    fout.write(block)
                    total += len(block)
            os.replace(tmp, dst)
            return h.hexdigest(), total
        except OSError as exc:  # lectura/escritura transitoria
            last = exc
            time.sleep(1.5 * (i + 1))
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
    raise OSError(f"{src}: {last}")


def _iter_source(
    source: Path,
    *,
    exclude: tuple[str, ...],
    pattern: str | None,
    only_top: tuple[str, ...],
    prefix: str = "",
):
    """Itera ``(src, rel)`` del origen, respetando exclusiones y filtros."""
    for top in sorted(p for p in source.iterdir()):
        if top.name in exclude:
            continue
        if only_top and top.name not in only_top:
            continue
        if top.is_file():
            if pattern and not top.match(pattern):
                continue
            if prefix and not top.name.startswith(prefix):
                continue
            yield top, Path(top.name)
            continue
        for path in sorted(top.rglob("*")):
            if not path.is_file():
                continue
            if pattern and not path.match(pattern):
                continue
            if prefix and not path.name.startswith(prefix):
                continue
            yield path, path.relative_to(source)


def _load_manifest(path: Path) -> dict[str, Entry]:
    out: dict[str, Entry] = {}
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[d["rel"]] = Entry(d["rel"], int(d["size"]), d["sha256"],
                                  float(d.get("mtime", 0.0)))
    return out


def _set_readonly(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    except OSError:
        pass


def make_mirror(
    source: Path,
    dest: Path,
    *,
    exclude: tuple[str, ...] = (),
    pattern: str | None = None,
    only_top: tuple[str, ...] = (),
    prefix: str = "",
    summary: Path | None = None,
    log_every: int = 200,
    write_stamp: bool = True,
) -> dict:
    if not source.is_dir():
        raise FileNotFoundError(f"Origen no encontrado: {source}")
    dest.mkdir(parents=True, exist_ok=True)
    manifest_path = dest / "_manifest.jsonl"
    entries = _load_manifest(manifest_path)
    if manifest_path.exists():
        # El manifiesto se deja en solo lectura al terminar; para reanudar hay
        # que devolverle el permiso de escritura antes de abrirlo en modo append.
        os.chmod(manifest_path, stat.S_IWRITE | stat.S_IREAD)

    t_start = time.time()
    n_copied = n_skipped = n_mismatch = n_failed = 0
    failed: list[str] = []
    total_bytes = 0
    t0 = time.time()
    with open(manifest_path, "a", encoding="utf-8") as mfh:
        for src, rel in _iter_source(
            source, exclude=exclude, pattern=pattern, only_top=only_top,
            prefix=prefix
        ):
            rel_s = rel.as_posix()
            dst = dest / rel
            size = src.stat().st_size
            prev = entries.get(rel_s)
            if (prev is not None and prev.size == size
                    and dst.exists()
                    and _sha256_file(dst) == prev.sha256):
                n_skipped += 1
                total_bytes += size
                continue
            if dst.exists():
                # El destino difiere del manifiesto: se recomputa desde el origen.
                n_mismatch += 1
            try:
                sha, written = _copy_and_hash(src, dst)
                verify = _sha256_file(dst)
                if verify != sha:
                    raise IOError(f"Verificación SHA-256 fallida en {rel_s}")
            except OSError as exc:
                # Un fichero ilegible no debe matar la copia entera: se apunta y
                # se sigue (el espejo NO se marcará como completo).
                n_failed += 1
                failed.append(rel_s)
                print(f"[mirror] FALLO {rel_s}: {exc}", flush=True)
                continue
            _set_readonly(dst)
            entry = Entry(rel_s, written, sha, src.stat().st_mtime)
            entries[rel_s] = entry
            mfh.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
            mfh.flush()
            n_copied += 1
            total_bytes += written
            if n_copied % log_every == 0:
                dt = time.time() - t0
                mbps = (total_bytes / (1024 * 1024)) / dt if dt else 0.0
                print(f"[mirror] {n_copied} copiados, {n_skipped} saltados, "
                      f"{total_bytes / 2**30:.1f} GiB, {mbps:.1f} MiB/s",
                      flush=True)

    # El manifiesto también en solo lectura; no debe reescribirse por error.
    _set_readonly(manifest_path)

    digests = sorted(entries.values(), key=lambda e: e.rel)
    root = hashlib.sha256()
    for e in digests:
        root.update(f"{e.rel}\0{e.size}\0{e.sha256}\n".encode("utf-8"))

    summary_doc = {
        "source": str(source),
        "dest": str(dest),
        "n_files": len(digests),
        "total_bytes": sum(e.size for e in digests),
        "total_gib": round(sum(e.size for e in digests) / 2**30, 3),
        "merkle_root": root.hexdigest(),
        "n_copied_this_run": n_copied,
        "n_skipped_this_run": n_skipped,
        "n_rewritten_mismatch": n_mismatch,
        "n_failed_this_run": n_failed,
        "failed": failed[:50],
        "elapsed_s": round(time.time() - t_start, 1),
        "manifest": str(manifest_path),
    }
    if summary is not None:
        summary.parent.mkdir(parents=True, exist_ok=True)
        with open(summary, "w", encoding="utf-8") as fh:
            json.dump(summary_doc, fh, ensure_ascii=False, indent=2)
    # Marca de "espejo COMPLETO" dentro del propio espejo: los builders solo lo
    # usan si existe (si no, leerían un espejo a medias). NO se deja si quedó
    # algún fichero sin copiar.
    stamp = dest / "_mirror_summary.json"
    if n_failed == 0 and write_stamp:
        if stamp.exists():
            os.chmod(stamp, stat.S_IWRITE | stat.S_IREAD)
        with open(stamp, "w", encoding="utf-8") as fh:
            json.dump(summary_doc, fh, ensure_ascii=False, indent=2)
        _set_readonly(stamp)
    else:
        print(f"[mirror] {n_failed} ficheros SIN copiar: el espejo NO se marca "
              f"como completo. Repetir la orden para reintentarlos.",
              flush=True)
    return summary_doc


def verify_mirror(
    manifest_path: Path, *, log_every: int = 500,
) -> tuple[int, list[str]]:
    """Relee el espejo y comprueba cada fichero contra el manifiesto."""
    entries = _load_manifest(manifest_path)
    bad: list[str] = []
    n = 0
    for e in entries.values():
        path = manifest_path.parent / e.rel
        if not path.exists():
            bad.append(f"FALTA  {e.rel}")
            continue
        if path.stat().st_size != e.size:
            bad.append(f"TAMAÑO {e.rel}")
            continue
        if _sha256_file(path) != e.sha256:
            bad.append(f"HASH   {e.rel}")
        n += 1
        if log_every and n % log_every == 0:
            print(f"[verify] {n}/{len(entries)} verificados…", flush=True)
    return len(entries), bad


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True)
    p.add_argument("--dest", required=True)
    p.add_argument("--exclude", default="", help="Carpetas top a excluir (coma)")
    p.add_argument("--only-top", default="", help="Solo estas carpetas top (coma)")
    p.add_argument("--prefix", default="", help="Solo ficheros con este prefijo")
    p.add_argument("--no-stamp", action="store_true",
                   help="No dejar la marca de espejo completo (uso por box)")
    p.add_argument("--pattern", default=None, help="Glob de ficheros (p.ej. *.vital)")
    p.add_argument("--summary", default=None, help="JSON de resumen (en el repo)")
    p.add_argument("--verify-only", action="store_true",
                   help="No copia: solo verifica el manifiesto ya existente")
    args = p.parse_args()

    dest = Path(args.dest)
    if args.verify_only:
        n, bad = verify_mirror(dest / "_manifest.jsonl")
        print(json.dumps({"verificados": n, "fallos": bad[:50],
                          "n_fallos": len(bad)}, ensure_ascii=False, indent=2))
        return 1 if bad else 0

    doc = make_mirror(
        Path(args.source),
        dest,
        exclude=tuple(x for x in args.exclude.split(",") if x),
        only_top=tuple(x for x in args.only_top.split(",") if x),
        pattern=args.pattern,
        prefix=args.prefix,
        summary=Path(args.summary) if args.summary else None,
        write_stamp=not args.no_stamp,
    )
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
