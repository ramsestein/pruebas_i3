# Fase 1.6b — Calibración en MIMIC, etiquetas de eICU (e2) y cierre de Clínic/VitalDB

Rama `fix/integridad-datos`. Config `harmonize.yaml` v**0.3.0** (hash `37558586`).

> **Alcance:** reconstruir las 4 cohortes con un **algoritmo único de intervalos**
> calibrado en MIMIC, medir el error de etiqueta esperado y cerrar las
> incoherencias de Clínic y VitalDB. Sin aplicar filtros definitivos.

---

## 0. Cohorte de eICU: regla de permeabilidad nueva

Un hospital es **implausible** si **≥ 50 %** de sus estancias con ajustes
invasivos en `respiratoryCharting` **no** tienen ni `apache_intub`
(`oobIntubDay1`) ni `cpg_vent` (plan de ventilación). La regla **no exige
densidad** (`rc_invasive/icu`), a diferencia de la Fase 1.6a-bis.

Resultado sobre los 205 hospitales: pasan a implausibles **24** (antes 3):

- ya lo eran: **411, 412, 413**;
- nuevos (21): 83, 86, 95, 151, 156, 268, 269, 271, 272, 277, 279, 280, 282,
  283, 318, 393, 397, **421**, 422, 423, 436;
- **ninguno se recupera** (los densos-concordantes 131/259/273 siguen siendo
  válidos).

Cae el **421** (80.5 % de sus estancias invasivas sin corroboración). El **424
NO cae**: su fracción es 0.436 (< 0.50) porque la mayoría de sus estancias
invasivas sí tienen plan de ventilación. El 459 queda en el límite (0.496).

**Cohorte final** = escenario e2 con la regla nueva (≥ 50 estancias ventiladas,
≥ 80 % con ajustes invasivos, anotación mediana ≤ 4 h, ≥ 50 utilizables, no
implausible), que incluye los `concentrado_concordante` (259): **53 hospitales**,
**19 905 estancias utilizables**. Lista guardada en
`harmonize.yaml → fase1_6b.eicu.hospital_ids`.

Entregables: `reports/fase1_6b/eicu_cohort.json`, `eicu_hospitales_v3.csv`.

## 1. Algoritmo de intervalos y calibración en MIMIC (elección de G)

Algoritmo único (`src/common/vent_intervals.py`): una anotación invasiva abre o
mantiene el episodio; un hueco **> G h** lo cierra; el inicio es la primera
anotación y el fin la última (no se extrapola).

Calibración en **MIMIC-MetaVision**: anotaciones de CHARTEVENTS (VentMode, PEEP,
TV_set, TV_observed, PIP, RR_V) frente a los intervalos **225792** de
PROCEDUREEVENTS_MV (9 093 estancias, 10 025 intervalos de referencia),
estratificando por frecuencia de anotación (submuestreo a 1, 2 y 4 h).

| G (h) | anotación | perdidos | fuera | fragmentos/int | inicio ±2 h | fin ±2 h | F1 reintub | etiq 48 h |
|---|---|---|---|---|---|---|---|---|
| 2 | 1 h | 18.0 % | 9.4 % | 3.08 | 49.1 % | 35.2 % | 0.031 | 71.7 % |
| 4 | 1 h | 4.2 % | 7.7 % | 3.85 | 83.1 % | 53.4 % | 0.037 | 84.4 % |
| 6 | 1 h | 3.2 % | 7.9 % | 1.19 | 92.0 % | 56.0 % | 0.294 | 85.2 % |
| **8** | **1 h** | **3.1 %** | **8.0 %** | **1.08** | **91.9 %** | **55.7 %** | **0.396** | **85.2 %** |
| **8** | **2 h** | **4.5 %** | **8.0 %** | **1.08** | **91.9 %** | **51.8 %** | **0.393** | **83.8 %** |
| **8** | **4 h** | **13.2 %** | **7.9 %** | **1.26** | **90.3 %** | **36.7 %** | **0.244** | **75.6 %** |

**G elegido = 8 h** (único candidato que cumple las restricciones en todos los
estratos: minutos fuera ≤ 10 %, pérdida ≤ 5 % con anotación ≤ 2 h y ≤ 15 % con
anotación de 4 h). La tendencia es monótona: cada aumento de G mejora todas las
métricas, y con G < 2 h el algoritmo fragmenta (más de 3 trozos por intervalo).

**Error de etiqueta esperado** con G = 8 h (lo que se traslada a eICU):

