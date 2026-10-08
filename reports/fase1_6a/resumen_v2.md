# Fase 1.6a-bis — Censo de eICU corregido (sin etiquetas)

Rama `fix/integridad-datos`. Corrige el punto 4 de la Fase 1.6a: el censo anterior
usaba `apache_vent` (`oobVentDay1`) como evidencia de ventilación invasiva, pero esa
variable **incluye ventilación no invasiva (VNI)** y no sirve para el criterio A4.
Aquí se sustituye por `apache_intub` (`oobIntubDay1`).

**No se calcula ninguna etiqueta de resultado ni se aplican filtros definitivos.**
Se mide, se clasifica y se proponen escenarios para que la selección de hospitales
la haga el investigador.

## 1. Evidencia invasiva: qué cambia (puntos 1 y 3)

| Evidencia | Fase 1.6a (v1) | Fase 1.6a-bis (v2) |
|---|---|---|
| `apache_vent` (`oobVentDay1`, incluye VNI) | **56 530 (sí)** | **excluida** |
| `apache_intub` (`oobIntubDay1`) | 44 530 | **44 530 (sí)** |
| `airway_ett` (respiratoryCare) | 8 149 | 8 149 |
| `airway_trach` | 1 028 | 1 028 |
| `rc_invasive` (ajustes invasivos en respiratoryCharting) | 56 075 | 56 075 |
| `cpg_vent` (carePlanGeneral) | 51 642 | 51 642 |
| `tx_vent` (treatment) | 41 063 | 41 063 |
| **Unión (evidencia invasiva confirmada)** | **74 638** | **66 911** |

`vent_stays` por hospital pasa a ser el recuento de estancias de la **unión corregida**
(66 911 estancias en 205 hospitales); el recuento puramente APACHE se conserva en la
columna `apache_intub_stays`. El cambio retira 7 727 estancias cuya única evidencia era
`oobVentDay1` (VNI o ventilación no confirmada invasiva).

### Concordancia entre fuentes (κ de Cohen)

| Par | v1 (κ) | v2 (κ) | v2 Jaccard | ambas |
|---|---|---|---|---|
| `apache_*` vs `cpg_vent` | 0.742 | **0.857** | 0.804 | 42 862 |
| `apache_*` vs `tx_vent` | 0.651 | **0.751** | 0.672 | 34 396 |
| `apache_*` vs `rc_invasive` | 0.626 | **0.666** | 0.599 | 37 669 |
| `apache_*` vs `airway_ett` | 0.159 | 0.208 | 0.151 | 6 919 |
| `apache_*` vs `airway_trach` | 0.017 | 0.024 | 0.017 | 761 |

Sustituir `oobVentDay1` por `oobIntubDay1` **mejora** la concordancia con todas las
demás fuentes: la variable corregida es la que mejor reproduce el plan de ventilación
y los ajustes del respirador. `airway_ett`/`airway_trach` siguen siendo evidencia
minoritaria y de baja concordancia (el `airwaytype` solo se cumplimenta en una parte
de los pacientes).

## 2. Plausibilidad por hospital (punto 2)

Cocientes pedidos: `rc_invasive/icu_stays_total` y `rc_invasive/apache_intub`.

Hospitales con `rc_invasive/icu > 0.6` (densidad anómala), 6 de 205:

| Hospital | ICU | rc_invasive | rc/ICU | rc/intub | Invasivas **sin** intubación APACHE | Veredicto |
|---|---|---|---|---|---|---|
| 411 | 3 199 | 2 917 | 0.912 | 7.60 | **87.5 %** | `implausible_permeable` |
| 412 | 308 | 294 | 0.955 | 4.14 | **76.9 %** | `implausible_permeable` |
| 413 | 2 411 | 2 320 | 0.962 | 3.59 | **72.8 %** | `implausible_permeable` |
| 273 | 171 | 150 | 0.877 | 1.55 | < 50 % | `concentrado_concordante` |
| 131 | 131 | 84 | 0.641 | 1.09 | < 50 % | `concentrado_concordante` |
| 259 | 586 | 356 | 0.608 | 1.11 | **11.5 %** | `concentrado_concordante` |

Resolución adoptada:

- **411, 412 y 413 → marcados `implausible` y excluidos.** Su `respiratoryCharting`
  es **permeable**: etiquetan como invasivas estancias sin intubación APACHE ni plan
  de ventilación documentado (2 366 de 2 917 en el 411; 1 609 de 2 320 en el 413;
  214 de 294 en el 412). Etiquetas responsables: `Total RR` (236 541 registros en el
  411), `f Total`, `PEEP`, `Set Vt (Servo,LTV)`, `Set Vt (Drager)` — ajustes de
  respirador charteados en todo el hospital, no solo en pacientes ventilados. La
  dirección del error es unidireccional: el 95–97 % de los pacientes **sí** intubados
  tienen también charting invasivo, pero además se marca a muchos no intubados.
