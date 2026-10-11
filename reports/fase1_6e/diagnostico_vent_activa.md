# Fase 1.6e, punto 1 — Diagnóstico de ventilación ACTIVA (antes de cambiar código)

Rama `fix/integridad-datos`. Cohorts de señal: **Clínic** (`cases_v0.5.0_eac1eb35`)
y **VitalDB** (`cases_v0.4.0_aa142f35`). Los crudos de `D:\data` son inmutables y
no se copian al repo: aquí solo hay agregados y PNG anonimizados.

## 1. Por qué: el síntoma que motiva la fase

En la Fase 1.6d, **75 eventos de Clínic** y **33 de VitalDB** pasaron de éxito a
`end_of_record` con **cola de monitor de 0,00 h**. La causa no era el paciente:
`vent_span_from_probe()` medía la ventilación con la **PRESENCIA** de registros de
pistas del ventilador (`VENT_TRACKS`). Con el respirador **en espera** (o con alto
flujo) FiO2, PEEP y las ondas AWP/FLOW siguen registrándose, así que la
ventilación se extendía hasta el fin del registro y **ocultaba la extubación real**.

La corrección es medir la ventilación por **ACTIVIDAD** real.

## 2. Método

**Criterios de ventilación activa (decisión D-a: umbrales fijos, clínicos, no
ajustados a los datos).** Un minuto es activo si cumple **al menos uno**:

| Criterio | Umbral | Justificación |
|---|---|---|
| `TV_EXP` | ≥ 100 mL | por debajo no hay volumen tidal entregado |
| `MV_EXP` | ≥ 1,0 L/min | por debajo no hay ventilación minuto efectiva |
| amplitud de `AWP_WAV` (p95−p5) **con `VENT_RR` > 0** | ≥ 5 cmH2O | un circuito en espera no da esa excursión, y se exige que el respirador declare respiraciones |

**FiO2 y PEEP por sí solas NO cuentan**: son ajustes que siguen registrándose con
el ventilador en espera. Los minutos con pistas presentes pero inactivos se
cuentan como `vent_standby` (se reportan, no cuentan como ventilación ni como
presencia de paciente).

**Ventana de análisis**: anclada al **fin de la ventilación grabada** (no al fin
del registro), 12 h antes y hasta `min(max(fin+2 h, fin del monitor), fin+24 h)`
después, para poder medir también la cola de los controles.

**Selección de ficheros**: se leen **todos** los `source_files` del evento y se
**enmascara por tiempo**. Ver el bug B2: en Clínic los ficheros de origen **no son
horarios** (hay grabaciones de 19–48 h), así que seleccionar ficheros por su hora
de nombre pierde los que cubren la ventana (fue el error de la primera versión del
diagnóstico: 24 ventanas «vacías» falsas).

**Afectados** = eventos del índice NUEVO con `labels.48h.censor_cause ==
"end_of_record"` y cola de monitor fisiológico < 1 h. **Controles** = eventos con
`eventual_type == successful_extubation` (muestreo equiespaciado, 20).
Los `event_id` son **posicionales por box** (`..._event_i`): emparejar ids entre el
índice antiguo (v0.1.0) y el nuevo compara eventos distintos, así que la
atribución por id se hace **dentro del mismo índice**.

## 3. Inventario de pistas presentes (punto 1d)

`scripts/verify/fase1_6e/track_inventory.py` (muestreo `--limit-per-box`).

### Clínic — 78 ficheros (de 5 471 en el crudo, 13 boxes), 131 pistas, 0 ilegibles

