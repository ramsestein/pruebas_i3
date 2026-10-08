# Fase 0b — Verificación honesta (solo lectura)

> Este informe se genera exclusivamente a partir de datos, sin modificar `datasets/`. Las comprobaciones con `passed` tienen controles negativos demostrados en `scripts/verify/fase0b/tests/test_checks.py`.

## Hallazgos clave

1. **Fusión ≠ origen.** En VitalDB, 80/83 ficheros fusionados no cubren el tramo de origen dentro del 1 % (el merge descarta ficheros separados >7 días; ej. `SICU1_01_event_1` fusiona 69 h de un tramo de 746 h). En Clínic, 47/50 fuera de tolerancia: la cabecera fusionada se extiende más allá del tramo declarado en el nombre de fichero (ej. `box14_event_1`: cabecera 89.8 h vs tramo 42.8 h).
2. **MIMIC sin monitor.** Las pistas HR/SpO2/RESP/ABP existen pero con 0 registros (se vacían ya en `mimic_full_cases`); `datasets/mimic3wdb/raw/` no existe y no hay `.parquet`. El RR de la config apunta a `MIMIC/RESP` (vacío) cuando el dato está en `MIMIC/RR_V`.
3. **Etiquetas engañosas.** `event_type` nunca toma `failure`: los fallos de MIMIC están en `n_failed_attempts` (33/82 a 48 h) y en `extubation_attempts` (169 fallos). `censored_no_extubation` solo cubre muerte en vent_end; las demás reglas de D3 no están implementadas.
4. **VitalDB pierde el ventilador.** `MERGE_TRACK_NAMES` descarta 6 de 8 pistas de ventilador (VENT_RR, FIO2, PEEP_CMH2O, PIP_CMH2O, FLOW_WAV, AWP_WAV).
5. **eICU sin regenerar.** 59 reintubaciones en 48-72 h (1182 ≤48 h) que el adaptador clasifica mal por su corte fijo `gap <= 48.0`.

---

## 1. Completitud real por caso

Duración fusionada (cabecera del fichero) frente al tramo cubierto por los ficheros de origen (tolerancia 1 %).

### Clínic

- Casos: **50** | fusión fuera de tolerancia: **47** | dentro: **3**
- Error relativo de la duración: mediana 0.182, máx 11.356

| Evento | Dur. fusionada (h) | Tramo origen (h) | Error rel. | Omitidos |
| box14_event_1 | 89.8 | 42.8 | 1.097 | 0 |
| box14_event_2 | 116.0 | 110.9 | 0.047 | 0 |
| box14_event_3 | 165.8 | 156.9 | 0.056 | 0 |
| box14_event_4 | 133.5 | 134.4 | 0.007 | 4 |
| box14_event_5 | 17.1 | 11.5 | 0.488 | 0 |
| box14_event_6 | 7.3 | 8.4 | 0.132 | 4 |
| box14_event_7 | 73.1 | 49.6 | 0.473 | 0 |
| box14_event_8 | 47.7 | 11.6 | 3.105 | 0 |
| box14_event_9 | 78.7 | 74.9 | 0.050 | 0 |
| box13_event_1 | 23.3 | 24.0 | 0.026 | 0 |
| box13_event_2 | 65.5 | 66.9 | 0.021 | 2 |
| box13_event_3 | 28.8 | 27.0 | 0.065 | 0 |
| box4_event_1 | 35.3 | 36.0 | 0.020 | 1 |
| box4_event_2 | 30.3 | 28.6 | 0.061 | 0 |
| box4_event_3 | 62.2 | 34.1 | 0.825 | 0 |

*Ficheros de origen dentro del tramo del evento que no están reflejados en la fusión (`n_omitted`).*

### VitalDB

- Casos: **83** | fusión fuera de tolerancia: **80** | dentro: **3**
- Error relativo de la duración: mediana 0.335, máx 0.999