- **259, 273 y 131 → NO son permeables** (`concentrado_concordante`): son hospitales
  pequeños y densamente ventilados, pero **concuerdan** con APACHE (en el 259, el
  98.4 % de los intubados tiene charting invasivo y solo el 11.5 % de las invasivas
  carece de intubación; 20 de 356 sin intubación *ni* plan). Se mantienen como
  candidatos válidos; quedan fuera de los escenarios sólo porque el filtro literal
  `rc/icu ≤ 0.6` los toca por un margen mínimo (0.608).

**Advertencia metodológica:** `oobIntubDay1` recoge la intubación **fuera de
quirófano**, así que `rc_invasive/apache_intub > 1` es esperable y **no** es indicio
de error (p. ej. 73 → 1.195, 420 → 1.475, 458 → 1.384, por cirugía con ingreso
ventilado). El indicio real de permeabilidad es el par (densidad > 0.6 **y** fracción
de invasivas sin intubación ≥ 0.5), no el cociente por sí solo.

Diagnóstico detallado de las 4 etiquetas pedidas: `reports/fase1_6a/eicu_plausibilidad.json`.

## 3. Cobertura por variable (punto 3)

Sobre las 56 075 estancias con ajustes invasivos (D8: variable presente en ≥ 50 % de las
horas del intervalo ventilado, LOCF 2 h señales / 12 h ajustes):

| Variable | `cov_HR` | `cov_SpO2` | `cov_MAP_inv` | `cov_MAP_nibp` | `cov_MAP` | `cov_RR` | `cov_FiO2` | `cov_PEEP` |
|---|---|---|---|---|---|---|---|---|
| Mediana entre hospitales | 93.0 % | 91.0 % | **21.8 %** | 84.4 % | 92.0 % | 86.3 % | 97.5 % | 91.8 % |

- La MAP se documenta casi siempre por vía **no invasiva** (`MAP_nibp` 84.4 %) y sólo en
  el 21.8 % por vía **invasiva** por hospital: cualquier requisito de MAP arterial
  reduciría la muestra drásticamente; el criterio `vars_ok` acepta el máximo de ambas.
- **Variable limitante** por hospital (la de menor cobertura ≥ 50 %): `RR` en **86**
  hospitales, `SpO2` en 29, `PEEP` en 26, `HR` en 19, `FiO2` en 11, `MAP` en 9.
  La frecuencia respiratoria de ajuste (`RR_V`) es, con diferencia, el cuello de botella.
- 22 518 estancias cumplen `vars_ok50` en 109 hospitales; 77 hospitales tienen ≥ 30
  utilizables y 65 tienen ≥ 50.

## 4. Escenarios (punto 4)

Todos recalculados sobre **estancias utilizables** = evidencia invasiva confirmada
(unión corregida) **y** `vars_ok` al 50 % (HR, SpO2, MAP, RR, FiO2, PEEP).
`usable` es el recuento de esas estancias en los hospitales del escenario.

| Escenario | Hospitales | Utilizables | min | P25 | mediana | P75 | máx | peso mayor | enseñ. |
|---|---|---|---|---|---|---|---|---|---|
| a — *mechanical power* (`apache_intub` ≥ 10 y ≥ 10 % con pico en 24 h) | 65 | 15 728 | 0 | 24 | 85 | 352 | 1 364 | 8.7 % | 8 |
| b — ≥ 50 vent., ≥ 80 % con ajustes, anotación ≤ 4 h | 82 | 20 593 | 0 | 38 | 103 | 303 | 1 519 | 7.4 % | 10 |
| c — como b con anotación ≤ 2 h | 37 | 8 478 | 0 | 60 | 119 | 309 | 1 254 | 14.8 % | 3 |
| d — b + `vars_ok` ≥ 70 % de las ventiladas | 43 | 15 662 | 38 | 90 | 239 | 454 | 1 519 | 9.7 % | 4 |
| **e** — ≥ 50 vent., ≥ 80 % ajustes, ≤ 4 h, `rc/icu` ≤ 0.6, ≥ 30 utilizables | **60** | **20 078** | 36 | 75 | 148 | 436 | 1 519 | 7.6 % | 5 |
| **e2** — como e con ≥ 50 utilizables | **53** | **19 780** | 51 | 111 | 189 | 455 | 1 519 | 7.7 % | 5 |
| **f** — como e con anotación ≤ 2 h | **29** | **8 027** | 38 | 79 | 156 | 359 | 1 254 | 15.6 % | 1 |
| **g** — sólo plausibilidad (`rc/icu` ≤ 0.6) y ≥ 30 utilizables | **74** | **21 737** | 33 | 64 | 129 | 390 | 1 519 | 7.0 % | 5 |

