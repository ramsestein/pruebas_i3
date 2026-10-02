"""
common/timeutils.py
===================
Utilidades de tiempo. Regla Fase 1.3: TODO en UTC. Ningún ``.timestamp()``
sobre datetimes naive en el repositorio.

``datetime.timestamp()`` sobre un datetime naive lo interpreta como hora LOCAL,
lo que produce desplazamientos silenciosos según la zona del equipo. Para
evitarlo, este es el ÚNICO módulo autorizado a llamar a ``.timestamp()``, y solo
después de fijar explícitamente la zona (UTC por defecto).

Uso:
    from src.common.timeutils import ensure_utc, to_epoch_utc
    t0 = to_epoch_utc(dt)          # adjunta UTC si el datetime es naive
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import numpy as np
import pandas as pd

UTC = timezone.utc

# Único punto autorizado a llamar a ``.timestamp()`` (ver test_no_naive_timestamp).
ALLOWED_TIMESTAMP_MODULES: frozenset[str] = frozenset({"src/common/timeutils.py"})


def ensure_utc(dt: datetime) -> datetime:
    """Devuelve ``dt`` en UTC.

    Si ``dt`` es naive se le adjunta UTC explícitamente (asumiendo que los
    timestamps de origen ya están en UTC). Si es "aware", se convierte a UTC.
    Nunca se deja un datetime naive (evita el uso implícito de hora local).
    """
    if not isinstance(dt, datetime):
        raise TypeError(f"ensure_utc espera datetime, recibido {type(dt)!r}")
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_epoch_utc(dt: datetime) -> float:
    """Segundos epoch UTC de un datetime (naive -> se asume UTC)."""
    return ensure_utc(dt).timestamp()


def series_to_epoch_utc(values: Iterable) -> np.ndarray:
    """Convierte una colección de datetimes a un array float64 de epoch UTC."""
    return np.array([to_epoch_utc(v) for v in values], dtype=np.float64)


def series_to_utc(values: pd.Series) -> pd.Series:
    """Convierte una Serie de pandas a datetime con tz UTC explícita."""
    s = pd.to_datetime(values)
    if s.dt.tz is None:
        return s.dt.tz_localize("UTC")
    return s.dt.tz_convert("UTC")
