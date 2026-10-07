"""Cobertura de variables (D8) y su unidad de tiempo.

Dos cosas que se han roto antes y que aquí quedan fijadas:

1. ``hourly_coverage`` trabaja en **minutos**; ``coverage_fractions`` recibe
   series en **horas**. Mezclarlas colapsa la rejilla horaria a un solo punto
   y la cobertura medida deja de tener sentido (bug de unidad de la Fase 1.6b).
2. Un evento con anotación horaria de las 8 variables durante toda la ventila-
   ción debe dar cobertura alta en **todas** (Fase 1.6c, punto 1).
"""

from __future__ import annotations

import pytest

from src.common.eicu_levels import hourly_coverage
from src.common.vital_signals import coverage_fractions

# Variables que un evento "completo" debería poder exhibir (Fase 1.6c).
OCHO_VARIABLES = ("HR", "SpO2", "MAP", "RR", "FiO2", "PEEP", "TV", "PIP")


def _hourly_series(hours: float = 48.0) -> dict[str, tuple[list[float], list[float]]]:
    """Serie horaria (una anotación por hora) para las 8 variables."""
    times = [float(h) for h in range(int(hours) + 1)]
    return {var: (list(times), [float(i) for i in range(len(times))])
            for var in OCHO_VARIABLES}


class TestCoberturaSintetica:
    def test_anotacion_horaria_da_cobertura_alta_en_las_8_variables(self):
        cov = coverage_fractions(_hourly_series(48.0), [(0.0, 48.0)])
        assert set(cov) == set(OCHO_VARIABLES)
        for var, frac in cov.items():
            assert frac >= 0.9, f"{var}: cobertura {frac:.3f} < 0.90"

    def test_locf_cubre_huecos_menores_que_la_ventana(self):
        # Un hueco de 3 h con LOCF de 4 h sigue siendo valor útil.
        series = {
            "HR": ([float(h) for h in range(0, 25, 4)], [70.0] * 7),
        }
        cov = coverage_fractions(series, [(0.0, 24.0)])
        assert cov["HR"] >= 0.9

    def test_sin_observaciones_la_cobertura_es_cero(self):
        cov = coverage_fractions({"HR": ([], [])}, [(0.0, 48.0)])
        assert cov["HR"] == pytest.approx(0.0)

    def test_sin_tramos_ventilados_la_cobertura_es_cero(self):
        cov = coverage_fractions(_hourly_series(24.0), [])
        assert all(v == pytest.approx(0.0) for v in cov.values())


class TestUnidades:
    """Regresión del bug de unidad (horas vs minutos) de la Fase 1.6b."""

    def test_coverage_fractions_espera_horas(self):
        # Serie horaria completa: cobertura 1.0. Si alguien la pasara a
        # ``hourly_coverage`` como minutos, la rejilla sería de 60 h y el
        # resultado se desplomaría.
        cov = coverage_fractions(_hourly_series(12.0), [(0.0, 12.0)])
        assert cov["HR"] == pytest.approx(1.0)

    def test_hourly_coverage_trabaja_en_minutos(self):
        # Anotación horaria en minutos con rejilla de 60 min.
        times_min = [float(m) for m in range(0, 12 * 60 + 1, 60)]
        vals = [70.0] * len(times_min)
        frac = hourly_coverage(times_min, vals, [(0.0, 12 * 60.0)])
        assert frac == pytest.approx(1.0)

    def test_pasar_horas_a_hourly_coverage_desploma_la_cobertura(self):
        # El error de la Fase 1.6b: 12 h leídas como 12 min concentran todas
        # las observaciones al principio de la rejilla y la cobertura cae de
        # 1.0 a lo que alcanza el LOCF (4 h de 12).
        frac = hourly_coverage([0.0, 1.0, 2.0, 3.0], [70.0] * 4, [(0.0, 720.0)])
        assert frac < 0.5
