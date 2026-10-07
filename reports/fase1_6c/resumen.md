# Fase 1.6c — Cierre de cohortes antes del dataset final

Rama `fix/integridad-datos`. Config `harmonize.yaml` v**0.4.0** (hash `aa142f35`).
**No** se aplican particiones ni se construye la base curada (eso es la Fase 2).

Decisiones nuevas:

- **D13 — inclusión por núcleo mínimo:** un evento entra si tiene **FC y SpO2** con
  ≥ 50 % de horas útiles (D8: arrastre de las constantes, 2 h y 12 h para ajustes
  según el caso). El resto de variables son opcionales y nunca excluyen;
  `vars_ok` (6 variables) es un **indicador de calidad**, no un filtro.
- **D14 — cohortes:** MIMIC-MetaVision; **eICU estrategia B** (hospitales de la
  cohorte e2 con intervalo mediano de anotación ≤ 2 h, 27 hospitales en
  `fase1_6c.eicu_b.hospital_ids`); Clínic; VitalDB.

---

## 1. MIMIC — cobertura de variables

### Diagnóstico por variable (9 093 eventos)

| Variable | % eventos con > 50 % de horas útiles | Cobertura mediana |
|---|---|---|
| HR | 99.3 % | 1.00 |
| SpO2 | 98.8 % | 1.00 |
| MAP (max invasiva / no invasiva) | 96.4 % | 0.97 |
| — MAP invasiva | 70.4 % | 0.90 |
| — MAP no invasiva | 33.8 % | 0.20 |
| RR del ventilador | 91.9 % | 0.97 |
| FiO2 | 98.3 % | 1.00 |
| **PEEP** | **39.6 %** | **0.34** |
| TV | 98.2 % | 1.00 |
| PIP | 97.6 % | 1.00 |

**Variable limitante:** PEEP en **6 760 eventos de 9 093 (74 %)**, después HR
(1 059), RR (609), MAP (479). Por eso `vars_ok` (6 variables) era tan bajo.

### Causa: faltaba el itemid canónico de PEEP

El catálogo **no incluía `220339 = "PEEP set"`** (Metavision, cmH2O), que es el
itemid con el que se anota la PEEP en MetaVision: solo se leían
`505/686/224700` (CareVue y "Total PEEP Level"). También faltaba
`682 = "Tidal Volume (Obser)"` (CareVue).

Corrección aplicada en `src/create_dataset/mimic_itemids.py` (etiqueta oficial de
`D_ITEMS` en cada comentario):

```
PEEP: ((505, "PEEP"), (506, "PEEP Set"), (686, "Total PEEP Level"),
       (224700, "Total PEEP Level"), (220339, "PEEP set"))     # 220339 NUEVO
TV_observed: ((681, "Tidal Volume"), (682, "Tidal Volume (Obser)"), ...)  # 682 NUEVO
```

**Excluidos a propósito** (documentado): `220210`/`618` ("Respiratory Rate") son
la FR del **monitor**, no la FR total del ventilador (`224690`), y mezclarlas
confundiría dos variables distintas; `224696`/`543` son presión de plateau, no PIP.

### Comprobación de la extracción

- los **29 itemids** del catálogo están presentes en el parquet de observaciones
  (0 faltantes) y **ninguna etiqueta discrepa** de `D_ITEMS`
  (`reports/fase1_6c/mimic_itemids_check.json`);
- el arrastre se aplica **en minutos**: `t_unix/60` para las marcas y
  `vent_*_h × 60` para los tramos (campo `unidades` del JSON).

### Recalculo (tras re-extraer CHARTEVENTS con el catálogo corregido)

(pendiente: el streaming de CHARTEVENTS está en curso)

**D13**: con FC y SpO2 ≥ 50 % entran el **98.8 %** de los eventos (8 983/9 093);
`vars_ok` 50 % (6 variables) = **37.5 %** (3 407) con el catálogo antiguo.

Entregables: `mimic_coverage.json`, `mimic_coverage_events.csv`,
`mimic_itemids_check.json`.

## 2. eICU — estrategia B (D14)

**27 hospitales** con intervalo mediano de anotación ≤ 2 h (de los 53 de la
cohorte e2). Guardados en `harmonize.yaml → fase1_6c.eicu_b.hospital_ids`.

| Métrica | 48 h | 72 h |
|---|---|---|
| Eventos | 9 754 | 9 754 |
| Éxito | 7 881 | 7 836 |
| Censura | 1 873 | 1 918 |

Causas de censura (48 h): `terminal_extubation` 1 106, `transfer_ventilated` 607,
`trach` 97, `death_at_vent` 63.

- **Eventos con ≥ 1 fallo**: 1 089.
- **Inclusión D13** (FC y SpO2 ≥ 50 %): **9 030 (92.6 %)**.
- `vars_ok` 50 % = **6 890 (70.6 %)**; 80 % = 4 939.
- Cobertura mediana por variable: FiO2 0.97, HR 0.96, MAP 0.95, PEEP 0.93,
  RR 0.94, SpO2 0.96.

