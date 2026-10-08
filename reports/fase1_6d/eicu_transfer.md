# eICU-B: validación de `transfer_ventilated` (Fase 1.6d, punto 2)

- Índice: `datasets\eicu_collaborative\cases_v0.4.0_aa142f35\eicu_cases_index.json`
- Eventos: 9754
- Censuras `transfer_ventilated` (corregido): 969
- Con evidencia de confort/limitación: 943
- `transfer_ventilated` con destino **incompatible**: 293 (30.24 %)
- La **regla propuesta** reclasificaría 48 eventos (`transfer_ventilated` → `end_of_record`)

## Destino × causa de censura — Con corrección del fin

| Destino | death_at_vent | terminal_extubation | trach | transfer_ventilated |
|---|---|---|---|---|
| Death | 439 | 830 | 8 | 0 |
| Floor | 0 | 0 | 17 | 242 |
| Step-Down Unit (SDU) | 0 | 0 | 16 | 124 |
| Other Hospital | 0 | 0 | 11 | 128 |
| Other ICU | 0 | 0 | 5 | 109 |
| Other External | 0 | 0 | 3 | 108 |
| Skilled Nursing Facility | 0 | 0 | 8 | 60 |
| Acute Care/Floor | 0 | 0 | 0 | 68 |
| ICU | 0 | 0 | 0 | 42 |
| Nursing Home | 0 | 0 | 13 | 21 |
| Home | 0 | 0 | 8 | 25 |
| Other | 0 | 0 | 0 | 26 |
| nan | 0 | 0 | 1 | 7 |
| Telemetry | 0 | 0 | 3 | 4 |
| Rehabilitation | 0 | 0 | 4 | 2 |
| Other Internal | 0 | 0 | 0 | 2 |
| Operating Room | 0 | 0 | 0 | 1 |

## Destino × causa de censura — Sin corrección del fin

| Destino | death_at_vent | terminal_extubation | trach | transfer_ventilated |
|---|---|---|---|---|
| Death | 63 | 1106 | 8 | 0 |
| Floor | 0 | 0 | 17 | 222 |
| Step-Down Unit (SDU) | 0 | 0 | 16 | 79 |
| Other Hospital | 0 | 0 | 11 | 61 |
| Other ICU | 0 | 0 | 5 | 61 |
| Acute Care/Floor | 0 | 0 | 0 | 60 |
| Other External | 0 | 0 | 3 | 37 |
| Skilled Nursing Facility | 0 | 0 | 8 | 24 |
| Nursing Home | 0 | 0 | 13 | 12 |
| ICU | 0 | 0 | 0 | 18 |
| Home | 0 | 0 | 8 | 8 |
| Other | 0 | 0 | 0 | 14 |
| Telemetry | 0 | 0 | 3 | 4 |
| nan | 0 | 0 | 1 | 5 |
| Rehabilitation | 0 | 0 | 4 | 0 |
| Other Internal | 0 | 0 | 0 | 2 |

## Regla propuesta (NO aplicada)

> Si la causa de censura a 48 h es `transfer_ventilated` y `unitDischargeLocation` es incompatible con seguir ventilado (casa/planta/hospicio/otro) y el último ajuste invasivo es >= 1 h anterior al alta, la estancia NO estaba ventilada al alta: se reclasifica como `end_of_record` (sin extubación observada).

Destinos considerados incompatibles: assisted living, floor, floor bed, home, home w/ home health, home with home health, home with hospice, hospice, other, psychiatric hospital, residential facility

