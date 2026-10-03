# Fase 1 — Informe

> **Estado:** Fase 1 completada. Los seis puntos de código están implementados con
> sus tests, se aplicaron las seis correcciones previas a los builds y los cuatro
> builds se han ejecutado sobre los datos crudos (Clínic, VitalDB, MIMIC y eICU).

## 1. Entregables de código (commits)

| Punto | Commit | Ficheros principales | Tests |
|-------|--------|----------------------|-------|
| 1 | `95ff59e` | `src/common/episodes.py` (D1/D2/D4) | `src/common/tests/test_episodes.py` (21) |
| 2 | `cff0d46` | `src/common/paths.py`, `src/common/vital_signals.py`, `src/create_dataset/build_signal_cases.py` | `src/create_dataset/tests/test_build_signal_cases.py` (15) |
| 3 | `1e5ffc7` | `src/common/timeutils.py`, `src/create_dataset/mimic_itemids.py`, `src/create_dataset/build_mimic_cases.py` | `test_no_naive_timestamp.py`, `test_mimic_cases.py` |
| 4 | `ef89b43` | `src/common/eicu_rules.py` + adaptador/builder eICU | `src/common/tests/test_eicu_rules.py` (19) |
| 5 | `ab15aa1` | `src/common/d5_events.py` | `src/common/tests/test_d5_events.py` (22) |
| 6 | `c7fe70a` | `src/common/labels.py` (D3) + índices de las 4 cohortes | `src/common/tests/test_labels.py` (10) |

**Entorno (versiones fijadas en `requirements.txt`, corrección 5):**
`pandas==2.2.3`, `numpy==2.3.5`, `pyarrow==23.0.1`, `vitaldb==1.6.0`,
`scipy==1.16.2`, `duckdb==1.5.4`, `PyYAML==6.0.3`.

## 1.bis Correcciones previas a los builds

Tras la revisión de `95ff59e..79147e0` se aplicaron cinco correcciones, cada una
con su commit y sus tests (el nº 6 es este informe):

| Corrección | Commit | Qué cambia |
|---|---|---|
| 1. Fin de observación = fin del monitor | `23731e5` | Extubación solo con ≥ 1 h de monitor sin VM; `_end_reason` contra el fin del monitor; la fusión incluye los ficheros hasta el fin del monitor |
| 2. MIMIC: observaciones con hora | `72ed0a4` | No se resume a primer/último registro (`mimic_observations.parquet`); tramos con huecos reales (D1); marcadores de VM sin FiO2 (modo, PEEP, TV pautado/observado, PIP, FR total) + 225792 |
| 3. D5 conectado en las 4 cohortes | `e6af3c7` | MIMIC (PROCEDUREEVENTS_MV 225448/226237, tipo de vía aérea, ICD-9 31.1/31.2x marcando sin hora, DEATHTIME), eICU (`airwaytype`, `Expired`), Clínic/VitalDB (pérdida de constantes); censura solo si ocurre ANTES del primer éxito |
| 4. Etiquetador único | `dd66832` | `survival.py` delega en `src/common/labels.py` + test de equivalencia |
| 5. Fechas independientes de pandas | `6c789d0` | `series_to_epoch_seconds` (sin `astype("int64")/1e9`); test con resolución µs y ns + control negativo |

Total: **146 tests en verde, 1 saltado** (el que valida itemids contra
`datasets/mimic3wdb/clinical/D_ITEMS.csv.gz`, no presente; el de `D:/data`
sí se ejecuta y pasa).

## 2. Decisiones implementadas

- **D1/D2/D4** en `src/common/episodes.py`, agnóstico de cohorte: fusión de
  desconexiones ≤ 2 h, separación de intentos > 2 h, cambio de paciente por
  hueco de monitor > 1 h o por frontera de estancia, y exclusión de ventilador
  sin paciente (≥ 80 % sin HR ni SpO2).
- **D6/D12** en los builders de Clínic/VitalDB y MIMIC: cada evento guarda
  `t0_unix`, `t0_source`, `arrived_ventilated`, intentos, `end_reason`,
  etiquetas e identificadores de origen.