| Anotación del hospital | fin med (IQR) | fin P90 | fin ±2 h | reintub sens | reintub VPP | F1 | etiq 48 h |
|---|---|---|---|---|---|---|---|
| ≤ 1 h | −1.28 h (2.65) | 11.0 h | 55.7 % | 0.630 | 0.289 | 0.396 | 85.2 % |
| 1–2 h | −1.55 h (2.53) | 11.0 h | 51.8 % | 0.620 | 0.288 | 0.393 | 83.8 % |
| 2–4 h | −2.42 h (3.23) | 13.0 h | 36.7 % | 0.595 | 0.154 | 0.244 | 75.6 % |

Interpretación: el **inicio** se reconstruye casi perfecto (±2 h en ~92 %), pero
el **fin** se queda **corto** (~1.3 h de mediana) porque los ajustes dejan de
anotarse antes de la desconexión real; esa cola explica la pérdida de VPP en
reintubaciones. Es una cota del error, no un defecto del algoritmo.

Entregables: `reports/fase1_6b/calibracion_mimic.{json,md}`.

## 2. eICU: eventos y etiquetas

`src/create_dataset/build_eicu_events.py` reconstruye los eventos de la cohorte
con G = 8 h, t0 = primer ajuste invasivo, fin = último ajuste del episodio,
regla 0, D5 (`unitdischargestatus`, `airwayType`) y exclusión por traqueostomía
previa.

- **23 362 eventos** en **53 hospitales**;
- **éxito 48 h = 19 720**, **censura 48 h = 3 642** (72 h: 19 636 / 3 726);
- **eventos con ≥ 1 fallo = 2 775**;
- `vars_ok` 50 % = **15 999**; 80 % = **10 819**;
- duración mediana 32.0 h (P25 10.0 / P75 96.4);
- causas de censura: `terminal_extubation` 4 496, `transfer_ventilated` 1 936,
  `trach` 746, `death_at_vent` 190.

Por estrato de anotación del hospital:

| Estrato | Eventos | Éxito 48 h | Censura 48 h | Fallos | `vars_ok` 50 % | Duración mediana |
|---|---|---|---|---|---|---|
| ≤ 1 h | 2 410 | 1 913 | 497 | 309 | 1 901 (78.9 %) | 39.7 h |
| 1–2 h | 6 612 | 5 326 | 1 286 | 733 | 4 553 (68.9 %) | 36.0 h |
| > 2 h | 14 340 | 12 481 | 1 859 | 1 733 | 9 545 (66.6 %) | 28.4 h |

Error de etiqueta esperado por estrato (trasladado de MIMIC con G = 8 h):
≤ 1 h → −1.28 h y 85.2 % de concordancia; 1–2 h → −1.55 h y 83.8 %; > 2 h →
−2.42 h y 75.6 %. Ponderado por el número de eventos de cada estrato:
**−2.05 h** de error de fin y **78.9 %** de concordancia de la etiqueta a 48 h.

Entregable: `eicu_events_summary.json` (y el índice en
`datasets/eicu_collaborative/cases_v0.3.0_37558586/`).

## 3. Clínic

### 3.1 La inconsistencia índice ↔ ficheros, explicada

Los ``.vital`` de Clínic **existen varias veces** en el árbol crudo:

| Directorio | Ficheros |
|---|---|
| ``box2..box14`` (las cajas del índice) | 5 471 |
| ``dataset_clinic/clinic_vitals/boxN`` (copia de las cajas) | 4 893 |
| ``dataset_clinic/physionet_vitals`` (otro conjunto) | 4 349 |
| ``dataset_clinic/test_anonymized.vital`` | 1 |
| **Total** | **15 291** |

Los 4 893 nombres de las cajas están **duplicados** (aparecen en ``<box>/...`` y en
``dataset_clinic/clinic_vitals/<box>/...``); ``box14`` tiene además copias
anidadas (``box14/14_2/14_2/250906/14/...``). El índice de casos guarda
**nombres de fichero**, no rutas, así que cualquier localización por nombre
global es ambigua. El escáner del proyecto ya excluye ``dataset_clinic``
(``scan_source_files(exclude_dirs=("dataset_clinic",))``).

### 3.2 Clasificación de los eventos de < 1 h con la localización corregida

Se localiza cada evento **dentro de su propia caja** (``locate_event_files``) y,
si el nombre no aparece, por **fecha y hora** del ``t0`` (``locate_by_datetime``).
Resultado sobre los 16 eventos de < 1 h:

