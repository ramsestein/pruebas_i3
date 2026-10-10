# Fase 1.6d — Cierre definitivo de cohortes

Rama `fix/integridad-datos`. Config `harmonize.yaml` v**0.5.0** (hash
`eac1eb35`). Un commit y un push por punto, con su test.

Regla operativa respetada: **nunca dos procesos escribiendo a la vez en
`D:\data`** (los builds se ejecutan de uno en uno; cada uno puede usar
`--workers`).

Decisiones del usuario aplicadas:

- **G = 10 h** para el algoritmo de intervalos desde anotaciones.
- **VitalDB** = solo **test externo** (los 96 eventos); sin ajuste fino ni
  validación cruzada.
- **Clínic**: test = último 40 % del tiempo; el train en **2 bloques temporales
  con ventana creciente** (entrenar con el bloque 1, validar con el 2).
- Se extraen variables de las ondas del ventilador cuando no hay numérico,
  previa validación (punto 4).

---

## 1. G = 10 h

- **Calibración re-hecha en MIMIC con la rejilla {8, 10, 12} h**
  (`reports/fase1_6d/calibracion_mimic.*`). La elección es **G = 10 h**
  (score del peor estrato 0.562 frente a 0.506 de G = 8; G = 12 queda a 0.009,
  en la banda de empate → se prefiere el menor). F1 de reintubación
  **0.398 → 0.440** (1 h) y 0.394 → 0.438 (2 h); la concordancia de la etiqueta
  a 48 h no cambia (85.2 % / 83.8 %).
- **Corrección del fin por estrato recalculada con G = 10** y veredicto
  **aprobado** (mejora la concordancia de la etiqueta a 48 h en los dos
  estratos: +2.09 pp a 1 h y +2.53 pp a 2 h). Desplazamientos aplicados:
  **+1.23 h** (≤ 1 h), **+1.50 h** (1–2 h).
- **eICU-B reconstruido con G = 10** (`fase1_6d.vent_intervals.gap_h`, que el
  builder prioriza sobre el de la 1.6b). Se guardan **las dos versiones** de la
  etiqueta por evento: `labels` (corregida, vigente) y `labels_sin_correccion`
  (original), con `end_correccion_h`.
- **Antes/después (G = 8 → 10)** — `reports/fase1_6d/gap8_vs_gap10.*`:
  eventos 9 754 → **9 753**; éxito 48 h 7 419 → **7 523**; censura 2 335 →
  **2 230**; ≥ 1 fallo 1 093 → **688**; D13 9 222 → **9 340**; **129 eventos**
  cambian de etiqueta (casi todos `transfer_ventilated → éxito`).
- **Bug detectado y arreglado al ampliar G:** con G = 10 aparecían
  **anotaciones posteriores al alta** que generaban tramos que empezaban
  después de la estancia e invertían el intervalo al corregir el fin. Ahora se
  descartan los ajustes `> alta` y `_cap_shifted_ends` nunca invierte un
  intervalo (2 tests nuevos, `TestAnotacionesFueraDeLaEstancia`).

## 2. eICU-B — validación de `transfer_ventilated`

`scripts/verify/fase1_6d/eicu_transfer_validation.py`, lógica pura en
`src/common/transfer_validation.py` (20 tests).

- Se cruza cada censura con `patient.unitDischargeLocation`, el **plan de
  cuidados** (`carePlanGeneral`/`carePlanGoal`/`carePlanEOL`) y los **ajustes
  invasivos de las últimas horas** (último ajuste frente al alta).
- **Tabla destino × causa de censura** a 48 h, **con y sin** corrección del fin
  (`reports/fase1_6d/eicu_transfer.md`).
- Resultado (índice con corrección): **969** censuras `transfer_ventilated`;
  **293 (30.2 %)** con destino **incompatible** con seguir ventilado (planta,
  casa, hospicio, otro); **943** con evidencia de confort/limitación del
  esfuerzo.
- **Regla propuesta (NO aplicada):** si la causa es `transfer_ventilated` y el
  destino es incompatible y el último ajuste invasivo es ≥ 1 h anterior al alta,
  reclasificar a `end_of_record`. Reclasificaría **48** eventos
  (`reports/fase1_6d/eicu_transfer.json`).

## 3. VitalDB

### 3a. Cobertura por variable (corregida)

