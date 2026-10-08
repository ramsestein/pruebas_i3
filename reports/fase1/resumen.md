# Fase 1 — Informe

> **Estado:** Fase 1 completada. Los seis puntos de código están implementados con
> sus tests, se aplicaron las seis correcciones previas a los builds, los cinco
> ajustes finales de la revisión `23731e5..2480a5a`, y los cuatro builds se han
> **re-ejecutado** sobre los datos crudos (Clínic, VitalDB, MIMIC y eICU) con los
> ajustes aplicados. Todas las cifras de este informe provienen de ese último
> build (versión `v0.1.0_a225d21b`); no se reutiliza ninguna salida anterior.

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

Total: **234 tests en verde, 1 saltado** (el que valida itemids contra
`datasets/mimic3wdb/clinical/D_ITEMS.csv.gz`, no presente; el de `D:/data`
sí se ejecuta y pasa).

Comando: `python -m pytest src/stage0/tests src/common/tests src/create_dataset/tests -q`

## 1.ter Ajustes finales de la Fase 1

Tras la revisión de `23731e5..2480a5a` se aplicaron cinco ajustes; cada uno con
su commit, su test (que falla antes y pasa después) y su control negativo:

| Ajuste | Commit | Qué cambia |
|---|---|---|
| 1. MIMIC solo MetaVision | `d7dabb2` | La cohorte pasa a ser las estancias con `PROCEDUREEVENTS_MV` (**23 401** de las 60 204 con VM; **36 803 estancias excluidas por ser CareVue**, reportadas en el índice). D1 se calcula sobre los intervalos explícitos 225792 (se fusionan los huecos ≤ 2 h) y la extubación (227194/225468/225477) confirma el final; CHARTEVENTS ya **no** crea ni parte tramos, solo aporta variables. QC nuevo: `n_carevue_stays_excluded`, `n_intervals_without_adjustments`, `n_adjustments_outside_intervals`, `n_invalid_vent_procedures` |
| 2. Muerte por señales | `12db6c7` | `src/common/signal_death.py` (función pura + tests): FC ≤ 5 bpm mantenida ≥ 10 min **hasta el final del monitor** en Clínic/VitalDB, más (deterioro en los 30 min previos o pérdida de pulsatilidad ABP/PPG) o (pérdida simultánea de pulsatilidad) ⇒ muerte; una caída brusca con el resto de canales normales es **desconexión**. La hora detectada se usa como `DEATHTIME`, por el mismo camino que MIMIC/eICU (`d5_censor_for_window`). Un PNG por muerte detectada en `reports/fase1/muertes_senal/` |
| 3. Traqueostomía previa a t0 | `f9f39a1` | Si la traqueostomía es **anterior a t0** el evento se excluye de inicio (`excluded`, `exclusion_reason = trach_preexisting`) en MIMIC (225448/226237, tipo de vía aérea, ICD-9 31.1/31.2x) y eICU (`airwaytype`). Una traqueostomía **durante** el episodio sigue siendo censura |
| 4. Informe | `5bfcc1c` | Recuento de tests y estado del informe |
| 5. Builds + informe completo | este commit | Los cuatro builds re-ejecutados con los ajustes 1–3 y este informe cerrado con las cifras nuevas |

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
python -m src.create_dataset.build_mimic_cases
python -m src.create_dataset.build_signal_cases --cohort clinic  --no-merge --workers 16
python -m src.create_dataset.build_signal_cases --cohort vitaldb --no-merge --workers 16
python scripts/verify/fase1/summarize.py        # tablas desde los índices
python scripts/verify/fase1/summarize_eicu.py   # tablas de eICU (reglas compartidas)
python scripts/verify/fase1/timelines.py --cohort <cohort>
python scripts/verify/fase1/plot_deaths.py --cohort <clinic|vitaldb>
python scripts/verify/fase1/review_attempts.py --review eicu  # control 2 % / 30 %
```

> Nota: los builds con señal se han ejecutado con `--no-merge` (índice de casos;
> la fusión de los `.vital` de cada evento queda para cuando se necesite el
> fichero, ya que reescribir 137 GB de señal no aporta a las etiquetas).
> Se añadió `--workers` (paralelización por box) porque en serie el build de
> Clínic+VitalDB tardaba ~15 h (un solo box de Clínic: 32,5 min).
> El build de MIMIC reutiliza `mimic_observations.parquet` (caché de CHARTEVENTS
> con hora, `--force` para reprocesar); Clínic/VitalDB se re-ejecutaron desde
> cero en la carpeta nueva de versión (las salidas antiguas no se reutilizan).

### 5.1 Eventos antes y después

| Cohorte | Antes (build previo) | Después | Excluidos | Motivos de exclusión |
|---|---|---|---|---|
| Clínic | 181 | **181** | 4 | `ventilator_without_patient`: 4 |
| VitalDB | 94 | **94** | 20 | `ventilator_without_patient`: 20 |
| MIMIC | 24 039 (de 60 202 estancias con VM) | **9 093** (de **23 401** estancias MetaVision) | 25 | `ventilator_without_patient`: 16; **`trach_preexisting`: 9** |
| eICU | 44 042 estancias con VM | **43 987** estancias con VM | 55 | **`trach_preexisting`: 55** |

El cambio de MIMIC (24 039 → 9 093) es el efecto del ajuste 1: la cohorte se
limita a MetaVision y D1 se calcula sobre los intervalos 225792 en vez de sobre
los marcadores de CHARTEVENTS (ver 5.12).

### 5.2 Intentos por evento y eventos con ≥ 1 fallo

Intentos por evento (todas las ventanas comparten el mismo conteo):

- Clínic: `{1: 172, 2: 6, 3: 2, 4: 1}` → máximo 4 intentos.
- VitalDB: `{1: 81, 2: 10, 3: 2, 4: 1}` → máximo 4 intentos.
- MIMIC: `{1: 8 355, 2: 593, 3: 111, 4: 23, 5: 8, 6: 2, 7: 1}` → máximo 7.
- eICU: `{1: 43 628, 2: 340, 3: 10, 4: 3, 5: 2, 7: 2, 8: 1, 12: 1}` → máximo 12.

Control obligatorio del informe (eventos con > 1 intento ⇒ al menos un fallo),
calculado con `scripts/verify/fase1/review_attempts.py`:

| Cohorte | Eventos | Con ≥ 1 fallo | % | Veredicto (> 30 % o < 2 %) |
|---|---|---|---|---|
| Clínic | 181 | 9 | 4,97 % | ok |
| VitalDB | 94 | 13 | 13,83 % | ok |
| MIMIC | 9 093 | 738 | 8,12 % | ok |
| eICU | 44 042 | 359 | **0,82 %** | **< 2 % ⇒ posible artefacto de segmentación** |

> Nota: en eICU el control se calcula sobre las 44 042 estancias que superan la
> limpieza (`sanitize_vent_episodes`), antes de descontar las 55 con
> traqueostomía previa; con 43 987 el porcentaje sigue siendo 0,82 %.

**Revisión manual de 10 casos de eICU** (los de más intentos). Los 10 tienen
episodios de VM separados por huecos de 3,5 h a 1 058 h (mediana 33 h), es decir
reintubaciones y **nuevas** ventilaciones mezcladas dentro de la misma estancia:

| # | stay | intentos (h desde t0) | huecos (h) | alta (h) | estado |
|---|---|---|---|---|---|
| 1 | 234178 | `[0, 7,4] → [192, 202] → [207, 210] → [471, 480] → [679, 685] → [1 069, 1 074] → [1 124, 1 129] → [1 208, 1 213] → [1 616, 1 634] → [1 721, 1 726] → [1 769, 1 774] → [1 816, 1 876]` | 184,9 · 4,2 · 260,7 · 198,3 · 384,5 · 49,5 · 79,0 · 403,0 · 86,9 · 43,2 · 42,0 | 2 656 | alive |
| 2 | 199499 | `[0, 16,6] → [659, 664] → [841, 846] → [1 000, 1 006] → [1 021, 1 086] → [1 464, 1 469] → [1 752, 1 757] → [1 960, 1 965]` | 642,0 · 177,9 · 154,0 · 15,1 · 378,1 · 283,5 · 203,0 | 5 926 | expired |
| 3 | 2718272 | `[0, 5,2] → [8,7, 20,2] → [308, 325] → [444, 456] → [1 514, 1 607] → [1 820, 1 838] → [2 385, 2 395]` | 3,5 · 287,6 · 118,9 · 1 058,1 · 212,4 · 546,7 | 2 603 | alive (`Tracheostomy`) |
| 4 | 3350978 | `[0, 23,6] → [694, 698] → [718, 722] → [745, 749] → [815, 819] → [929, 933] → [991, 995]` | 670,2 · 20,2 · 22,8 · 65,9 · 110,0 · 58,5 | 1 466 | expired (`Tracheostomy`) |
| 5 | 183752 | `[0, 5,0] → [515, 520] → [1 198, 1 203] → [1 519, 1 524] → [2 098, 2 140]` | 509,6 · 678,5 · 315,8 · 573,7 | 2 307 | alive |
| 6 | 1125506 | `[0, 160,7] → [165,9, 169,9] → [257,6, 261,6] → [285,1, 289,1] → [493,6, 497,6]` | 5,2 · 87,7 · 23,4 · 204,5 | 547 | alive (`Tracheostomy`) |
| 7 | 221190 | `[0, 304,4] → [397, 402] → [597, 602] → [645, 650]` | 92,8 · 194,3 · 43,5 | 662 | alive |
| 8 | 230218 | `[0, 797,5] → [869, 874] → [1 074, 1 082] → [1 120, 1 125]` | 71,6 · 199,8 · 38,1 | 1 159 | alive |
| 9 | 1568216 | `[0, 995] → [998, 1 066] → [1 099, 1 159] → [1 310, 1 367]` | 3,5 · 32,6 · 151,2 | 1 367 | alive |
| 10 | 209311 | `[0, 335] → [931, 936] → [955, 960]` | 596,3 · 19,0 | 1 080 | alive |

Huecos entre episodios de VM en eICU: n = 407, de los cuales **252 caen dentro
de las 48 h** posteriores a la desconexión previa (reintubación) y 155 fuera
(episodios distintos). **Conclusión:** la tasa de 0,82 % **no** es un error de
código sino un artefacto de documentación de `respiratoryCare`: en eICU la
mayoría de las estancias tienen **un único** episodio de VM (43 683 de 44 042,
el 99,2 %) que se documenta hasta el alta (235 102 registros con `ventendoffset`
posterior al alta), de modo que las reintubaciones dentro de la misma estancia no
siempre generan un episodio nuevo. Por eso **no se comparan** las tasas de
reintubación de eICU con las de Clínic/VitalDB/MIMIC. Ninguna cohorte supera el
30 % (no hay fragmentación excesiva).

### 5.3 Éxito / censura a 48 h y 72 h (desglose por causa)

| Cohorte | Ventana | Éxito | Censura | Causas de la censura |
|---|---|---|---|---|
| Clínic | 48 h | 140 (77,3 %) | 41 | `end_of_record`: 40; `terminal_extubation`: 1 |
| Clínic | 72 h | 140 (77,3 %) | 41 | `end_of_record`: 40; `terminal_extubation`: 1 |
| VitalDB | 48 h | 73 (77,7 %) | 21 | `end_of_record`: 21 |
| VitalDB | 72 h | 72 (76,6 %) | 22 | `end_of_record`: 22 |
| MIMIC | 48 h | 7 905 (86,9 %) | 1 188 | `death_at_vent`: 550; `terminal_extubation`: 412; `trach`: 226 |
| MIMIC | 72 h | 7 846 (86,3 %) | 1 247 | `death_at_vent`: 560; `terminal_extubation`: 454; `trach`: 233 |
| eICU | 48 h | 37 414 (85,1 %) | 6 573 | `terminal_extubation`: 4 547; `death_at_vent`: 1 074; `trach`: 952 |
| eICU | 72 h | 37 249 (84,7 %) | 6 738 | `terminal_extubation`: 4 710; `death_at_vent`: 1 074; `trach`: 954 |

Intentos fallidos por evento contados **por etiqueta** (≥ 1 fallo hasta la
ventana): Clínic 7 (48 h) / 8 (72 h); VitalDB 5 / 7; MIMIC 471 / 563 (el
recuento de 5.2 es el del evento completo, por eso es mayor en MIMIC).

En VitalDB ya **no** hay censuras `terminal_extubation`: con el ajuste 2 la
pérdida de señal al final no es una muerte (ver 5.7 y 5.13).

### 5.4 Traqueostomías, extubaciones terminales y traqueostomía previa

- **eICU**: `trach` 952 (48 h) / 954 (72 h) eventos censurados;
  `terminal_extubation` 4 547 (48 h) / 4 710 (72 h); `death_at_vent` 1 074
  (idéntico en ambas ventanas, como corresponde a una muerte estando ventilado).
  Además, **55 estancias** con traqueostomía **previa a t0** quedan excluidas de
  inicio (`trach_preexisting`, ajuste 3).
- **MIMIC**: `trach` 226 (48 h) / 233 (72 h); `terminal_extubation` 412 (48 h) /
  454 (72 h); `death_at_vent` 550 (48 h) / 560 (72 h). Traqueostomías detectadas
  por `PROCEDUREEVENTS_MV` (225448/226237) y por tipo de vía aérea; por ICD-9 sin
  hora: **0** (todas tuvieron hora en las tablas con tiempo). `end_reason`:
  `tracheostomy` 310, `death` 1 297. **9 eventos** con traqueostomía previa a t0
  quedan excluidos (`trach_preexisting`).
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
| MIMIC | 0,0 | 9,2 | 24,9 | 96,0 | 2 425,4 |

### 5.6 Desconexión → muerte (eICU/MIMIC)

- **eICU**: n = 5 190 muertes no ventiladas tras una desconexión →
  min 0,02 h | Q1 1,48 h | **mediana 4,17 h** | Q3 12,73 h | máx 3 960,65 h.
  De ellas, 4 547 caen dentro de las 48 h posteriores a la desconexión.
- **MIMIC**: 985 muertes no ventiladas tras una desconexión (de los 1 594
  eventos con `DEATHTIME`) → min 0,0 h | Q1 0,9 h | mediana 12,4 h |
  Q3 91,8 h | máx 2 928,9 h. De ellas, **662 caen dentro de las 48 h**; las que
  además ocurren antes de un éxito consolidado son 412 (censura
  `terminal_extubation`).

### 5.7 Apagado simultáneo VM+monitor y muertes por señal (Clínic/VitalDB)

- Apagado simultáneo de ventilador y monitor (≤ 15 min): Clínic **40** eventos,
  VitalDB **23** eventos.
- Pérdida de constantes al final del registro (`signal_loss_at_end`, QC):
  Clínic **0**, VitalDB **26**. Desde el ajuste 2 la pérdida de señal **no** se
  interpreta como extubación terminal ni como muerte salvo que el detector la
  confirme (ver 5.13).
- **Muertes detectadas por señales** (`src/common/signal_death.py`): Clínic **1**
  (`clinic_box9_event_7`, a 2,75 h; FC 0 mantenida 25,3 min hasta el final del
  monitor + `bradicardia`, `desaturacion` y `map_baja` en los 30 min previos;
  la muerte ocurre **tras** la desconexión a 2,59 h ⇒ censura
  `terminal_extubation`, la única de la cohorte), VitalDB **0**.
- Motivos por los que **no** se declara muerte (control negativo del detector):
  - Clínic: `hr_not_zero_at_end` 166, `zero_run_too_short` 8, `no_files` 6,
    `deterioration` (muerte) 1.
  - VitalDB: `hr_not_zero_at_end` 66, `no_hr` 23, `no_files` 4,
    `zero_run_too_short` 1.
- Un PNG por cada muerte detectada en `reports/fase1/muertes_senal/`
  (`clinic_box9_event_7.png`, con FC, SpO2, MAP, pulsatilidad ABP/PPG e intentos).

### 5.8 Eventos excluidos (actividad sin paciente D4 + traqueostomía previa)

- Clínic: 4 (todos `ventilator_without_patient`).
- VitalDB: 20 (todos `ventilator_without_patient`).
- eICU: 55 (todos `trach_preexisting`); 0 estancias descartadas por actividad sin
  paciente con el criterio usado (HR/SpO2 disponibles en `vitalPeriodic`).
- MIMIC: 25 → 16 `ventilator_without_patient` y 9 `trach_preexisting`.

Todos los motivos se registran en `excluded_events[].exclusion_reason`; ninguno
se descarta en silencio.

### 5.9 Revisión clínica (PNG, semilla fija 20261002)

Generados con `scripts/verify/fase1/timelines.py` en
`reports/fase1/figs/<cohorte>/` (ficheros `.png`; están en `.gitignore`, se
generan en local):

- 10 eventos al azar por cohorte: Clínic 12 PNG (10 + 2 largos), VitalDB 15 PNG
  (10 + 7 largos, 2 de ellos ya caían en el azar) y MIMIC 10 PNG. eICU no genera
  PNG (no tiene índice de casos: su ruta es el adaptador de la Etapa 0).
- Muertes detectadas por señales: 1 PNG en `reports/fase1/muertes_senal/`
  (`clinic_box9_event_7.png`; VitalDB tiene 0 muertes detectadas, por eso no
  existe `signal_deaths.json` en esa cohorte).
- **Eventos ≥ 7 días** (todos, en Clínic/VitalDB):
  - **Clínic** (5): `clinic_box12_event_2` (260,6 h, extubación),
    `clinic_box12_event_18` (183,5 h, fin de registro), `clinic_box6_event_11`
    (207,0 h, fin de registro), `clinic_box6_event_12` (201,6 h, 2 intentos,
    extubación), `clinic_box6_event_13` (405,0 h, 2 intentos, fin de registro).
  - **VitalDB** (15). Terminan en **extubación** (7, los que se revisan como
    candidatos a traqueostomía no detectable): `vitaldb_SICU1_04_event_5`
    (299,4 h, 2 intentos), `vitaldb_SICU1_05_event_2` (450,1 h),
    `vitaldb_SICU1_09_event_4` (258,2 h, 2 intentos), `vitaldb_SICU1_10_event_2`
    (449,0 h), `vitaldb_SICU1_11_event_3` (234,0 h), `vitaldb_SICU1_12_event_2`
    (450,1 h), `vitaldb_SICU2_08_event_4` (257,3 h, 3 intentos).
    Terminan en muerte/traslado (4): `vitaldb_SICU1_07_event_2` (264,6 h),
    `vitaldb_SICU1_08_event_3` (208,5 h), `vitaldb_SICU1_09_event_3`
    (191,8 h), `vitaldb_SICU2_12_event_3` (174,9 h). Terminan en fin de registro
    (4): `vitaldb_SICU1_06_event_2` (179,9 h), `vitaldb_SICU2_09_event_1`
    (236,1 h), `vitaldb_SICU2_12_event_2` (251,5 h), `vitaldb_SICU2_14_event_2`
    (225,6 h).

### 5.10 Casos que no encajan (regla 5)

- **Eventos de duración < 1 h**: Clínic 16 de 181 (p. ej. `clinic_box13_event_5`,
  0,9 min), VitalDB 6 de 94 (`vitaldb_SICU1_03_event_1`, 20,2 min) y MIMIC 76 de
  9 093. Son episodios con actividad de ventilador muy breve tras fusionar huecos
  ≤ 2 h. **No se filtran** (D4: sin duración mínima), se reportan.
- **VitalDB**: 15 eventos ≥ 7 días; los 7 que terminan en `extubation_observed`
  son candidatos a traqueostomía no detectable y se listan en 5.9 para revisión
  manual.
- **MIMIC**: 1 594 eventos llevan hora de muerte del hospital (`DEATHTIME`); el
  máximo de intentos es 7 (antes 70, con la cohorte no restringida a
  MetaVision); 4 filas de `PROCEDUREEVENTS_MV` (225792) con `endtime <= starttime`
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

### 5.12 MIMIC: estancias excluidas por CareVue, eventos tras la fusión D1 y QC de los intervalos 225792

- **Estancias excluidas por CareVue: 36 803.** Son estancias con ajustes de
  ventilador anotados en CHARTEVENTS cuyos itemids son de CareVue (5xx/6xx/3xxx)
  y que por tanto **no** tienen filas en `PROCEDUREEVENTS_MV`. La cohorte del
  pipeline es MetaVision: **23 401** estancias de las 60 204 con VM (38,9 %).
- **Eventos tras la fusión D1: 9 093** (antes 24 039 al incluir también CareVue y
  segmentar con CHARTEVENTS). Cada evento toma los intervalos 225792 de su
  estancia y fusiona los huecos ≤ 2 h; un registro de extubación
  (227194/225468/225477) que cae dentro de un intervalo recorta su final.
  `t0_source`: `vent_start_observed` 8 677, `already_ventilated_at_icu_admission`
  416. `end_reason`: `extubation_observed` 7 249, `death` 1 297,
  `tracheostomy` 310, `end_of_icu_stay` 237.
- **QC de los intervalos 225792 frente a los ajustes anotados en CHARTEVENTS:**

| Métrica | Valor | Interpretación |
|---|---|---|
| `n_intervals_without_adjustments` | 168 | intervalos sin ningún marcador de VM dentro (sin constancia en CHARTEVENTS); se conservan porque 225792 es la fuente D1 |
| `n_adjustments_outside_intervals` | 335 296 de 3 479 624 marcadores (9,6 %) | ajustes fuera de todo intervalo: huecos > 2 h con el paciente aún monitorizado (NIV/HFNC o desconexión no documentada) y ajustes previos al primer intervalo |
| `n_invalid_vent_procedures` | 4 | filas de 225792 con `endtime <= starttime` (descartadas) |
| `n_trach_time_unknown` | 0 | traqueostomías ICD-9 31.1/31.2x sin hora (censurarán en el último fin de VM) |

### 5.13 Muertes detectadas por cohorte y por fuente

| Cohorte | Fuente | Muertes | Uso en D5 (ventana 48 h) |
|---|---|---|---|
| Clínic | señal (`signal_death.py`) | 1 | `terminal_extubation`: 1 |
| Clínic | tabla clínica | 0 | — |
| VitalDB | señal | 0 | — |
| VitalDB | tabla clínica | 0 | — |
| MIMIC | tabla (`DEATHTIME`) | 1 594 eventos con hora de muerte (`end_reason = death`: 1 297) | `death_at_vent`: 550; `terminal_extubation`: 412 |
| eICU | tabla (`unitdischargestatus = expired`) | 6 279 estancias | `death_at_vent`: 1 074; `terminal_extubation`: 4 547 |

En Clínic/VitalDB **no existe tabla de defunción**: la única fuente posible son
las señales. En VitalDB el detector no confirma ninguna muerte porque en los 23
eventos que terminan con pérdida de señal los ficheros finales **no tienen canal
de FC** (`no_hr`); se comprobó en los ficheros de origen que sus ondas de
pulsatilidad (`Intellivue/PLETH` e `Intellivue/ABP`) están **planas** (amplitud
0,00), es decir el monitor seguía grabando con el paciente desconectado: son
desconexiones, no muertes. Antes del ajuste 2 esos casos se censuraban como
`terminal_extubation` (12–13 eventos en el build anterior); ahora no.

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
- **MIMIC**: la cohorte se restringe a MetaVision (36 803 estancias CareVue
  fuera), porque solo esas tienen intervalos explícitos 225792 con los que
  aplicar D1 sin depender de CHARTEVENTS. El 9,6 % de los marcadores de VM cae
  fuera de los intervalos (huecos > 2 h, NIV/HFNC); se reporta y no se fuerza.
- **eICU**: la tasa de reintubación está infra-documentada (0,82 % de eventos con
  > 1 episodio de VM frente al 4,97 % de Clínic, el 8,12 % de MIMIC y el 13,83 %
  de VitalDB). Es un artefacto de `respiratoryCare` (99,2 % de las estancias con
  un único episodio, muchos de ellos con `ventendoffset` posterior al alta), por
  lo que **sus tasas de fallo no se comparan** con las de las otras cohortes.
  Revisados 10 casos a mano (5.2).
- **VitalDB (muerte)**: en 23 eventos el registro termina con pérdida de señal y
  los ficheros finales no tienen canal de FC, así que la muerte **no se puede
  confirmar con la regla acordada** (FC 0 ≥ 10 min). Se documenta como límite y
  **no** se infiere muerte por la desaparición del monitor.