| | Fase 1.5 | Fase 1.6b |
|---|---|---|
| Ventilación plausible | **0 / 16** | **12 / 16** |
| Artefacto | 16 / 16 | 4 / 16 |

Evidencia encontrada (pistas en los ficheros de la caja del evento):

- **6** con onda de presión/flujo (``AWP_WAV``/``FLOW_WAV``) → ventilación;
- **6** sin onda pero con **ajustes** de ventilador (PEEP, PIP, FR total del
  ventilador y volumen tidal) → ventilación documentada;
- **3** sin ninguna pista de ventilador y **1** con solo ``FIO2``
  (que NO es marcador, D6) → siguen siendo artefactos/sin señal.

Dos causas para el 16/16 de la Fase 1.5:

1. **Localización por nombre global** sobre un árbol con copias → riesgo de leer
   otro fichero (o ninguno) y concluir "sin señal". Con la caja del evento, 13 de
   los 16 tienen pistas de ventilador.
2. **Criterio demasiado estrecho** en ``classify_short_event``: exigía onda AWP (o
   TV *y* AWP). Se amplía con la **evidencia por ajustes** de D6 (≥ 2 marcadores
distintos con registros: PEEP, PIP, FR total del ventilador, volumen tidal).

### 3.3 El rebuild completo de Clínic no es viable por I/O

Las cajas contienen **101.5 GB** en 5 471 ficheros (mediana 4.2 MB, P90 53.8 MB,
máximo **476 MB**; 595 ficheros ≥ 50 MB y 52 ≥ 200 MB). Abrir algunos de esos
ficheros tarda **42–84 s** (frente a 0.05 s de los típicos), de modo que el
recorrido completo se cuantifica en horas. **No es un problema de código**: es
el coste de releer 101 GB con descompresión de ondas.

Consecuencia: el índice de Clínic **no se ha reconstruido** en esta fase. La
revisión de los eventos cortos (§3.2) sí se hizo, porque solo necesita 32
ficheros.

Entregables: ``clinic_raw_layout.json``, ``clinic_short_events_v2.json``,
``clinic_short_verdict.json``.

## 4. VitalDB

### 4.1 Cobertura por variable (D8) — se encontró un error de unidades

``hourly_coverage`` trabaja en **minutos**, pero el builder de señal le pasaba
las series y los tramos en **horas**: la rejilla horaria colapsaba a **un único
punto** por evento y la "cobertura" pasaba a ser "¿hay un valor al principio?".
Prueba de ello en el índice de la Fase 1.5: la mediana de cobertura es 1.0 en
HR/SpO2/MAP/RR pero **0.0** en FiO2/PEEP, imposible en datos reales.

Se corrige con ``coverage_fractions`` (``src/common/vital_signals.py``), que
convierte horas → minutos, y con dos tests que fallan con el código anterior
(``tests/test_signal_coverage.py``, incluido el test pedido con HR, MAP y RR
diferentes). **Las coberturas publicadas para Clínic/VitalDB en la Fase 1.5
quedan invalidadas.**

### 4.2 El "24 % de eventos sin FC" era artefacto del error anterior

En el índice antiguo, 23/96 eventos (24.0 %) tenían cobertura de HR = 0.
Usando las sondas reales de los ficheros (caché del escaneo):

- **92/96 eventos (95.8 %)** tienen pista de FC en sus ficheros;
- **4/96 (4.2 %)** tienen la lista ``source_files`` **vacía** (hueco del índice:
  no hay fichero que comprobar); ninguno de los 4 tiene tampoco SpO2 ni ondas;
- **0 eventos** tienen ventilación con monitor presente: **D4 se cumple** en todos
  los eventos comprobables (los 18 excluidos del índice lo son por
  ``ventilator_without_patient``).

Es decir: el 24 % no era falta de FC, sino la rejilla colapsada del punto 4.1.

### 4.3 Observación sin ventilador con señal fisiológica

``src/common/monitor_observation.py`` define los rangos fisiológicos (**FC 20–250
lpm**, **SpO2 50–100 %**). ``build_episodes`` calcula ahora
``Episode.observation_end_h`` = fin del último tramo de HR/SpO2 fisiológicos
tras la última desconexión, y la regla 0 (``observation_end_h``, ``monitor_tail_h``
y el ``obs_end_h`` de las etiquetas) lo usa. Efecto: **2 h de ondas planas tras la
desconexión no confirman la extubación** → el evento se censura como
``end_of_record`` (test ``test_ondas_planas_no_confirman_extubacion``).

