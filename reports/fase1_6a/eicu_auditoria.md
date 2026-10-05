# Auditoría documental — cómo registra eICU-CRD la ventilación mecánica

> Fase 1.6a, punto 1. Cada afirmación se marca como **confirmada**, **refutada**
> o **no comprobable** contra nuestros datos (`datasets/eicu_collaborative/`,
> 44 772 estancias en `respiratoryCare`, 20 168 176 filas en
> `respiratoryCharting`). Las cifras locales provienen de
> `scripts/verify/fase1_6a/verify_claims.py` (`eicu_claims.json`) y del censo
> (`eicu_hospitales.csv`).

## 1. Fuentes consultadas

| # | Fuente | URL |
|---|---|---|
| F1 | eICU-CRD — `respiratoryCare` (esquema) | https://github.com/MIT-LCP/eicu-code/blob/main/website/content/eicutables/respiratoryCare.md |
| F2 | eICU-CRD — `respiratoryCharting` (esquema) | https://github.com/MIT-LCP/eicu-code/blob/main/website/content/eicutables/respiratoryCharting.md |
| F3 | eICU-CRD — `carePlanGeneral` (esquema) | https://github.com/MIT-LCP/eicu-code/blob/main/website/content/eicutables/carePlanGeneral.md |
| F4 | eICU-CRD — `treatment` (esquema) | https://github.com/MIT-LCP/eicu-code/blob/main/website/content/eicutables/treatment.md |
| F5 | eICU-CRD — `apachePredVar` (esquema; `oOBVentDay1`/`oOBIntubDay1`) | https://github.com/MIT-LCP/eicu-code/blob/main/website/content/eicutables/apachePredVar.md |
| F6 | eICU-CRD — `apacheApsVar` (esquema; `vent`/`intubated`) | https://github.com/MIT-LCP/eicu-code/blob/main/website/content/eicutables/apacheApsVar.md |
| F7 | eICU-CRD — `nurseCharting` (esquema) | https://github.com/MIT-LCP/eicu-code/blob/main/website/content/eicutables/nurseCharting.md |
| F8 | eICU-CRD — `patient` (esquema) | https://github.com/MIT-LCP/eicu-code/blob/main/website/content/eicutables/patient.md |
| F9 | eICU-CRD — `hospital` (esquema) | https://github.com/MIT-LCP/eicu-code/blob/main/website/content/eicutables/hospital.md |
| F10 | Issue MIT-LCP/eicu-code **#82** "Mechanical ventilation in eICU" | https://github.com/MIT-LCP/eicu-code/issues/82 |
| F11 | Issue MIT-LCP/eicu-code **#192** "Mechanical ventilation duration" | https://github.com/MIT-LCP/eicu-code/issues/192 |
| F12 | Issue relacionada **#49** (offsets en `respiratoryCare` casi siempre 0) | https://github.com/MIT-LCP/eicu-code/issues/49 |
| F13 | Pollard TJ, Johnson AEW, Raffa JD, Celi LA, Mark RG, Badawi O. *The eICU Collaborative Research Database, a freely available multi-center database for critical care research.* **Sci Data** 5:180178 (2018). PMID 30204154 | https://doi.org/10.1038/sdata.2018.178 |
| F14 | O'Halloran HM, Kwong K, Veldhoen RA, Maslove DM. *Characterizing the Patients, Hospitals, and Data Quality of the eICU Collaborative Research Database.* **Crit Care Med** 48(12):1737–1743 (2020). PMID 33044284 | https://doi.org/10.1097/CCM.0000000000004632 |
| F15 | alistairewj/mechanical-power — `eicu/hospitals-with-vent-data.sql` | https://github.com/alistairewj/mechanical-power/blob/master/eicu/hospitals-with-vent-data.sql |
| F16 | alistairewj/mechanical-power — `eicu/README.md` (usa `ventdurations` derivadas de `respiratoryCharting`) | https://github.com/alistairewj/mechanical-power/blob/master/eicu/README.md |
| F17 | alistairewj/mechanical-power — cross-reference de `treatment` y `vent_unpivot_rc` | https://github.com/MIT-LCP/eicu-code/blob/main/notebooks/cross-reference-apache-treatments.ipynb |
| F18 | Peine A, et al. *Development and validation of a reinforcement learning algorithm to dynamically optimize mechanical ventilation in critical care.* **npj Digit Med** 4:32 (2021). PMID 33608661 | https://doi.org/10.1038/s41746-021-00384-3 |
| F19 | Documentación de `ventstartoffset`/`ventendoffset` (offsets en minutos desde el alta de UCI) | F1 |

## 2. Afirmaciones y contraste con nuestros datos

> Estado: **C** = confirmada, **R** = refutada, **NC** = no comprobable. Las
> cifras locales provienen de `eicu_claims.json`, `eicu_vent_labels.csv` y
> `eicu_evidence.json`.

