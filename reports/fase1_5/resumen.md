# Fase 1.5 — Recuento de eventos completos (eICU) y reconstrucción de VitalDB

> **Estado:** puntos 0–3 implementados, cada uno con su test (que falla antes y
> pasa después) en su propio commit (ver §0). Los builds de MIMIC, eICU y
> VitalDB se han re-ejecutado sobre los datos crudos; Clínic no cambia con la
> regla 0 (no tiene frontera de estancia) y se reutiliza su índice de Fase 1.
> Ninguna cifra procede de salidas antiguas salvo Clínic (indicado).

---

## 0. Regla común de extubación confirmada (las 4 cohortes) — commit `28e7e07`

`src/common/extubation.py` implementa la regla en una función **pura**
(`resolve_extubation`):

- Un fin de ventilación es **extubación** solo si va seguido de **≥ 1 h de
  observación sin ventilador** (monitor en Clínic/VitalDB; estancia en
  MIMIC/eICU).
- Si no, el evento se **censura con su causa**: `transfer_ventilated`
  (alta/traslado ventilado), `death_at_vent` (muerte estando ventilado) o
  `end_of_record` (fin de datos).

Cambios de comportamiento:

- **MIMIC**: se elimina la tolerancia de 30 s frente a `OUTTIME`. Los 237
  eventos que terminaban en `end_of_icu_stay` y **todo** fin de ventilación a
  < 1 h del alta pasan a censurarse como `transfer_ventilated`.
- **Clínic/VitalDB**: la regla ya existía (`≥ 1 h` de monitor); ahora se
  comparte el mismo código (`extubation_decision`). Al no existir frontera de
  estancia, la censura es siempre `end_of_record` (o `death_at_vent` si la
  muerte por señales la confirma).
- **eICU**: se aplica en el builder de casos (§1).

Test exigido: fin de ventilación 20 min antes del alta → `transfer_ventilated`
(`src/create_dataset/tests/test_mimic_cases.py::TestCommonExtubationRule`).

### Efecto en MIMIC (v0.2.0, 9 093 eventos; 25 excluidos)

| `end_reason` | Fase 1 | Fase 1.5 |
|---|---|---|
| `extubation_observed` | 7 249 | **7 027** |
| `transfer_ventilated` | — | **459** |
| `death` | 1 297 | 1 297 |
| `tracheostomy` | 310 | 310 |

Los **459** `transfer_ventilated` incluyen los **237** que antes terminaban en
`end_of_icu_stay` (tolerancia de 30 s) **más** ~222 eventos cuyo fin de
ventilación está entre 30 s y 1 h antes del `OUTTIME` y que la regla antigua
daba por extubados.

Etiquetas a 48 h en MIMIC: éxito **7 455** (antes 7 905), censura **1 638**
(`death_at_vent` 556, `trach` 226, `terminal_extubation` 418,
**`transfer_ventilated` 438**), eventos con ≥ 1 fallo **471** (5,18 %).

---

## 1. eICU — clasificación por completitud — commit `ee6931b`

`src/create_dataset/build_eicu_index.py` construye el índice de casos con el
**mismo esquema** que las otras cohortes (más `hospital_id`) y clasifica cada
estancia en un nivel A/B/C/D (`src/common/eicu_levels.py`).

**Hallazgo central:** en `respiratoryCare`, `ventendoffset` **no está
documentado** en la práctica (solo **1 estancia** de 44 772 tiene `> 0`; el
pipeline lo *imputa* con el último `respcarestatusoffset`). Por tanto, con la
definición literal (fin documentado = `ventendoffset > 0`) **no hay estancias A
ni B**: la clasificación se reparte entre C (fin imputado pero **rescatable**
por el último ajuste invasivo a ≥ 1 h del alta) y D (no rescatable).

- Ventana de coherencia con `respiratoryCharting`: último ajuste invasivo
  (modo, PEEP, volumen tidal pautado, PIP, FR total del ventilador) a ≤ 4 h del
  fin. Se excluyen **FiO2** y **VNI/alto flujo** (CPAP/EPAP/IPAP/BiPAP/NIV).

| Nivel | Nº estancias | Definición |
|---|---|---|
| A — completa | **0** | fin documentado + extubación/censura válida + ajuste invasivo coherente (≤ 4 h) |
| B — documentada sin verificar | **0** | como A pero sin ajustes invasivos |
| C — rescatable | **36 285** | fin imputado/incoherente, último ajuste invasivo ≥ 1 h del alta → se propone ese momento |
| D — no fiable | **7 702** | fin imputado sin forma de recuperarlo |

