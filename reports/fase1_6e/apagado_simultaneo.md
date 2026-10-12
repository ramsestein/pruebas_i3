# Fase 1.6e, punto 2c.1 — apagado simultáneo monitor+ventilador

### clinic: 2c.3 control negativo y discriminación

Candidatos (hueco ≤ 12 h, posible mismo curso): **45**

| Criterio | Pares control (>24 h) | Cumplen | Tasa FP |
|---|---|---|---|
| ΔPEEP ≤ 3 y ΔFiO2 ≤ 20 (D-f) | 27 | 16 | **59.3 %** |
| a + |ΔTV| ≤ 50 mL | 0 | 0 | — |
| a + |ΔRR| ≤ 2 | 0 | 0 | — |
| a + TV y RR | 0 | 0 | — |

De los candidatos (≤12 h), cumplen: {'a': 40, 'b': 0, 'c': 0, 'd': 0}

|Δ| del hueco | grupo | n | p10 | p50 | p90 |
|---|---|---|---|---|---|
| d_peep | control | 28 | 0.10 | 1.35 | 4.00 |
| d_peep | candidatos | 44 | 0.00 | 0.10 | 2.10 |
| d_fio2 | control | 28 | 0.00 | 10.00 | 40.00 |
| d_fio2 | candidatos | 44 | 0.00 | 0.00 | 6.00 |

Sensibilidad de la regla a los umbrales de continuidad:

| ΔPEEP ≤ | ΔFiO2 ≤ | FP (control >24 h) | Candidatos que pasarían |
|---|---|---|---|
| 3 | 20 | 60.7 % | 90.9 % (40/44) |
| 2 | 10 | 39.3 % | 84.1 % (37/44) |
| 1 | 10 | 28.6 % | 72.7 % (32/44) |
| 1 | 5 | 17.9 % | 68.2 % (30/44) |
| 0.5 | 5 | 10.7 % | 68.2 % (30/44) |
| 0.5 | 2 | 10.7 % | 59.1 % (26/44) |

Aun con el umbral más estricto (ΔPEEP ≤ 0,5 y ΔFiO2 ≤ 2) la tasa de FP queda en el límite (> 10 %) y solo conserva ~59 % de los candidatos: el criterio casi no separa, porque PEEP y FiO2 se repiten entre pacientes.


**Veredicto 2c.3**: la tasa de falsos positivos estimada es **> 10 %** → **NO se aplica** la regla de unión (D-f) tal cual: requiere decisión del usuario.

## clinic

- Eventos con apagado simultáneo (≤ 15 min): **63**
- Clases: {'A_vuelta_ventilada': 7, 'C_salida': 55, 'D_indeterminado': 1}

