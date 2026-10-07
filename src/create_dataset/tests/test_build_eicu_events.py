"""
tests/test_build_eicu_events.py
===============================
Tests de los eventos de eICU reconstruidos desde **ajustes invasivos** con el
gap calibrado (Fase 1.6b, punto 2), con datos sintéticos.

Cubren: t0 = primer ajuste, fin = último ajuste del episodio, cierre por hueco
> G, regla 0, D5 (muerte y traqueostomía), exclusión por traqueostomía previa,
esquema del índice (``hospital_id``, ``inter_adj_median_min``) y el resumen por
estrato de anotación.
"""

from __future__ import annotations

import pandas as pd

from src.create_dataset.build_eicu_events import (
    build_eicu_events,
    stratum_of_annotation,
)


def _patients(rows) -> pd.DataFrame:
    return pd.DataFrame([
        {"patientunitstayid": pid, "hospitalid": hosp,
         "unitdischargeoffset": disch, "unitdischargestatus": status,
         "uniquepid": f"pid-{hosp}-{pid}"}
        for pid, hosp, disch, status in rows
    ])


class TestStrata:
    def test_bins(self):
        assert stratum_of_annotation(30) == "le1h"
        # Límites inclusivos por arriba (Fase 1.6c): 2 h exactas es 1_2h.
        assert stratum_of_annotation(60) == "le1h"
        assert stratum_of_annotation(90) == "1_2h"
        assert stratum_of_annotation(120) == "1_2h"
        assert stratum_of_annotation(120.1) == "gt2h"
        assert stratum_of_annotation(None) == "le1h"


class TestIntervals:
    def test_t0_es_el_primer_ajuste_y_fin_el_ultimo(self):
        patients = _patients([(1, 10, 2880, "Alive")])
        idx, _ = build_eicu_events(
            patients, {1: [600.0, 660.0, 720.0]}, {}, {},
            gap_h=8.0, with_coverage=False)
        ev = idx["events"][0]
        assert ev["t0_minutes"] == 600.0
        assert ev["t0_source"] == "first_invasive_adjustment"
        assert ev["attempts"][0]["vent_start_h"] == 0.0
        assert ev["attempts"][0]["vent_end_h"] == 2.0
        assert ev["duration_seconds"] == 7200

    def test_hueco_mayor_que_g_crea_reintubacion(self):
        patients = _patients([(1, 10, 5000, "Alive")])
        # 0-120 min, hueco de 20 h, 1320-1440 min.
        idx, _ = build_eicu_events(
            patients, {1: [600.0, 720.0, 720.0 + 20 * 60, 720.0 + 20 * 60 + 120]},
            {}, {}, gap_h=8.0, with_coverage=False)
        ev = idx["events"][0]
        assert ev["n_attempts"] == 2
        # La reintubacion (primer ajuste del 2.o episodio) esta a 22 h de t0.
        assert ev["attempts"][0]["reintubation_h"] == 22.0
        assert ev["attempts"][1]["reintubation_h"] is None

    def test_hueco_dentro_de_g_no_parte(self):
        patients = _patients([(1, 10, 2880, "Alive")])
        idx, _ = build_eicu_events(
            patients, {1: [0.0, 60.0, 60.0 + 7 * 60]}, {}, {},
            gap_h=8.0, with_coverage=False)
        assert idx["events"][0]["n_attempts"] == 1

    def test_ajuste_aislado_no_es_evento(self):
        patients = _patients([(1, 10, 2880, "Alive")])
        idx, _ = build_eicu_events(
            patients, {1: [600.0]}, {}, {}, gap_h=8.0, with_coverage=False)
        assert idx["total_events"] == 0

    def test_solo_hospitales_pedidos(self):
        patients = _patients([(1, 10, 2880, "Alive"), (2, 99, 2880, "Alive")])
        idx, _ = build_eicu_events(
            patients, {1: [0.0, 60.0], 2: [0.0, 60.0]}, {}, {},
            gap_h=8.0, hospitals={10}, with_coverage=False)
        assert idx["total_events"] == 1
        assert idx["events"][0]["hospital_id"] == 10