| Pista | Ficheros | % |
|---|---|---|
| `Intellivue/ECG_HR` | 77 | 98,7 % |
| `Intellivue/PLETH_SAT_O2` | 77 | 98,7 % |
| `Intellivue/RR` | 77 | 98,7 % |
| `Intellivue/PLETH_HR` | 76 | 97,4 % |
| `Intellivue/NIBP_MEAN` | 43 | 55,1 % |
| `Intellivue/ABP_MEAN` | 32 | 41,0 % |
| `Intellivue/AWAY_CO2_INSP_MIN` | 16 | 20,5 % |
| `Intellivue/FIO2` | 16 | 20,5 % |
| `Intellivue/PEEP_CMH2O` | 15 | 19,2 % |
| `Intellivue/PIP_CMH2O` | 15 | 19,2 % |
| `Intellivue/TV_EXP` | 15 | 19,2 % |
| `Intellivue/VENT_RR` | 15 | 19,2 % |
| `Intellivue/TV_INSP` | 10 | 12,8 % |
| `Intellivue/MV_EXP` | 7 | 9,0 % |
| `Intellivue/AWP_WAV` | 2 | 2,6 % |
| `Intellivue/FLOW_WAV` | 2 | 2,6 % |

**No hay** pista de **fuga** (`LEAK`), ni de **modo** del respirador, ni de
**interfaz** (tubo/mascarilla). Consecuencia para el punto 3 (VNI): en Clínic solo
se puede intentar la firma `TV_INSP` vs `TV_EXP`; las demás no son verificables.

### VitalDB — 208 ficheros, 0 ilegibles

| Pista | Ficheros | % |
|---|---|---|
| `Intellivue/PLETH` | 197 | 94,7 % |
| `Intellivue/RESP` | 174 | 83,7 % |
| `Intellivue/ABP` | 169 | 81,3 % |
| `Intellivue/ECG_HR` | 129 | 62,0 % |
| `Intellivue/ABP_MEAN` | 104 | 50,0 % |
| `Intellivue/MV_EXP` | 72 | 34,6 % |
| `Intellivue/MV_INSP` | 72 | 34,6 % |
| `Intellivue/TV_EXP` | 72 | 34,6 % |
| `Intellivue/TV_INSP` | 72 | 34,6 % |
| `Intellivue/VENT_RR` | 72 | 34,6 % |
| `Intellivue/FLOW_WAV` | 51 | 24,5 % |
| `Intellivue/AWAY_CO2_INSP_MIN` | 47 | 22,6 % |
| `Intellivue/AWP_WAV` | 45 | 21,6 % |
| `Intellivue/FIO2` | 25 | 12,0 % |
| `Intellivue/PEEP_CMH2O` | 19 | 9,1 % |
| `Intellivue/PIP_CMH2O` | 19 | 9,1 % |
| `Intellivue/SET_IE_RATIO` | 11 | 5,3 % |
| `Intellivue/SET_SIMV` | 8 | 3,9 % |

Tampoco hay `LEAK`. Sí hay **ajustes de modo** (`SET_SIMV`, `SET_IE_RATIO`,
`SET_DELTA_PEEP_CMH2O`), que el punto 3 puede usar como firma de modo.

## 4. Hallazgos de integridad (diagnóstico, antes de tocar el builder)

### B5 — el criterio de onda comparaba unidades **crudas** con un umbral en cmH₂O

`minute_amplitude` se aplicaba sobre los valores **crudos** de `AWP_WAV`, mientras
que el umbral (`ACTIVITY_AWP_AMP_MIN_CMH2O = 5.0`) está en cmH₂O. Los valores de
los registros de `vitaldb` **no** llevan aplicados `gain`/`offset`.

Verificado pista a pista en ficheros reales:

| Pista | `gain` | `offset` | `unit` | Mediana cruda | Mediana física |
|---|---|---|---|---|---|
| `Intellivue/AWP_WAV` | 0,015 | −10,0 | cmH₂O | 1 378 | **10,7 cmH₂O** |
| `Intellivue/FLOW_WAV` | 0,065 | −130,0 | L/min | −130 | — |
| `Intellivue/TV_EXP` | 1,0 | 0,0 | mL | 324 | 324 mL |
| `Intellivue/MV_EXP` | 1,0 | 0,0 | L | 5,8 | 5,8 L |
| `Intellivue/PIP_CMH2O` | 1,0 | 0,0 | cmH₂O | 25,0 | 25,0 cmH₂O |
| `Intellivue/VENT_RR` | 1,0 | 0,0 | /min | 18 | 18/min |