| Evento | hueco (h) | ficheros en el hueco | empieza activa | fragmento | ΔPEEP | ΔFiO2 | ΔTV | ΔRR | ΔFC | ΔSpO2 | clase |
|---|---|---|---|---|---|---|---|---|---|---|---|
| clinic_box10_event_4 | 10.21 | None | True | False | -1.3 | 0.0 | 18.0 | -10.0 | None | None | A_vuelta_ventilada |
| clinic_box13_event_3 | 2.653 | None | True | True | 0.0 | 0.0 | -88.0 | 2.0 | None | None | A_vuelta_ventilada |
| clinic_box14_event_36 | 3.698 | None | True | False | 0.0 | 0.0 | -11.25 | 3.5 | None | None | A_vuelta_ventilada |
| clinic_box14_event_37 | 11.564 | None | True | False | 0.0 | 0.0 | 38.0 | -2.0 | None | None | A_vuelta_ventilada |
| clinic_box2_event_9 | 2.294 | None | True | False | 0.0 | 6.0 | 35.5 | -7.0 | None | None | A_vuelta_ventilada |
| clinic_box3_event_5 | 2.784 | None | True | False | 0.1 | 1.0 | 1.0 | 0.0 | None | None | A_vuelta_ventilada |
| clinic_box8_event_2 | 4.338 | None | True | False | -2.1 | 3.0 | -453.0 | -6.0 | None | None | A_vuelta_ventilada |
| clinic_box12_event_1 | 618.068 | None | False | False | 6.0 | -2.0 | -228.0 | 2.0 | None | None | C_salida |
| clinic_box12_event_16 | 3.379 | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box12_event_18 | None | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box12_event_3 | 390.373 | None | False | False | 2.1 | -11.0 | -250.0 | -3.0 | None | None | C_salida |
| clinic_box12_event_4 | 459.432 | None | False | False | -0.6 | 4.0 | 45.5 | 3.0 | None | None | C_salida |
| clinic_box12_event_5 | 2.108 | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box12_event_7 | 4.774 | None | False | False | -0.3 | 0.0 | 2.0 | 0.0 | None | None | C_salida |
| clinic_box12_event_8 | 5.399 | None | False | False | -0.1 | 0.0 | 1.0 | 0.0 | None | None | C_salida |
| clinic_box12_event_9 | 1563.009 | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box13_event_1 | 11.705 | None | False | False | -0.05 | -1.0 | -102.0 | 1.0 | None | None | C_salida |
| clinic_box13_event_4 | 2.175 | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box14_event_10 | 380.859 | None | False | False | 1.5 | 11.0 | -197.5 | 4.0 | None | None | C_salida |
| clinic_box14_event_16 | 233.203 | None | False | False | -0.2 | None | 146.0 | 1.0 | None | None | C_salida |
| clinic_box14_event_2 | 452.988 | None | False | True | None | None | None | None | None | None | C_salida |
| clinic_box14_event_23 | 2.982 | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box14_event_25 | 3.638 | None | False | False | 2.1 | 0.0 | -2.0 | 0.0 | None | None | C_salida |
| clinic_box14_event_29 | 14.165 | None | False | True | None | None | None | None | None | None | C_salida |
| clinic_box14_event_33 | 96.385 | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box14_event_35 | 48.001 | None | True | False | -0.1 | -26.0 | 56.0 | -3.0 | None | None | C_salida |
| clinic_box14_event_38 | 3148.097 | None | False | False | None | 0.0 | None | None | None | None | C_salida |
| clinic_box14_event_5 | 599.801 | None | False | False | None | None | None | None | None | None | C_salida |
| clinic_box14_event_9 | 2.337 | None | False | False | 0.05 | 3.0 | 59.0 | -3.0 | None | None | C_salida |
| clinic_box2_event_1 | 16.822 | None | True | False | -2.9 | 10.0 | -59.0 | 3.0 | None | None | C_salida |
| clinic_box2_event_10 | None | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box2_event_2 | 4.723 | None | False | False | 3.0 | 0.0 | 31.0 | -6.0 | None | None | C_salida |
| clinic_box2_event_3 | 369.974 | None | False | False | 4.0 | -11.0 | 51.0 | 1.0 | None | None | C_salida |
| clinic_box2_event_6 | 241.294 | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box3_event_1 | 144.429 | None | False | False | -1.7 | -7.0 | 46.0 | -4.0 | None | None | C_salida |
| clinic_box3_event_6 | 75.133 | None | True | False | 0.1 | 0.0 | 39.0 | 6.0 | None | None | C_salida |
| clinic_box3_event_8 | 52.455 | None | True | False | 2.1 | -5.0 | 22.0 | 2.0 | None | None | C_salida |
| clinic_box3_event_9 | 76.355 | None | True | False | -0.7 | -9.0 | -22.0 | -12.0 | None | None | C_salida |
| clinic_box4_event_14 | 3.567 | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box4_event_16 | 3.032 | None | False | False | -3.6 | -6.0 | 30.75 | -6.0 | None | None | C_salida |
| clinic_box4_event_18 | 39.671 | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box4_event_21 | 3.936 | None | False | False | -0.5 | -5.0 | -63.0 | 5.0 | None | None | C_salida |
| clinic_box4_event_23 | 3.24 | None | False | False | 0.45 | 6.0 | -16.5 | 1.25 | None | None | C_salida |
| clinic_box4_event_24 | 2.63 | None | False | False | 0.1 | 0.0 | 5.5 | 0.0 | None | None | C_salida |
| clinic_box4_event_26 | 54.994 | None | False | False | -0.1 | 11.0 | -168.0 | 16.0 | None | None | C_salida |
| clinic_box4_event_7 | 110.795 | None | False | False | 0.4 | 9.0 | -6.0 | -7.0 | None | None | C_salida |
| clinic_box5_event_2 | 714.259 | None | True | False | -2.0 | -40.0 | 51.0 | 1.0 | None | None | C_salida |
| clinic_box5_event_6 | 285.258 | None | False | False | -5.9 | -5.5 | -390.0 | 29.0 | None | None | C_salida |
| clinic_box6_event_16 | 41.094 | None | True | False | 0.0 | -45.5 | 4.5 | 3.0 | None | None | C_salida |
| clinic_box6_event_18 | 26.694 | None | False | False | 1.1 | 21.0 | 256.0 | 8.0 | None | None | C_salida |
| clinic_box6_event_19 | None | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box6_event_2 | 2.329 | None | False | False | 0.2 | 0.0 | 15.5 | -6.0 | None | None | C_salida |
| clinic_box6_event_8 | 42.306 | None | False | False | 0.0 | 0.0 | 86.0 | 2.0 | None | None | C_salida |
| clinic_box6_event_9 | 326.938 | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box7_event_1 | 22.425 | None | False | False | 0.0 | 0.0 | 6.5 | 0.0 | None | None | C_salida |
| clinic_box7_event_2 | 3219.698 | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box7_event_5 | 312.492 | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box7_event_7 | None | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box8_event_10 | None | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box8_event_4 | 415.796 | None | False | False | -4.7 | -10.0 | 147.5 | 5.0 | None | None | C_salida |
| clinic_box8_event_8 | 58.107 | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box9_event_4 | 1571.931 | None | None | None | None | None | None | None | None | None | C_salida |
| clinic_box2_event_5 | 2.165 | None | True | True | 5.0 | 52.0 | -59.5 | 4.0 | None | None | D_indeterminado |

