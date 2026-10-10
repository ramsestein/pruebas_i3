"""
Tests de ``common/timeout_batches.py`` (Fase 1.6d, punto 5): lotes con
bisección para aislar el elemento que se cuelga.
"""

from __future__ import annotations

import pytest

from src.common.timeout_batches import chunk, run_with_bisection


class TestChunk:
    def test_tamano(self):
        assert chunk(["a", "b", "c", "d", "e"], 2) == [["a", "b"], ["c", "d"], ["e"]]

    def test_vacio(self):
        assert chunk([], 3) == []

    def test_size_invalido(self):
        with pytest.raises(ValueError):
            chunk(["a"], 0)


class TestBiseccion:
    def test_todos_ok_un_solo_lote(self):
        calls = []

        def run(items):
            calls.append(list(items))
            return True

        ok, bad = run_with_bisection(["a", "b", "c"], run)
        assert ok == ["a", "b", "c"] and bad == []
        assert len(calls) == 1

    def test_bisecciona_hasta_aislar_el_culpable(self):
        """Un elemento 'venenoso' falla siempre; el resto debe salvarse."""
        poison = "b"

        def run(items):
            return poison not in items

        ok, bad = run_with_bisection(["a", "b", "c", "d"], run)
        assert bad == ["b"]
        assert ok == ["a", "c", "d"]

    def test_varios_culpables(self):
        def run(items):
            return not ({"b", "d"} & set(items))

        ok, bad = run_with_bisection(["a", "b", "c", "d", "e"], run)
        assert bad == ["b", "d"]
        assert ok == ["a", "c", "e"]

    def test_todo_falla(self):
        def run(items):
            return False

        ok, bad = run_with_bisection(["a", "b"], run)
        assert ok == [] and bad == ["a", "b"]

    def test_min_batch_no_baja_de_ahi(self):
        """Con ``min_batch=2`` no se llega a lotes de 1 elemento."""
        tam = []

        def run(items):
            tam.append(len(list(items)))
            return False

        ok, bad = run_with_bisection(["a", "b", "c", "d"], run, min_batch=2)
        assert ok == [] and bad == ["a", "b", "c", "d"]
        assert min(tam) == 2

    def test_orden_estable(self):
        def run(items):
            return "c" not in items

        ok, bad = run_with_bisection(["a", "b", "c", "d"], run)
        assert ok == ["a", "b", "d"]
        assert bad == ["c"]

    def test_min_batch_invalido(self):
        with pytest.raises(ValueError):
            run_with_bisection(["a"], lambda items: True, min_batch=0)

    def test_lista_vacia(self):
        assert run_with_bisection([], lambda items: True) == ([], [])