- **D7/D9/D10** documentados en config; MAP invasiva→no invasiva ya resuelta en
  eICU (`pick_map_source`) y MIMIC (itemids 220052 → 220181).
- **D8/D11** pendientes (Fase 2).

## 3. Bugs corregidos (cada uno con test que falla antes)

1. **Detección de ventilación por bytes** en `build_clinical_cases.py` y
   `build_vitaldb_cases.py`: sustituida por lectura con el parser de `vitaldb`
   exigiendo presencia real de pistas (`src/common/vital_signals.py`).
2. **Eliminación de episodios "con mismo inicio"** (descartaba eventos
   legítimos): sustituida por segmentación D1.
3. **Límite de 7 días y "resume" del merge** (VitalDB): eliminados; las
   versiones son inmutables y el build aborta si la carpeta existe.
4. **`itemid 224696` usado como PIP**: es *"Plateau Pressure"*; el PIP correcto
   es `224695` (*"Peak Insp. Pressure"*). Corregido y probado.
5. **`itemid 682` usado como MV**: es *"Tidal Volume (Obser)"*; el MV correcto
   es `224687` (*"Minute Volume"*) / `448` (CareVue). Corregido.
6. **RR = 220210 (RR del monitor)** en vez de la FR total del ventilador
   (`224690`, *"Respiratory Rate (Total)"*). Corregido en el catálogo.
7. **Regex `'epine'`** capturaba también `norepinephrine`: sustituida por
   patrones sin solape (`d5`→`eicu_rules`).
8. **FR de enfermería sobrescribía la del ventilador** en eICU: la de
   enfermería pasa a `eICU/RR_nurse` y NO se usa como RR (D7).
9. **Vasopresores de `medication` (bolos) sobrescribían los de `infusionDrug`**:
   la infusión continua tiene prioridad (`overwrite=False` para los bolos).
10. **Corte fijo `gap <= 48.0`** en el adaptador eICU: eliminado; la
    clasificación por ventanas la hace `classify_attempts` (D3).
11. **Duraciones/huecos imposibles** de eICU (hasta 25 713 h, offsets fuera de
    la estancia): reglas explícitas + registro de anomalías
    (`sanitize_vent_episodes`), nunca silenciadas.
12. **`.timestamp()` sobre datetimes naive** (usaba la hora LOCAL del equipo) en
    26 puntos del repo: refactorizado a `to_epoch_utc`/`ensure_utc`
    (`src/common/timeutils.py`). Un test repo-wide lo prohíbe a partir de ahora.
13. **Falso positivo de traqueostomía**: el patrón `trach` capturaba
    *"Endotracheal"*; corregido con `\btrach\w*`. *(Lo detectó el control
    negativo del propio test, no una revisión manual.)*

## 4. Hallazgos de datos relevantes

- El `CHARTEVENTS` de `D:/data/mimiciii` mezcla itemids de **CareVue** (5xx,
  6xx, 3xxx) y **Metavision** (22xxxx): es MIMIC-III, no MIMIC-IV. El catálogo
  cubre ambos orígenes.
- `D:/data/mimic-iv-3.1/` existe pero **no** es la fuente del pipeline actual.
- La columna de `Time` de `vitaldb.get_samples` es naive: los epochs anteriores
  dependían de la zona horaria del equipo.

## 5. Resultados de los builds

Comandos ejecutados (desde la raíz del repo; rutas crudas de `harmonize.yaml`):

```powershell
python -m src.create_dataset.build_signal_cases --cohort clinic  --no-merge --workers 16
python -m src.create_dataset.build_signal_cases --cohort vitaldb --no-merge --workers 16
python -m src.create_dataset.build_mimic_cases
python scripts/verify/fase1/summarize.py       # tablas desde los índices
python scripts/verify/fase1/summarize_eicu.py  # tablas de eICU (reglas compartidas)
python scripts/verify/fase1/timelines.py --cohort <cohort>
```

