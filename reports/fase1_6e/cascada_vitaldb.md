# Cascada de candidatas — vitaldb

| Paso | Pares |
|---|---|
| 0. Pares consecutivos del mismo box | 72 |
| 1. Primero censurado y hueco <= 12 h | 21 |
| 2. + vuelve con ventilacion activa (indice) | 21 |
| 3. + deltas de TV y FR medibles | 0 |
| 4. + VARIANTE d (PEEP, FiO2, TV, FR) | **0** |

Por que estos numeros no coinciden con el 45 -> 21 -> 7 del informe de 2c.3 (todas las causas son de BASE o de FILTRO, no de calculo):

- **45** era 'hueco <= 12 h' contado SOLO sobre los pares cuyo primer evento analizo la sonda (135 de 181 eventos), y **sin exigir que el primero estuviera censurado**. Aqui el paso 1 recorre TODOS los pares (168) y exige censura: 62.
- **21** exigia las cuatro diferencias medidas (cobertura de la sonda). Aqui el paso 3 cae a 19 porque quedan 38 pares sin medir: con `--medir-faltantes` suben los pasos 3 y 4.
- **7** (commit 1f048cc) anadia el filtro de **apagado simultaneo <= 15 min**. Ese filtro YA NO se aplica: el desfase fin-de-ventilador vs fin-de-monitor se informa como columna por pareja.
- El paso 2 no resta nada porque 'vuelve con ventilacion activa' es casi siempre cierto en Clinic (llegan intubados de quirofano).

- Indice: `datasets\vitaldb\cases_v0.4.0_aa142f35\vitaldb_cases_index.json`
- Deltas: sonda 2c.1 0 · medidos dirigidos 0 · sin medir 21
- Controles disponibles: 0
- Identificadores estables: box + epochs + hash del nombre de los ficheros de origen (nunca `event_id` posicional).
