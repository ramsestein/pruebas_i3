"""
common/paths.py
===============
Resolución de rutas de datos a partir de la config (regla 7: nada de rutas
absolutas escritas en el código).

Soporta el formato ``${VAR}`` y ``${VAR:-default}`` en los valores del YAML, y
resuelve las rutas relativas contra la raíz del repositorio. La raíz del repo se
puede forzar con la variable de entorno ``IPROVE3_ROOT``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_ENV_PATTERN = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")


def repo_root() -> Path:
    """Raíz del repositorio (configurable con ``IPROVE3_ROOT``)."""
    env = os.environ.get("IPROVE3_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # src/common/paths.py -> parents[2] == raíz del repo
    return Path(__file__).resolve().parents[2]


def expand_env(value: str) -> str:
    """Expande ``${VAR}`` y ``${VAR:-default}`` en una cadena."""
    def _sub(m: re.Match) -> str:
        name = m.group("name")
        default = m.group("default")
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        raise KeyError(
            f"Variable de entorno '{name}' no definida y sin valor por defecto "
            f"(cadena original: {value!r})"
        )

    return _ENV_PATTERN.sub(_sub, value)


def resolve_path(value: str, base: Path | None = None) -> Path:
    """Resuelve una ruta de config a un ``Path`` absoluto.

    - Expande variables de entorno.
    - Expande ``~``.
    - Si es relativa, se interpreta respecto a ``base`` (por defecto la raíz
      del repo).
    """
    if not isinstance(value, str):
        raise TypeError(f"resolve_path espera str, recibido {type(value)!r}")
    expanded = expand_env(value)
    p = Path(expanded).expanduser()
    if not p.is_absolute():
        p = (base or repo_root()) / p
    return p.resolve()


def config_path(config: dict, *keys: str, required: bool = True) -> Path | None:
    """Busca una ruta anidada en la config (p.ej. ``paths.clinic_raw_dir``)."""
    node = config
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            if required:
                raise KeyError(f"Clave de config no encontrada: {'.'.join(keys)}")
            return None
        node = node[k]
    if node is None:
        if required:
            raise KeyError(f"Clave de config vacía: {'.'.join(keys)}")
        return None
    return resolve_path(node)
