# Fase 1.5 — Recuento de eventos completos (eICU) y reconstrucción de VitalDB

> **Estado:** puntos 0–3 implementados, cada uno con su test (que falla antes y
> pasa después) en su propio commit (ver §0). Los builds de MIMIC y eICU se han
> re-ejecutado sobre los datos crudos; el de VitalDB está en curso (ver §2).
> Ninguna cifra procede de salidas antiguas.

---

## 0. Regla común de extubación confirmada (las 4 cohortes) — commit `28e7e07`

`src/common/extubation.py` implementa la regla en una función **pura**
(`resolve_extubation`):

- Un fin de ventilación es **extubación** solo si va seguido de **≥ 1 h de
  observación sin ventilador** (monitor en Clínic/VitalDB; estancia en
  MIMIC/eICU).
- Si no, el evento se **censura con su causa**: `transfer_ventilated`
  (alta/traslado ventilado), `death_at_vent` (muerte estando ventilado) o
  `end_of_record` (fin de datos).

Cambios de comportamiento:

- **MIMIC**: se elimina la tolerancia de 30 s frente a `OUTTIME`. Los 237
  eventos que terminaban en `end_of_icu_stay` y **todo** fin de ventilación a
  < 1 h del alta pasan a censurarse como `transfer_ventilated`.
- **Clínic/VitalDB**: la regla ya existía (`≥ 1 h` de monitor); ahora se
  comparte el mismo código (`extubation_decision`). Al no existir frontera de
  estancia, la censura es siempre `end_of_record` (o `death_at_vent` si la
  muerte por señales la confirma).
- **eICU**: se aplica en el builder de casos (§1).

Test exigido: fin de ventilación 20 min antes del alta → `transfer_ventilated`
(`src/create_dataset/tests/test_mimic_cases.py::TestCommonExtubationRule`).

### Efecto en MIMIC (v0.2.0, 9 093 eventos; 25 excluidos)

| `end_reason` | Fase 1 | Fase 1.5 |
|---|---|---|
| `extubation_observed` | 7 249 | **7 027** |
| `transfer_ventilated` | — | **459** |
| `death` | 1 297 | 1 297 |
| `tracheostomy` | 310 | 310 |

Etiquetas a 48 h en MIMIC: éxito **7 455** (antes 7 905), censura **1 638**
(`death_at_vent` 556, `trach` 226, `terminal_extubation` 418,
**`transfer_ventilated` 438**), eventos con ≥ 1 fallo **471** (5,18 %).

---

## 1. eICU — clasificación por completitud — commit `ee6931b`

`src/create_dataset/build_eicu_index.py` construye el índice de casos con el
**mismo esquema** que las otras cohortes (más `hospital_id`) y clasifica cada
estancia en un nivel A/B/C/D (`src/common/eicu_levels.py`).

**Hallazgo central:** en `respiratoryCare`, `ventendoffset` **no está
documentado** en la práctica (solo **1 estancia** de 44 772 tiene `> 0`; el
pipeline lo *imputa* con el último `respcarestatusoffset`). Por tanto, con la
definición literal (fin documentado = `ventendoffset > 0`) **no hay estancias A
ni B**: la clasificación se reparte entre C (fin imputado pero **rescatable**
por el último ajuste invasivo a ≥ 1 h del alta) y D (no rescatable).

- Ventana de coherencia con `respiratoryCharting`: último ajuste invasivo
  (modo, PEEP, volumen tidal pautado, PIP, FR total del ventilador) a ≤ 4 h del
  fin. Se excluyen **FiO2** y **VNI/alto flujo** (CPAP/EPAP/IPAP/BiPAP/NIV).

| Nivel | Nº estancias | Definición |
|---|---|---|
| A — completa | **0** | fin documentado + extubación/censura válida + ajuste invasivo coherente (≤ 4 h) |
| B — documentada sin verificar | **0** | como A pero sin ajustes invasivos |
| C — rescatable | **36 285** | fin imputado/incoherente, último ajuste invasivo ≥ 1 h del alta → se propone ese momento |
| D — no fiable | **7 702** | fin imputado sin forma de recuperarlo |

- **Total**: 43 987 eventos (55 excluidos por traqueostomía previa a t0).
- **Hospitales con ≥ 50 estancias de nivel A**: **ninguno** (157 hospitales; A = 0).
- **Cobertura de variables** (`vars_ok` al 50 % y 80 % en A / A+B): **N/A**
  (A+B = 0). *[Pendiente: se puede reportar la cobertura de las 36 285 C.]*

Éxito / fallo / censura a 48 h por nivel (para A y A+B no aplica):

| Conjunto | Eventos | Éxito | Censura | Con ≥ 1 fallo |
|---|---|---|---|---|
| A | 0 | 0 | 0 | 0 |
| A+B | 0 | 0 | 0 | 0 |
| A+B+C | 36 285 | 29 723 | 6 562 | 215 (0,59 %) |

Causas de censura en A+B+C: `terminal_extubation` 2 736,
`transfer_ventilated` 2 325, `death_at_vent` 721, `trach` 780.

> **Reconstrucción propuesta (nivel C):** la extubación es el último ajuste
> invasivo anotado; se reporta por evento en `proposed_extubation_h` y
> `proposed_shift_h` (diferencia con el fin original imputado).

---

## 2. VitalDB — reconstrucción — commit `ee6931b` (mismo commit del punto 2)

