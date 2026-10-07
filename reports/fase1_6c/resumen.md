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

23 312 533 observaciones → **9 093 eventos** (25 excluidos). Cobertura por evento
(> 50 % de horas útiles, D8) y su mediana:

| Variable | antes (> 50 %) | **ahora (> 50 %)** | mediana | > 80 % |
|---|---|---|---|---|
| HR | 99.3 % | 99.3 % | 1.00 | 94.3 % |
| SpO2 | 98.8 % | 98.8 % | 1.00 | 93.0 % |
| MAP (invasiva / no invasiva) | 96.4 % | 96.4 % | 0.97 | 80.9 % |
| — invasiva | 70.4 % | 70.4 % | 0.90 | 58.1 % |
| — no invasiva | 33.8 % | 33.8 % | 0.20 | 24.6 % |
| RR del ventilador | 91.9 % | 91.9 % | 0.97 | 74.2 % |
| FiO2 | 98.3 % | 98.3 % | 1.00 | 92.9 % |
| **PEEP** | **39.6 %** | **98.2 %** | **1.00** | **92.2 %** |
| TV | 98.2 % | 98.2 % | 1.00 | 91.9 % |
| PIP | 97.6 % | 97.6 % | 1.00 | 90.3 % |

- `vars_ok` 50 % (6 variables): **3 407 (37.5 %) → 8 035 (88.4 %)**.
- `vars_ok` 80 %: **5 514 (60.6 %)**.
- **D13** (FC y SpO2 ≥ 50 %): **8 983 (98.8 %)** — no cambia con el arreglo.
- 0 eventos sin ninguna serie.

Variables que **de verdad** bloquean `vars_ok` (eventos por debajo del 50 %):
RR 733, MAP 323, PEEP 163, FiO2 151, SpO2 107, HR 63. (El campo
`variable_limitante` del JSON es el mínimo de las 6 y con coberturas altas suele
ser un empate sin significado: se conserva solo por trazabilidad.)

Entregables: `mimic_coverage.json`, `mimic_coverage_events.csv`,
`mimic_itemids_check.json`. Tests: `TestItemidsFase16c` (catálogo: 220339, 682 y
FR del ventilador = 224690) y `test_coverage_units.py` (evento sintético con
anotación horaria de las 8 variables → ≥ 90 % en todas, más la regresión del bug
horas/minutos).

## 2. eICU — estrategia B (D14)

**27 hospitales** con intervalo mediano de anotación ≤ 2 h (de los 53 de la
cohorte e2). Guardados en `harmonize.yaml → fase1_6c.eicu_b.hospital_ids`.

| Métrica | 48 h | 72 h |
|---|---|---|
| Eventos | 9 754 | 9 754 |
| Éxito | 7 419 | 7 367 |
| Censura | 2 335 | 2 387 |

Causas de censura (48 h): `transfer_ventilated` 1 965, `terminal_extubation`
1 680, `death_at_vent` 881, `trach` 196. Estas cifras **incluyen la corrección
del fin por estrato** (§3), que es la que convierte en censura por "alta estando
ventilado" los eventos cuyo último ajuste está a menos de ~1.5 h del alta.

- **Eventos con ≥ 1 fallo**: 1 093.
- **Inclusión D13** (FC y SpO2 ≥ 50 %): **9 222 (94.5 %)**.
- `vars_ok` 50 % = **7 021 (72.0 %)**; 80 % = 5 157 (52.9 %).
- Cobertura mediana por variable: FiO2 0.97, HR 0.96, SpO2 0.96, MAP 0.95,
  RR 0.94, PEEP 0.93.

Distribución: **West 14, Midwest 6, South 5**, sin región 2 hospitales; tamaño
`100–249` 10, `250–499` 9, `≥ 500` 3, `< 100` 1; docencia: 2 docentes / 25 no
docentes. Eventos por hospital: mínimo 60, mediana 311, máximo 1 416 (el mayor
concentra el **14.5 %** de los eventos).

