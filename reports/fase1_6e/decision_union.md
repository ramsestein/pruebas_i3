# Decisión: no se unen cursos partidos (13/10/2026)

**Decisión (opción a):** la unión de cursos de ventilación partidos por un corte
de grabación queda **DESACTIVADA**. El cohorte de análisis tiene **0 uniones**.

Estado en código: `fase1_6e.union.enabled: false` (`harmonize.yaml`, v0.6.1) y
`UNION_ENABLED = False` (`build_signal_cases.segment_box`). La función
`join_courses` se conserva y se puede activar con `unir_cursos=True` **solo**
como análisis de sensibilidad.

## Por qué

### 1. La adjudicación visual no era respondible

Se generó un paquete ciego de 34 parejas (22 candidatas de la variante d + 12
controles) en `reports/fase1_6e/adjudicacion/clinic/`. La pregunta planteada era
si las dos mitades de cada PNG eran "el mismo paciente". **No se puede
responder**: en 12 h de FC, SpO2, PAM, TV_EXP, VENT_RR, PEEP y FiO2 no hay
ninguna firma que identifique a una persona; cualquier paciente de UCI se parece
a cualquier otro. El paquete queda **CANCELADO** (`CANCELADO.md` en esa carpeta).

### 2. La pregunta real tampoco la resuelve la señal

Lo que había que decidir no era "mismo paciente" sino si el corte fue
**"el paciente salió del box y volvió aún intubado"** (unir) o **"se extubó y lo
reintubaron"** (no unir). La evidencia decisiva sería ver qué pasó durante el
hueco, y **el hueco no está grabado**: es justo el intervalo sin datos. Tampoco
lo resuelve el patrón de ajustes: una extubación fallida con reintubación a los
minutos puede tener PEEP, FiO2, TV y FR idénticos a los de antes.

### 3. La medida con la que se filtraba la población no refleja la señal

En `box9`, una candidata con **hueco declarado 0,00 h** muestra en la ventana
ancha (`reports/fase1_6e/figs/ventana_ancha_box9.png`, reproducible con
`python scripts/verify/fase1_6e/ventana_ancha.py --box box9 --gap 0.0`):

- un agujero de **~1 h en FC/SpO2 justo en el corte**;
- **caídas de TV_EXP de 1,5-2,5 h** sin ningún dato de ventilador, varias veces,
  dentro del mismo box.

Y `obs_end_h == vent_end_h` en **las 22 candidatas**: el fin de observación del
índice es un tope del constructor, no una observación del monitor. El `gap_h`
que alimenta el paso 1 de la cascada (y la propia regla estricta) descansa sobre
ese número.

### 4. El riesgo que decide la cuestión

Este proyecto estudia el **fallo de extubación**. Unir una extubación fallida con
su reintubación **borra exactamente la variable de estudio**. Ante esa
asimetría de costes (una unión de más destruye un desenlace; una unión de menos
solo deja un curso partido), la opción conservadora es obligada.

## Consecuencias

| Qué | Estado |
|---|---|
| Cohorte de análisis (Clínic) | v0.5.0 (181 eventos) y el rebuild con la unión **desactivada** (v0.6.1) |
| v0.6.0 (143 eventos) | sus 38 eventos de diferencia con v0.5.0 venían de la unión y de D-i: **no es el cohorte de análisis** |
| Unión de cursos | desactivada; `join_courses` disponible para sensibilidad |
| Paquete de adjudicación | CANCELADO, no se usa |
| D-i (fragmentos de registro < 30 min) | se mantiene: es integridad, no unión |

## Pendiente derivado

- **Rebuild v0.6.1** (unión OFF) de Clínic y VitalDB: es el cohorte definitivo,
  porque v0.5.0 no lleva los arreglos de 1.6e (B1, B2, B4/D-g, D-h, VNI).
- **Análisis de sensibilidad** de la unión (con `enabled: true`) reportado
  aparte, nunca en el cohorte principal.
- Revisar si el `gap_h` basado en `obs_end_h` afecta a la tabla final y a las
  etiquetas de censura (`end_of_record`).

## Evidencia

- `scripts/verify/fase1_6e/cascada_candidatas.py` + `reports/fase1_6e/cascada_clinic.{json,md}`
- `reports/fase1_6e/adjudicacion/clinic/` (paquete cancelado)
- `scripts/verify/fase1_6e/ventana_ancha.py` +
  `reports/fase1_6e/figs/ventana_ancha_box9.png` (vista del corte con eje de
  tiempo continuo y el hueco visible; es la figura que descarta la unión)
