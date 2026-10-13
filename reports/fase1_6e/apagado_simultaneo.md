# Fase 1.6e, punto 2c.1 — apagado simultáneo monitor+ventilador

### clinic: 2c.3 control negativo y discriminacion

Candidatos (hueco <= 12 h, posible mismo curso): **45**

| Criterio | Pares control (>24 h) | Cumplen | Tasa FP | IC 95 % (Wilson) |
|---|---|---|---|---|
| dPEEP <= 3 y dFiO2 <= 20 (D-f) | 27 | 16 | 59.3 % | [40.7 %, 75.5 %] |
| a + |dTV| <= 50 mL | 27 | 6 | 22.2 % | [10.6 %, 40.8 %] |
| a + |dRR| <= 2 | 27 | 3 | 11.1 % | [3.9 %, 28.1 %] |
| a + TV y RR | 27 | 1 | 3.7 % | [0.7 %, 18.3 %] |

De los candidatos (<= 12 h), cumplen: {'a': 40, 'b': 29, 'c': 24, 'd': 21}

|Δ| del hueco | grupo | n | p10 | p50 | p90 |
|---|---|---|---|---|---|
| d_peep | control | 28 | 0.10 | 1.35 | 4.00 |
| d_peep | candidatos | 44 | 0.00 | 0.10 | 2.10 |
| d_fio2 | control | 28 | 0.00 | 10.00 | 40.00 |
| d_fio2 | candidatos | 44 | 0.00 | 0.00 | 6.00 |
| d_tv | control | 28 | 22.00 | 128.00 | 250.50 |
| d_tv | candidatos | 44 | 1.00 | 18.00 | 86.00 |
| d_rr | control | 28 | 1.00 | 3.00 | 9.00 |
| d_rr | candidatos | 44 | 0.00 | 2.00 | 7.00 |

Sensibilidad de la regla a los umbrales de continuidad:

| dPEEP <= | dFiO2 <= | FP (control >24 h) | IC 95 % (Wilson) | Candidatos que pasarian |
|---|---|---|---|---|
| 3 | 20 | 60.7 % | [42.4 %, 76.4 %] | 90.9 % (40/44) |
| 2 | 10 | 39.3 % | [23.6 %, 57.6 %] | 84.1 % (37/44) |
| 1 | 10 | 28.6 % | [15.3 %, 47.1 %] | 72.7 % (32/44) |
| 1 | 5 | 17.9 % | [7.9 %, 35.6 %] | 68.2 % (30/44) |
| 0.5 | 5 | 10.7 % | [3.7 %, 27.2 %] | 68.2 % (30/44) |
| 0.5 | 2 | 10.7 % | [3.7 %, 27.2 %] | 59.1 % (26/44) |

Aun con el umbral mas estricto (dPEEP <= 0,5 y dFiO2 <= 2) la tasa de FP queda en el limite (> 10 %) y solo conserva ~59 % de los candidatos: el criterio casi no separa, porque PEEP y FiO2 se repiten entre pacientes.

**Veredicto 2c.3**: la tasa de falsos positivos estimada es **> 10 %** → **NO se aplica** la regla de union (D-f) tal cual.

## clinic

- Eventos con apagado simultaneo (<= 15 min): **63**
- Clases: {'A_vuelta_ventilada': 6, 'C_salida': 55, 'B_parada_grabador': 1, 'D_indeterminado': 1}

### Union de cursos (regla estrecha, D-f)

- Cumplen (a) hueco <= 2 h, (b) sin ficheros en el hueco y (c) la region siguiente empieza ventilada: **0**
- Descartados por (c) (no vuelve ventilada en <= 15 min): 0
- Sin sondeo (`tiene_ficheros = None`): 5 (sin leer el hueco la regla no se puede evaluar)

| Variante | Uniones en esta cohorte |
|---|---|
| Aplicada: sin ficheros y <= 2 h | 0 |
| Tambien con grabacion (box on, paciente fuera) y <= 6 h | 6 |
| Tambien con grabacion (box on, paciente fuera) y <= 12 h | 8 |

