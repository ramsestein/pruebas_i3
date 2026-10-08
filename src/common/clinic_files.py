"""
common/clinic_files.py
======================
Localización de los ficheros ``.vital`` de **Clínic** (Fase 1.6b, punto 3).

Los ficheros de Clínic están anidados (``<box>/<sub>/.../<fecha>/fichero.vital``)
y **el nombre de fichero no es único**: dos cajas distintas pueden contener un
fichero con el mismo nombre. Localizar un evento por nombre global (lo que hacía
la verificación de la Fase 1.5) puede leer el fichero EQUIVOCADO y hacer que el
evento parezca un artefacto (sin pistas de ventilador).

Aquí se indexa por **nombre + caja** y se prefiere siempre el fichero que está
dentro de la caja del evento.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from src.common.timeutils import to_epoch_utc

_EPS = 1e-6
_NAME_DT = re.compile(r"(?P<token>[A-Za-z0-9]+?)_(?P<date>\d{6})_(?P<time>\d{6})\.vital$",
                      re.IGNORECASE)


def index_by_name(paths: Iterable[Path]) -> dict[str, list[Path]]:
    """Indexa ficheros por nombre: ``{nombre: [rutas ordenadas]}``."""
    by_name: dict[str, list[Path]] = defaultdict(list)
    for p in sorted(Path(x) for x in paths):
        by_name[p.name].append(p)
    return dict(by_name)


def duplicate_names(by_name: Mapping[str, Sequence[Path]]) -> dict[str, list[Path]]:
    """Nombres de fichero presentes en más de una ruta."""
    return {n: list(ps) for n, ps in by_name.items() if len(ps) > 1}


def locate_event_files(
    source_files: Sequence[str],
    box: str | None,
    by_name: Mapping[str, Sequence[Path]],
) -> tuple[list[Path], list[str]]:
    """Localiza los ficheros de un evento prefiriendo su **caja**.

    Devuelve ``(rutas, no_encontrados)``. Si el nombre está en varias rutas, se
    elige la que contiene la caja del evento como componente de la ruta; si
    ninguna la contiene, la primera por orden alfabético.
    """
    found: list[Path] = []
    missing: list[str] = []
    box = box or ""
    for name in source_files:
        hits = by_name.get(name)
        if not hits:
            missing.append(name)
            continue
        in_box = [p for p in hits if box and box in p.parts]
        found.append((in_box or list(hits))[0])
    return found, missing


def locate_by_datetime(
    paths: Iterable[Path],
    t0_unix: float,
    *,
    box: str | None = None,
    tolerance_s: float = 3600.0,
) -> list[Path]:
    """Localiza ficheros ``.vital`` por la **fecha y hora** de ``t0_unix``.

    Respaldo cuando el nombre que guarda el índice ya no existe en los datos
    crudos: el nombre de fichero de Clínic codifica ``token_AAAAMMDD_HHMMSS``,
    así que se busca por marca temporal (con tolerancia de ±``tolerance_s``).
    """
    if t0_unix is None or t0_unix <= 0:
        return []
    out: list[tuple[float, Path]] = []
    for p in paths:
        if box and box not in p.parts:
            continue
        m = _NAME_DT.search(p.name)
        if not m:
            continue
        try:
            dt = datetime.strptime(m.group("date") + m.group("time"), "%y%m%d%H%M%S")
        except ValueError:
            continue
        t = to_epoch_utc(dt)
        if abs(t - float(t0_unix)) <= tolerance_s:
            out.append((abs(t - float(t0_unix)), p))
    out.sort(key=lambda x: (x[0], str(x[1])))
    return [p for _, p in out]