> Nota: los builds con señal se han ejecutado con `--no-merge` (índice de casos;
> la fusión de los `.vital` de cada evento queda para cuando se necesite el
> fichero, ya que reescribir 137 GB de señal no aporta a las etiquetas).
> Se añadió `--workers` (paralelización por box) porque en serie el build de
> Clínic+VitalDB tardaba ~15 h (un solo box de Clínic: 32,5 min).

### 5.1 Eventos antes y después

| Cohorte | Antes | Después | Excluidos (ventilador sin paciente) |
|---|---|---|---|
| Clínic | 50 (v0.1.0_df652b7b) | **181** | 4 |
| VitalDB | 83 (v0.1.0_df652b7b) | **94** | 20 |
| eICU | 44 772 (v0.1.0_fbb5b280) | **44 042** estancias con VM | 0 (descartadas 0 por alta ≤ t0) |
| MIMIC | 82 (v0.1.0_df652b7b) | **24 039** (de 60 202 estancias con VM) | 87 |

### 5.2 Intentos por evento

- Clínic: `{1: 172, 2: 6, 3: 2, 4: 1}`
- VitalDB: `{1: 81, 2: 10, 3: 2, 4: 1}`
- eICU: `{1: 43 683, 2: 340, 3: 10, 4: 3, 5: 2, 7: 2, 8: 1, 12: 1}`
- MIMIC: `{1: 14 776, 2: 3 566, 3: 1 613, 4: 915, 5: 587, ...}` (máx 70 intentos;
  es la cohorte con más reintubaciones dentro de la misma estancia)

### 5.3 Éxito / censura a 48 h y 72 h (desglose por causa)

| Cohorte | Ventana | Éxito | Censura | Causas de la censura |
|---|---|---|---|---|
| Clínic | 48 h | 141 (77,9 %) | 40 | `end_of_record`: 40 |
| Clínic | 72 h | 141 (77,9 %) | 40 | `end_of_record`: 40 |
| VitalDB | 48 h | 64 (68,1 %) | 30 | `end_of_record`: 18; `terminal_extubation`: 12 |
| VitalDB | 72 h | 63 (67,0 %) | 31 | `end_of_record`: 18; `terminal_extubation`: 13 |
| eICU | 48 h | 37 414 (85,0 %) | 6 628 | `terminal_extubation`: 4 547; `death_at_vent`: 1 074; `trach`: 1 007 |
| eICU | 72 h | 37 249 (84,6 %) | 6 793 | `terminal_extubation`: 4 710; `death_at_vent`: 1 074; `trach`: 1 009 |
| MIMIC | 48 h | 21 334 (88,7 %) | 2 705 | `terminal_extubation`: 1 166; `trach`: 822; `death_at_vent`: 717 |
| MIMIC | 72 h | 20 877 (86,8 %) | 3 162 | `terminal_extubation`: 1 434; `trach`: 978; `death_at_vent`: 750 |

Intentos fallidos por evento (≥ 1 fallo): Clínic 7 (48 h) / 8 (72 h);
VitalDB 5 (48 h) / 7 (72 h); MIMIC 7 869 (48 h) / 8 497 (72 h).

### 5.4 Traqueostomías y extubaciones terminales

- **eICU**: `trach` 1 007 (48 h) / 1 009 (72 h) eventos; `terminal_extubation`
  4 547 (48 h) / 4 710 (72 h); `death_at_vent` 1 074 (idéntico en ambas
  ventanas, como corresponde a una muerte ventilado).
- **MIMIC**: `trach` 822 (48 h) / 978 (72 h); `terminal_extubation` 1 166 (48 h) /
  1 434 (72 h); `death_at_vent` 717 (48 h) / 750 (72 h). Traqueostomías
  detectadas por `PROCEDUREEVENTS_MV` (225448/226237) y por tipo de vía aérea;
  por ICD-9 sin hora: **0** (todas tuvieron hora en las tablas con tiempo).
  `end_reason`: `death` 2 960, `tracheostomy` 1 812.