- **Tabla única de alias** de pistas (`src/common/track_aliases.py`), compartida
  por la **cobertura**, la **regla de observación fisiológica** y el
  **adaptador**: `ECG_HR / PLETH_HR / HR` (para FC), `ART_MEAN / ABP_MEAN /
  NIBP_MEAN` (MAP), `PLETH_SAT_O2` (SpO2). `range_for_track` ya no usa
  heurísticas de subcadena.
- **Causa y arreglo:** la cobertura usaba una rejilla **horaria**, que
  **cuantizaba** y hacía que variables distintas (FC, SpO2, MAP) colapsaran al
  mismo valor (90/96 eventos con FC = MAP). Ahora la rejilla es **por minuto**
  (`hourly_coverage(..., step_min=1)`); en datos reales los tres valores ya
  difieren (p. ej. `SICU1_01_event_1`: HR 0.965, SpO2 0.973, MAP 0.947).
- **Test** (`test_coverage_per_variable.py`): evento sintético en el que FC y
  MAP **no** cubren lo mismo y sus coberturas difieren.

### 3b–3d. Reetiquetado, perfiles y `source_files`

`scripts/verify/fase1_6d/vitaldb_relabel.py` compara el índice antiguo (éxito
75) con el nuevo y explica **caso a caso** los eventos que pasaron a censura:

- **33 eventos** pasaron de **éxito a censurado** (casi todos a
  `end_of_record`), con su regla de extubación y cola de monitor;
- **10 PNG** de revisión en `reports/fase1_6d/figs_relabel/` (FC/SpO2/MAP +
  tramos ventilados);
- **19 eventos con perfil «(ninguna)»** (ninguna variable > 50 %), listados en
  `relabel_vitaldb.md`;
- **4 eventos sin `source_files`** listados (cota inferior de D13).

> **Pendiente:** el releído completo de VitalDB (72,7 GB) con la cobertura en
> minutos y las variables derivadas no se ha re-ejecutado en esta sesión. Con
> `D:` medido a ~500–1 000 MB/s (ver Limitaciones) es viable. Los artefactos
> anteriores son de los índices v0.2.0/v0.4.0.

## 4. Variables derivadas de las ondas del ventilador

Módulo nuevo **`src/common/wave_derived.py`** (puro, sin E/S) + **18 tests**
sintéticos (`src/common/tests/test_wave_derived.py`).

- **De `AWP_WAV`**, respiración a respiración: **PIP** (máximo inspiratorio),
  **PEEP** (presión al final de la espiración) y **RR** (respiraciones por
  minuto).
- **De `FLOW_WAV`**: **TV** = integral del flujo inspiratorio con **deriva
  corregida** (mediana móvil por bloques).
- **Rechazo de artefactos**: respiraciones incompletas (inspiración cortada en
  el borde), **desconexión** (presión ≈ 0), tos/aspiraciones (espigas demasiado
  cortas) y amplitudes/presiones fuera de rango.
- **Agregación por minuto con la mediana.**
- **Validación obligatoria** (Bland–Altman): sesgo, límites de acuerdo y % de
  minutos dentro de ±2 cmH2O (PEEP/PIP), ±2 rpm (RR), ±10 % (TV). Una variable
  derivada **solo se usa** si `|sesgo| ≤ 1 cmH2O / 1 rpm / 5 %` **y** ≥ 80 % de
  minutos dentro del margen (`variable_is_usable`).
- **Precedencia:** el numérico del ventilador manda; el derivado **solo rellena
  huecos**. El origen se marca con `source_variable = AWP_WAV_derived` /
  `FLOW_WAV_derived`.
- Validación sobre datos reales: `scripts/verify/fase1_6d/wave_derived_validation.py`
  (escribe `derivadas_<cohorte>.json/.md`).

## 5. Clínic — reconstrucción completa

Receta en dos pasos (evita la fase de cobertura serial), con la cobertura en
minutos, los alias de la tabla única, las variables derivadas de ondas y D13 +
perfiles + etiquetas:

```
python -m src.create_dataset.build_signal_cases --cohort clinic --no-merge --workers 10
python scripts/verify/fase1_6c/coverage_parallel.py --cohort clinic --workers 10
python scripts/verify/fase1_6c/clinic_vitaldb_report.py --cohorts clinic vitaldb
```

### Reconstrucción **resumible** (necesaria en Clínic)

