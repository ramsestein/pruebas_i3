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