class TestRule0AndD5:
    def test_regla_0_censura_traslado_ventilado(self):
        # Ultimo ajuste 20 min antes del alta -> no hay >= 1 h sin ventilador.
        patients = _patients([(1, 10, 1000, "Alive")])
        idx, _ = build_eicu_events(
            patients, {1: [0.0, 60.0, 900.0, 980.0]}, {}, {},
            gap_h=8.0, with_coverage=False)
        ev = idx["events"][0]
        assert ev["end_reason"] == "transfer_ventilated"
        assert ev["extubation_rule"].startswith("transfer")

    def test_regla_0_extubacion_con_1h_de_tail(self):
        patients = _patients([(1, 10, 2000, "Alive")])
        idx, _ = build_eicu_events(
            patients, {1: [0.0, 60.0, 120.0]}, {}, {}, gap_h=8.0,
            with_coverage=False)
        ev = idx["events"][0]
        assert ev["end_reason"] == "extubation_observed"
        assert ev["labels"]["48h"]["event_type"] == "successful_extubation"

    def test_muerte_ventilado_censura(self):
        # El ultimo ajuste coincide con el alta (muerte estando ventilado).
        patients = _patients([(1, 10, 2000, "Expired")])
        idx, _ = build_eicu_events(
            patients, {1: [0.0, 60.0, 1900.0, 2000.0]}, {}, {},
            gap_h=8.0, with_coverage=False)
        ev = idx["events"][0]
        assert ev["labels"]["48h"]["event_type"].startswith("censored")
        assert ev["end_reason"] == "death_at_vent"

    def test_ultimo_ajuste_antes_del_alta_es_traslado(self):
        # Convencion de la regla 0: si el ultimo ajuste es anterior al alta y no
        # hay >= 1 h de cola, la estancia se censura como traslado ventilado
        # (el fin por anotacion se queda corto respecto al fin real).
        patients = _patients([(1, 10, 2000, "Expired")])
        idx, _ = build_eicu_events(
            patients, {1: [0.0, 60.0, 1900.0, 1980.0]}, {}, {},
            gap_h=8.0, with_coverage=False)
        assert idx["events"][0]["end_reason"] == "transfer_ventilated"

    def test_traqueostomia_previa_excluye(self):
        patients = _patients([(1, 10, 2880, "Alive")])
        idx, _ = build_eicu_events(
            patients, {1: [1200.0, 1260.0]}, {}, {}, gap_h=8.0,
            trach_by_pid={1: 600.0}, with_coverage=False)
        assert idx["total_events"] == 0
        assert idx["excluded_events"][0]["exclusion_reason"] == "trach_preexisting"

    def test_traqueostomia_posterior_censura(self):
        patients = _patients([(1, 10, 5000, "Alive")])
        idx, _ = build_eicu_events(
            patients, {1: [0.0, 60.0, 2400.0, 2460.0]}, {}, {}, gap_h=8.0,
            trach_by_pid={1: 1300.0}, with_coverage=False)
        ev = idx["events"][0]
        assert ev["labels"]["48h"]["censor_cause"] == "trach"


class TestSchemaAndSummary:
    def _index(self):
        patients = _patients([(1, 10, 2880, "Alive"), (2, 11, 2880, "Alive")])
        meta = {10: {"inter_adj_median_min": 90.0}, 11: {"inter_adj_median_min": 240.0}}
        return build_eicu_events(
            patients, {1: [0.0, 60.0, 120.0], 2: [0.0, 60.0, 120.0]}, {}, {},
            gap_h=8.0, hospital_meta=meta, with_coverage=False)

    def test_esquema_del_indice(self):
        idx, summary = self._index()
        ev = idx["events"][0]
        assert ev["cohort"] == "eicu"
        assert "hospital_id" in ev
        assert ev["inter_adj_median_min"] is not None
        assert idx["gap_h"] == 8.0
        assert summary["gap_h"] == 8.0

    def test_campos_para_la_fase2(self):
        """Fase 1.6c (punto 5): agrupación y origen de la etiqueta."""
        idx, _ = self._index()
        for ev in idx["events"]:
            assert ev["patientunitstayid"] > 0
            assert ev["uniquepid"] == f"pid-{ev['hospital_id']}-{ev['patientunitstayid']}"
            assert ev["hospital_id"] > 0
            assert ev["label_source"] == "anotaciones"
            assert ev["annotation_stratum"] in ("le1h", "1_2h", "gt2h")
            for window in ("48h", "72h"):
                lab = ev["labels"][window]
                assert "censor_cause" in lab and "censor_time_h" in lab

    def test_resumen_por_estrato_de_anotacion(self):
        _, summary = self._index()
        assert set(summary["by_annotation_stratum"]) == {"1_2h", "gt2h"}
        assert summary["by_annotation_stratum"]["1_2h"]["events"] == 1
        assert summary["by_annotation_stratum"]["gt2h"]["events"] == 1
        assert summary["success_48h"] == 2
        assert summary["censored_48h"] == 0

    def test_resumen_cuenta_fallos(self):
        patients = _patients([(1, 10, 8640, "Alive")])
        # Episodio, reintubacion a las 24 h -> fallo a 48 h.
        idx, summary = build_eicu_events(
            patients, {1: [0.0, 60.0, 1500.0, 1560.0]}, {}, {},
            gap_h=8.0, with_coverage=False)
        assert idx["events"][0]["labels"]["48h"]["n_failed_attempts"] == 1
        assert summary["failure_events_48h"] == 1