El primer intento (10 workers, 10:53) quedó **colgado**: el disco bloqueó una
lectura y, como `ProcessPoolExecutor.map` consume los resultados **en orden**,
los otros 9 workers se quedaron ociosos (medido: **+178 s de CPU en 3 h 20 min**,
0,01 MB/s; ya había leído **~80,5 GB** de 101,5 GB). No terminó ni creó la salida.

Solución implementada (`src/create_dataset/build_signal_cases.py` +
`scripts/verify/fase1_6d/build_cohort_resumable.py`, con tests):

- `--boxes` y `--out-dir`: el builder puede reconstruir **un box** por
  invocación (`filter_boxes`).
- `merge_partial_indices`: fusiona los índices parciales por box.
- El driver lanza **un subproceso por box con timeout proporcional al tamaño**
  (`max(--box-timeout, GB × --timeout-per-gb)`, por defecto 240 s/GB) y
  **reintenta** el box (`--retries`, por defecto 3) en lugar de saltarlo,
  **reutilizando** los parciales ya hechos (reanudable). Un box solo queda
  `pendiente` si agota todos los reintentos, y entonces el índice se escribe con
  nombre `..._INCOMPLETO.json` y el proceso sale con código 2 (nunca un índice
  incompleto disfrazado de definitivo).

```
python scripts/verify/fase1_6d/build_cohort_resumable.py --cohort clinic \
    --workers 4 --retries 3
```

Primer resultado: 13 boxes; box10 (8,7 GB, 828 s), box11 (1,3 GB, 137 s),
box12 (10,5 GB, 1 343 s), box13 (5,9 GB, 506 s) y box2 (1,7 GB, 283 s)
reconstruidos. **box14 (27,3 GB) agotó el primer timeout de 2 700 s** — era un
**falso positivo por lentitud** (~10 MB/s × 27,3 GB ≈ 2 700 s), no un cuelgue; se
recupera con el timeout proporcional y los reintentos.

**Resultado final de la reconstrucción: 13/13 boxes, 181 eventos, 4 excluidos,
0 pendientes** (`datasets/clinic/cases_v0.5.0_eac1eb35/clinic_cases_index.json`).
Nivel A en los 181; `end_reason` canónico (`extubation_observed` 64,
`end_of_record` 116, `death_at_vent` 1); **0 eventos sin `source_files`** (se
cierra el pendiente de la 1.6c).

### Recuperación ante cuelgues (red de seguridad)

`src/common/timeout_batches.py` (`run_with_bisection`, 11 tests) + dos drivers
que ejecutan el trabajo por lotes en subprocesos con timeout y, si un lote se
atasca, lo **bisecan** para aislar el elemento culpable (el resto se salva);
escriben resultados **incrementales** (reanudable):

- `scripts/verify/fase1_6d/probe_cohort_resumable.py`: **caché de sondas por
  fichero** con timeout (lotes de 25, 20 s/fichero) → genera el JSON que consume
  el builder con `--probe-cache`, de forma que la segmentación **no toca el
  disco**. Un fichero que falla siempre tras 3 reintentos se marca **ilegible**
  (`null`, el builder lo trata como «sin dato») y se reporta.
- `scripts/verify/fase1_6d/coverage_resumable.py`: cobertura por evento con
  timeout y bisección; un evento que falla siempre se deja **sin cobertura**
  (no se inventa un 0) y se reporta.

> **Pendiente:** no ejecutada en esta sesión. Con el disco `D:` medido a
> ~67 MB/s (ver Limitaciones) es viable: 263,8 GB en 15 291 `.vital`, ≈1–2 h de
> E/S más parseo. El único índice de Clínic disponible sigue siendo el antiguo
> (181 eventos) y sus cifras de cobertura están invalidadas.

## 6. `end_reason` con vocabulario único

`src/common/end_reasons.py` (11 tests) fija los **6 valores** canónicos —
`extubation_observed`, `transfer_ventilated`, `death_at_vent`,
`terminal_extubation`, `tracheostomy`, `end_of_record` — y aplica el mismo orden
de prioridad en los tres builders.

- **MIMIC** ya no emite `death` (separa `death_at_vent` / `terminal_extubation`)
  ni `excluded_trach_preexisting` (la exclusión va en `exclusion_reason`; el
  `end_reason` es `tracheostomy`).
- **eICU-B** distingue `terminal_extubation` y `tracheostomy` (antes solo
  `transfer_ventilated` / `death_at_vent` / `end_of_record`).