_(Sección en curso: escaneo de los 15 327 ficheros de origen y rebuild en
`cases_v0.2.0_*`. Se completará con las cifras definitivas.)_

1. Recorrido de **todos** los ficheros de origen: nº ilegibles y error.
2. Reintento de los ilegibles con `vitaldb >= 1.7`; el pin pasa de 1.6.0 a
   **1.7.2** (los ficheros que sigan fallando se listan como corruptos).
3. Un fichero ilegible es **"sin dato"**, no "sin señal": sus horas **no**
   crean huecos de ventilador (D1) ni cortes de paciente (D2). Los eventos que
   los atraviesan se marcan con `has_missing_files` y `missing_hours`.
   Test: dos ficheros ilegibles en mitad de una ventilación → **1 intento**
   (`src/create_dataset/tests/test_missing_files.py`).
4. Rebuild en carpeta de versión nueva + regla 0.
5. Niveles: **A** (etiqueta confirmada y sin horas perdidas en la ventilación ni
   en la hora posterior a la desconexión), **B** (horas perdidas que no afectan
   a la etiqueta), **D** (la etiqueta depende de horas perdidas).
6. Cobertura `vars_ok` al 50 % y 80 % (con FiO2 y PEEP aparte).

---

## 3. Clínic — eventos de menos de 1 h — commit (punto 3)

PNG de los **16 eventos < 1 h** (ventilador, onda de presión de vía aérea, FC,
SpO2) en `reports/fase1_5/figs/clinic_short/`, clasificados **por la señal**
(nunca por la duración) con `src/common/short_events.py`:

| Evento | Duración (min) | Clasificación | Señal |
|---|---|---|---|
| `clinic_box10_event_2` | 14,6 | artefacto | sin AWP/TV |
| `clinic_box13_event_4` | 19,4 | artefacto | sin AWP/TV |
| `clinic_box13_event_5` | 0,9 | artefacto | sin AWP/TV |
| `clinic_box14_event_1` | 9,3 | artefacto | sin AWP/TV |
| `clinic_box14_event_2` | 8,6 | artefacto | sin AWP/TV |
| `clinic_box14_event_3` | 0,4 | artefacto | sin AWP/TV |
| `clinic_box14_event_7` | 50,8 | artefacto | sin AWP/TV |
| `clinic_box14_event_25` | 18,9 | artefacto | sin AWP/TV |
| `clinic_box14_event_30` | 19,0 | artefacto | sin AWP/TV |
| `clinic_box14_event_35` | 0,3 | artefacto | sin AWP/TV |
| `clinic_box14_event_37` | 59,4 | artefacto | sin AWP/TV |
| `clinic_box2_event_6` | 13,7 | artefacto | sin AWP/TV |
| `clinic_box4_event_1` | 3,7 | artefacto | sin AWP/TV |
| `clinic_box5_event_6` | 4,1 | artefacto | sin AWP/TV |
| `clinic_box5_event_7` | 48,0 | artefacto | sin AWP/TV |
| `clinic_box8_event_2` | 59,2 | artefacto | sin AWP/TV |

**Resultado: 0 plausibles, 16 artefactos.** Los ficheros de origen de estos
eventos **no contienen pistas de ventilador** (ni onda de presión de vía aérea
ni volumen tidal): en los ficheros comprobados del índice actual solo hay
pistas de monitor. Se clasifican como artefacto por señal, no por duración. El
detalle está en `reports/fase1_5/clinic_short_events.json`.

> **Cautela:** el índice de Clínic (v0.1.0) marcó estos tramos como
> ventilación, pero los ficheros crudos actuales no tienen pistas de
> ventilador. Es una **inconsistencia índice↔datos crudos** que hay que
> aclarar antes de dar el resultado por definitivo (¿se reorganizaron los
> `.vital` de Clínic tras el build?). Se informa, no se excluye nada.

---

## 4. Entregable — tabla final

| Cohorte | Eventos totales | Completos (nivel A) | A+B | Éxito / fallo / censura (48 h) en A | `vars_ok` 50 % en A | `vars_ok` 80 % en A |
|---|---|---|---|---|---|---|
| **MIMIC** | 9 093 (25 excl.) | 9 093 | 9 093 | 7 455 / 471 / 1 638 | n/d | n/d |
| **Clínic** | 181 (4 excl.) | 181 | 181 | 140 / 9 / 41 | n/d | n/d |
| **eICU** | 43 987 (55 excl.) | **0** | **0** | — | — | — |
| **VitalDB** | _pendiente_ | _pendiente_ | _pendiente_ | _pendiente_ | _pendiente_ | _pendiente_ |

- **MIMIC / Clínic**: nivel A = todos los eventos válidos tras la regla 0. En
  Clínic, de los 16 eventos < 1 h, **16 son artefactos** por señal (§3).
- **eICU**: A = 0 porque `ventendoffset` no está documentado; los 36 285
  eventos rescatables son de nivel C (fin reconstruido con el último ajuste
  invasivo). Desglose por nivel y por hospital en
  `datasets/eicu_collaborative/cases_*/eicu_levels_summary.json`.

### Desglose por hospital de eICU

157 hospitales. **Ninguno con ≥ 50 estancias de nivel A** (A = 0 en todos).
El desglose completo A/B/C/D por hospital está en `levels_by_hospital` de
`eicu_levels_summary.json`.

### Antes / después de VitalDB

_Pendiente del rebuild._