- **Total**: 43 987 eventos (55 excluidos por traqueostomía previa a t0).
- **Hospitales con ≥ 50 estancias de nivel A**: **ninguno** (157 hospitales; A = 0).
- **Cobertura de variables** (`vars_ok` al 50 % y 80 % en A / A+B): **N/A**
  (A+B = 0). *[Pendiente: se puede reportar la cobertura de las 36 285 C.]*

Éxito / fallo / censura a 48 h por nivel (para A y A+B no aplica):

| Conjunto | Eventos | Éxito | Censura | Con ≥ 1 fallo |
|---|---|---|---|---|
| A | 0 | 0 | 0 | 0 |
| A+B | 0 | 0 | 0 | 0 |
| A+B+C | 36 285 | 29 723 | 6 562 | 215 (0,59 %) |

Causas de censura en A+B+C: `terminal_extubation` 2 736,
`transfer_ventilated` 2 325, `death_at_vent` 721, `trach` 780.

> **Reconstrucción propuesta (nivel C):** la extubación es el último ajuste
> invasivo anotado; se reporta por evento en `proposed_extubation_h` y
> `proposed_shift_h` (diferencia con el fin original imputado).

---

## 2. VitalDB — reconstrucción — commits `ee6931b`, `4ee5947`

### 2.1 Escaneo de TODOS los ficheros de origen

`scripts/verify/fase1_5/scan_vitaldb.py` recorre los **15 327** `.vital` de
`D:/data/vitaldb_sicu` (no una muestra) y guarda una **caché de sondas** para
que el rebuild no vuelva a parsearlos.

| Versión de `vitaldb` | Legibles | Ilegibles | Error |
|---|---|---|---|
| **1.6.0** (pin anterior) | 15 326 | **1** | `SICU2_02_250203_130000.vital` → `PermissionError [Errno 13]` |
| **1.7.2** (pin nuevo) | **15 327** | **0** | — |

El único fichero ilegible con 1.6.0 **se lee con 1.7.2** (pistas:
`Intellivue/PLETH`), así que la causa era la versión fijada. **El pin se
actualiza a `vitaldb==1.7.2`** en `requirements.txt`. Ficheros corruptos
(ilegibles incluso con 1.7.2): **0**.

> **Corrección de un diagnóstico previo:** la muestra de 300 ficheros de la
> Fase 1 ("100 sin pistas legibles") confundía *"sin pistas"* con *"ilegible"*.
> Aquí se distinguen: un fichero legible **sin** pistas de interés es "sin
> señal" (no "sin dato"); solo un fallo del parser es "sin dato". Con esa
> distinción, **solo 1 fichero** era ilegible, y no por "sin pistas".

### 2.2 Ficheros ilegibles = "sin dato" (no "sin señal")

`segment_box` puentea los huecos cubiertos por ficheros ilegibles
(`merge_spans_ignoring_missing`): sus horas **no** crean huecos de ventilador
(D1) ni cortes de paciente (D2). Los eventos que los atraviesan se marcan con
`has_missing_files` y `missing_hours`.
Test: dos ficheros ilegibles en mitad de una ventilación → **1 intento**
(`src/create_dataset/tests/test_missing_files.py`).

### 2.3 Rebuild + niveles + cobertura

Rebuild en `datasets/vitaldb/cases_v0.2.0_a805771c` con regla 0, clasificación
A/B/D y cobertura (LOCF 4 h):

- **96 eventos** (18 excluidos por ventilador sin paciente). Como **0 ficheros
  son ilegibles** con 1.7.2, **`has_missing_files` = 0** y **los 96 eventos son
  nivel A** (ninguna hora perdida en la ventilación ni tras la desconexión).
- Intentos por evento: `{1: 83, 2: 10, 3: 2, 4: 1}`.
- `end_reason`: `extubation_observed` 51, `death_or_transfer` 26,
  `end_of_record` 19.
- Etiquetas a 48 h: éxito **75**, censura **21** (`end_of_record`), eventos con
  ≥ 1 fallo **5**.

**Cobertura de variables** (96 eventos A; fracción de horas ventiladas con
valor útil, LOCF 4 h):

