# G = 8 h frente a G = 10 h (Fase 1.6d, punto 1)

## Calibración en MIMIC

- G elegido: **10.0 h**

| Métrica | G = 8 h | G = 10 h |
|---|---|---|
| f1_reintub_1h | 0.398 | 0.440 |
| f1_reintub_2h | 0.394 | 0.438 |
| label48_agreement_1h | 85.242 | 85.242 |
| label48_agreement_2h | 83.844 | 83.855 |
| start_pct_2h_1h | 91.932 | 91.524 |
| start_pct_2h_2h | 91.869 | 91.457 |
| end_pct_2h_1h | 56.787 | 56.363 |
| end_pct_2h_2h | 52.531 | 52.123 |

## eICU-B

- Índices: `datasets\eicu_collaborative\cases_v0.4.0_aa142f35\eicu_cases_index.json` (8.0 h) → `datasets\eicu_collaborative\cases_v0.5.0_eac1eb35\eicu_cases_index.json` (10.0 h)

| Métrica | G = 8 h | G = 10 h |
|---|---|---|
| Eventos | 9754 | 9753 |
| Éxito 48 h | 7419 | 7523 |
| Censura 48 h | 2335 | 2230 |
| Fallos (≥1) | 1093 | 688 |
| D13 | 9222 | 9340 |

- Eventos sólo en G = 8: 7; sólo en G = 10: 6
- Eventos que **cambian de etiqueta** a 48 h: 129
- Tipos de cambio: {'censored_transfer_ventilated->successful_extubation': 105, 'censored_death_at_vent->censored_terminal_extubation': 12, 'successful_extubation->censored_transfer_ventilated': 7, 'successful_extubation->censored_terminal_extubation': 2, 'censored_death_at_vent->successful_extubation': 1, 'censored_trach->successful_extubation': 1, 'censored_terminal_extubation->successful_extubation': 1}

- `end_reason` G = 8: {'extubation_observed': 7948, 'transfer_ventilated': 1358, 'death_at_vent': 448}
- `end_reason` G = 10: {'extubation_observed': 7167, 'terminal_extubation': 1141, 'transfer_ventilated': 900, 'death_at_vent': 432, 'tracheostomy': 113}

