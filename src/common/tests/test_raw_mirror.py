"""
tests/test_raw_mirror.py
========================
Fase 1.6f, punto 0 (D-m): resolución del espejo de solo lectura y verificación
del manifiesto SHA-256.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.common.raw_mirror import (
    manifest_merkle_root,
    read_manifest,
    resolve_raw_dir,
    verify_manifest_sizes,
)


def _config(root: Path, *, use: bool = True) -> dict:
    return {
        "paths": {
            "clinic_raw_dir": "D:/data/clinic_vitals",
            "vitaldb_raw_dir": "D:/data/vitaldb_sicu",
        },
        "fase1_6f": {"mirror": {"root": str(root), "use": use}},
    }


def _write_manifest(mirror: Path, entries: list[tuple[str, bytes]]) -> None:
    lines = []
    for rel, payload in entries:
        path = mirror / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        import hashlib
        lines.append(json.dumps({
            "rel": rel, "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(), "mtime": 0.0,
        }))
    (mirror / "_manifest.jsonl").write_text("\n".join(lines) + "\n",
                                            encoding="utf-8")


class TestResolveRawDir:
    def test_sin_espejo_cae_al_crudo(self, tmp_path: Path):
        cfg = _config(tmp_path / "no_existe")
        raw, is_mirror = resolve_raw_dir(cfg, "clinic")
        assert not is_mirror
        assert str(raw).replace("\\", "/").endswith("clinic_vitals")

    def test_con_espejo_completo_usa_el_espejo(self, tmp_path: Path):
        mirror = tmp_path / "mirror" / "clinic_vitals"
        mirror.mkdir(parents=True)
        (mirror / "_mirror_summary.json").write_text("{}", encoding="utf-8")
        raw, is_mirror = resolve_raw_dir(_config(tmp_path / "mirror"), "clinic")
        assert is_mirror
        assert raw == mirror

    def test_espejo_a_medias_no_se_usa(self, tmp_path: Path):
        """Sin la marca de copia completa se lee el crudo (nunca medio espejo)."""
        mirror = tmp_path / "mirror" / "clinic_vitals"
        mirror.mkdir(parents=True)
        (mirror / "_manifest.jsonl").write_text("", encoding="utf-8")
        raw, is_mirror = resolve_raw_dir(_config(tmp_path / "mirror"), "clinic")
        assert not is_mirror

    def test_use_false_ignora_el_espejo(self, tmp_path: Path):
        mirror = tmp_path / "mirror" / "clinic_vitals"
        mirror.mkdir(parents=True)
        (mirror / "_mirror_summary.json").write_text("{}", encoding="utf-8")
        raw, is_mirror = resolve_raw_dir(
            _config(tmp_path / "mirror", use=False), "clinic")
        assert not is_mirror

    def test_sin_config_de_espejo_no_falla(self):
        cfg = {"paths": {"clinic_raw_dir": "D:/data/clinic_vitals"}}
        raw, is_mirror = resolve_raw_dir(cfg, "clinic")
        assert not is_mirror


class TestManifiesto:
    def test_verify_ok(self, tmp_path: Path):
        mirror = tmp_path / "clinic_vitals"
        mirror.mkdir()
        _write_manifest(mirror, [("box2/a.vital", b"1234"),
                                 ("box2/b.vital", b"567890")])
        info = verify_manifest_sizes(mirror)
        assert info["n_ok"] == 2
        assert info["n_missing"] == 0 and info["n_bad_size"] == 0
        assert len(read_manifest(mirror)) == 2
        assert len(manifest_merkle_root(read_manifest(mirror))) == 64

    def test_detecta_fichero_que_falta(self, tmp_path: Path):
        mirror = tmp_path / "clinic_vitals"
        mirror.mkdir()
        _write_manifest(mirror, [("box2/a.vital", b"1234")])
        (mirror / "box2" / "a.vital").unlink()
        info = verify_manifest_sizes(mirror)
        assert info["n_missing"] == 1
        assert info["n_ok"] == 0

    def test_detecta_tamano_distinto(self, tmp_path: Path):
        mirror = tmp_path / "clinic_vitals"
        mirror.mkdir()
        _write_manifest(mirror, [("box2/a.vital", b"1234")])
        (mirror / "box2" / "a.vital").write_bytes(b"12345")
        info = verify_manifest_sizes(mirror)
        assert info["n_bad_size"] == 1
