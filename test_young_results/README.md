# test_young_results — exploración de deflexión P–V por respiración

> Exploración de señal (Etapas 0 y 1). **No** es un pipeline: scripts desechables, ya borrados.
> Fecha: 2026-10-08 · Fuente: `D:\data\vitaldb_sicu` (`.vital`, Philips IntelliVue, 125 Hz)

## Ficheros

| Fichero | Qué es |
|---|---|
| `fig0_diag_crudo.png` | Paw y flujo crudos, 60 s, para verificar convención de signo y forma de onda |
| `fig1_PEEP5_FiO2_40.png` | Etapa 1 en `SICU2_02_250103_140000.vital` (PEEP 5, FiO₂ 40 %): **la deflexión NO es real** |
| `fig2_PEEP10_FiO2_100.png` | Etapa 1 en `SICU2_07_250102_090000.vital` (PEEP 10, FiO₂ 100 %, VT 324, RR 18): **sí se ve reclutamiento** |
| `respiraciones_40_PEEP5_FiO2_40.txt` | Tabla de 40 respiraciones (Vb_frac, ratio, dAIC, Pel(Vb)−PEEP) + resumen |
| `respiraciones_40_PEEP10_FiO2_100.txt` | Íd. para el paciente con PEEP 10 |
| `seleccion_casos_FiO2_PEEP.txt` | Escaneo de FiO₂/PEEP sobre 1.277 ficheros-hora con onda; de ahí salió el caso ARDS |
| `tests_output.txt` | Salida completa de los tests 1–4 (ambos pacientes) |
| `test1_scatter_{ards,ctrl}.png` | TEST 1: Vb vs volumen en el pico de flujo |
| `test2_decelerante_{ards,ctrl}.png` | TEST 2: histogramas de Vb_frac, método original vs sólo fase decelerante |
| `test3_histeresis_ards.png` | TEST 3: ramas inspiratoria y espiratoria superpuestas |
| `test4_sensibilidad_R_{ards,ctrl}.png` | TEST 4: ratio y Vb_frac frente a perturbaciones de R |

## Datos disponibles en `D:\data` (Etapa 0)

- Sólo los `.vital` de IntelliVue traen **presión de vía aérea Y flujo**. `vitaldb_sicu` (15.327 ficheros-hora) es la fuente principal; `clinic_vitals` es secundaria (ondas en ventanas de 7–20 min).
- `mimic4wdb_full/waves` (WFDB, sin Paw/flow), `inspire`, `eicu-database`, `mimic-iv-3.1`, `mimiciii` y `vitaldb_pacu` **no** sirven para el lazo P–V.
- En `vitaldb_sicu`: `FLOW_WAV` 3.337 ficheros y `AWP_WAV` 2.872, ambos a **125 Hz**, onda continua durante la hora. **2.872 ficheros-hora con las dos ondas**; 1.288 con PEEP/PIP numéricos.
- Escalado: `FLOW[L/min] = −130 + 0,065·raw` · `AWP[cmH₂O] = −10 + 0,015·raw`; `raw = 0` ⇒ sin señal (descartar).
- Metadatos de ventilador: tracks `SET_*` (`SET_SIMV` = modo, `SET_PEEP_CMH2O`, `SET_FIO2`, `SET_IE_RATIO`). **No hay sedación (RASS) ni bloqueo neuromuscular (TOF).**

## Método (por respiración)

1. Volumen por integración del flujo + corrección de deriva (V vuelve a ~0 al final de la espiración).
2. Ajuste LS: `P = E1·V + E2·V² + R·Flujo + P0` en la fase inspiratoria.
3. Presión elástica `Pel = P − R·Flujo`.
4. Regresión segmentada `Pel` vs `V`, ruptura `V_b` por búsqueda en rejilla (15–85 %).
5. Deflexión sólo si: mejora sobre el lineal **y** `|E_alto/E_bajo − 1| > 0,15` **y** `V_b` dentro de 15–85 %.

## Resultados

Validación contra los numéricos del propio monitor (misma hora): VT integrado 305 vs `TV_EXP` 324 mL · PIP 26,9 vs 25,0 · PEEP 10,1 vs `SET_PEEP` 10 · FR 18,0 vs 18,0.

| | **PEEP 10 / FiO₂ 100 %** | PEEP 5 / FiO₂ 40 % |
|---|---|---|
| E₁ (C) | 72 cmH₂O/L (≈14 mL/cmH₂O) | 29,4 (≈34) |
| R | 15–16 estable | 13–17 |
| `V_b` fracción (40 resp.) | **med 0,26** — 0/40 en los bordes | med 0,18 — **26/40 en los bordes** |
| ratio E_alto/E_bajo | **med 0,59, las 40 < 1 → reclutamiento** | med 1,21, inestable (24/16) |
| `Pel(V_b) − PEEP` | **siempre positivo** (3,6–9,3) | negativo en casi todas |

