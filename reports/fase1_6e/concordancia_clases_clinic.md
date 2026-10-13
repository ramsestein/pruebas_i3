# Concordancia de clases sonda vs indice (clinic)

- Indice: `datasets\clinic\cases_v0.5.0_eac1eb35\clinic_cases_index.json`
- Eventos: 181 · con clase de sonda: 63 · sin sonda: 118 · pares comparables: 58

| sonda \ indice | A | B | C | D | — | total |
|---|---|---|---|---|---|---|
| A | 0 | 0 | 0 | 6 | 0 | 6 |
| B | 0 | 0 | 0 | 1 | 0 | 1 |
| C | 0 | 0 | 34 | 16 | 0 | 50 |
| D | 0 | 0 | 0 | 1 | 0 | 1 |
| — | 0 | 0 | 0 | 0 | 0 | 0 |

## Interpretacion

- Pares comparables: **58** · coinciden: **35** (60 %).
- Sonda A -> indice D: **6**. Es lo ESPERADO: sin `--probar` el indice no puede ver ni la grabacion del hueco ni la continuidad de ajustes, asi que no asigna A.
- Sonda C -> indice D: **16**. Difieren en el paso 'vuelve ventilada': la sonda mide la actividad por minuto DENTRO de su ventana de analisis (que para el evento siguiente puede estar recortada), mientras el indice usa el inicio del intento (el t0). Por eso la definicion unica se cierra corriendo la sonda Y la anotacion `--probar` sobre el MISMO indice (v0.6.0).

Definicion unica (2c.1) en ambas columnas. Con `--probar` la columna del indice debe acercarse fila a fila a la de la sonda.