| Variable | Media | Mediana | % eventos > 0 | % eventos > 0,5 | % eventos > 0,8 |
|---|---|---|---|---|---|
| HR | 0,65 | 1,00 | 76 % | 58 % | 52 % |
| SpO2 | 0,68 | 1,00 | 78 % | 60 % | 55 % |
| MAP | 0,65 | 1,00 | 76 % | 58 % | 52 % |
| RR | 0,65 | 1,00 | 73 % | 59 % | 54 % |
| **FiO2** | **0,24** | 0,00 | 28 % | 21 % | 18 % |
| **PEEP** | **0,08** | 0,00 | 12 % | 5 % | 2 % |

- `vars_ok` al **50 %** en A: **5 / 96**.
- `vars_ok` al **80 %** en A: **2 / 96**.

FiO2 y PEEP aparte: la cobertura es **muy baja** (medias 0,24 y 0,08; solo
28 % y 12 % de los eventos tienen algún valor). Confirma el límite de la Fase 1:
VitalDB sirve como validación externa con **modelo reducido** (HR, SpO2, MAP,
RR), no con las 6 variables obligatorias.

### 2.4 Antes / después de VitalDB

| | Antes (Fase 1, v0.1.0) | Después (Fase 1.5, v0.2.0) |
|---|---|---|
| Eventos | 94 | **96** |
| Excluidos | 20 | **18** |
| Ficheros ilegibles | no medido (muestra ~1/3 "sin pistas") | **0** (1 con 1.6.0, legible con 1.7.2) |
| Pin `vitaldb` | 1.6.0 | **1.7.2** |
| Niveles A/B/D | no existía | **96 / 0 / 0** |
| `has_missing_files` | no existía | **0** |
| Cobertura FiO2 / PEEP | muestra: 22 % / 18 % (presencia de pista) | **24 % / 8 %** (cobertura horaria real, LOCF 4 h) |

El cambio 94→96 eventos viene de tratar los ficheros sin pistas como "sin
señal" (no "sin dato") de forma consistente y del puenteo de huecos por
ficheros ilegibles (aquí sin efecto, al no haber ninguno con 1.7.2).



---

## 3. Clínic — eventos de menos de 1 h — commit (punto 3)

PNG de los **16 eventos < 1 h** (ventilador, onda de presión de vía aérea, FC,
SpO2) en `reports/fase1_5/figs/clinic_short/`, clasificados **por la señal**
(nunca por la duración) con `src/common/short_events.py`:

| Evento | Duración (min) | Clasificación | Señal |
|---|---|---|---|
| `clinic_box10_event_2` | 14,6 | artefacto | sin AWP/TV |
| `clinic_box13_event_4` | 19,4 | artefacto | sin AWP/TV |
| `clinic_box13_event_5` | 0,9 | artefacto | sin AWP/TV |
| `clinic_box14_event_1` | 9,3 | artefacto | sin AWP/TV |
| `clinic_box14_event_2` | 8,6 | artefacto | sin AWP/TV |
| `clinic_box14_event_3` | 0,4 | artefacto | sin AWP/TV |
| `clinic_box14_event_7` | 50,8 | artefacto | sin AWP/TV |
| `clinic_box14_event_25` | 18,9 | artefacto | sin AWP/TV |
| `clinic_box14_event_30` | 19,0 | artefacto | sin AWP/TV |
| `clinic_box14_event_35` | 0,3 | artefacto | sin AWP/TV |
| `clinic_box14_event_37` | 59,4 | artefacto | sin AWP/TV |
| `clinic_box2_event_6` | 13,7 | artefacto | sin AWP/TV |
| `clinic_box4_event_1` | 3,7 | artefacto | sin AWP/TV |
| `clinic_box5_event_6` | 4,1 | artefacto | sin AWP/TV |
| `clinic_box5_event_7` | 48,0 | artefacto | sin AWP/TV |
| `clinic_box8_event_2` | 59,2 | artefacto | sin AWP/TV |

**Resultado: 0 plausibles, 16 artefactos.** Los ficheros de origen de estos
eventos **no contienen pistas de ventilador** (ni onda de presión de vía aérea
ni volumen tidal): en los ficheros comprobados del índice actual solo hay
pistas de monitor. Se clasifican como artefacto por señal, no por duración. El
detalle está en `reports/fase1_5/clinic_short_events.json`.

> **Cautela:** el índice de Clínic (v0.1.0) marcó estos tramos como
> ventilación, pero los ficheros crudos actuales no tienen pistas de
> ventilador. Es una **inconsistencia índice↔datos crudos** que hay que
> aclarar antes de dar el resultado por definitivo (¿se reorganizaron los
> `.vital` de Clínic tras el build?). Se informa, no se excluye nada.