**Conclusion (clinic)**: se mantiene la regla ESTRICTA. En clinic no hay ninguna pareja con grabador parado y hueco <= 2 h (la unica candidata de ese tipo tiene un hueco mayor), asi que no se une ninguna y los apagados simultaneos quedan censurados como hasta ahora (`end_of_record`). Las variantes de la tabla son informativas, no se aplican. Total de eventos con apagado simultaneo en clinic: 63.

| Evento | hueco (h) | ficheros en el hueco | empieza activa | fragmento | dPEEP | dFiO2 | dTV | dRR | dFC | dSpO2 | clase |
|---|---|---|---|---|---|---|---|---|---|---|---|
| clinic_box10_event_4 | 10.21 | True | True | False | -1.3 | 0.0 | 18.0 | -10.0 | 21.0 | -1.4 | A_vuelta_ventilada |
| clinic_box13_event_3 | 2.653 | True | True | True | 0.0 | 0.0 | -88.0 | 2.0 | -5.0 | -2.1 | A_vuelta_ventilada |
| clinic_box14_event_36 | 3.698 | True | True | False | 0.0 | 0.0 | -11.25 | 3.5 | 17.0 | -0.5 | A_vuelta_ventilada |
| clinic_box2_event_9 | 2.294 | True | True | False | 0.0 | 6.0 | 35.5 | -7.0 | 2.0 | -2.0 | A_vuelta_ventilada |
| clinic_box3_event_5 | 2.784 | True | True | False | 0.1 | 1.0 | 1.0 | 0.0 | -3.0 | 0.0 | A_vuelta_ventilada |
| clinic_box8_event_2 | 4.338 | True | True | False | -2.1 | 3.0 | -453.0 | -6.0 | -30.0 | 5.3 | A_vuelta_ventilada |
| clinic_box14_event_37 | 11.564 | False | True | False | 0.0 | 0.0 | 38.0 | -2.0 | -10.0 | -0.9 | B_parada_grabador |
| clinic_box12_event_1 | 618.068 | True | False | False | 6.0 | -2.0 | -228.0 | 2.0 | -22.0 | -5.6 | C_salida |
| clinic_box12_event_16 | 3.379 | True | None | None | None | None | None | None | 16.0 | -6.9 | C_salida |
| clinic_box12_event_18 | None | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box12_event_3 | 390.373 | True | False | False | 2.1 | -11.0 | -250.0 | -3.0 | None | None | C_salida |
| clinic_box12_event_4 | 459.432 | True | False | False | -0.6 | 4.0 | 45.5 | 3.0 | -16.0 | -0.1 | C_salida |
| clinic_box12_event_5 | 2.108 | True | None | None | None | None | None | None | 5.0 | -3.8 | C_salida |
| clinic_box12_event_7 | 4.774 | True | False | False | -0.3 | 0.0 | 2.0 | 0.0 | -10.0 | 0.3 | C_salida |
| clinic_box12_event_8 | 5.399 | True | False | False | -0.1 | 0.0 | 1.0 | 0.0 | -21.0 | 0.0 | C_salida |
| clinic_box12_event_9 | 1563.009 | True | None | None | None | None | None | None | 51.0 | -4.8 | C_salida |
| clinic_box13_event_1 | 11.705 | True | False | False | -0.05 | -1.0 | -102.0 | 1.0 | -29.0 | 11.2 | C_salida |
| clinic_box13_event_4 | 2.175 | True | None | None | None | None | None | None | 21.0 | 5.0 | C_salida |
| clinic_box14_event_10 | 380.859 | True | False | False | 1.5 | 11.0 | -197.5 | 4.0 | -8.0 | -5.5 | C_salida |
| clinic_box14_event_16 | 233.203 | False | False | False | -0.2 | None | 146.0 | 1.0 | 16.0 | -3.7 | C_salida |
| clinic_box14_event_2 | 452.988 | True | False | True | None | None | None | None | 3.0 | -3.7 | C_salida |
| clinic_box14_event_23 | 2.982 | False | None | None | None | None | None | None | 5.0 | -2.0 | C_salida |
| clinic_box14_event_25 | 3.638 | True | False | False | 2.1 | 0.0 | -2.0 | 0.0 | -1.0 | -0.45 | C_salida |
| clinic_box14_event_29 | 14.165 | False | False | True | None | None | None | None | -60.0 | 17.9 | C_salida |
| clinic_box14_event_33 | 96.385 | True | None | None | None | None | None | None | -5.0 | 6.1 | C_salida |
| clinic_box14_event_35 | 48.001 | True | True | False | -0.1 | -26.0 | 56.0 | -3.0 | None | None | C_salida |
| clinic_box14_event_38 | 3148.097 | True | False | False | None | 0.0 | None | None | 49.0 | -1.6 | C_salida |
| clinic_box14_event_5 | 599.801 | True | False | False | None | None | None | None | None | None | C_salida |
| clinic_box14_event_9 | 2.337 | True | False | False | 0.05 | 3.0 | 59.0 | -3.0 | 6.0 | 0.3 | C_salida |
| clinic_box2_event_1 | 16.822 | True | True | False | -2.9 | 10.0 | -59.0 | 3.0 | 11.0 | -3.9 | C_salida |
| clinic_box2_event_10 | None | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box2_event_2 | 4.723 | True | False | False | 3.0 | 0.0 | 31.0 | -6.0 | 9.0 | -2.0 | C_salida |
| clinic_box2_event_3 | 369.974 | True | False | False | 4.0 | -11.0 | 51.0 | 1.0 | -11.0 | 0.0 | C_salida |
| clinic_box2_event_6 | 241.294 | True | None | None | None | None | None | None | -12.0 | 0.4 | C_salida |
| clinic_box3_event_1 | 144.429 | True | False | False | -1.7 | -7.0 | 46.0 | -4.0 | -28.0 | 3.0 | C_salida |
| clinic_box3_event_6 | 75.133 | True | True | False | 0.1 | 0.0 | 39.0 | 6.0 | 12.0 | -0.8 | C_salida |
| clinic_box3_event_8 | 52.455 | True | True | False | 2.1 | -5.0 | 22.0 | 2.0 | None | None | C_salida |
| clinic_box3_event_9 | 76.355 | True | True | False | -0.7 | -9.0 | -22.0 | -12.0 | -8.0 | 4.0 | C_salida |
| clinic_box4_event_14 | 3.567 | True | None | None | None | None | None | None | 23.0 | -2.3 | C_salida |
| clinic_box4_event_16 | 3.032 | True | False | False | -3.6 | -6.0 | 30.75 | -6.0 | 4.0 | 0.1 | C_salida |
| clinic_box4_event_18 | 39.671 | False | None | None | None | None | None | None | 48.0 | 26.0 | C_salida |
| clinic_box4_event_21 | 3.936 | True | False | False | -0.5 | -5.0 | -63.0 | 5.0 | 21.0 | 0.0 | C_salida |
| clinic_box4_event_23 | 3.24 | True | False | False | 0.45 | 6.0 | -16.5 | 1.25 | 6.0 | 0.0 | C_salida |
| clinic_box4_event_24 | 2.63 | True | False | False | 0.1 | 0.0 | 5.5 | 0.0 | -38.0 | 0.0 | C_salida |
| clinic_box4_event_26 | 54.994 | True | False | False | -0.1 | 11.0 | -168.0 | 16.0 | 4.0 | -2.2 | C_salida |
| clinic_box4_event_7 | 110.795 | True | False | False | 0.4 | 9.0 | -6.0 | -7.0 | 7.0 | 2.0 | C_salida |
| clinic_box5_event_2 | 714.259 | True | True | False | -2.0 | -40.0 | 51.0 | 1.0 | None | None | C_salida |
| clinic_box5_event_6 | 285.258 | True | False | False | -5.9 | -5.5 | -390.0 | 29.0 | -26.0 | 1.6 | C_salida |
| clinic_box6_event_16 | 41.094 | True | True | False | 0.0 | -45.5 | 4.5 | 3.0 | -17.0 | -3.2 | C_salida |
| clinic_box6_event_18 | 26.694 | True | False | False | 1.1 | 21.0 | 256.0 | 8.0 | 20.0 | 36.15 | C_salida |
| clinic_box6_event_19 | None | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box6_event_2 | 2.329 | True | False | False | 0.2 | 0.0 | 15.5 | -6.0 | -2.0 | 1.8 | C_salida |
| clinic_box6_event_8 | 42.306 | True | False | False | 0.0 | 0.0 | 86.0 | 2.0 | 20.0 | -0.3 | C_salida |
| clinic_box6_event_9 | 326.938 | True | None | None | None | None | None | None | None | None | C_salida |
| clinic_box7_event_1 | 22.425 | True | False | False | 0.0 | 0.0 | 6.5 | 0.0 | 8.0 | 0.0 | C_salida |
| clinic_box7_event_2 | 3219.698 | True | None | None | None | None | None | None | None | None | C_salida |
| clinic_box7_event_5 | 312.492 | True | None | None | None | None | None | None | -5.0 | 1.8 | C_salida |
| clinic_box7_event_7 | None | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box8_event_10 | None | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box8_event_4 | 415.796 | True | False | False | -4.7 | -10.0 | 147.5 | 5.0 | 22.0 | -0.2 | C_salida |
| clinic_box8_event_8 | 58.107 | True | None | None | None | None | None | None | 14.0 | 2.4 | C_salida |
| clinic_box9_event_4 | 1571.931 | True | None | None | None | None | None | None | 15.0 | -2.2 | C_salida |
| clinic_box2_event_5 | 2.165 | True | True | True | 5.0 | 52.0 | -59.5 | 4.0 | -24.0 | 2.8 | D_indeterminado |

