"""
io/versioning.py
================
Versionado determinista del dataset: hash de la config + timestamp.

El dataset_version es un string corto que identifica de forma única
la combinación de config usada para generar los datos.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def compute_config_hash(config: dict, length: int = 8) -> str:
    """
    Calcula un hash corto (SHA256 truncado) del diccionario de config.
    El config se serializa a JSON con claves ordenadas para determinismo.
    """
    config_str = json.dumps(config, sort_keys=True, ensure_ascii=True, default=str)
    full_hash = hashlib.sha256(config_str.encode()).hexdigest()
    return full_hash[:length]


def make_dataset_version(config: dict) -> str:
    """
    Genera el identificador de versión del dataset:
    formato: v{version_yaml}_{hash_config}
    Ejemplo: v0.1.0_a3f2b91c
    """
    yaml_version = config.get("version", "0.0.0")
    cfg_hash = compute_config_hash(config)
    return f"v{yaml_version}_{cfg_hash}"


def save_version_manifest(
    output_dir: Path,
    dataset_version: str,
    config: dict,
    cohort_counts: dict[str, int],
) -> Path:
    """
    Guarda un manifest JSON en output_dir con:
    - dataset_version
    - timestamp de generación
    - hash de config
    - número de pacientes por cohorte

    Returns:
        Ruta al archivo manifest.json
    """
    manifest = {
        "dataset_version": dataset_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config_hash": compute_config_hash(config),
        "config_version": config.get("version", "unknown"),
        "cohort_counts": cohort_counts,
    }
    path = output_dir / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    logger.info("[versioning] Manifest guardado en %s (version=%s)", path, dataset_version)
    return path


def load_config(config_path: str | Path) -> dict:
    """Carga el YAML de config y devuelve el diccionario."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config no encontrada: {path}")
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    logger.info("[versioning] Config cargada desde %s", path)
    return config