Estratos de anotación del hospital (con la frontera corregida): **`le1h` 5 888,
`1_2h` 3 866, `gt2h` 0** (los 732 eventos que caían en `gt2h` por la frontera
estricta eran hospitales de exactamente 2 h: se les aplicaba la corrección de
4 h, que no les corresponde).

Identificadores: **todos** los eventos guardan `patientunitstayid`, `uniquepid` y
`hospital_id` (0 faltantes).

Entregable: `eicu_b.json`.

## 3. Calibración del algoritmo de intervalos (MIMIC)

Con el catálogo corregido se reevalúa la rejilla en **{8, 10, 12} h** (1.6b usó
{2,4,6,8} h), con la misma regla de elección: `extra ≤ 10 %` y `perdidos ≤ 5 %`
(anotación ≤ 2 h) / `≤ 15 %` (4 h) en **todos** los estratos; entre los que
cumplen, máximo del mínimo entre estratos de
`score = media(% inicio ±2 h, % fin ±2 h, F1 reintubación)`; empate a < 0.02 → G
menor.

| G (h) | ¿cumple? | score (peor estrato) |
|---|---|---|
| 8 | sí | 0.506 |
| 10 | sí | **0.562** |
| 12 | sí | 0.571 |

**La rejilla ampliada cambia la elección: G = 10 h** (12 queda a 0.009 del 10, en
la banda de empate, y se prefiere el menor). Diferencias frente a G = 8:

| Métrica (estratos 1 h / 2 h) | G = 8 h | G = 10 h |
|---|---|---|
| perdidos | 3.1 / 4.4 % | 3.0 / 4.4 % |
| ventilación inventada (`extra`) | 8.1 / 8.0 % | 8.2 / 8.2 % |
| fragmentos por intento | 1.07 / 1.08 | 1.04 / 1.04 |
| inicio ±2 h | 91.9 / 91.9 % | 91.5 / 91.5 % |
| fin ±2 h | 56.8 / 52.5 % | 56.4 / 52.1 % |
| F1 reintubación | 0.398 / 0.394 | **0.440 / 0.438** |
| etiqueta 48 h | 85.2 / 83.8 % | 85.2 / 83.9 % |

G = 10 mejora sobre todo el **F1 de reintubación** (+0.04) y la fragmentación, y
es neutro para la etiqueta a 48 h. **Los índices ya construidos (MIMIC v0.4.0 y
eICU-B) usan G = 8 h** por coherencia con la 1.6b y para no repetir una
re-extracción de MIMIC de 2 h 20 min; pasar a G = 10 h es la recomendación y
requiere esa re-extracción (decisión pendiente).

### Distribución **con signo** del error (G = 8 h)

`error = reconstruido − real`. El inicio está centrado; el **fin se queda corto**
de forma sistemática:

| Estrato de anotación | inicio mediana (p10, p90) | **fin mediana** (p10, p25, p75, p90) |
|---|---|---|
| 1 h | −0.07 h (−1.00, +0.42) | **−1.25 h** (−5.00, −2.83, −0.28, +0.42) |
| 2 h | −0.08 h (−1.00, +0.42) | **−1.52 h** (−5.23, −3.00, −0.50, +0.30) |
| 4 h | −0.08 h (−1.00, +0.50) | **−2.35 h** (−7.17, −4.05, −0.93, +0.17) |

### Matriz de confusión 3×3 de la etiqueta a 48 h (G = 8 h)

Estrato de 1 h (filas = reconstruido, columnas = real):

| | éxito | éxito tras fallo | censura |
|---|---|---|---|
| **éxito** | **6 602** | 92 | 1 007 |
| **éxito tras fallo** | 351 | 229 | 161 |
| **censura** | 172 | 0 | 466 |