### vitaldb: 2c.3 control negativo y discriminacion

Candidatos (hueco <= 12 h, posible mismo curso): **15**

| Criterio | Pares control (>24 h) | Cumplen | Tasa FP | IC 95 % (Wilson) |
|---|---|---|---|---|
| dPEEP <= 3 y dFiO2 <= 20 (D-f) | 2 | 1 | 50.0 % | [9.5 %, 90.5 %] |
| a + |dTV| <= 50 mL | 2 | 1 | 50.0 % | [9.5 %, 90.5 %] |
| a + |dRR| <= 2 | 2 | 0 | 0.0 % | [0.0 %, 65.8 %] |
| a + TV y RR | 2 | 0 | 0.0 % | [0.0 %, 65.8 %] |

De los candidatos (<= 12 h), cumplen: {'a': 1, 'b': 1, 'c': 1, 'd': 1}

|Δ| del hueco | grupo | n | p10 | p50 | p90 |
|---|---|---|---|---|---|
| d_peep | control | 2 | 0.75 | 5.30 | 0.75 |
| d_peep | candidatos | 1 | 2.20 | 2.20 | 2.20 |
| d_fio2 | control | 2 | 10.40 | 31.30 | 10.40 |
| d_fio2 | candidatos | 12 | 0.00 | 0.15 | 0.40 |
| d_tv | control | 10 | 18.00 | 120.50 | 275.25 |
| d_tv | candidatos | 15 | 1.50 | 37.00 | 104.50 |
| d_rr | control | 9 | 1.00 | 5.00 | 10.00 |
| d_rr | candidatos | 15 | 0.00 | 1.90 | 6.78 |

