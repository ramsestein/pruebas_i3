#!/usr/bin/env python3
"""
scripts/verify/patch_index_t0.py
===============================
Inyecta `t0_unix` / `tend_unix` (epoch) en un índice JSON de casos, leyendo
solo la cabecera de cada .vital (rápido, sin descomprimir el fichero).

Uso:
    python scripts/verify/patch_index_t0.py <cases_dir> <index.json>

Lee <index.json>, para cada evento localiza <cases_dir>/<file>, lee su cabecera
y añade los campos t0_unix/tend_unix. Escribe el resultado en el mismo fichero
(guarda una copia de seguridad *.bak).
"""
from __future__ import annotations

import gzip
import json
import shutil
import struct
import sys
from pathlib import Path


def read_vital_header(path: Path) -> dict | None:
    try:
        with open(path, "rb") as fh:
            gz = gzip.GzipFile(fileobj=fh)
            if gz.read(4) != b"VITA":
                return None
            gz.read(4)
            hlen_b = gz.read(2)
            if len(hlen_b) < 2:
                return None
            headerlen = struct.unpack("<H", hlen_b)[0]
            header = gz.read(headerlen)
            if len(header) < 26:
                return None
            dtstart = struct.unpack("<d", header[10:18])[0]
            dtend = struct.unpack("<d", header[18:26])[0]
        return {"dtstart": dtstart, "dtend": dtend}
    except Exception as e:  # noqa: BLE001
        if not hasattr(read_vital_header, "_dbg"):
            read_vital_header._dbg = True
            print(f"[patch_index_t0] EXC {path}: {e!r}")
        return None


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    cases_dir = Path(sys.argv[1])
    index_path = Path(sys.argv[2])

    with open(index_path, encoding="utf-8") as fh:
        idx = json.load(fh)

    events = idx.get("events", [])
    ok = 0
    fail = 0
    _shown = 0
    for ev in events:
        fname = ev.get("file")
        path = cases_dir / fname
        if not path.exists():
            fail += 1
            if _shown < 3:
                print(f"[patch_index_t0] no existe: {path}")
                _shown += 1
            continue
        hdr = read_vital_header(path)
        if hdr is None:
            fail += 1
            if _shown < 3:
                print(f"[patch_index_t0] cabecera ilegible: {path}")
                _shown += 1
            continue
        ev["t0_unix"] = hdr["dtstart"]
        ev["tend_unix"] = hdr["dtend"]
        ok += 1

    shutil.copy2(index_path, str(index_path) + ".bak")
    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=2)

    print(f"[patch_index_t0] {index_path}: {ok} eventos parcheados, {fail} fallos")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