| Evento | Dur. fusionada (h) | Tramo origen (h) | Error rel. | Omitidos |
| SICU1_01_event_1 | 69.0 | 746.0 | 0.908 | 10 |
| SICU1_01_event_2 | 1.8 | 5.0 | 0.636 | 5 |
| SICU1_02_event_1 | 69.0 | 746.0 | 0.908 | 10 |
| SICU1_02_event_2 | 144.2 | 146.2 | 0.014 | 10 |
| SICU1_02_event_3 | 1.7 | 5.0 | 0.668 | 5 |
| SICU1_02_event_4 | 1.1 | 4.0 | 0.725 | 4 |
| SICU1_02_event_5 | 1.7 | 5.0 | 0.655 | 5 |
| SICU1_03_event_1 | 69.0 | 746.0 | 0.908 | 10 |
| SICU1_03_event_2 | 1.1 | 4.0 | 0.730 | 4 |
| SICU1_03_event_3 | 1.2 | 4.0 | 0.710 | 4 |
| SICU1_03_event_4 | 23.3 | 26.0 | 0.104 | 10 |
| SICU1_03_event_5 | 2.0 | 3.0 | 0.335 | 3 |
| SICU1_03_event_6 | 92.5 | 194.0 | 0.523 | 18 |
| SICU1_04_event_1 | 69.0 | 746.0 | 0.908 | 10 |
| SICU1_04_event_2 | 1.5 | 4.0 | 0.620 | 4 |

*Ficheros de origen dentro del tramo del evento que no están reflejados en la fusión (`n_omitted`).*

## 1b. % de horas con datos por canal (mediana por cohorte)

> `n_cases_with_data` = casos (de la muestra) con cobertura > 0. La lista de canales es la misma en las 4 cohortes.

| Cohorte | RR | HR | SpO2 | PEEP | MAP | FiO2 | TV | PIP | ECG | PPG | ABP |
|---|---|---|---|---|---|---|---|---|---|---|---|
| clinic | 97% (n=12) | 99% (n=12) | 96% (n=12) | 97% (n=12) | 88% (n=12) | 98% (n=12) | 97% (n=12) | 97% (n=12) | 100% (n=12) | 100% (n=12) | 100% (n=12) |
| vitaldb | 96% (n=12) | 100% (n=12) | 100% (n=12) | 0% (n=0) | 100% (n=12) | 0% (n=1) | 96% (n=11) | 0% (n=0) | 100% (n=12) | 100% (n=12) | 100% (n=12) |
| mimic | 87% (n=12) | 0% (n=0) | 0% (n=0) | 2% (n=12) | 0% (n=0) | 3% (n=12) | 24% (n=12) | 4% (n=12) | 0% (n=0) | 0% (n=0) | 100% (n=8) |
| eicu | 69% (n=12) | 84% (n=12) | 77% (n=12) | 10% (n=11) | 0% (n=4) | 16% (n=11) | 7% (n=9) | 0% (n=4) | 0% (n=0) | 0% (n=0) | 0% (n=0) |

### Canales ausentes (ROJO)

- **clinic**: ninguno
- **vitaldb**: PEEP, FiO2, PIP
- **mimic**: HR, SpO2, MAP, ECG, PPG
- **eicu**: MAP, PIP, ECG, PPG, ABP

## 2. Plausibilidad de los eventos

### clinic

- Casos muestreados: 15 | eventos > 21 días: **0**
- Duración (h): min 4.8, P5 17.1, mediana 26.2, P95 160.3, máx 210.5
- Intentos por evento (señal): min 1, mediana 1, máx 5
- Eventos tras cortes D2 (hueco monitor > 1 h): min 1, mediana 1, máx 5

### vitaldb

- Casos muestreados: 15 | eventos > 21 días: **0**
- Duración (h): min 1.2, P5 1.7, mediana 22.0, P95 92.5, máx 168.8
- Intentos por evento (señal): min 0, mediana 1, máx 1
- Eventos tras cortes D2 (hueco monitor > 1 h): min 0, mediana 1, máx 1

### Duración por cohorte (todos los casos)

> Clínic/VitalDB: cabecera del fichero fusionado. MIMIC: nombre de fichero. eICU: episodios fusionados de `respiratoryCare` (la cabecera de eICU está corrupta).

| Cohorte | N | min (h) | P5 (h) | mediana (h) | P95 (h) | máx (h) |
|---|---|---|---|---|---|---|
| clinic | 50 | 4.8 | 7.3 | 47.7 | 182.0 | 210.5 |
| vitaldb | 83 | 0.1 | 1.2 | 35.0 | 168.6 | 169.0 |
| mimic | 82 | 364.5 | 370.8 | 547.0 | 1430.3 | 2254.4 |
| eicu | 44772 | 0.0 | — | 36.8 | — | 25713.5 |

## 4. MIMIC — monitor

- WFDB numéricos en `datasets/mimic3wdb/raw/`: **NO**
- `.vital` base: 82 | enriquecidos: 82
- `.parquet` en `mimic_full_cases`: **0** | en enriquecidos: **0**