---

## 4. Entregable — tabla final

| Cohorte | Eventos totales | Completos (nivel A) | A+B | Éxito / fallo / censura (48 h) en A | `vars_ok` 50 % en A | `vars_ok` 80 % en A |
|---|---|---|---|---|---|---|
| **MIMIC** | 9 093 (25 excl.) | 9 093 | 9 093 | 7 455 / 471 / 1 638 | n/d | n/d |
| **Clínic** | 181 (4 excl.) | 181 | 181 | 140 / 9 / 41 | n/d | n/d |
| **VitalDB** | 96 (18 excl.) | **96** | **96** | 75 / 5 / 21 | **5** | **2** |
| **eICU** | 43 987 (55 excl.) | **0** | **0** | — | — | — |

- **MIMIC / Clínic**: nivel A = todos los eventos válidos tras la regla 0. En
  Clínic, de los 16 eventos < 1 h, **16 son artefactos** por señal (§3).
  `vars_ok` para MIMIC/Clínic no se ha medido en esta fase (no lo pedía el punto
  1/2; en la Fase 1, Clínic/VitalDB era la ruta señal).
- **VitalDB**: A = sin horas perdidas en la ventilación ni tras la desconexión
  (96/96, al no haber ficheros ilegibles con 1.7.2). Fallo = eventos con ≥ 1
  intento fallido a 48 h.
- **eICU**: A = 0 porque `ventendoffset` no está documentado; los 36 285
  eventos rescatables son de nivel C (fin reconstruido con el último ajuste
  invasivo). Desglose por nivel y por hospital en
  `datasets/eicu_collaborative/cases_*/eicu_levels_summary.json`.

### Desglose por hospital de eICU

157 hospitales. **Ninguno con ≥ 50 estancias de nivel A** (A = 0 en todos).
El desglose completo A/B/C/D por hospital está en `levels_by_hospital` de
`eicu_levels_summary.json`.

### Antes / después de VitalDB

Ver §2.4: 94 → **96** eventos; 20 → **18** excluidos; **0** ficheros ilegibles
(1 con 1.6.0, legible con 1.7.2 → pin a **1.7.2**); niveles **A/B/D = 96/0/0**;
`has_missing_files` = 0; cobertura FiO2/PEEP **24 % / 8 %**.

---

## 5. Comandos de reproducción

```powershell
# Punto 0 (MIMIC con regla 0)
python -m src.create_dataset.build_mimic_cases

# Punto 1 (eICU)
python -m src.create_dataset.build_eicu_index

# Punto 2 (VitalDB): escaneo completo + reintento + rebuild con caché
python scripts/verify/fase1_5/scan_vitaldb.py --workers 16 `
  --cache reports/fase1_5/vitaldb_probe_160.json `
  --out   reports/fase1_5/vitaldb_scan_160.json
#   (instalar vitaldb==1.7.2)
python scripts/verify/fase1_5/scan_vitaldb.py --retry `
  --previous reports/fase1_5/vitaldb_scan_160.json `
  --cache-in reports/fase1_5/vitaldb_probe_160.json `
  --cache    reports/fase1_5/vitaldb_probe.json `
  --out      reports/fase1_5/vitaldb_scan.json --workers 1
python -m src.create_dataset.build_signal_cases --cohort vitaldb --no-merge `
  --coverage --probe-cache reports/fase1_5/vitaldb_probe.json

# Punto 3 (Clínic < 1 h)
python scripts/verify/fase1_5/plot_short_events.py

# Tests
python -m pytest src/stage0/tests src/common/tests src/create_dataset/tests -q   # 287 verde, 1 saltado
```

## 6. Limitaciones

- **eICU**: `ventendoffset` no documentado ⇒ A/B = 0. La reconstrucción de C
  (último ajuste invasivo ≥ 1 h antes del alta) es una **propuesta**; el
  `proposed_shift_h` reporta cuánto se desplaza el fin imputado.
- **Clínic (punto 3)**: los ficheros crudos de los eventos < 1 h **no tienen
  pistas de ventilador**, pero el índice los dio por ventilación: posible
  inconsistencia índice↔datos crudos que hay que aclarar.
- **VitalDB**: FiO2/PEEP insuficientes ⇒ modelo reducido para validación
  externa.
- La cobertura de eICU para A/A+B es N/A (A+B = 0); no se calculó la de C.