- **Clínic/VitalDB**: la traqueostomía **no es detectable con señales**
  (limitación documentada, sin estimarla con tasas de otras cohortes).
- **Sensibilidad `exclude`** (cuántos casos/horas se perderían): pendiente de
  calcular en la Fase 2 sobre `stays`/`labels` con la columna de sensibilidad.

### 5.5 Distribución completa de la duración (horas)

| Cohorte | min | Q1 | mediana | Q3 | máx |
|---|---|---|---|---|---|
| Clínic | 0,0 | 7,0 | 19,0 | 45,3 | 405,0 |
| VitalDB | 0,0 | 5,5 | 19,3 | 85,7 | 450,1 |
| eICU | 0,0 | 10,6 | 32,9 | 91,5 | 2 395,2 |
| MIMIC | 0,0 | 4,0 | 20,0 | 100,5 | 4 082,0 |

### 5.6 Desconexión → muerte (eICU/MIMIC)

- **eICU**: n = 5 194 muertes no ventiladas tras una desconexión →
  min 0,02 h | Q1 1,48 h | **mediana 4,17 h** | Q3 12,68 h | máx 3 960,65 h.
  De ellas, 4 547 caen dentro de las 48 h posteriores a la desconexión.
- **MIMIC**: 3 262 muertes no ventiladas tras una desconexión → min 0,0 h |
  Q1 7,4 h | mediana 27,0 h | Q3 87,9 h | máx 2 928,8 h. De ellas, **2 039 caen
  dentro de las 48 h** (min 0,0 | Q1 3,3 | mediana 11,2 | Q3 23,6 h); las que
  además ocurren antes de un éxito consolidado son 1 166 (censura
  `terminal_extubation`).

### 5.7 Ventilador y monitor apagándose a la vez (≤ 15 min)

- Clínic: **40** eventos (posibles muertes o traslados no visibles).
- VitalDB: **23** eventos.
- Pérdida de constantes (HR 0 / SpO2 perdida sin recuperación) antes o en la
  desconexión: Clínic **0**, VitalDB **26**.
- MIMIC y eICU: **no aplica** — su frontera de paciente es el identificador de
  estancia (D2) y la muerte/traslado se leen de las tablas (D5), no de la
  desaparición del monitor.

### 5.8 Eventos excluidos como actividad sin paciente (D4)

- Clínic: 4 (todos `ventilator_without_patient`).
- VitalDB: 20 (todos `ventilator_without_patient`).
- eICU: 0 estancias descartadas por este motivo con el criterio usado
  (HR/SpO2 de CHARTEVENTS/vitalPeriodic disponibles).
- MIMIC: 87 (todos `ventilator_without_patient`).

### 5.9 Revisión clínica (PNG, semilla fija 20261002)

Generados con `scripts/verify/fase1/timelines.py` en
`reports/fase1/figs/<cohorte>/` (ficheros `.png`; están en `.gitignore`, se
generan en local):

- 10 eventos al azar por cohorte: Clínic 12 PNG (10 + 2 largos), VitalDB 15 PNG
  (10 + 5 largos), MIMIC 10 PNG. eICU no genera PNG (no tiene índice de casos:
  su ruta es el adaptador de la Etapa 0).
- Eventos ≥ 7 días que terminan en "extubación":
  - **Clínic** (2): `clinic_box12_event_2` (260,6 h), `clinic_box6_event_12`
    (201,6 h, 2 intentos).
  - **VitalDB** (7): `vitaldb_SICU1_04_event_5` (299,4 h, 2 intentos),
    `vitaldb_SICU1_05_event_2` (450,1 h), `vitaldb_SICU1_09_event_4` (258,2 h,
    2 intentos), `vitaldb_SICU1_10_event_2` (449,0 h),
    `vitaldb_SICU1_11_event_3` (234,0 h), `vitaldb_SICU1_12_event_2` (450,1 h),
    `vitaldb_SICU2_08_event_4` (257,3 h, 3 intentos).