Sensibilidad de la regla a los umbrales de continuidad:

| dPEEP <= | dFiO2 <= | FP (control >24 h) | IC 95 % (Wilson) | Candidatos que pasarian |
|---|---|---|---|---|
| 3 | 20 | 50.0 % | [9.5 %, 90.5 %] | 100.0 % (1/1) |
| 2 | 10 | 0.0 % | [0.0 %, 65.8 %] | 0.0 % (0/1) |
| 1 | 10 | 0.0 % | [0.0 %, 65.8 %] | 0.0 % (0/1) |
| 1 | 5 | 0.0 % | [0.0 %, 65.8 %] | 0.0 % (0/1) |
| 0.5 | 5 | 0.0 % | [0.0 %, 65.8 %] | 0.0 % (0/1) |
| 0.5 | 2 | 0.0 % | [0.0 %, 65.8 %] | 0.0 % (0/1) |

Aun con el umbral mas estricto (dPEEP <= 0,5 y dFiO2 <= 2) la tasa de FP queda en el limite (> 10 %) y solo conserva ~59 % de los candidatos: el criterio casi no separa, porque PEEP y FiO2 se repiten entre pacientes.

**Veredicto 2c.3**: la tasa de falsos positivos estimada es **> 10 %** → **NO se aplica** la regla de union (D-f) tal cual.

