# Fase 1 — Informe (PARCIAL, interino)

> **Estado:** Fase 1 **no cerrada**. Se han implementado y dejado en verde los
> seis puntos de código (commits por punto), pero **las ejecuciones pesadas
> sobre los datos crudos no se han lanzado en esta sesión**, por lo que las
> tablas numéricas de este informe quedan **PENDIENTES**. No se rellenan con
> estimaciones: se indica el comando exacto que las produce.
>
> Este documento NO es la parada obligatoria de fin de Fase 1; es el punto de
> control intermedio acordado con el usuario.

## 1. Entregables de código (commits)

| Punto | Commit | Ficheros principales | Tests |
|-------|--------|----------------------|-------|
| 1 | `95ff59e` | `src/common/episodes.py` (D1/D2/D4) | `src/common/tests/test_episodes.py` (21) |
| 2 | `cff0d46` | `src/common/paths.py`, `src/common/vital_signals.py`, `src/create_dataset/build_signal_cases.py` | `src/create_dataset/tests/test_build_signal_cases.py` (15) |
| 3 | `1e5ffc7` | `src/common/timeutils.py`, `src/create_dataset/mimic_itemids.py`, `src/create_dataset/build_mimic_cases.py` | `test_no_naive_timestamp.py`, `test_mimic_cases.py` |
| 4 | `ef89b43` | `src/common/eicu_rules.py` + adaptador/builder eICU | `src/common/tests/test_eicu_rules.py` (19) |
| 5 | `6b47c029` | `src/common/d5_events.py` | `src/common/tests/test_d5_events.py` (22) |
| 6 | `c7fe70a` | `src/common/labels.py` (D3) + índices de las 4 cohortes | `src/common/tests/test_labels.py` (10) |

Total: **146 tests en verde, 1 saltado** (el que valida itemids contra
`datasets/mimic3wdb/clinical/D_ITEMS.csv.gz`, no presente; el de `D:/data`
sí se ejecuta y pasa).

## 2. Decisiones implementadas

- **D1/D2/D4** en `src/common/episodes.py`, agnóstico de cohorte: fusión de
  desconexiones ≤ 2 h, separación de intentos > 2 h, cambio de paciente por
  hueco de monitor > 1 h o por frontera de estancia, y exclusión de ventilador
  sin paciente (≥ 80 % sin HR ni SpO2).
- **D6/D12** en los builders de Clínic/VitalDB y MIMIC: cada evento guarda
  `t0_unix`, `t0_source`, `arrived_ventilated`, intentos, `end_reason`,
  etiquetas e identificadores de origen.
- **D7/D9/D10** documentados en config; MAP invasiva→no invasiva ya resuelta en
  eICU (`pick_map_source`) y MIMIC (itemids 220052 → 220181).
- **D8/D11** pendientes (Fase 2).

## 3. Bugs corregidos (cada uno con test que falla antes)

1. **Detección de ventilación por bytes** en `build_clinical_cases.py` y
   `build_vitaldb_cases.py`: sustituida por lectura con el parser de `vitaldb`
   exigiendo presencia real de pistas (`src/common/vital_signals.py`).
2. **Eliminación de episodios "con mismo inicio"** (descartaba eventos
   legítimos): sustituida por segmentación D1.
3. **Límite de 7 días y "resume" del merge** (VitalDB): eliminados; las
   versiones son inmutables y el build aborta si la carpeta existe.
4. **`itemid 224696` usado como PIP**: es *"Plateau Pressure"*; el PIP correcto
   es `224695` (*"Peak Insp. Pressure"*). Corregido y probado.
5. **`itemid 682` usado como MV**: es *"Tidal Volume (Obser)"*; el MV correcto
   es `224687` (*"Minute Volume"*) / `448` (CareVue). Corregido.
6. **RR = 220210 (RR del monitor)** en vez de la FR total del ventilador
   (`224690`, *"Respiratory Rate (Total)"*). Corregido en el catálogo.
7. **Regex `'epine'`** capturaba también `norepinephrine`: sustituida por
   patrones sin solape (`d5`→`eicu_rules`).