Distribución: **West 14, Midwest 6, South 5**, sin región 2 hospitales; tamaño
`100–249` 10, `250–499` 9, `≥ 500` 3, `< 100` 1; docencia: 2 docentes / 25 no
docentes. Eventos por hospital: mínimo 60, mediana 311, máximo 1 416 (el mayor
concentra el **14.5 %** de los eventos).

Identificadores: **todos** los eventos guardan `patientunitstayid`, `uniquepid` y
`hospital_id` (0 faltantes).

Entregable: `eicu_b.json`.

## 3. Calibración del algoritmo de intervalos (MIMIC)

(pendiente: depende de la re-extracción de CHARTEVENTS, porque PEEP es un
concepto marcador y su ausencia alteraba la densidad de anotaciones)

Se amplía la rejilla a **{8, 10, 12} h** con la misma regla de elección, se añade
la **matriz de confusión 3×3** de la etiqueta a 48 h (éxito / éxito con fallo
previo / censura) en los estratos de 1 h y 2 h, la **distribución con signo** del
desplazamiento del fin y la **simulación de la corrección por estrato** (sumar al
fin reconstruido la mediana del desplazamiento medido), aplicable a eICU solo si
mejora la concordancia en **ambos** estratos.

Lógica pura (con tests): `label3`, `shift_interval_ends`, `confusion_matrix` y
`correction_verdict` en `src/common/vent_intervals.py`.

## 4. Clínic y VitalDB — reconstrucción completa

Código actual aplicado: regla 0 con **observación fisiológica** (FC 20–250,
SpO2 50–100), *sin dato ≠ sin señal*, ficheros localizados **dentro de su caja**
(se ignora `dataset_clinic`), cobertura D8 **en minutos** y `label_source`.
Se añade un **respaldo de ficheros** para los eventos cuya región de monitor no
solapa ningún fichero del box (resuelve los 4 eventos de VitalDB sin
`source_files`).

(pendiente: reconstrucciones en curso — ver §8)

## 5. Campos que necesitará la Fase 2

Comprobados en los 4 índices (`reports/fase1_6c/campos_fase2.json`):

| Cohorte | `censor_cause`/`censor_time_h` por ventana | `label_source` | Agrupación |
|---|---|---|---|
| MIMIC | ✅ | `explicita` | `subject_id` ✅ |
| eICU | ✅ | `anotaciones` | `uniquepid` + `hospital_id` ✅ |
| Clínic | ✅ | `senal` | `box` ✅ |
| VitalDB | ✅ | `senal` | `box` ✅ |

Los builders ya escriben `label_source` (`explicita` / `anotaciones` / `senal`) y
eICU añade `uniquepid`; para los índices anteriores se completaron los campos con
`scripts/verify/fase1_6c/ensure_fields.py --apply` (solo añade, nunca reescribe).
`annotation_stratum` está en eICU.

## 6. Viabilidad de las particiones (sin aplicarlas)

- **MIMIC**: 8 160 pacientes para 9 093 eventos; **695 pacientes (8.5 %) con más
  de una estancia** → hay que partir **por `subject_id`**. Un test del 15 % =
  **1 224 pacientes**; 5 pliegues ≈ 1 632 pacientes por pliegue.
- **eICU-B**: 27 hospitales. Propuesta a nivel de hospital (muestreo fijo por
  región): **8 hospitales de test (29.6 %) con 18.3 % de los eventos**, 19 de
  train (7 973 eventos). Viabilidad: ✅ (≥ 4 test y ≥ 10 train).
  Aviso: con 27 hospitales no se puede lograr a la vez 25 % de hospitales y 25 %
  de eventos (los grandes concentran la muestra). Alternativa si se quiere evitar
  el riesgo de hospital no visto: partición por `uniquepid` estratificada por
  hospital.
- **Clínic**: corte temporal 60/40 → train `2024-06 … 2025-06` (**108 eventos**),
  test `2025-07 … 2026-02` (**73**). Bloques del train: 5 / 13 / 42 / 48 eventos
  (fallos: 0 / 0 / 0 / 1) → los fallos se concentran al final; hay que vigilar
  que cada bloque tenga desenlaces de las dos clases.
- **VitalDB**: los 96 eventos caen en **3 meses (2024-12 … 2025-02)** → **no hay
  corte temporal posible** (test vacío). Alternativa propuesta: partición
  aleatoria fija (semilla 42) 75/25 → train 72 eventos (5 fallos, 14 censuras),
  test 24 (0 fallos, 7 censuras).

## 7. Perfiles de disponibilidad

(pendiente de las reconstrucciones; eICU-B ya está)

- **eICU-B**: perfil dominante = las **6 variables** presentes (70.6 %), después
  `HR+SpO2+MAP+RR` (7.9 %, sin FiO2/PEEP), `HR+SpO2+MAP+FiO2+PEEP` (7.0 %) y
  `HR+SpO2+MAP+RR+FiO2` (5.6 %).

## 8. Tabla final

(pendiente)

## 9. Limitaciones

- El disco de datos es el cuello de botella: Clínic son 101.5 GB en 5 471
  ficheros de caja y algunos tardan decenas de segundos en abrirse.
- MIMIC y Clínic/VitalDB se re-extraen con itemids y code corregidos; sus
  cifras anteriores (Fase 1.5/1.6b) quedan superadas.