Concordancia de 3 clases: **80.4 %** (1 h), 78.9 % (2 h), 66.1 % (4 h). El error
es casi todo "éxito reconstruido que en realidad es censura" (1 007), es decir
extubaciones que el algoritmo cree ver y no lo son.

### Corrección del fin por estrato (aplicada)

Sumar al fin reconstruido el **opuesto** de la mediana del error de fin del
estrato del hospital. (El script sumaba la mediana tal cual, lo que **duplicaba**
el sesgo: el error mediano pasaba de −1.23 h a −2.45 h. Corregido el signo —
test `TestSignoDeLaCorreccion` — la corrección pasa a centrar el fin en 0.00 h.)

| Estrato | desplazamiento aplicado | fin mediana antes → después | etiqueta 48 h antes → después |
|---|---|---|---|
| 1 h | **+1.23 h** | −1.23 h → **0.00 h** | 85.2 % → **87.3 %** |
| 2 h | **+1.50 h** | −1.50 h → **0.00 h** | 83.9 % → **86.4 %** |

**Veredicto: SÍ se aplica** (mejora en los dos estratos: +2.09 y +2.53 pp; media
+2.31 pp). Aplicada a eICU-B:

- **815 de 9 754 eventos (8.4 %)** cambian la etiqueta a 48 h (825 a 72 h);
- éxito 48 h **7 881 → 7 419**, censura 1 873 → 2 335, fallos 1 089 → 1 093;
- el fin corregido **se recorta al alta** (no puede haber ventilación después del
  alta) y al inicio del intento siguiente; aun así el efecto es el mismo, porque
  un fin que toca el alta ya censura por "alta estando ventilado";
- **las dos versiones quedan guardadas**: `labels` (corregida, vigente) y
  `labels_sin_correccion` (original) por evento, con `end_correccion_h`.
  Error de etiqueta resultante: 84.7 % → **87.0 %** de concordancia.

Lógica pura (con tests): `label3`, `shift_interval_ends`, `confusion_matrix` y
`correction_verdict` en `src/common/vent_intervals.py`.

## 4. Clínic y VitalDB — reconstrucción completa

Código aplicado: regla 0 con **observación fisiológica** (FC 20–250, SpO2 50–100),
*sin dato ≠ sin señal*, ficheros localizados **dentro de su caja** (se ignora
`dataset_clinic`), cobertura D8 **en minutos** (arreglo del bug de unidades) y
`label_source`. Se añaden **dos respaldos** para localizar los ficheros de un
evento (Fase 1.6c, punto 4):

1. los que solapan algún intento (región de monitor fuera de los ficheros);
2. los que solapan el episodio por la **hora de cabecera** del sondeo: hay
   ficheros de VitalDB cuyo **nombre no coincide con la fecha del contenido** y
   su episodio nace de la cabecera. Sin este respaldo el evento quedaba sin
   `source_files`, su cobertura salía 0 y D13 lo excluía por un artefacto.

### VitalDB (índice `v0.4.0`)

| Métrica | Valor |
|---|---|
| Eventos / excluidos | 96 / 18 (nivel A: 96) |
| Éxito 48 h / censura 48 h | 40 / 56 (todos `end_of_record`) |
| Eventos con ≥ 1 fallo | 5 |
| **Inclusión D13** (FC y SpO2 ≥ 50 %) | **67 (69.8 %)** |
| `vars_ok` 50 % / 80 % | 22 / 16 |

`end_reason`: `end_of_record` 60, `extubation_observed` 36 (vocabulario único).
Cobertura mediana: RR 0.92, SpO2 0.91, HR 0.87, MAP 0.87 y **FiO2 / PEEP 0.00**:
en VitalDB esos numéricos del ventilador **no se registran** en las pistas
`Intellivue/FIO2` / `PEEP_CMH2O` (muchos ficheros solo traen `ABP`, `PLETH` y las
ondas `AWP_WAV`/`FLOW_WAV`). Por eso `vars_ok` (6 variables) no es alcanzable
aquí y el criterio útil es D13.