class TestCorreccionDelFin:
    """Fase 1.6c (punto 3): corrección del fin y las dos versiones de etiqueta.

    El fin reconstruido desde los ajustes se queda corto, así que se alarga
    sumando la mediana (positiva) medida en MIMIC para el estrato del hospital.
    La etiqueta vigente es la corregida y la original queda en
    ``labels_sin_correccion``.
    """

    @staticmethod
    def _build(shift_by_stratum):
        patients = _patients([(1, 10, 2880, "Alive")])
        meta = {10: {"inter_adj_median_min": 90.0}}      # estrato 1_2h
        return build_eicu_events(
            patients, {1: [0.0, 60.0, 1500.0, 1560.0]}, {}, {},
            gap_h=8.0, hospital_meta=meta, with_coverage=False,
            end_shift_by_stratum=shift_by_stratum)

    def test_sin_correccion_no_guarda_segunda_version(self):
        idx, _ = self._build(None)
        ev = idx["events"][0]
        assert ev["end_correccion_h"] == 0.0
        assert ev["labels_sin_correccion"] is None
        assert idx["end_correccion"]["aplicada"] is False

    def test_alarga_el_fin_y_guarda_las_dos_versiones(self):
        base, _ = self._build(None)
        idx, summary = self._build({"1_2h": 1.5})
        ev = idx["events"][0]
        assert ev["end_correccion_h"] == 1.5
        assert ev["annotation_stratum"] == "1_2h"
        # El fin del último intento se alarga exactamente 1.5 h.
        assert (ev["attempts"][-1]["vent_end_h"]
                == base["events"][0]["attempts"][-1]["vent_end_h"] + 1.5)
        # Las dos versiones conviven: la corregida en ``labels`` y la original
        # en ``labels_sin_correccion``.
        assert ev["labels_sin_correccion"]["48h"] == base["events"][0]["labels"]["48h"]
        assert idx["end_correccion"]["shifts_por_estrato_h"] == {"1_2h": 1.5}
        assert "labels_cambiados_por_correccion" in summary

    def test_correccion_de_otro_estrato_no_afecta(self):
        idx, _ = self._build({"le1h": 1.2})
        ev = idx["events"][0]
        assert ev["end_correccion_h"] == 0.0
        assert ev["labels_sin_correccion"] is None

    def test_la_correccion_puede_cambiar_la_etiqueta(self):
        # Alta a las 10 h con el último ajuste a 8.6 h: alargar el fin 1.5 h lo
        # lleva más allá del alta (10.1 h), o sea que el alta pasa a ocurrir
        # estando ventilado y la etiqueta cambia de éxito a censura.
        patients = _patients([(1, 10, 600, "Alive")])
        meta = {10: {"inter_adj_median_min": 90.0}}
        adj = {1: [0.0, 60.0, 480.0, 516.0]}   # último ajuste a 8.6 h
        base, _ = build_eicu_events(patients, adj, {}, {}, gap_h=8.0,
                                    hospital_meta=meta, with_coverage=False)
        corr, _ = build_eicu_events(patients, adj, {}, {}, gap_h=8.0,
                                    hospital_meta=meta, with_coverage=False,
                                    end_shift_by_stratum={"1_2h": 1.5})
        ev_b = base["events"][0]
        ev_c = corr["events"][0]
        assert ev_b["labels"]["48h"]["event_type"] == "successful_extubation"
        # El fin corregido (10.1 h) se recorta al alta (10 h): el alta ocurre
        # estando ventilado y la etiqueta pasa a censura.
        assert ev_c["attempts"][-1]["vent_end_h"] == 10.0
        assert ev_c["labels"]["48h"]["event_type"] == "censored_transfer_ventilated"
        assert ev_c["labels_sin_correccion"]["48h"] == ev_b["labels"]["48h"]

    def test_el_fin_corregido_no_pasa_del_alta(self):
        patients = _patients([(1, 10, 2880, "Alive")])
        meta = {10: {"inter_adj_median_min": 90.0}}
        # Último ajuste a 47 h y alta a 48 h: una corrección de 4 h se recorta.
        adj = {1: [0.0, 60.0, 2400.0, 2820.0]}
        idx, _ = build_eicu_events(patients, adj, {}, {}, gap_h=8.0,
                                   hospital_meta=meta, with_coverage=False,
                                   end_shift_by_stratum={"1_2h": 4.0})
        assert idx["events"][0]["attempts"][-1]["vent_end_h"] == 48.0

    def test_el_fin_corregido_no_solapa_el_intento_siguiente(self):
        patients = _patients([(1, 10, 2880, "Alive")])
        meta = {10: {"inter_adj_median_min": 90.0}}
        # Dos intentos con hueco > G: el primero acaba a 1 h y el segundo
        # empieza a 4 h.
        adj = {1: [0.0, 30.0, 60.0, 240.0, 270.0]}
        idx, _ = build_eicu_events(patients, adj, {}, {}, gap_h=1.0,
                                   hospital_meta=meta, with_coverage=False,
                                   end_shift_by_stratum={"1_2h": 5.0})
        att = idx["events"][0]["attempts"]
        assert len(att) == 2
        assert att[0]["vent_end_h"] == att[1]["vent_start_h"] == 4.0