### 5.10 Casos que no encajan (regla 5)

- **Eventos de duración < 1 h**: Clínic 16 de 181 (p. ej. `clinic_box13_event_5`,
  0,9 min) y VitalDB 6 de 94 (`vitaldb_SICU2_16_event_2`, 0,3 min). Son
  episodios con actividad de ventilador muy breve tras fusionar huecos ≤ 2 h.
  **No se filtran** (D4: sin duración mínima), se reportan.
- **VitalDB**: 40 eventos ≥ 7 días que terminan en "extubación" son candidatos a
  traqueostomía no detectable; los 7 con `end_reason = extubation_observed` se
  listan arriba para revisión manual.
- **MIMIC**: 1 188 de 24 039 eventos duran < 1 h; hay eventos con hasta 70
  intentos; 4 filas de `PROCEDUREEVENTS_MV` (225792) con `endtime <= starttime`
  (descartadas y contadas en el índice como `n_invalid_vent_procedures`).
- **eICU**: 235 102 registros con `ventendoffset` posterior al alta (recortados),
  119 611 con inicio antes del ingreso (recortados a 0), 16 729 con duración
  imposible (> 60 días, descartados) y 4 249 de duración ≤ 0. Todos registrados
  como anomalías (`sanitize_vent_episodes`), ninguno silenciado.

### 5.11 VitalDB y las variables obligatorias (D7)

Cobertura de pistas medida sobre una muestra de 300 ficheros de origen (semilla
fija 0; 200 legibles, 100 sin pistas legibles / error de lectura):

| Pista | Cobertura |
|---|---|
| `Intellivue/ECG_HR` (HR) | 96 % |
| `Intellivue/PLETH_SAT_O2` (SpO2) | 96 % |
| `Intellivue/TV_EXP` (TV) | 49 % |
| `Intellivue/MV_EXP` (MV) | 49 % |
| `Intellivue/VENT_RR` (RR) | 49 % |
| `Intellivue/FLOW_WAV` | 39 % |
| `Intellivue/AWP_WAV` | 34 % |
| `Intellivue/FIO2` (FiO2) | 22 % |
| `Intellivue/PEEP_CMH2O` (PEEP) | 18 % |
| `Intellivue/PIP_CMH2O` (PIP) | 18 % |

**Conclusión: VitalDB NO cumple las variables obligatorias** (FiO2 22 %,
PEEP 18 %; MAP no invasiva muy rara). Sirve como **validación externa con un
modelo reducido** (HR, SpO2, RR, TV y, donde exista, FiO2/PEEP). Se documenta
sin estimar la parte ausente con tasas de otras cohortes. Además, ~1/3 de los
ficheros muestreados no fueron legibles con el parser (se registran y excluyen).

## 6. Limitaciones explícitas

- En Clínic/VitalDB la única frontera de paciente es el hueco de monitor > 1 h;
  eventos largos con monitor continuo (p. ej. la cama ocupada por otro
  paciente sin hueco de señal) **no se pueden separar** y quedan documentados.
- `obs_end_h` en Clínic/VitalDB es el fin del monitor de la región (no hay
  tabla de estancia); es la mejor cota disponible.
- La fusión de los `.vital` por evento (Fase 1.2) está implementada pero **no
  ejecutada** en este build (solo índice), por coste de reescritura; MIMIC sí
  guarda sus observaciones con hora (`mimic_observations.parquet`, 136 MB).
- En MIMIC la única frontera de paciente es el `ICUSTAY` (D2): dos estancias del
  mismo paciente son eventos distintos, y una reintubación tras un reingreso
  solo se contará como sensibilidad (pendiente).
- eICU no genera índice de casos propio en esta fase: sus cifras se calculan
  con las reglas compartidas sobre `respiratoryCare`/`patient` (el adaptador de
  la Etapa 0 sigue siendo la ruta de consumo).
- La variante de sensibilidad `exclude` de D5 (traqueostomía/extubación
  terminal) se calculará en la Fase 2 sobre `stays`/`labels`.