**Pendiente de 4 eventos (4.2 %):** `vitaldb_SICU1_08_event_4`,
`SICU1_12_event_4`, `SICU2_05_event_4` y `SICU2_16_event_15` siguen **sin
`source_files`**: su episodio nace de la **cabecera** de un fichero cuyo nombre
está a días de distancia (box sin ficheros con esa fecha en el nombre) y el
sondeo completo de esas 4 cajas se quedó bloqueado por el estado del disco D:
(§9). Sin ficheros su cobertura es 0 y D13 los excluye, así que el **69.8 % de
D13 es una cota inferior**. Mitigación implementada y probada con tests:
`_files_for_episode(..., probes=...)` (respaldo por cabecera) y
`scripts/verify/fase1_6c/repair_source_files.py` para aplicarlo a un índice ya
construido cuando la E/S esté sana.

### Clínic (índice `v0.4.0`) — **no completado**

La reconstrucción completa **no ha terminado** por el estado del disco D:
(§9). Evidencia medida en la ejecución abortada: 35.1 GB leídos, 2 110 s de CPU
en ~14 h de reloj (≈4.7 MB/s y tramos con lecturas bloqueadas minutos), sin
llegar a escribir el índice. `datasets/clinic/cases_v0.4.0_aa142f35` queda
**vacío**; el único índice de Clínic disponible sigue siendo el antiguo
(`cases_v0.1.0_a225d21b`, 181 eventos) y **sus cifras de cobertura están
invalidadas** por el bug de unidades de la 1.6b, así que la fila de Clínic en la
tabla final se marca como pendiente.

Para cerrarlo se deja preparada la receta en dos pasos (evita la fase serial que
es el cuello de botella):

```
python -m src.create_dataset.build_signal_cases --cohort clinic --no-merge --workers 10
python scripts/verify/fase1_6c/coverage_parallel.py --cohort clinic --workers 10
```

El builder ya incorpora los dos respaldos de `source_files`, así que esta
receta no necesita reparación posterior. Lo ya verificado para Clínic en fases
previas sigue vigente: los 16 eventos de < 1 h son **12 ventilación real** (6 por
onda AWP/FLOW, 6 por ajustes D6) y 4 artefactos
(`reports/fase1_6b/clinic_short_verdict.json`).

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

Generada por `scripts/verify/fase1_6c/final_table.py` a partir de los
artefactos de los puntos 1-4, 6 y 7 (`tabla_final.json` / `tabla_final.md`):

| Cohorte | Eventos | Incluidos D13 | Éxito 48 h | Censura 48 h | Fallos (≥1) | `vars_ok` 50 % | `vars_ok` 80 % | Error de etiqueta | Perfil dominante |
|---|---|---|---|---|---|---|---|---|---|
| MIMIC | 9 093 | 8 983 (98.8 %) | 7 455 | 1 638 | 471 | 88.4 % | 60.6 % | 0.00 h / 100 % (referencia) | las 6 variables (88.4 %) |
| eICU-B | 9 754 | 9 222 (94.5 %) | 7 419 | 2 335 | 1 093 | 72.0 % | 52.9 % | 0.00 h / 87.0 % (corregida) | las 6 variables (72.0 %) |
| Clínic | 181 (índice antiguo) | **pendiente** | 140 | 41 | 7 | pendiente | pendiente | **no aplica (señal continua)** | pendiente |
| VitalDB | 96 | 67 (69.8 %, cota inferior) | 40 | 56 | 5 | 22.9 % | 16.7 % | **no aplica (señal continua)** | HR+SpO2+MAP+RR (30.2 %) |

Notas de la tabla:

- Éxito y censura son **mutuamente excluyentes**; los eventos con ≥ 1 fallo van en
  columna aparte porque también existen entre los censurados.
