"""Tests de ``src/common/clinic_files.py`` (Fase 1.6b, punto 3)."""

from __future__ import annotations

from pathlib import Path

from src.common.clinic_files import (
    duplicate_names,
    index_by_name,
    locate_by_datetime,
    locate_event_files,
)


def _paths():
    return [
        Path("D:/data/clinic_vitals/box2/250414/tvpir4b4i_250414_121109.vital"),
        Path("D:/data/clinic_vitals/box5/sub/250414/tvpir4b4i_250414_121109.vital"),
        Path("D:/data/clinic_vitals/box2/250415/otro_250415_090000.vital"),
    ]


class TestIndex:
    def test_indexa_por_nombre(self):
        by = index_by_name(_paths())
        assert len(by) == 2
        assert len(by["tvpir4b4i_250414_121109.vital"]) == 2

    def test_duplicados(self):
        dup = duplicate_names(index_by_name(_paths()))
        assert list(dup) == ["tvpir4b4i_250414_121109.vital"]


class TestLocate:
    def test_prefiere_la_caja_del_evento(self):
        by = index_by_name(_paths())
        found, missing = locate_event_files(
            ["tvpir4b4i_250414_121109.vital"], "box5", by)
        assert missing == []
        assert "box5" in found[0].parts

    def test_sin_caja_usa_la_primera(self):
        by = index_by_name(_paths())
        found, _ = locate_event_files(
            ["tvpir4b4i_250414_121109.vital"], None, by)
        assert "box2" in found[0].parts

    def test_nombre_no_encontrado(self):
        by = index_by_name(_paths())
        found, missing = locate_event_files(["no_existe.vital"], "box2", by)
        assert found == []
        assert missing == ["no_existe.vital"]

    def test_varios_ficheros(self):
        by = index_by_name(_paths())
        found, missing = locate_event_files(
            ["otro_250415_090000.vital", "tvpir4b4i_250414_121109.vital"],
            "box5", by)
        assert len(found) == 2 and missing == []
        assert found[1].parts[-3] == "sub"


class TestLocateByDatetime:
    """Respaldo cuando el nombre del índice ya no existe en los datos crudos."""

    def _t(self, s: str) -> float:
        from datetime import datetime

        from src.common.timeutils import to_epoch_utc

        return to_epoch_utc(datetime.strptime(s, "%y%m%d%H%M%S"))

    def test_encuentra_por_fecha_y_hora(self):
        t0 = self._t("250414121109")
        found = locate_by_datetime(_paths(), t0)
        assert len(found) == 2          # el mismo nombre en dos cajas
        assert all("121109" in p.name for p in found)

    def test_filtra_por_caja(self):
        t0 = self._t("250414121109")
        found = locate_by_datetime(_paths(), t0, box="box5")
        assert len(found) == 1 and "box5" in found[0].parts

    def test_tolerancia(self):
        # 20 min despues del fichero de las 12:11: entra con 1 h, no con 60 s.
        t0 = self._t("250414123109")
        assert locate_by_datetime(_paths(), t0) != []
        assert locate_by_datetime(_paths(), t0, tolerance_s=60) == []

    def test_sin_t0(self):
        assert locate_by_datetime(_paths(), 0.0) == []
