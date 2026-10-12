# Fase 1.6e, punto 2c.1 — apagado simultáneo monitor+ventilador

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

**Decision (12/10)**: se mantiene la regla ESTRICTA tal cual. Las variantes de la tabla son informativas: en ClinIC no hay ningun caso con grabador parado y hueco <= 2 h, asi que no se une ninguna pareja y los eventos con apagado simultaneo quedan censurados como hasta ahora (`end_of_record`).

| Evento | hueco (h) | ficheros en el hueco | empieza activa | fragmento | dPEEP | dFiO2 | dTV | dRR | dFC | dSpO2 | clase |
|---|---|---|---|---|---|---|---|---|---|---|---|
| clinic_box10_event_4 | 10.21 | True | True | False | -1.3 | 0.0 | 18.0 | -10.0 | None | None | A_vuelta_ventilada |
| clinic_box13_event_3 | 2.653 | True | True | True | 0.0 | 0.0 | -88.0 | 2.0 | None | None | A_vuelta_ventilada |
| clinic_box14_event_36 | 3.698 | True | True | False | 0.0 | 0.0 | -11.25 | 3.5 | None | None | A_vuelta_ventilada |
| clinic_box2_event_9 | 2.294 | True | True | False | 0.0 | 6.0 | 35.5 | -7.0 | None | None | A_vuelta_ventilada |
| clinic_box3_event_5 | 2.784 | True | True | False | 0.1 | 1.0 | 1.0 | 0.0 | None | None | A_vuelta_ventilada |
| clinic_box8_event_2 | 4.338 | True | True | False | -2.1 | 3.0 | -453.0 | -6.0 | None | None | A_vuelta_ventilada |
| clinic_box14_event_37 | 11.564 | False | True | False | 0.0 | 0.0 | 38.0 | -2.0 | None | None | B_parada_grabador |
| clinic_box12_event_1 | 618.068 | True | False | False | 6.0 | -2.0 | -228.0 | 2.0 | None | None | C_salida |
| clinic_box12_event_16 | 3.379 | True | None | None | None | None | None | None | None | None | C_salida |
| clinic_box12_event_18 | None | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box12_event_3 | 390.373 | True | False | False | 2.1 | -11.0 | -250.0 | -3.0 | None | None | C_salida |
| clinic_box12_event_4 | 459.432 | True | False | False | -0.6 | 4.0 | 45.5 | 3.0 | None | None | C_salida |
| clinic_box12_event_5 | 2.108 | True | None | None | None | None | None | None | None | None | C_salida |
| clinic_box12_event_7 | 4.774 | True | False | False | -0.3 | 0.0 | 2.0 | 0.0 | None | None | C_salida |
| clinic_box12_event_8 | 5.399 | True | False | False | -0.1 | 0.0 | 1.0 | 0.0 | None | None | C_salida |
| clinic_box12_event_9 | 1563.009 | True | None | None | None | None | None | None | None | None | C_salida |
| clinic_box13_event_1 | 11.705 | True | False | False | -0.05 | -1.0 | -102.0 | 1.0 | None | None | C_salida |
| clinic_box13_event_4 | 2.175 | True | None | None | None | None | None | None | None | None | C_salida |
| clinic_box14_event_10 | 380.859 | True | False | False | 1.5 | 11.0 | -197.5 | 4.0 | None | None | C_salida |
| clinic_box14_event_16 | 233.203 | False | False | False | -0.2 | None | 146.0 | 1.0 | None | None | C_salida |
| clinic_box14_event_2 | 452.988 | True | False | True | None | None | None | None | None | None | C_salida |
| clinic_box14_event_23 | 2.982 | False | None | None | None | None | None | None | None | None | C_salida |
| clinic_box14_event_25 | 3.638 | True | False | False | 2.1 | 0.0 | -2.0 | 0.0 | None | None | C_salida |
| clinic_box14_event_29 | 14.165 | False | False | True | None | None | None | None | None | None | C_salida |
| clinic_box14_event_33 | 96.385 | True | None | None | None | None | None | None | None | None | C_salida |
| clinic_box14_event_35 | 48.001 | True | True | False | -0.1 | -26.0 | 56.0 | -3.0 | None | None | C_salida |
| clinic_box14_event_38 | 3148.097 | True | False | False | None | 0.0 | None | None | None | None | C_salida |
| clinic_box14_event_5 | 599.801 | True | False | False | None | None | None | None | None | None | C_salida |
| clinic_box14_event_9 | 2.337 | True | False | False | 0.05 | 3.0 | 59.0 | -3.0 | None | None | C_salida |
| clinic_box2_event_1 | 16.822 | True | True | False | -2.9 | 10.0 | -59.0 | 3.0 | None | None | C_salida |
| clinic_box2_event_10 | None | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box2_event_2 | 4.723 | True | False | False | 3.0 | 0.0 | 31.0 | -6.0 | None | None | C_salida |
| clinic_box2_event_3 | 369.974 | True | False | False | 4.0 | -11.0 | 51.0 | 1.0 | None | None | C_salida |
| clinic_box2_event_6 | 241.294 | True | None | None | None | None | None | None | None | None | C_salida |
| clinic_box3_event_1 | 144.429 | True | False | False | -1.7 | -7.0 | 46.0 | -4.0 | None | None | C_salida |
| clinic_box3_event_6 | 75.133 | True | True | False | 0.1 | 0.0 | 39.0 | 6.0 | None | None | C_salida |
| clinic_box3_event_8 | 52.455 | True | True | False | 2.1 | -5.0 | 22.0 | 2.0 | None | None | C_salida |
| clinic_box3_event_9 | 76.355 | True | True | False | -0.7 | -9.0 | -22.0 | -12.0 | None | None | C_salida |
| clinic_box4_event_14 | 3.567 | True | None | None | None | None | None | None | None | None | C_salida |
| clinic_box4_event_16 | 3.032 | True | False | False | -3.6 | -6.0 | 30.75 | -6.0 | None | None | C_salida |
| clinic_box4_event_18 | 39.671 | False | None | None | None | None | None | None | None | None | C_salida |
| clinic_box4_event_21 | 3.936 | True | False | False | -0.5 | -5.0 | -63.0 | 5.0 | None | None | C_salida |
| clinic_box4_event_23 | 3.24 | True | False | False | 0.45 | 6.0 | -16.5 | 1.25 | None | None | C_salida |
| clinic_box4_event_24 | 2.63 | True | False | False | 0.1 | 0.0 | 5.5 | 0.0 | None | None | C_salida |
| clinic_box4_event_26 | 54.994 | True | False | False | -0.1 | 11.0 | -168.0 | 16.0 | None | None | C_salida |
| clinic_box4_event_7 | 110.795 | True | False | False | 0.4 | 9.0 | -6.0 | -7.0 | None | None | C_salida |
| clinic_box5_event_2 | 714.259 | True | True | False | -2.0 | -40.0 | 51.0 | 1.0 | None | None | C_salida |
| clinic_box5_event_6 | 285.258 | True | False | False | -5.9 | -5.5 | -390.0 | 29.0 | None | None | C_salida |
| clinic_box6_event_16 | 41.094 | True | True | False | 0.0 | -45.5 | 4.5 | 3.0 | None | None | C_salida |
| clinic_box6_event_18 | 26.694 | True | False | False | 1.1 | 21.0 | 256.0 | 8.0 | None | None | C_salida |
| clinic_box6_event_19 | None | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box6_event_2 | 2.329 | True | False | False | 0.2 | 0.0 | 15.5 | -6.0 | None | None | C_salida |
| clinic_box6_event_8 | 42.306 | True | False | False | 0.0 | 0.0 | 86.0 | 2.0 | None | None | C_salida |
| clinic_box6_event_9 | 326.938 | True | None | None | None | None | None | None | None | None | C_salida |
| clinic_box7_event_1 | 22.425 | True | False | False | 0.0 | 0.0 | 6.5 | 0.0 | None | None | C_salida |
| clinic_box7_event_2 | 3219.698 | True | None | None | None | None | None | None | None | None | C_salida |
| clinic_box7_event_5 | 312.492 | True | None | None | None | None | None | None | None | None | C_salida |
| clinic_box7_event_7 | None | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box8_event_10 | None | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box8_event_4 | 415.796 | True | False | False | -4.7 | -10.0 | 147.5 | 5.0 | None | None | C_salida |
| clinic_box8_event_8 | 58.107 | True | None | None | None | None | None | None | None | None | C_salida |
| clinic_box9_event_4 | 1571.931 | True | None | None | None | None | None | None | None | None | C_salida |
| clinic_box2_event_5 | 2.165 | True | True | True | 5.0 | 52.0 | -59.5 | 4.0 | None | None | D_indeterminado |

