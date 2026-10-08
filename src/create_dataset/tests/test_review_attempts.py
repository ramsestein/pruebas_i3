"""
tests/test_review_attempts.py
=============================
Test de la regla del informe de Fase 1 «posible artefacto de segmentación»
(> 30 % o < 2 % de eventos con ≥ 1 fallo).

El módulo vive en ``scripts/verify/fase1/review_attempts.py`` y no es un paquete
importable, así que se carga por ruta relativa al repo (sin rutas absolutas).

Incluye control negativo: los umbrales frontera NO deben marcarse.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify" / "fase1" / "review_attempts.py"


def _load():
    spec = importlib.util.spec_from_file_location("review_attempts", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ra():
    if not SCRIPT.exists():
        pytest.skip(f"no se encuentra {SCRIPT}")
    return _load()


def test_thresholds_are_the_documented_ones(ra):
    assert ra.LOW_PCT == 2.0
    assert ra.HIGH_PCT == 30.0


@pytest.mark.parametrize("pct,flagged", [
    (0.0, True),       # < 2 %
    (1.99, True),      # justo por debajo del mínimo
    (2.00, False),     # frontera inferior: NO se marca (control negativo)
    (5.0, False),      # caso normal
    (13.83, False),    # VitalDB real
    (30.00, False),    # frontera superior: NO se marca (control negativo)
    (30.01, True),     # justo por encima del máximo
    (100.0, True),     # > 30 %
])
def test_flag_boundaries(ra, pct, flagged):
    assert ra.is_flagged(pct) is flagged
    if flagged:
        assert "artefacto" in ra.verdict(pct)
    else:
        assert ra.verdict(pct) == "ok"


def test_verdict_names_the_side(ra):
    """El mensaje debe distinguir «demasiado alto» de «demasiado bajo»."""
    assert ">" in ra.verdict(50.0)
    assert "<" in ra.verdict(0.5)


def test_n_with_failure_counts_both_shapes(ra):
    """Los episodios llegan como lista de intentos (índices) o como dict (eICU)."""
    as_lists = [[(0.0, 1.0, None)],
                [(0.0, 1.0, 5.0), (7.0, 9.0, None)]]
    as_dicts = [{"id": 1, "spans": [(0.0, 1.0)]},
                {"id": 2, "spans": [(0.0, 1.0), (7.0, 9.0)]}]
    assert ra._n_with_failure(as_lists) == 1
    assert ra._n_with_failure(as_dicts) == 1
    # Control negativo: si no se distinguieran las formas, el dict daría 2.
    assert ra._n_attempts(as_dicts[0]) == 1