| # | Afirmación (con fuente) | Contraste local | Estado |
|---|---|---|---|
| A1 | `ventendoffset` es 0 o nulo en ~99,98 % de las filas de `respiratoryCare` (F10: «865224 of 865381»). | 865 224 / 865 381 = **99,982 %** 0 o nulo; solo **1** estancia con `ventendoffset > 0`. | **C** |
| A2 | El fin de ventilación **no** se puede leer de `ventendoffset`; hay que derivarlo de los «timestamps de registros que implican ventilación» (`respiratoryCharting`) (F10, F11, F16). | Los ajustes invasivos de `respiratoryCharting` existen en **56 075** estancias (`rc_invasive`). | **C** |
| A3 | Las duraciones «likely estimable for some hospitals»; el método «may need to be adapted at the hospital-level» y debe **validarse por hospital** (F11). | Dispersión por hospital en `eicu_hospitales.csv` (§4 de `resumen.md`). | **C** |
| A4 | `oOBVentDay1 = 1` sin `oOBIntubDay1 = 1` ⇒ ventilación **no invasiva** (F5). | **12 000** estancias con `oobVentDay1=1` sin `oobIntubDay1=1`; **0** al revés (intubado ⇒ ventilado). | **C** |
| A5 | `airwayType` es un *picklist* de vía aérea (`Oral ETT`, `Nasal ETT`, `Tracheostomy`, `No Artificial Airway`, …) (F1). | Valores observados en `eicu_vent_labels.csv` (Oral ETT 8 110 estancias/92 hospitales; Tracheostomy 1 028/62). | **C** |
| A6 | `respChartOffset` (observación) y `respChartEntryOffset` (entrada) son distintos (F2). | Solo **74,91 %** de las 20 168 176 filas coinciden; mediana 0 min, **p90 = 47 min**. | **C** |
| A7 | `treatment` incluye `…\|mechanical ventilation` como marcador de VM invasiva (F17). | `tx_vent` en **41 063** estancias. | **C** |
| A8 | Criterio de validez por hospital de mechanical-power: ≥ 10 pacientes con `oOBVentDay1=1` y ≥ 10 % con presión pico en las primeras 24 h (F15). | Escenario (a): **65** hospitales, **31 836** estancias. | **C** |
| A9 | La documentación varía entre hospitales (F13, F14). | Dispersión por hospital (§4). | **C** |
| A10 | Peine et al. 2021 derivan la ventilación en eICU y la usan para Q-learning (F18). | No se repite su derivación exacta; se documenta como referencia. | **NC** |

## 3. Cómo registra eICU la ventilación (síntesis)

- **`respiratoryCare`** documenta la vía aérea (`airwayType`) y, en teoría, el
  inicio/fin de VM (`ventstartoffset`/`ventendoffset`), pero **`ventendoffset`
  no se cumplimenta** (A1). `ventstartoffset` se marca en 8 637 estancias.
- **`respiratoryCharting`** es la fuente práctica: contiene los **ajustes de
  ventilación** (modo, PEEP, volumen tidal, PIP, FR total) y **FiO2**, además
  de valores de VNI y oxigenoterapia. De ahí se derivan los intervalos
  ventilados (F10, F16).
- **`carePlanGeneral`** (grupos *Ventilation* y *Airway*), **`treatment`**
  (`…|mechanical ventilation`) y **APACHE** (`oobVentDay1`/`oobIntubDay1`,
  `apacheApsVar.vent`/`intubated`) son fuentes independientes que permiten
  cruzar la evidencia invasiva.
- **`nurseCharting`** contiene parámetros de enfermería (p. ej. respiraciones)
  pero **no** es una fuente de VM invasiva fiable.

La consecuencia metodológica (A3) es que **cualquier criterio de ventilación en
eICU debe validarse por hospital**; de ahí el censo de `eicu_hospitales.csv` y
los escenarios de selección de `resumen.md`.

## 4. `ventstartoffset` no marca la intubación

Contraste local (`eicu_claims.json`):

- 60 565 filas con `ventstartoffset > 0` (**8 637** estancias); 119 611 filas con
  valor **negativo**.
- De las estancias con `ventstartoffset > 0`: solo **23,42 %** tienen `airwayType`
  Oral/Nasal ETT, **61,05 %** tienen `oobVentDay1=1` y **52,96 %**
  `oobIntubDay1=1`.
- **Solo el 1,72 %** de las 3 888 estancias comparables tiene el primer ajuste
  invasivo de `respiratoryCharting` a **±2 h** del `ventstartoffset`.

⇒ `ventstartoffset` **no** marca de forma fiable el inicio de ventilación
invasiva; probablemente registra el momento de conexión al equipo de
oxigenoterapia/ventilador sin distinguir la vía aérea. **No debe usarse como
criterio de inclusión** (ver escenarios en `resumen.md`).

