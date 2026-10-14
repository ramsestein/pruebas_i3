"""
common/raw_mirror.py
====================
Fase 1.6f, punto 0 (D-m): espejo local de SOLO LECTURA de los crudos.

``D:\\data`` sigue siendo la fuente de verdad; el espejo (``D:/iprove3_raw_mirror``
por defecto, configurable con ``IPROVE3_RAW_MIRROR``) es una copia con manifiesto
SHA-256 que permite reconstruir sin volver a golpear los crudos y sin riesgo de
modificarlos (los ficheros del espejo se marcan como solo lectura).

Lo que NO hace este módulo: copiar. La copia la hace
``scripts/verify/fase1_6f/make_mirror.py`` (con ``--verify-only`` para auditar el
manifiesto) y **nunca** se ejecuta a la vez que un builder sobre el mismo disco.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from src.common.paths import config_path, resolve_path

logger = logging.getLogger(__name__)

# Subcarpeta del espejo por cohorte con señal.
MIRROR_SUBDIR: dict[str, str] = {
    "clinic": "clinic_vitals",
    "vitaldb": "vitaldb_sicu",
}

MANIFEST_NAME = "_manifest.jsonl"
# Marca que deja ``make_mirror.py`` al terminar una copia completa. Los builders
# solo usan el espejo si existe: sin ella podrían leer un espejo a medias (el
# manifiesto solo lista lo que YA se copió, así que no basta con él).
SUMMARY_NAME = "_mirror_summary.json"


def mirror_root(config: dict) -> Path | None:
    """Raíz del espejo según la config (``fase1_6f.mirror.root``)."""
    node = ((config.get("fase1_6f") or {}).get("mirror") or {})
    value = node.get("root")
    if not value:
        return None
    try:
        return resolve_path(value)
    except KeyError:
        return None


def mirror_dir(config: dict, cohort: str) -> Path | None:
    """Carpeta del espejo de una cohorte, o ``None`` si no está configurado."""
    root = mirror_root(config)
    if root is None:
        return None
    return root / MIRROR_SUBDIR.get(cohort, cohort)


def is_complete(mirror: Path) -> bool:
    """¿El espejo terminó de copiarse? (marca ``_mirror_summary.json``)."""
    return (mirror / SUMMARY_NAME).is_file()


def read_summary(mirror: Path) -> dict:
    """Resumen agregado del espejo (``{}`` si no hay marca de copia completa)."""
    path = mirror / SUMMARY_NAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_raw_dir(config: dict, cohort: str) -> tuple[Path, bool]:
    """Origen de los ``.vital`` de una cohorte: ``(ruta, es_espejo)``.

    Fase 1.6f (D-m): si el espejo está COMPLETO se usa el espejo; si no, el crudo
    de ``D:``. El crudo nunca se modifica, así que caer al crudo es seguro.
    """
    raw = config_path(config, "paths", f"{cohort}_raw_dir")
    node = ((config.get("fase1_6f") or {}).get("mirror") or {})
    if node.get("use", True) is False:
        return raw, False
    mirror = mirror_dir(config, cohort)
    if mirror is not None and mirror.is_dir():
        if is_complete(mirror):
            return mirror, True
        logger.warning(
            "[raw_mirror] el espejo %s existe pero NO está marcado como completo "
            "(falta %s); se lee el crudo %s",
            mirror, SUMMARY_NAME, raw)
    return raw, False


def read_manifest(mirror: Path) -> dict[str, dict]:
    """Lee el manifiesto ``_manifest.jsonl`` del espejo."""
    path = mirror / MANIFEST_NAME
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[d["rel"]] = d
    return out


def verify_manifest_sizes(mirror: Path) -> dict:
    """Comprueba (barato) que cada fichero del manifiesto está y con su tamaño.

    Es la verificación que corre un builder: no relee el contenido (eso cuesta
    horas y lo hace ``make_mirror.py --verify-only``), solo existencia y tamaño.
    """
    entries = read_manifest(mirror)
    n_ok = n_missing = n_size = 0
    bad: list[str] = []
    for rel, d in entries.items():
        p = mirror / rel
        if not p.exists():
            n_missing += 1
            bad.append(f"FALTA {rel}")
        elif p.stat().st_size != int(d["size"]):
            n_size += 1
            bad.append(f"TAMAÑO {rel}")
        else:
            n_ok += 1
    return {
        "mirror": str(mirror),
        "n_files": len(entries),
        "n_ok": n_ok,
        "n_missing": n_missing,
        "n_bad_size": n_size,
        "bad": bad[:20],
    }


def manifest_merkle_root(entries: dict[str, dict]) -> str:
    """Raíz de Merkle del manifiesto (misma receta que ``make_mirror.py``)."""
    root = hashlib.sha256()
    for rel, d in sorted(entries.items()):
        root.update(f"{rel}\0{int(d['size'])}\0{d['sha256']}\n".encode("utf-8"))
    return root.hexdigest()


def sha256_file(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    """SHA-256 de un fichero (para verificaciones puntuales)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def set_readonly(path: Path) -> None:
    """Marca un fichero del espejo como solo lectura (nunca se escribe encima)."""
    try:
        os.chmod(path, 0o444)
    except OSError:  # pragma: no cover - depende del sistema de ficheros
        pass