## vitaldb

**PRELIMINAR (B1)**: la base temporal del indice v0.4.0 de VitalDB mezcla dos relojes (nombre y contenido); estas horas se recalculan en el rebuild v0.6.0. En Clinic NO ocurre.

- Eventos con apagado simultaneo (<= 15 min): **15**
- Clases: {'C_salida': 15}

### Union de cursos (regla estrecha, D-f)

- Cumplen (a) hueco <= 2 h, (b) sin ficheros en el hueco y (c) la region siguiente empieza ventilada: **0**
- Descartados por (c) (no vuelve ventilada en <= 15 min): 0
- Sin sondeo (`tiene_ficheros = None`): 2 (sin leer el hueco la regla no se puede evaluar)

| Variante | Uniones en esta cohorte |
|---|---|
| Aplicada: sin ficheros y <= 2 h | 0 |
| Tambien con grabacion (box on, paciente fuera) y <= 6 h | 0 |
| Tambien con grabacion (box on, paciente fuera) y <= 12 h | 0 |

**Conclusion (vitaldb)**: se mantiene la regla ESTRICTA. En vitaldb no hay ninguna pareja con grabador parado y hueco <= 2 h (la unica candidata de ese tipo tiene un hueco mayor), asi que no se une ninguna y los apagados simultaneos quedan censurados como hasta ahora (`end_of_record`). Las variantes de la tabla son informativas, no se aplican. Total de eventos con apagado simultaneo en vitaldb: 15.

| Evento | hueco (h) | ficheros en el hueco | empieza activa | fragmento | dPEEP | dFiO2 | dTV | dRR | dFC | dSpO2 | clase |
|---|---|---|---|---|---|---|---|---|---|---|---|
| vitaldb_SICU1_03_event_6 | 91.008 | True | True | False | None | None | -238.0 | 10.0 | -9.0 | 1.7 | C_salida |
| vitaldb_SICU1_04_event_5 | None | None | None | None | None | None | None | None | None | None | C_salida |
| vitaldb_SICU1_05_event_2 | None | None | None | None | None | None | None | None | None | None | C_salida |
| vitaldb_SICU1_09_event_2 | 806.013 | True | False | False | None | None | None | None | 5.0 | -2.7 | C_salida |
| vitaldb_SICU1_09_event_4 | 91.007 | True | True | False | None | None | 86.0 | 5.0 | 23.0 | 0.0 | C_salida |
| vitaldb_SICU1_10_event_1 | 805.951 | True | False | False | None | None | 153.5 | -1.0 | -6.0 | 2.1 | C_salida |
| vitaldb_SICU1_10_event_2 | 92.007 | True | True | False | None | None | -94.75 | -4.0 | -5.0 | 0.0 | C_salida |
| vitaldb_SICU1_11_event_1 | 814.575 | True | None | None | None | None | None | None | None | None | C_salida |
| vitaldb_SICU1_12_event_2 | 18.691 | True | None | None | None | None | None | None | 17.0 | -0.6 | C_salida |
| vitaldb_SICU2_05_event_3 | 92.007 | True | None | None | None | None | None | None | None | None | C_salida |
| vitaldb_SICU2_07_event_1 | 675.007 | True | None | None | None | None | None | None | 3.0 | 0.0 | C_salida |
| vitaldb_SICU2_11_event_2 | 321.825 | True | False | False | -0.75 | -10.4 | 31.0 | -7.05 | None | None | C_salida |
| vitaldb_SICU2_12_event_1 | 674.006 | True | False | False | -5.3 | 31.3 | -18.0 | 7.55 | 0.0 | 2.4 | C_salida |
| vitaldb_SICU2_14_event_1 | 674.006 | True | False | False | None | None | 275.25 | -1.8 | -22.0 | 0.0 | C_salida |
| vitaldb_SICU2_19_event_1 | 738.183 | True | False | False | None | None | -120.5 | 10.15 | None | None | C_salida |