Contraste independiente: en `SICU2_07_250102_090000.vital` (paciente del estudio
lazo P-V con `SET_PEEP = 10`) la mediana cruda de AWP es 1 378 → convertida
**10,7 cmH₂O** ✓. Las pistas **numéricas** del ventilador tienen `gain = 1` y
`offset = 0`, así que crudo == físico (`TV_EXP` 324 mL, `PIP` 25 cmH₂O…).

**Efecto:** el umbral efectivo era 5 crudo ≈ **0,075 cmH₂O**, de modo que el
**23,3 %** de los minutos «en espera» contaban como ventilación activa. Los
5 cmH₂O del criterio equivalen a **333 crudo**.

**Corrección (en el punto 2):** se aplica `gain`/`offset` al leer las pistas de
actividad (`to_physical`, expuesto y con test de regresión). **El umbral no se
toca** (D-a): sigue siendo 5 cmH₂O, ahora en cmH₂O de verdad. Impacto medido en
Clínic: los minutos que activan solo por AWP pasan de 8 832 a 8 518 (−3,6 %) y
**ningún evento depende solo del criterio de AWP**.

> Consecuencia para el **punto 5**: `scripts/verify/fase1_6d/wave_derived_validation.py`
> pasa la onda **cruda** a `wave_derived.py`, que trabaja en cmH₂O
> (`INSP_THRESHOLD_CMH2O = 2,0`, `FLAT_PRESSURE_CMH2O = 1,0`). La validación
> Bland–Altman sobre datos reales **no llegó a ejecutarse** en la 1.6d (no hay
> informe publicado), así que no hay resultados inválidos que retirar, pero el
> punto 5 debe aplicar la misma conversión antes de comparar.

### B1 — `censor_time_h` en **horas absolutas** en Clínic y VitalDB

`build_signal_cases.extubation_decision()` pasa `observation_end_h(episode)`
(**epoch/3600**) como `observation_end_h` a `resolve_extubation()`, que lo
devuelve tal cual en `censor_time_h` / `is_at_risk_until_h`.

| Cohorte | Eventos con `censor_time_h > 1000` h | Con valor relativo plausible |
|---|---|---|
| MIMIC | 0 / 9 093 | 90 |
| eICU-B | 0 / 9 754 | 59 |
| **Clínic** | **115 / 181** | 1 |
| **VitalDB** | **56 / 96** | 0 |

Ejemplo: `clinic_box2_event_1` → `censor_time_h = 486 559,38 h` con
`t0_unix/3600 = 486 519,60 h` y `vent_end_h = 39,79 h` (la diferencia es
exactamente el fin de ventilación).

**Efecto:** `censor_time_h` e `is_at_risk_until_h` no son utilizables en las
cohortes de señal (cualquier cálculo de horizonte a 48 h con ellos es erróneo).
`event_type` y `censor_cause` NO se ven afectados. La comparación
`apply_censor = t_censor <= attempts[idx].extubation_h` siempre da falso, pero la
rama alternativa devuelve el mismo resultado, así que la etiqueta no cambia.

### B2 — `_files_for_episode()` supone ficheros **horarios**

`_files_for_episode()` calcula `f_end = f_start + 1.0` («los ficheros de origen son
horarios»). En Clínic **no lo son**: medidos 39,84 h, 19,24 h, 35,75 h, 48,00 h,
34,68 h de duración, y la hora del **nombre** va 1–2 h por detrás del inicio real
del contenido. **126 de 181** eventos tienen `vent_end_h > 2 × n_ficheros`.

**Efecto:** la lista de `source_files` puede excluir ficheros que sí cubren el
episodio (cuando la hora del nombre cae más allá del fin de región). En los casos
revisados la selección acertó, pero el criterio es estructuralmente incorrecto.
**Corrección propuesta:** seleccionar por **solape de intervalos reales**
(`dtstart`/`dtend` de la sonda), no por hora de nombre + 1 h.