8. **FR de enfermería sobrescribía la del ventilador** en eICU: la de
   enfermería pasa a `eICU/RR_nurse` y NO se usa como RR (D7).
9. **Vasopresores de `medication` (bolos) sobrescribían los de `infusionDrug`**:
   la infusión continua tiene prioridad (`overwrite=False` para los bolos).
10. **Corte fijo `gap <= 48.0`** en el adaptador eICU: eliminado; la
    clasificación por ventanas la hace `classify_attempts` (D3).
11. **Duraciones/huecos imposibles** de eICU (hasta 25 713 h, offsets fuera de
    la estancia): reglas explícitas + registro de anomalías
    (`sanitize_vent_episodes`), nunca silenciadas.
12. **`.timestamp()` sobre datetimes naive** (usaba la hora LOCAL del equipo) en
    26 puntos del repo: refactorizado a `to_epoch_utc`/`ensure_utc`
    (`src/common/timeutils.py`). Un test repo-wide lo prohíbe a partir de ahora.
13. **Falso positivo de traqueostomía**: el patrón `trach` capturaba
    *"Endotracheal"*; corregido con `\btrach\w*`. *(Lo detectó el control
    negativo del propio test, no una revisión manual.)*

## 4. Hallazgos de datos relevantes

- El `CHARTEVENTS` de `D:/data/mimiciii` mezcla itemids de **CareVue** (5xx,
  6xx, 3xxx) y **Metavision** (22xxxx): es MIMIC-III, no MIMIC-IV. El catálogo
  cubre ambos orígenes.
- `D:/data/mimic-iv-3.1/` existe pero **no** es la fuente del pipeline actual.
- La columna de `Time` de `vitaldb.get_samples` es naive: los epochs anteriores
  dependían de la zona horaria del equipo.

## 5. PENDIENTE (requiere ejecutar los builds pesados)

Comandos (desde la raíz del repo; las rutas crudas se leen de `harmonize.yaml`
o de sus variables de entorno):

```powershell
# Segmentación + índice (sin fusionar ficheros .vital)
python -m src.create_dataset.build_signal_cases --cohort clinic  --no-merge
python -m src.create_dataset.build_signal_cases --cohort vitaldb --no-merge
python -m src.create_dataset.build_mimic_cases
```

Tablas que faltan (todas dependen de lo anterior):

- [ ] eventos antes/después por cohorte;
- [ ] intentos por evento;
- [ ] éxito/fallo/censura a 48 h y 72 h, desglosando la censura por causa;
- [ ] traqueostomías y extubaciones terminales (censura principal + sensibilidad);
- [ ] distribución completa de duración;
- [ ] distribución desconexión→muerte (eICU/MIMIC);
- [ ] eventos con ventilador y monitor apagándose a la vez (≤ 15 min) en
      Clínic/VitalDB;
- [ ] eventos excluidos como actividad sin paciente;
- [ ] 10 eventos aleatorios por cohorte (semilla fija) con PNG;
- [ ] eventos ≥ 7 días de Clínic/VitalDB que terminan en "extubación" con PNG.

Además, para completar el punto 5 falta **conectar la detección** con las tablas
clínicas reales:

- MIMIC: `DIAGNOSES_ICD` (ICD-9 31.1/31.2x) y `ADMISSIONS.DEATHTIME`;
- eICU: `respiratoryCare.airwaytype` y `unitdischargestatus = 'Expired'`;
- Clínic/VitalDB: solo la heurística de pérdida de constantes
  (`terminal_from_signal_loss`) — documentada como limitación, sin estimar con
  tasas de otras cohortes.

## 6. Limitaciones explícitas

- En Clínic/VitalDB la única frontera de paciente es el hueco de monitor > 1 h;
  eventos largos con monitor continuo (p. ej. la cama ocupada por otro
  paciente sin hueco de señal) **no se pueden separar** y quedan documentados.
- `obs_end_h` para las etiquetas D3 en Clínic/VitalDB se aproxima con la
  duración del propio episodio (no hay tabla de estancia).
- Las reintubaciones tras un reingreso en MIMIC (sensibilidad) aún no se
  calculan.