Pistas de monitor (con 0 registros) por paso:

| Pista | En base (n vacíos/n presentes) | En enriched (n vacíos/n presentes) |
| `ABP_D` | 6/6 | 6/6 |
| `ABP_M` | 6/6 | 6/6 |
| `ABP_S` | 6/6 | 6/6 |
| `HR` | 10/10 | 10/10 |
| `PULSE` | 10/10 | 10/10 |
| `RESP` | 10/10 | 10/10 |
| `SpO2` | 10/10 | 10/10 |

## 5. MIMIC — etiquetas

- ADMISSIONS: **sí** | ICUSTAYS: **sí** | PROCEDUREEVENTS_MV: **sí**
- Episodios 225792 totales: 10749 (8182 sujetos; 1504 con >1 episodio)
- Casos actuales: 82 | con DEATHTIME: 29
- Episodios 225792 por sujeto (actuales): {'1': 31, '2': 14, '3': 17, '4': 10, '5': 5, '6': 1, '7': 1, '11': 1, '13': 1, '16': 1}
- Sujetos actuales con >1 episodio: 51 | con episodios en >1 HADM: 20

**Por qué la tabla da 0 fallos:** El campo event_type de las tablas de supervivencia solo toma dos valores ('successful_extubation' y 'censored_no_extubation'): nunca 'failure'. Los fallos existen en n_failed_attempts (mimic: 33/82 con >=1 fallo a 48h en v0.1.0_df652b7b) y en extubation_attempts (169 intentos 'failure').

**Por qué la tabla da 0 censurados:** censored_no_extubation solo se activa para 'death_at_vent_end' (DEATHTIME dentro de +/-5 min del vent_end del fichero). Ninguno de los 82 sujetos cumple esa coincidencia; las demás reglas de censura de D3 (fin de monitor/datos <=15 min o seguimiento < ventana) no están implementadas.

## 6. VitalDB — pistas de ventilador perdidas en MERGE_TRACK_NAMES

| Pista de origen | Frecuencia en muestra | ¿Conservada por MERGE_TRACK_NAMES? |
| `Intellivue/TV_EXP` | 103 | sí |
| `Intellivue/MV_EXP` | 103 | sí |
| `Intellivue/VENT_RR` | 102 | **NO** |
| `Intellivue/FLOW_WAV` | 65 | **NO** |
| `Intellivue/AWP_WAV` | 54 | **NO** |
| `Intellivue/FIO2` | 29 | **NO** |
| `Intellivue/PIP_CMH2O` | 26 | **NO** |
| `Intellivue/PEEP_CMH2O` | 26 | **NO** |

- Pistas de ventilador en origen: 8 | perdidas: **6**

## 7. eICU — reintubaciones 48-72 h (tablas crudas, sin adaptador)

- Pacientes con VM: 44772 | con >1 episodio: 1373
- Huecos de reintubación: 1417 | ≤48 h: 1182 | **48-72 h: 59** | >72 h: 176
- Hueco min/mediana/máx (h): 2.0 / 4.9 / 8988.2

## 8. Sesgo de selección actual

### MIMIC (mín. 12 h + top-150)

- Episodios 225792 totales: 10749 | duración: min -0.0, mediana 26.8, máx 2254.4
- Se perderían con el mínimo de 12 h: **3067** episodios
- El selector actual toma los 150 episodios más largos (>=12h) de la cohorte matched; de ellos quedan 82 casos.

### eICU (mín. 2 h)

- Episodios fusionados: 46189 | duración: min 0.0, mediana 36.8, máx 25713.5
- Se perderían con el mínimo de 2 h: **1104** episodios

- Clínic/VitalDB: La distribución pre-filtro de Clínic/VitalDB requiere re-ejecutar la detección sin MIN_EVENT_SECONDS=7200 (Fase 1).

## Limitaciones

- La cobertura horaria por canal se calcula sobre una **muestra** de casos (12 por cohorte) para no saturar memoria con ficheros de cientos de horas.
- La segmentación D1/D2 de la sección 2 usa señales leídas de los ficheros de caso ya construidos; no sustituye a la re-segmentación de Fase 1.
- La cabecera VITA de los ficheros MIMIC y eICU está **corrupta** (`dtstart` ≈ fecha actual, `dtend` = 0 o erróneo); por eso la duración de esas cohortes se deriva del nombre de fichero (MIMIC) o de los tracks relativos (eICU), y no de la cabecera.