### B3 — unidades de `active_vent_span_from_probe()` (corregido en este punto)

Devolvía **horas** mientras que `vent_span_from_probe()` devuelve **segundos
epoch**; el llamador (`_to_hours`) volvía a dividir por 3600 y quedaba un tramo de
**5 segundos** → **26 tests en rojo** y la segmentación produciendo 0 episodios.
Corregido: devuelve segundos epoch (mismas unidades que `vent_span_from_probe`),
con test de regresión.

Además se acotó el fin del tramo a la **última muestra observada** de las pistas de
actividad: el «último minuto activo completo» alargaba el fin hasta 1 min de más y
eso **invertía la etiqueta D3 en el umbral exacto de 1 h de cola** (caso
`test_labels_d3_are_consistent_with_attempts`).

### B4 — `box14`: colisión de nombres entre `14/` y `14_2/`

`D:\data\clinic_vitals\box14` contiene dos subcarpetas, `14` (556 ficheros) y
`14_2` (679), con **578 nombres repetidos**. Todos los pares tienen **el mismo
tamaño** y **los mismos tokens de dispositivo** (`7gibrwfgp`, `mj6uua9n3`), pero
solo 17/578 coinciden en fecha de modificación → parecen **el mismo box partido en
dos carpetas** (mismo aparato), no dos pacientes distintos.

**Efecto:** `source_files` guarda **solo el nombre**, así que la selección coge
**ambos** ficheros homónimos → **30 eventos de box14** (de 181) listan cada
fichero **dos veces**; al fusionar, la misma grabación puede entrar dos veces.
**Propuesta (requiere confirmación):** deduplicar por nombre dentro del box (mismo
token = mismo aparato), o tratar `14` y `14_2` como boxes distintos y no emparejar
nunca por nombre.

## 5. Resultado del diagnóstico

`scripts/verify/fase1_6e/diag_vent_activa.py` (ventana anclada al fin de la
ventilación, enmascarado por tiempo) + `summarize_diag.py`.
**115 afectados** y **20 controles** por cohorte, reanudable y con timeout por
evento en subproceso (0 pendientes).

### Clínic

| | n |
|---|---|
| Con ventilación activa detectada | 102 (88,7 %) |
| **No cambian** (fin activo ≈ fin grabado) | 65 |
| **Sí cambian** (la actividad acaba antes) | 37 |
| **Sin actividad ninguna** | 13 |
| **Recuperan el éxito** (cola corregida ≥ 1 h) | **9** |
| Siguen censurados (cola corregida < 1 h) | 93 |
| Desaparecen (no hay episodio ventilado) | 13 |

Recorte del fin de ventilación en los 37 que cambian: p10 = 0,52 h,
**mediana = 0,79 h**, p90 = 5,32 h.

Los 9 que recuperan el éxito (recorte de ventilación → cola corregida):

| Evento | Recorte | Cola nueva |
|---|---|---|
| `clinic_box14_event_17` | 1,03 h | 1,026 h |
| `clinic_box9_event_3` | 1,20 h | 1,196 h |
| `clinic_box9_event_1` | 2,02 h | 2,014 h |
| `clinic_box4_event_31` | 2,65 h | 2,646 h |
| `clinic_box14_event_21` | 5,02 h | 5,021 h |
| `clinic_box14_event_20` | 5,32 h | 5,411 h |
| `clinic_box8_event_7` | 6,48 h | 6,485 h |
| `clinic_box8_event_3` | 7,38 h | 7,381 h |
| `clinic_box6_event_11` | 8,37 h | 8,367 h |

**Corrección de la sospecha de la 1.6d:** los «75 eventos que pasan de éxito a
`end_of_record`» **no eran 75 censuras falsas**. De los 115 afectados, en **65**
la actividad acaba exactamente donde acababa el registro (censura correcta: la
ventilación y el monitor se apagan a la vez, cola 0,000–0,002 h), en **37** la
actividad acaba antes, y solo en **9** de ellos la cola fisiológica corregida
llega a 1 h. Es decir: **la corrección recupera 9 eventos, no 75**, y tres de los
nueve son de `box14` (el box con nombres duplicados, ver B4).

