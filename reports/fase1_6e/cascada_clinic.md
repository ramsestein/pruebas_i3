# Cascada de candidatas — clinic

| Paso | Pares |
|---|---|
| 0. Pares consecutivos del mismo box | 168 |
| 1. Primero censurado y hueco <= 12 h | 62 |
| 2. + vuelve con ventilacion activa (indice) | 62 |
| 3. + deltas de TV y FR medibles | 19 |
| 4. + VARIANTE d (PEEP, FiO2, TV, FR) | **7** |

- Indice: `datasets\clinic\cases_v0.5.0_eac1eb35\clinic_cases_index.json`
- Deltas: sonda 2c.1 24 · medidos dirigidos 0 · sin medir 38
- El desfase fin-de-ventilador vs fin-de-monitor se informa por pareja, NO se usa como filtro.
- Identificadores estables: box + epochs + hash del nombre de los ficheros de origen (nunca `event_id` posicional).