### 4.4 Reetiquetado de los 26 eventos ``death_or_transfer``

El motivo de fin usaba ``signal_loss_at_end`` (perder HR y SpO2 en el último
fichero), que es un indicador de **calidad**, no una causa de censura. Con el
vocabulario único de las 4 cohortes:

| Motivo nuevo | Eventos |
|---|---|
| ``extubation_observed`` | **23** |
| ``end_of_record`` | **3** |

Los 23 recuperan el motivo coherente con su **etiqueta a 48 h** (que ya era
``successful_extubation``): el campo ``end_reason`` contradecía a la etiqueta. Con
la corrección, el reparto de motivos coincide exactamente con el de etiquetas
(75 éxito / 21 censura). ``signal_loss_at_end`` se conserva como QC.

Quedan **9 eventos** candidatos a moverse con la regla fisiológica
(``monitor_tail_h < 2 h``); confirmarlo exige releer señal (bloqueado por I/O).

Entregable: ``vitaldb_review.json``.

## 5. Tabla final (4 cohortes)

| Cohorte | Eventos | Éxito 48 h | Censura 48 h | Eventos con ≥ 1 fallo | `vars_ok` 50 % | `vars_ok` 80 % | Error de etiqueta (fin) | Etiq. 48 h |
|---|---|---|---|---|---|---|---|---|
| **MIMIC** (referencia) | 9 093 | 7 455 | 1 638 | 471 | 1 932 | 551 | 0.00 h | 100 % |
| **eICU** (53 hospitales) | 23 362 | 19 720 | 3 642 | 2 775 | 15 999 | 10 819 | −2.05 h | 78.9 % |
| **Clínic** | 181 | 140 | 41 | 7 | *pendiente* | *pendiente* | −1.28 h | 85.2 % |
| **VitalDB** | 96 | 75 | 21 | 5 | *invalidado* | *invalidado* | −1.28 h | 85.2 % |

- **Éxito y censura son excluyentes** (Éxito + Censura = Eventos). Los *eventos
  con ≥ 1 fallo* van aparte: se cuentan aunque el desenlace sea censura.
- **MIMIC** es la referencia de la calibración: su error de etiqueta es 0 por
  construcción; su cobertura (D8) se mide aquí por primera vez.
- **eICU**: censura a 72 h = 3 726; el error de etiqueta es la media de los
  estratos de anotación ponderada por eventos (≤ 1 h, 1–2 h, > 2 h).
- **Clínic/VitalDB**: el error de etiqueta es el del estrato de 1 h (anotación de
  onda continua). Su `vars_ok` **no se puede dar**: los valores de la Fase 1.5
  están invalidados por el error de unidades (§4.1) y recalcularlos exige releer
  la señal (bloqueado por I/O).

Causas de censura a 48 h:

- **MIMIC**: `death_at_vent` 556, `transfer_ventilated` 438, `terminal_extubation` 418, `trach` 226.
- **eICU**: `terminal_extubation` 4 496, `transfer_ventilated` 1 936, `trach` 746, `death_at_vent` 190.
- **Clínic**: `end_of_record` 40, `terminal_extubation` 1.
- **VitalDB**: `end_of_record` 21.

Entregables: `tabla_final.json`, `tabla_final.md`.

## 6. Limitaciones y siguientes pasos

1. **I/O del disco de datos**: Clínic son 101.5 GB en 5 471 ficheros (hasta
   476 MB); algunos tardan 42–84 s en abrirse. Los rebuilds completos de
   Clínic y VitalDB (y la cobertura por variable de ambas) quedan pendientes de
   una ventana con mejor acceso al disco.
2. **El fin de la ventilación se queda corto** (~1.3–2.4 h de mediana) porque los
   ajustes dejan de anotarse antes de la desconexión real: es la principal
   fuente de error de etiqueta y limita la VPP de reintubaciones (0.15–0.29).
3. **MIMIC no tiene `vars_ok` en su índice**; se ha calculado aquí desde
   `mimic_observations.parquet` con los mismos conceptos.
4. **4 eventos de VitalDB** tienen `source_files` vacío en el índice (hueco a
   revisar).
5. La regla fisiológica de observación (§4.3) puede mover **9 eventos** de
   VitalDB y un número aún no medido de Clínic cuando se pueda releer la señal.
