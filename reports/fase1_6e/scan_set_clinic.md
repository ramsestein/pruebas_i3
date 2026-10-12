# Fase 1.6e, punto 2b/D-g — conjunto de ficheros de `clinic`

Origen: `D:\data\clinic_vitals` (la funcion real `scan_source_files`).

## Ficheros que ve el builder

| box | ficheros | tokens | homonimos descartados |
|---|---|---|---|
| box10 | 510 | 2 | 0 |
| box11 | 64 | 1 | 0 |
| box12 | 401 | 2 | 0 |
| box13 | 549 | 2 | 0 |
| box14 | 657 | 2 | 0 |
| box2 | 183 | 1 | 0 |
| box3 | 223 | 2 | 0 |
| box4 | 545 | 1 | 0 |
| box5 | 371 | 1 | 0 |
| box6 | 415 | 1 | 0 |
| box7 | 335 | 2 | 0 |
| box8 | 346 | 1 | 0 |
| box9 | 294 | 1 | 0 |

**TOTAL: 4893 ficheros, 13 boxes.**

## Homonimos en el crudo (D-g)

`dataset_clinic` queda excluido por `exclude_dirs`; los ficheros de `physionet_vitals` no casan con el patron de nombres de la cohorte.

### `box14`

- Nombres repetidos: **578** (578 ficheros de mas); copias por nombre: {'2': 578}

| nombres | carpetas de las copias |
|---|---|
| 25 | `14\250506` ↔ `14_2\14_2\250906\14\250506` |
| 24 | `14\250505` ↔ `14_2\14_2\250906\14\250505` |
| 21 | `14\250508` ↔ `14_2\14_2\250906\14\250508` |
| 20 | `14\250504` ↔ `14_2\14_2\250906\14\250504` |
| 16 | `14\250512` ↔ `14_2\14_2\250906\14\250512` |
| 15 | `14\250511` ↔ `14_2\14_2\250906\14\250511` |
| 12 | `14\250509` ↔ `14_2\14_2\250906\14\250509` |
| 11 | `14\250220` ↔ `14_2\14_2\250906\14\250220` |

Verificacion de contenido en `reports/fase1_6e/box14_dedupe.md` (SHA-256).

