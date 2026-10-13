# Cascada de candidatas — clinic

| Paso | Pares |
|---|---|
| 0. Pares consecutivos del mismo box | 168 |
| 1. Primero censurado y hueco <= 12 h | 62 |
| 2. + vuelve con ventilacion activa (indice) | 62 |
| 3. + deltas de TV y FR medibles | 56 |
| 4. + VARIANTE d (PEEP, FiO2, TV, FR) | **22** |

Por que estos numeros no coinciden con el 45 -> 21 -> 7 del informe de 2c.3 (todas las causas son de BASE o de FILTRO, no de calculo):

- **45** era 'hueco <= 12 h' contado SOLO sobre los pares cuyo primer evento analizo la sonda (135 de 181 eventos), y **sin exigir que el primero estuviera censurado**. Aqui el paso 1 recorre TODOS los pares (168) y exige censura: 62.
- **21** exigia las cuatro diferencias medidas (cobertura de la sonda). Aqui el paso 3 cae a 19 porque quedan 38 pares sin medir: con `--medir-faltantes` suben los pasos 3 y 4.
- **7** (commit 1f048cc) anadia el filtro de **apagado simultaneo <= 15 min**. Ese filtro YA NO se aplica: el desfase fin-de-ventilador vs fin-de-monitor se informa como columna por pareja.
- El paso 2 no resta nada porque 'vuelve con ventilacion activa' es casi siempre cierto en Clinic (llegan intubados de quirofano).

- Indice: `datasets\clinic\cases_v0.5.0_eac1eb35\clinic_cases_index.json`
- Deltas: sonda 2c.1 19 · medidos dirigidos 37 · sin medir 6 (desde cache 52)
- Controles medidos: 12 de 96 pares con hueco > 24 h (intentos 14)
- Identificadores estables: box + epochs + SHA-256 del NOMBRE de los ficheros de origen (nunca `event_id` posicional). El SHA-256 del CONTENIDO va en la clave de adjudicacion.

## Parejas sin TV/FR medibles (6)

`motivo` = por que no se pudo medir esa pareja (no es 0, es `no evaluado`).

| Pareja (evento previo) | Motivo |
|---|---|
| `clinic_box12_event_11` | sin PEEP_CMH2O en la ventana; sin TV_EXP en la ventana; sin VENT_RR en la ventana |
| `clinic_box12_event_12` | sin PEEP_CMH2O en la ventana; sin TV_EXP en la ventana; sin VENT_RR en la ventana |
| `clinic_box14_event_6` | sin PEEP_CMH2O en la ventana; sin FIO2 en la ventana; sin TV_EXP en la ventana; sin VENT_RR en la ventana |
| `clinic_box14_event_14` | sin PEEP_CMH2O en la ventana; sin FIO2 en la ventana; sin TV_EXP en la ventana; sin VENT_RR en la ventana |
| `clinic_box14_event_21` | sin PEEP_CMH2O en la ventana; sin TV_EXP en la ventana; sin VENT_RR en la ventana |
| `clinic_box4_event_1` | sin PEEP_CMH2O en la ventana; sin TV_EXP en la ventana; sin VENT_RR en la ventana |

### Candidatas variante d (22)

| Box | prev_end_epoch | next_start_epoch | Hueco h | Desfase vent-monitor h | dPEEP | dFiO2 | dTV | dFR |
|---|---|---|---|---|---|---|---|---|
| box11 | 1750551222 | 1750567725 | 4.58 | 0.00 | 0.00 | -1.00 | 0.00 | 0.00 |
| box12 | 1751729160 | 1751736750 | 2.11 | 0.00 | -0.10 | 0.00 | 3.00 | 0.00 |
| box12 | 1753615547 | 1753632732 | 4.77 | 0.00 | -0.30 | 0.00 | 2.00 | 0.00 |
| box12 | 1753691349 | 1753710785 | 5.40 | 0.00 | -0.10 | 0.00 | 1.00 | 0.00 |
| box13 | 1742587998 | 1742587998 | -0.00 | 0.00 | 0.10 | 0.00 | -14.00 | 2.00 |
| box14 | 1746348454 | 1746348454 | -0.00 | 0.00 | 0.20 | 0.00 | 11.00 | -1.00 |
| box14 | 1747393294 | 1747404031 | 2.98 | 0.00 | 0.00 | -1.00 | -3.00 | 0.00 |
| box14 | 1748245802 | 1748258897 | 3.64 | 0.00 | 2.10 | 0.00 | -2.00 | 0.00 |
| box14 | 1752452396 | 1752452396 | 0.00 | 0.00 | 0.00 | 0.00 | -1.00 | 0.00 |
| box14 | 1752482961 | 1752482961 | -0.00 | 0.00 | 0.00 | 0.00 | 5.00 | 0.00 |
| box14 | 1753961902 | 1754003533 | 11.56 | 0.00 | 0.00 | 0.00 | 38.00 | -2.00 |
| box3 | 1751402114 | 1751412137 | 2.78 | 0.00 | 0.10 | 1.00 | 1.00 | 0.00 |
| box3 | 1751742805 | 1751742805 | -0.00 | 0.00 | 0.00 | 1.00 | 3.00 | 0.00 |
| box4 | 1747654754 | 1747654754 | 0.00 | 0.00 | 0.00 | 0.00 | -6.00 | 0.00 |
| box4 | 1747968854 | 1747968854 | 0.00 | 0.00 | 0.00 | 0.00 | 7.00 | 0.00 |
| box4 | 1748004989 | 1748004988 | -0.00 | 0.00 | 0.00 | 0.00 | -2.00 | 0.00 |
| box4 | 1761657731 | 1761657731 | 0.00 | 0.00 | 0.00 | 0.00 | 12.00 | -2.00 |
| box4 | 1761807812 | 1761819474 | 3.24 | 0.00 | 0.45 | 6.00 | -16.50 | 1.25 |
| box4 | 1762211762 | 1762221230 | 2.63 | 0.00 | 0.10 | 0.00 | 5.50 | 0.00 |
| box6 | 1740328565 | 1740328565 | -0.00 | 0.00 | 0.00 | 0.00 | 16.00 | -1.00 |
| box6 | 1740978868 | 1740978868 | -0.00 | 0.00 | -0.10 | 0.00 | -12.00 | 2.00 |
| box9 | 1747032906 | 1747032906 | -0.00 | 0.00 | 0.00 | 0.00 | 18.50 | 0.00 |
