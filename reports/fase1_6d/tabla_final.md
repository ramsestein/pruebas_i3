# Tabla final de las 4 cohortes (Fase 1.6d, punto 8)

| Cohorte | Eventos | D13 | Éxito 48 h | Censura 48 h | Fallos (≥1) | vars_ok 50 % | vars_ok 80 % | vars_ok 50 % + ondas | Error de etiqueta | Perfil dominante | Partición |
|---|---|---|---|---|---|---|---|---|---|---|---|
| mimic | 9093 | 8983 (98.8 %) | 7455 | 1638 | 471 | 8035 | 5514 | — | 0.00 h / 100 % (referencia) | las 6 variables (88.4 %) | test 15% por subject_id + 5 pliegues por paciente |
| eicu_b | 9753 | 9340 (95.8 %) | 7523 | 2230 | 688 | 7068 | 5242 | — | eICU-B: corregida 87.0 % (0.00 h) | HR+SpO2+MAP+RR+FiO2+PEEP | test = 8 hospitales (fijos) + 5 pliegues por hospital del resto |
| clinic | 181 | 172 (95.0 %) | 65 | 116 | 7 | 151 | 139 | — | no aplica (señal continua) | HR+SpO2+MAP+RR+FiO2+PEEP | test = último 40% del tiempo; train en 2 bloques temporales (ventana c |
| vitaldb | 96 | 67 (69.8 %) | 40 | 56 | 5 | 22 | 16 | — | no aplica (señal continua) | HR+SpO2+MAP+RR | los 96 eventos = test (test externo; sin ajuste fino ni validación cru |

Notas:

- Éxito y censura son mutuamente excluyentes; los eventos con ≥ 1 fallo van aparte (también existen entre los censurados).
- `vars_ok` (6 variables) es indicador de calidad, nunca filtro; el filtro es D13 (FC y SpO2 > 50 %).
- `vars_ok + ondas` solo aparece si la validación Bland–Altman del punto 4 aprueba la variable derivada.