## Avisos (sin maquillar)

1. **El transitorio de inicio de inspiración** (el flujo sube antes que Paw) hace que `Pel` caiga por debajo de PEEP durante ~0,1–0,2 s. En el paciente de PEEP 5 la ruptura detectada es **sólo ese transitorio**; en el de PEEP 10 pesa poco.
2. El **signo depende del paciente**: el criterio "cumple" da 40/40 y 26/40 respectivamente ⇒ **el criterio por sí solo no discrimina**; hay que exigir coherencia física (`Pel(V_b) > PEEP`).
3. `E₂` sale grande y negativo en el caso ARDS: el "knee" no es perfectamente bilineal.
4. PEEP debe tomarse **al final de la espiración** (último 10 %), no al inicio de la inspiración (bug corregido a mitad de la exploración; los primeros números que se comentaron estaban mal por esto).
5. PIP medido sale ~2 cmH₂O alto (el máximo incluye el sobrepico inicial); mejor usar la meseta.

> Nota: `.gitignore` ignora `*.png`. Si quieres versionar las figuras: `git add -f test_young_results/*.png`.

## Tests 1–4: ¿la rodilla es un artefacto del transitorio?

### TEST 1 — ¿La rodilla es el pico de flujo?

`r(Vb, V_pico_flujo) = 0.089` (ARDS) y `−0.221` (control), pero **`V_pico` casi no varía** (ARDS: med 65 mL, rango 62–70), así que *r* no es informativo. La métrica útil es la diferencia:

| | ARDS | control |
|---|---|---|
| `Vb − V_pico` mediana | **+10,4 mL** | −9,8 mL |
| `\|Vb − V_pico\| < 25 mL` | **34/40 (85 %)** | 25/40 (62 %) |

Las 6 respiraciones fuera del paquete (idx 0, 2, 26, 29, 31, 38; 36–71 mL de diferencia) son **exactamente** las que formaban el segundo modo de Vb_frac (0,44–0,48). → En las 34 restantes **la rodilla está clavada al pico de flujo**.

### TEST 2 — Sólo fase decelerante (R por modelo lineal, sin E₂)

| | ARDS | control |
|---|---|---|
| Vb_frac original → decelerante | 0,26 → **0,36** (p10 0,08 / p90 0,74) | 0,18 → 0,32 (p10 0,21 / p90 0,38) |
| ratio original → decelerante | 0,59 → 0,62 | **1,21 → 0,49 (el signo se invierte)** |
| R con E₂ vs R lineal | 15,4 → 13,2 | 14,8 → 11,3 |

- **El codo de ~0,45 NO emerge sistemáticamente**: Vb salta entre ~20, ~150 y ~250 mL.
- **R estaba inflada un 15–25 %** por el término E₂ → **circularidad confirmada**.
- En el control el signo **cambia de rigidización a reclutamiento** sólo por quitar el transitorio.

### TEST 3 — Rama espiratoria

- Área de histéresis: **0,48 cmH₂O·L ≈ 47 mJ** (p10 0,42 / p90 0,55).
- Al mismo volumen, la rama espiratoria queda **1,95 cmH₂O por debajo** de la inspiratoria.
- Ambas ramas son cóncavas, pero **la inspiratoria bastante más** → parte de la concavidad es del tejido y parte del transitorio/viscoelasticidad.

### TEST 4 — Sensibilidad a R

| R | −30 % | −20 % | −10 % | 0 | +10 % | +20 % | +30 % |
|---|---|---|---|---|---|---|---|
| ratio mediano | 0,23 | 0,30 | 0,41 | **0,59** | **1,00** | 3,23 | −5,89 |
| Vb_frac mediano | 0,33 | 0,33 | 0,34 | 0,26 | 0,24 | 0,20 | 0,20 |

**El ratio cruza 1 (cambio de signo) entre 0 % y +10 %.** → Ni el signo ni la intensidad de la "deflexión" son identificables: los determina el valor supuesto de R, no los datos. La **posición** (Vb_frac) sí es más estable (0,20–0,34).

### Veredicto

1. La rodilla **está anclada al pico de flujo** en el 85 % de las respiraciones → tu sospecha del transitorio se confirma.
2. Pero **quitar el transitorio no revela una rodilla mejor**: la desestabiliza (Vb salta por todo el rango). El codo de 0,45 no reaparece.
3. El **índice ratio es tan sensible a R que el signo no es identificable**; cualquier "reclutamiento" o "rigidización" reportado con este método es una afirmación sobre R, no sobre el pulmón.
4. Lo que **sí** parece robusto: la concavidad de Pel(V) (compliance creciente con V) y una histéresis de ~47 mJ.