## Notas de interpretacion

1. **TV y FR SI se evaluan, y discriminan mucho mejor que los ajustes.** Las variantes b/c/d salian 0 pares por un fallo de nombres de clave en `_pair_deltas` (generaba `d_tv_exp`/`d_vent_rr` y el criterio buscaba `d_tv`/`d_rr`), no porque no hubiera datos: ya estan calculadas con su IC de Wilson. Con las 27 parejas de control:

   - a) PEEP y FiO2: **59,3 %** [40,7 %, 75,5 %] de falsos positivos - no discrimina (son ajustes que se repiten entre pacientes).

   - b) a + |dTV| <= 50 mL: **22,2 %** [10,6 %, 40,8 %].

   - c) a + |dRR| <= 2: **11,1 %** [3,9 %, 28,1 %].

   - d) a + TV y FR: **3,7 %** [0,7 %, 18,3 %]. El TV y la FR medidos son propios de cada paciente, y anadirlos baja el FP de 59 % a 3,7 %.

   **Consecuencia**: el punto de parada de 2c.3 (> 10 % de FP) se basaba en la variante a. La variante d queda por debajo en la estimacion puntual (limite superior 18,3 %). Aplicarla uniria 21 de las 45 parejas candidatas de Clinic. **Requiere decision del usuario**: cambiar la regla implica rehacer el rebuild v0.6.0, que ya esta aplicando la regla estricta.

   (La fila con 0 pares de control se imprime como **no evaluado**, nunca como "0 %".)

2. **La clase A/B/C/D tiene dos cifras porque son dos criterios distintos**, no porque haya un error:

   - El diagnostico (con lectura de ficheros) da **A = 6** entre los 135 eventos que puede analizar, con la continuidad de ajustes (PEEP/FiO2) evaluada.

   - La anotacion del indice da **A = 20** con el criterio aproximado (solo senal) y **A = 0** si no se sondea (cae a D, conservador). El indice no puede evaluar la continuidad de ajustes sin `--probar`.

   - Ademas, `simultaneous_shutdown` del builder compara con el fin de la REGION, mientras 2c.1 usa el fin de la observacion FISIOLOGICA. Con el test de 2c.1 (ya implementado en `annotate_index.py`) los pares pasan de 36 a 106.

   **Definicion unica**: la de 2c.1, aplicada con `--probar` sobre el indice v0.6.0 (misma base que el diagnostico que se corra sobre ese indice). Hasta esa anotacion, `prev_exit_class` debe leerse como aproximado.

3. **`next_return_ventilated_30min` es casi siempre cierto** (168 de 181 en Clinic): los pacientes llegan intubados de quirofano. Por si solo no informa. El campo util es el COMBINADO `next_return_ventilated_12h` (68 de 181), que exige ademas hueco <= 12 h, y es la base de la columna (c) de la tabla final.

