"""
common/timeout_batches.py
=========================
Fase 1.6d — utilidades para ejecutar **trabajo por lotes tolerante a cuelgues**.

Motivación: en Clínic el disco `D:` puede **bloquear una lectura durante minutos
o indefinidamente**. Cuando un lote se atasca, no se puede saber *qué* elemento
es el culpable, pero sí **bisecarlo**: se reintenta mitad y mitad hasta aislar
el elemento problemático (y así el resto del lote se salva).

`run_with_bisection` es **pura**: recibe una función ``run_fn(items) -> bool``
que ejecuta un lote y devuelve si fue bien. No sabe nada de subprocesos ni de
disco, así que se puede testear con funciones de mentira.
"""

from __future__ import annotations

from typing import Callable, Sequence


def run_with_bisection(
    items: Sequence[str],
    run_fn: Callable[[Sequence[str]], bool],
    *,
    min_batch: int = 1,
    max_depth: int = 24,
) -> tuple[list[str], list[str]]:
    """Ejecuta ``run_fn`` por lotes; si un lote falla, lo bisecciona.

    Devuelve ``(ok, fallidos)``: ``ok`` son los elementos cuyo lote (o sub-lote)
    se ejecutó con éxito; ``fallidos`` son los que quedaron en un lote de tamaño
    ``<= min_batch`` que seguía fallando (candidatos a colgado).

    ``min_batch`` debe ser >= 1. ``max_depth`` acota la recursión (protección
    frente a listas enormes).
    """
    if min_batch < 1:
        raise ValueError("min_batch debe ser >= 1")
    ok: list[str] = []
    failed: list[str] = []
    stack: list[tuple[tuple[str, ...], int]] = [(tuple(items), 0)]
    while stack:
        batch, depth = stack.pop()
        if not batch:
            continue
        if run_fn(batch):
            ok.extend(batch)
            continue
        if len(batch) <= min_batch or depth >= max_depth:
            failed.extend(batch)
            continue
        mid = len(batch) // 2
        stack.append((batch[mid:], depth + 1))
        stack.append((batch[:mid], depth + 1))
    # El orden de los elementos hallados no importa, pero se devuelve ordenado
    # según la lista original para que los informes sean estables.
    order = {it: i for i, it in enumerate(items)}
    ok.sort(key=lambda x: order.get(x, 0))
    failed.sort(key=lambda x: order.get(x, 0))
    return ok, failed


def chunk(items: Sequence[str], size: int) -> list[list[str]]:
    """Parte ``items`` en trozos de tamaño ``size`` (>= 1)."""
    if size < 1:
        raise ValueError("size debe ser >= 1")
    return [list(items[i:i + size]) for i in range(0, len(items), size)]