- `vars_ok` (6 variables) es **indicador de calidad**, nunca filtro; el filtro es
  D13.
- eICU-B aparece con la etiqueta **corregida** (§3); sin corrección era
  7 881 / 1 873 con error de etiqueta −1.34 h y 84.7 % de concordancia.
- **`end_reason` no tiene todavía un vocabulario idéntico entre cohorts** (queda
  para la Fase 2): MIMIC `{extubation_observed, death, transfer_ventilated,
  tracheostomy}`; eICU-B `{extubation_observed, transfer_ventilated,
  death_at_vent}`; Clínic/VitalDB `{extubation_observed, end_of_record}`. Cada
  una es coherente dentro de su índice, pero la unión no es un vocabulario común.

## 9. Limitaciones y decisiones abiertas

- **El disco de datos es el cuello de botella** y está degradado: Clínic son
  101.5 GB en 5 471 ficheros de caja y VitalDB 72.7 GB en 15 493, con tramos en
  los que una lectura se bloquea minutos (la reconstrucción de VitalDB tardó
  13.7 h de reloj y la de Clínic no llegó a terminar: 35.1 GB leídos en ~14 h).
  De ahí las dos consecuencias ya descritas: Clínic pendiente y los 4 eventos de
  VitalDB sin `source_files`. Medidas tomadas: la cobertura se puede medir aparte
  y **en paralelo** (`coverage_parallel.py`) porque en el builder era **serial**.
- **G**: la rejilla ampliada {8, 10, 12} h elige **G = 10 h** (score 0.562 frente
  a 0.506 de G = 8), con la misma etiqueta a 48 h y mejor F1 de reintubación
  (+0.04). Los índices construidos usan **G = 8 h**: pasar a 10 h exige
  re-extraer MIMIC (2 h 20 min medidos) y reconstruir eICU. Es la decisión
  pendiente más importante antes de la Fase 2.
- **La corrección del fin es la que más mueve la etiqueta de eICU**: 815 de
  9 754 eventos (8.4 %) cambian de etiqueta a 48 h, casi todos de "éxito" a
  "alta estando ventilado". Es lo que dice la calibración en MIMIC (+2.1/+2.5 pp),
  pero conviene revisarlo antes de la Fase 2, porque la mitad de ese efecto nace
  de que el fin corregido **toca el alta**.
- MIMIC y Clínic/VitalDB se re-extraen con itemids y código corregidos; sus
  cifras anteriores (Fase 1.5/1.6b) quedan superadas.
- Los datos crudos son inmutables: no se ha modificado nada en `D:\data`.

## 10. Reproducibilidad

```
# 1. MIMIC: catálogo de itemids + cobertura
python -m src.create_dataset.build_mimic_cases
python scripts/verify/fase1_6c/mimic_coverage.py

# 2. Calibración (G, matriz 3x3, corrección del fin)
python scripts/verify/fase1_6b/calibrate_gap_mimic.py --gaps 8,10,12 --out-dir reports/fase1_6c

# 3. eICU-B (aplica la corrección si el veredicto la aprueba)
python -m src.create_dataset.build_eicu_events
python scripts/verify/fase1_6c/eicu_b_report.py

# 4. Cohortes con señal (dos pasos: evita la cobertura serial)
python -m src.create_dataset.build_signal_cases --cohort vitaldb --no-merge --workers 6
python scripts/verify/fase1_6c/coverage_parallel.py --cohort vitaldb --workers 8
python scripts/verify/fase1_6c/clinic_vitaldb_report.py --cohorts clinic vitaldb

# 6-8. Particiones, perfiles y tabla final
python scripts/verify/fase1_6c/cohorts_report.py
python scripts/verify/fase1_6c/final_table.py
```

Tests: `python -m pytest src/stage0/tests src/common/tests src/create_dataset/tests -q`
→ **432 pasan, 1 se salta** (el que valida el catálogo contra `D_ITEMS` real).
