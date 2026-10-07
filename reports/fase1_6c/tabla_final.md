# Tabla final de las 4 cohortes (Fase 1.6c, punto 8)

D13 = evento incluido si tiene FC y SpO2 con ≥ 50 % de horas útiles (D8). `vars_ok` (6 variables) es indicador de calidad, no filtro.

| Cohorte | Eventos | Incluidos D13 | Éxito 48 h | Censura 48 h | Fallos (≥1) | vars_ok 50 % | vars_ok 80 % | Error de etiqueta | Perfil dominante |
|---|---|---|---|---|---|---|---|---|---|
| mimic | 9093 | 8983 (98.8 %) | 7455 | 1638 | 471 | 88.4 % | 60.6 % | 0.00 h / 100.0 % | HR+SpO2+MAP+RR+FiO2+PEEP (88.4 %) |
| eicu | 9754 | 9222 (94.5 %) | 7419 | 2335 | 1093 | 72.0 % | 52.9 % | 0.00 h / 87.0 % | HR+SpO2+MAP+RR+FiO2+PEEP (72.0 %) |
| clinic | 181 | — (— %) | 140 | 41 | 7 | — % | — % | no aplica (señal continua) | (ninguna) (100.0 %) |
| vitaldb | 96 | 67 (69.8 %) | 40 | 56 | 5 | 22.9 % | 16.7 % | no aplica (señal continua) | HR+SpO2+MAP+RR (30.2 %) |

## Detalle

### mimic

- Índice: `datasets\mimic3wdb\cases_v0.4.0_aa142f35\mimic_cases_index.json` (9093 eventos)
- Censura 48 h por causa: {'death_at_vent': 556, 'transfer_ventilated': 438, 'terminal_extubation': 418, 'trach': 226}
- `end_reason`: {'extubation_observed': 7027, 'death': 1297, 'transfer_ventilated': 459, 'tracheostomy': 310}
- Error de etiqueta: referencia → 0.00 h / 100.0 %

### eicu

- Índice: `datasets\eicu_collaborative\cases_v0.4.0_aa142f35\eicu_cases_index.json` (9754 eventos, 27 hospitales)
- Censura 48 h por causa: {'transfer_ventilated': 969, 'terminal_extubation': 830, 'death_at_vent': 439, 'trach': 97}
- `end_reason`: {'extubation_observed': 7948, 'transfer_ventilated': 1358, 'death_at_vent': 448}
- Error de etiqueta: corrección del fin por estrato aplicada → 0.00 h / 87.0 %

- Corrección del fin por estrato: desplazamientos {'1.0': 1.2332999999634922, '2.0': 1.5, '4.0': 2.3083333333488554}; ¿mejora en MIMIC (1 h y 2 h)? **SÍ**

- eICU sin corrección: -1.34 h / 84.7 % (estratos {'1_2h': 3866, 'le1h': 5888})

### clinic

- Índice: `datasets\clinic\cases_v0.1.0_a225d21b\clinic_cases_index.json` (181 eventos)
- Censura 48 h por causa: {'end_of_record': 40, 'terminal_extubation': 1}
- `end_reason`: {'extubation_observed': 139, 'end_of_record': 41, 'death_signal': 1}
- Error de etiqueta: no aplica (señal continua)
- **reconstrucción de la fase pendiente (índice antiguo)** (las cifras de la fila son del índice antiguo, no de esta fase)

### vitaldb

- Índice: `datasets\vitaldb\cases_v0.4.0_aa142f35\vitaldb_cases_index.json` (96 eventos)
- Censura 48 h por causa: {'end_of_record': 56}
- `end_reason`: {'end_of_record': 60, 'extubation_observed': 36}
- Error de etiqueta: no aplica (señal continua)

