#!/usr/bin/env python3
"""
scripts/verify/inspect_vital_header.py
======================================
Utilidad de inspección (solo lectura) de archivos .vital.

Uso:
    python scripts/verify/inspect_vital_header.py <ruta.vital> [--header-only] [--maxlen N]

Imprime dtstart/dtend/duración/dgmt y por cada track: srate, tipo y nº de
registros (en la ventana `maxlen` si no es header-only).
"""
from __future__ import annotations

import sys

import vitaldb


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    path = sys.argv[1]
    args = sys.argv[2:]
    header_only = "--header-only" in args
    maxlen = None
    if "--maxlen" in args:
        maxlen = float(args[args.index("--maxlen") + 1])
    if header_only:
        maxlen = None

    vf = vitaldb.VitalFile(path, header_only=header_only, maxlen=maxlen)
    print("file   :", path)
    print("dtstart:", vf.dtstart)
    print("dtend  :", vf.dtend)
    print("dur_h  :", round((vf.dtend - vf.dtstart) / 3600.0, 3))
    print("dgmt   :", vf.dgmt)
    print("tracks :", len(vf.trks))
    for t, trk in sorted(vf.trks.items()):
        n = len(trk.recs) if trk.recs else 0
        print(f"  {t}\tsrate={trk.srate}\ttype={trk.type}\tnrecs={n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
