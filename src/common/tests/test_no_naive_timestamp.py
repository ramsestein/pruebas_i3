"""
tests/test_no_naive_timestamp.py
================================
Comprobación repo-wide (Fase 1.3): ningún ``.timestamp()`` fuera de
``src/common/timeutils.py``.

``datetime.timestamp()`` sobre un datetime naive usa la hora LOCAL del equipo,
lo que desplaza silenciosamente las marcas temporales. La única forma segura es
pasar por ``to_epoch_utc`` (que fija UTC antes de llamar a ``.timestamp()``).
"""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.common.timeutils import (
    ALLOWED_TIMESTAMP_MODULES,
    ensure_utc,
    series_to_epoch_seconds,
    to_epoch_utc,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCAN_DIRS = ("src", "scripts")


def find_timestamp_calls(source: str) -> list[int]:
    """Líneas donde aparece una llamada ``<expr>.timestamp()``."""
    tree = ast.parse(source)
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "timestamp"
            and not node.args
        ):
            lines.append(node.lineno)
    return lines


def _iter_py_files():
    for d in SCAN_DIRS:
        base = REPO_ROOT / d
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path


def test_no_timestamp_calls_outside_allowlist():
    offenders: list[str] = []
    for path in _iter_py_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWED_TIMESTAMP_MODULES:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno in find_timestamp_calls(source):
            offenders.append(f"{rel}:{lineno}")

    assert not offenders, (
        "Uso de .timestamp() fuera de src/common/timeutils.py "
        "(usa to_epoch_utc/ensure_utc):\n  " + "\n  ".join(offenders)
    )


def test_detector_detects_real_call():
    """Control: el detector SÍ encuentra una llamada de verdad (puede fallar)."""
    assert find_timestamp_calls("x = a.timestamp()\n") == [1]


def test_detector_ignores_non_calls():
    """Control negativo: una mención textual no es una llamada."""
    assert find_timestamp_calls("s = 'a.timestamp()'\n") == []
    assert find_timestamp_calls("y = a.timestamp\n") == []


def test_ensure_utc_attaches_naive_local_independence():
    naive = __import__("datetime").datetime(2025, 1, 1, 12, 0, 0)
    aware = ensure_utc(naive)
    assert aware.tzinfo is not None
    # Un naive se interpreta como UTC: epoch independiente de la hora local.
    assert to_epoch_utc(naive) == 1735732800.0
    # Un aware en otra zona se convierte a UTC (mismo instante).
    import datetime as _dt
    cet = _dt.timezone(_dt.timedelta(hours=1))
    assert to_epoch_utc(naive.replace(tzinfo=cet)) == 1735729200.0


def test_to_epoch_utc_rejects_non_datetime():
    with pytest.raises(TypeError):
        to_epoch_utc("2025-01-01")  # type: ignore[arg-type]


def test_series_to_epoch_seconds_is_resolution_independent():
    """Corrección 5: microsegundos y nanosegundos dan el MISMO epoch."""
    ts = ["2150-01-01 00:00:00", "2150-01-01 06:00:00"]
    ref = None
    for unit in ("us", "ns"):
        s = pd.to_datetime(pd.Series(ts)).astype(f"datetime64[{unit}]")
        out = series_to_epoch_seconds(s)
        assert out[1] - out[0] == pytest.approx(6 * 3600.0)
        if ref is None:
            ref = out
        else:
            assert np.allclose(out, ref)


def test_negative_control_old_astype_depends_on_resolution():
    """Control: ``astype('int64') / 1e9`` SÍ depende de la resolución."""
    ts = ["2150-01-01 00:00:00", "2150-01-01 06:00:00"]
    s_us = pd.to_datetime(pd.Series(ts)).astype("datetime64[us]")
    s_us = s_us.dt.tz_localize("UTC")
    naive_old = s_us.astype("int64") / 1e9
    # Con microsegundos el delta sale 1000x menor: el método nuevo lo evita.
    assert not np.isclose(naive_old.iloc[1] - naive_old.iloc[0], 6 * 3600.0)