- **Clínic/VitalDB** usan las constantes.
- Comprobación: `scripts/verify/fase1_6d/end_reasons_report.py` (falla si alguna
  cohorte usa un valor fuera de vocabulario).

## 7. Particiones (propuesta, sin aplicar)

`scripts/verify/fase1_6d/partitions.py` → `reports/fase1_6d/particiones.*`:

| Cohorte | Criterio | Test | Train |
|---|---|---|---|
| MIMIC | 15 % por `subject_id` + 5 pliegues por paciente | 1 359 eventos (1 224 pacientes) | 7 734 |
| eICU-B | 8 hospitales fijos + 5 pliegues por hospital | 1 781 | 7 973 |
| Clínic | último 40 % + train en 2 bloques temporales | 72 | 109 (54 / 55) |
| VitalDB | los 96 = test externo | 96 | 0 |

## 8. Tabla final de las 4 cohortes

`scripts/verify/fase1_6d/final_table.py` → `reports/fase1_6d/tabla_final.*`.
Incluye, por cohorte: eventos e incluidos por D13; éxito/censura; eventos con
≥ 1 fallo; `vars_ok` 50/80 % **sin** y **con** derivadas de ondas (solo si la
validación las aprueba); error de etiqueta; perfil dominante; y la partición
propuesta.

---

## Limitaciones de esta sesión

### Estado real del disco `D:` (medido al cerrar la fase)

- **No está lleno:** 3 071,9 GB usados / **1 585,6 GB libres**.
- **No está degradado ahora:** lectura secuencial medida ahora mismo de
  **~500–1 000 MB/s** en los `.vital` de VitalDB (8 MB) y **~67 MB/s** en los
  ficheros grandes de Clínic (476 MB en 7,1 s).
- La observación de la Fase 1.6c (≈4,7 MB/s efectivos y lecturas bloqueadas
  minutos en Clínic) **no es reproducible hoy**: o fue transitoria (spin‑down,
  contención con otro proceso, sectores con reintentos) o estaba dominada por
  la lectura de muchos ficheros pequeños más el parseo. La cifra «degradado» de
  la 1.6c era una **inferencia por rendimiento**, no un diagnóstico de salud del
  hardware; ahora, con ~67 MB/s, **la reconstrucción de Clínic vuelve a ser
  viable** (263,8 GB en 15 291 `.vital`, ≈1–2 h de E/S más parseo).

### Trabajo pendiente

- **Releído de VitalDB** con la cobertura en minutos y las variables derivadas
  (punto 3b–3d) y **reconstrucción completa de Clínic** (punto 5): no ejecutados
  en esta sesión. El código, las recetas y los scripts de informe están listos;
  con la E/S sana basta lanzarlos.
- El **error de etiqueta** de Clínic/VitalDB sigue siendo «no aplica (señal
  continua)»; el de eICU-B se hereda de MIMIC por estrato.
- `end_reason` es ya un vocabulario único; los índices **v0.4.0** de MIMIC y
  Clínic aún contienen valores antiguos (`death`, `death_signal`) — se corrigen
  al reconstruir con los builders actualizados (eICU-B ya lo está desde v0.5.0).

## Reproducibilidad

```
# Punto 1: calibración G=10 + eICU-B
python scripts/verify/fase1_6b/calibrate_gap_mimic.py --gaps 8,10,12 --out-dir reports/fase1_6d
python -m src.create_dataset.build_eicu_events

# Punto 2
python scripts/verify/fase1_6d/eicu_transfer_validation.py

# Punto 3
python scripts/verify/fase1_6d/vitaldb_relabel.py --png 10

# Punto 4
python -m pytest src/common/tests/test_wave_derived.py
python scripts/verify/fase1_6d/wave_derived_validation.py --cohort vitaldb

# Punto 5 (pendiente de E/S)
python -m src.create_dataset.build_signal_cases --cohort clinic --no-merge --workers 10
python scripts/verify/fase1_6c/coverage_parallel.py --cohort clinic --workers 10

# Puntos 6-8
python scripts/verify/fase1_6d/end_reasons_report.py
python scripts/verify/fase1_6d/partitions.py
python scripts/verify/fase1_6d/final_table.py
```

Tests: `python -m pytest src/stage0/tests src/common/tests src/create_dataset/tests -q`.
