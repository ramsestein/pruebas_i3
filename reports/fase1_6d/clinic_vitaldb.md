# Clínic y VitalDB tras la reconstrucción (Fase 1.6c, punto 4)

| Cohorte | Eventos | Exc. | Éxito 48 h | Censura 48 h | Fallos (≥1) | D13 (FC+SpO2 > 50 %) | vars_ok 50 % | vars_ok 80 % |
|---|---|---|---|---|---|---|---|---|
| clinic | 181 | 4 | 65 | 116 | 7 | 172 (95.03 %) | 151 | 139 |
| vitaldb | 96 | 18 | 40 | 56 | 5 | 67 (69.79 %) | 22 | 16 |

## Detalle por cohorte

### clinic

- Índice: `datasets\clinic\cases_v0.5.0_eac1eb35\clinic_cases_index.json`
- Excluidos: 4 · niveles: {'A': 181, 'B': 0, 'D': 0}
- Censura 48 h por causa: {'end_of_record': 115, 'terminal_extubation': 1}
- Censura 72 h por causa: {'end_of_record': 115, 'terminal_extubation': 1}
- `end_reason`: {'end_of_record': 116, 'extubation_observed': 64, 'death_at_vent': 1} (desconocidos: ninguno)
- Eventos sin `source_files`: 0
- Error de etiqueta: no aplica (señal continua)
- Variables obligatorias (señal): FiO2, HR, MAP, PEEP, RR, SpO2

| Variable | n | Mediana | p10 | Eventos > 50 % |
|---|---|---|---|---|
| FiO2 | 181 | 1.000 | 0.594 | 91.7 % |
| HR | 181 | 1.000 | 0.920 | 95.0 % |
| MAP | 181 | 1.000 | 0.815 | 93.4 % |
| PEEP | 181 | 1.000 | 0.000 | 85.1 % |
| RR | 181 | 1.000 | 0.000 | 85.1 % |
| SpO2 | 181 | 1.000 | 0.918 | 95.0 % |

### vitaldb

- Índice: `datasets\vitaldb\cases_v0.4.0_aa142f35\vitaldb_cases_index.json`
- Excluidos: 18 · niveles: {'A': 96, 'B': 0, 'D': 0}
- Censura 48 h por causa: {'end_of_record': 56}
- Censura 72 h por causa: {'end_of_record': 57}
- `end_reason`: {'end_of_record': 60, 'extubation_observed': 36} (desconocidos: ninguno)
- Eventos sin `source_files`: 4
- Error de etiqueta: no aplica (señal continua)
- Variables obligatorias (señal): FiO2, HR, MAP, PEEP, RR, SpO2

| Variable | n | Mediana | p10 | Eventos > 50 % |
|---|---|---|---|---|
| FiO2 | 96 | 0.000 | 0.000 | 33.3 % |
| HR | 96 | 0.871 | 0.000 | 70.8 % |
| MAP | 96 | 0.871 | 0.000 | 70.8 % |
| PEEP | 96 | 0.000 | 0.000 | 24.0 % |
| RR | 96 | 0.921 | 0.000 | 71.9 % |
| SpO2 | 96 | 0.913 | 0.000 | 71.9 % |

