# Tabla final de las 4 cohortes (Fase 1.6b, punto 5)

> Corrección de la Fase 1.6c (punto 4): el error de etiqueta de Clínic y VitalDB es **no aplica (señal continua)**; la 1.6b les trasladaba el error de anotación calibrado en MIMIC, que no corresponde a una etiqueta derivada de la onda.

| Cohorte | Eventos | Éxito 48 h | Censura 48 h | Fallos (≥1) | vars_ok 50 % | vars_ok 80 % | Error de etiqueta (fin) | Etiq. 48 h |
|---|---|---|---|---|---|---|---|---|
| mimic | 9093 | 7455 | 1638 | 471 | 1932 | 551 | 0.00 h | 100.0 % |
| eicu | 23362 | 19720 | 3642 | 2775 | 15999 | 10819 | -2.05 h | 78.9 % |
| clinic | 181 | 140 | 41 | 7 | 0 | 0 | no aplica (señal continua) | — |
| vitaldb | 96 | 75 | 21 | 5 | 5 | 2 | no aplica (señal continua) | — |

Causas de censura (48 h):

- **mimic**: {'death_at_vent': 556, 'trach': 226, 'terminal_extubation': 418, 'transfer_ventilated': 438}
- **eicu**: {'terminal_extubation': 4496, 'transfer_ventilated': 1936, 'trach': 746, 'death_at_vent': 190}
- **clinic**: {'end_of_record': 40, 'terminal_extubation': 1}
- **vitaldb**: {'end_of_record': 21}