**Los 13 que desaparecen** (sin actividad ninguna) se parten en:

- **9 sin señal de ventilación real**: ondas AWP/FLOW planas (amplitud máxima
  0,0) o **solo FiO2** registrada → respirador en espera / alto flujo. Correcto
  que no cuenten como ventilación.
- **1 con un minuto suelto** de onda y otro con 4–19 min.
- **3 casos no verificables** (`clinic_box14_event_22` amplitud AWP 243 en crudo,
  `clinic_box14_event_39` 100, `clinic_box14_event_40` **760** ≈ 11 cmH₂O de
  excursión real): no activan el criterio porque **en esos ficheros no hay pista
  `VENT_RR`** y el criterio de AWP exige `VENT_RR > 0`. No son falsos negativos
  demostrables, pero tampoco verificables con las pistas disponibles.

**Controles** (eventos con extubación confirmada, muestreo equiespaciado):

- cola nueva mediana **27,6 h**; **15/20** con cola ≥ 1 h;
- **1/20 con cola < 1 h** (`clinic_box6_event_13`) → ojo: los controles se miden
  desde el **último** intento, así que un evento con un éxito previo seguido de
  reintubación puede tener cola corta al final;
- **4/20 sin actividad detectable**: 2 con **solo FiO2** en la ventana
  (`clinic_box10_event_1`, `clinic_box12_event_14` — su ventilación, según el
  índice, es precisamente la presencia de FiO2), 1 con **ondas planas**
  (`clinic_box14_event_30`) y 1 con **solo 2 min** de registro (`clinic_box5_event_7`).
  Ninguno demuestra ventilación con las pistas disponibles, así que el criterio
  no puede evaluarse ahí.

### Comprobación de los umbrales (punto 2)

`scripts/verify/fase1_6e/umbrales_hist.py` compara los minutos de los **controles**
(ventilación clara) con los minutos **sin actividad** de los afectados (cola
sospechosa), sin tocar los umbrales (D-a).

| Criterio | Clara (mediana) | Sospechosa (mediana) | % ≥ umbral |
|---|---|---|---|
| `TV_EXP` ≥ 100 mL | 464 mL (p5 334) | 23 mL (p95 83) | **100 % vs 0 %** |
| `MV_EXP` ≥ 1,0 L/min | 8,22 L/min (p5 5,53) | 0,02 L/min (p95 0,58) | **100 % vs 0 %** |
| amplitud `AWP_WAV` ≥ 5 | 1 240 | 0 (p95 73) | 100 % vs **23,3 %** |

`TV_EXP` y `MV_EXP` **separan perfectamente**: ningún minuto dudoso llega al
umbral. La amplitud de `AWP_WAV` **no separa**, y la causa es un **bug de unidades
(B5)**: la amplitud se calcula sobre los valores **crudos** de la onda
(`AWP_WAV cmH₂O = −10 + 0,015 × crudo`), así que el umbral «5 cmH₂O» está
comparándose con **5 unidades crudas ≈ 0,075 cmH₂O**. La excursión real de una
ventilación clara es ~1240 crudo (≈ 18,6 cmH₂O) y el equivalente a 5 cmH₂O es
**333 crudo**.

Efecto medido en Clínic: los minutos que activan solo por AWP pasan de 8 832 a
8 518 al corregir las unidades (**−3,6 %**), y **ningún evento depende solo del
criterio de AWP** → los números de Clínic de arriba son robustos frente a B5.

Minutos en **espera** (`vent_standby`, pistas del ventilador presentes pero
inactivas) en los afectados de Clínic: **5 175** (`AWP_WAV` 3 261, `TV_EXP` 477,
`VENT_RR` 477, `PIP` 477, `PEEP` 477, `MV_EXP` 6).

### VitalDB

_(pendiente de terminar la ejecución)_