Distribución regional (Midwest/South/West/Northeast/sin región):

| Escenario | Midwest | South | West | Northeast | s/r |
|---|---|---|---|---|---|
| a | 21 | 16 | 14 | 7 | 7 |
| b | 25 | 17 | 25 | 9 | 6 |
| c | 10 | 6 | 18 | 1 | 2 |
| d | 13 | 7 | 18 | 1 | 4 |
| e | 16 | 12 | 22 | 4 | 6 |
| e2 | 12 | 11 | 21 | 3 | 6 |
| f | 6 | 5 | 15 | 1 | 2 |
| g | 19 | 19 | 25 | 5 | 6 |

### Sensibilidad al filtro de plausibilidad

Sustituyendo el umbral duro `rc/icu ≤ 0.6` por el veredicto (`≠ implausible_permeable`),
los escenarios sólo ganan el hospital **259** (+244 estancias): e → 61 hospitales /
20 322 utilizables; e2 → 54 / 20 024; g → 75 / 21 981. Es decir, la corrección de
plausibilidad cuesta exactamente **tres hospitales** (411, 412, 413), no seis.

### Desviación deliberada del escenario a

El criterio original hablaba de `oOBVentDay1 = 1`. Como el punto 1 demuestra que
`oobVentDay1` confunde VNI con ventilación invasiva, el escenario a se calcula con
`oobIntubDay1` (`apache_intub`), que es la misma variable APACHE sin el ruido de la VNI.
El conjunto resultante (65 hospitales, 15 728 utilizables, ninguno implausible) es el
mismo que el de la Fase 1.6a en número de hospitales, pero ahora la medida son
estancias utilizables y no estancias ventiladas.

## 5. Entregables

| Fichero | Contenido |
|---|---|
| `reports/fase1_6a/eicu_hospitales_v2.csv` | 205 hospitales × 35 columnas: banderas corregidas, `rc_over_icu`, `rc_over_intub`, `frac_rc_without_intub`, cobertura por variable, `limiting_var`, `usable_stays`, `plausibility`, `implausible` |
| `reports/fase1_6a/eicu_scenarios_v2.json` | Fase 1.6a-bis: banderas, unión corregida, concordancia κ y escenarios a–g con hospitales/utilizables/distribución/peso mayor/regiones/teaching **y la lista de hospitales de cada escenario** |
| `reports/fase1_6a/eicu_plausibilidad.json` | Densos e implausibles, veredicto por hospital y diagnóstico de etiquetas de 411/413/412/259 |
| `scripts/verify/fase1_6a/hospital_census_v2.py` | Cálculo del censo v2 |
| `scripts/verify/fase1_6a/scenarios_v2_table.py` | Reproduce la tabla de escenarios y la sensibilidad desde el CSV |

## 6. Limitaciones

1. `oobIntubDay1` no cubre intubaciones en quirófano: la sensibilidad de la evidencia
   APACHE es baja por diseño y varios hospitales con cirugía intensiva quedan con
   `rc/intub > 1` sin que ello sea un error.
2. `airwaytype` sólo está cumplimentado en ~1 % de las estancias (8 149 `airway_ett`):
   `pct_vent_with_airway` no puede usarse como criterio de selección.
3. La cobertura de MAP invasiva (21.8 %) impide exigir MAP arterial en todo el dataset.
4. `respiratoryCare` (`ventstartoffset`/`ventendoffset`) sigue sin documentar: la
   ventana ventilada se reconstruye desde los ajustes invasivos, no desde la tabla.

## 7. Propuesta para la selección (la decide el investigador)

- **Recomendado: escenario e2** (53 hospitales, 19 780 estancias utilizables, peso mayor
  7.7 %, 5 docentes, 4 regiones): máxima muestra conservando plausibilidad estricta y
  ≥ 50 utilizables por hospital.
- **Máxima cobertura: escenario g’** (75 hospitales, 21 981 utilizables) si se acepta
  incluir el hospital 259 (denso pero concordante).
- **Máxima homogeneidad temporal: escenario f** (29 hospitales, 8 027 utilizables) si se
  exige anotación cada ≤ 2 h; pierde 3 de las 4 regiones docentes.

**PARADA.** No se ha calculado ninguna etiqueta ni aplicado la selección.
