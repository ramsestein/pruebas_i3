# iPROVE3 — Predicción dinámica del destete de ventilación mecánica

> Repositorio de investigación sobre el **tiempo restante hasta una extubación exitosa** en pacientes de UCI, a partir de señales vitales (formas de onda y numéricos) armonizadas desde múltiples cohortes.

---

## 1. Objetivo

Predecir, de forma **dinámica y continua durante el ingreso**, cuánto tiempo falta hasta que un paciente ventilado mecánicamente puede ser extubado con éxito (sin reintubación). El problema se modela como una **regresión distribucional**: en cada instante `t` se estima la distribución del tiempo restante $R(t)$ (log-normal), no un único valor puntual.

Objetivos específicos:

1. **Armonizar** datos crudos de 4 cohortes heterogéneas (frecuencias, nombres de señal y fuentes de eventos clínicos distintos) a un formato común versionado.
2. **Etiquetar** cada caso con sus *landmarks* de intubación → extubación → reintubación (o éxito), y ventanas de fallo a 48 h y 72 h.
3. **Escalón 1 (baseline de supervivencia):** caracterizar la estructura de riesgo $R(t)$ — ¿el tiempo restante tiene señal explotable?
4. **Escalón 2 (modelo dinámico):** entrenar una red LSTM con cabeza distribucional log-normal que consuma secuencias de ventanas vitales y devuelva $(\mu, \log\sigma)$ de $R(t)$, con transferencia externa (entrenar en una cohorte, evaluar en otra).

---

## 2. Datos / Cohortes

| Cohorte | Fuente | Señales | Rol actual |
|---|---|---|---|
| `mimic` | MIMIC-III Waveform Database | Numéricos + ABP (subconjunto) | Test externo (Escalón 2) |
| `vitaldb` | VitalDB (SICU) | ECG, PPG, ABP + numéricos | Análisis / Escalón 1 |
| `clinic` | Hospital Clínic (`clinic_vitals`) | ECG, PPG, ABP + numéricos | Análisis / Escalón 1 |
| `eicu` | eICU Collaborative Research Database | Solo numéricos | Train/val (Escalón 2) |

Los datos crudos viven en `datasets/` y el resultado armonizado en `datasets/harmonized/<version>/` (actualmente `v0.1.0_fbb5b280` para numéricos y `v0.1.0_e9d2ab7c` para la tabla de supervivencia MIMIC).

---

## 3. Arquitectura del pipeline

### Etapa 0 — Armonización, etiquetado y QC (`src/stage0/`)

Pipeline declarativo orquestado por `run_stage0.py` y configurado íntegramente en `src/stage0/config/harmonize.yaml`:

1. Listar pacientes por cohorte activa (via *adapters* por cohorte).
2. Obtener eventos clínicos (t0, extubación, reintubación) y señales (numéricos + formas de onda).
3. Resamplear a 125 Hz, filtrar (Butterworth fase cero), generar *landmarks*.
4. Extraer ventanas de forma de onda, calcular **SQI** y aplicar máscaras de calidad.
5. Construir tablas de supervivencia (48 h / 72 h), tablas de numericos y reporte de QC → salida parquet + NPZ.

### Escalón 1 — Estructura del riesgo (`src/stage0/escalon1_experiment.py`)

Baseline de supervivencia (LightGBM + Theil-Sen) para responder la pregunta previa: **¿$R(t)$ tiene estructura?** Salidas en `results/escalon1_results/<cohort>/` (reportes y figuras).

### Escalón 2 — Modelo dinámico (`src/stage2/`)

Modelo `ExtubationModel` = `WindowEncoder` (MLP compartido) + `TimeDeltaLSTM` + `LogNormalHead`:

- **Entrada:** secuencia de 4 ventanas de 15 min (features vitales + indicadores de missingness).
- **Features:** 8 base (RR, HR, SpO2, PEEP, MAP, FiO2, TV, PIP) + 5 derivados (RSBI, SF_ratio, compliance, driving_pressure, MAP_FiO2).
- **Salida:** $(\mu, \log\sigma)$ de una log-normal → mediana e intervalos de $R(t)$.
- **Entrenamiento:** eICU (85 % train / 15 % val); **test externo:** MIMIC.
- **V2:** target residual sobre `R_base(t)` (LOESS), pérdida NLL + coherencia λ.

Flujo: `run_stage2.py --mode all` → `train.py` / `train_v2.py` → `inference.py` → `evaluate.py`.

---

## 4. Estado actual (resumen)

| Componente | Estado |
|---|---|
| Etapa 0 (armonización) | ✅ Completada — 5 versiones en `datasets/harmonized/` |
| Escalón 1 (estructura de $R(t)$) | ✅ Completado — las 4 cohortes muestran estructura |
| Escalón 2 — Entrenamiento eICU | ✅ Completado — checkpoints en `stage2_results/checkpoints/` |
| Escalón 2 — Evaluación interna (eICU) | ✅ Completada — ver tabla de abajo |
| Escalón 2 — Evaluación externa (MIMIC) | ⏳ Pendiente — solo existe `trajectories_eicu_test.parquet` |
| Scripts de diagnóstico (`stats/`, `test/`) | 🔧 En uso / iterativos |

### Resultados clave

**Escalón 1** — pendiente mediana de $R(t)$ y % de casos convergentes (ideal ≈ −1.0):

| Cohorte | Pendiente | Convergentes | Monotonía (Real vs Null) |
|---|---|---|---|
| MIMIC | −1.000 | 100 % | 0.894 vs 0.612 |
| VitalDB | −3.915 | 72 % | 0.703 vs 0.564 |
| Clinic | −1.019 | 63 % | 0.560 vs 0.542 |
| eICU | −0.990 | 85 % | 0.679 vs 0.548 |

→ Conclusión: **$R(t)$ sí muestra estructura explotable** en todas las cohortes.

**Escalón 2** — evaluación interna eICU (`stage2_results/metrics_summary.csv`):

| Métrica | Valor |
|---|---|
| Pacientes / landmarks | 3 592 / 923 198 |
| MAE global | 117.15 h |
| RMSE global | 233.5 h |
| Cobertura 80 % / 90 % | 0.82 / 0.91 |
| MAE pre-extubación (75–100 %) | 50.4 h |

> El error es elevado en términos absolutos y está dominado por los tramos tempranos del ingreso (238 h MAE al inicio). La cobertura de intervalos (≈0.91 al 90 %) sugiere que la **incertidumbre** está bien calibrada aunque la predicción puntual sea imprecisa. Este es el principal frente de mejora abierto.

---

## 5. Estructura del repositorio

```
iprove3/
├── datasets/                 # Datos crudos + armonizados (versionados)
├── results/escalon1_results/ # Reportes y figuras del Escalón 1 por cohorte
├── src/
│   ├── create_dataset/       # Scripts de descarga y construcción de casos
│   ├── stage0/               # Armonización, etiquetado y QC (+ Escalón 1)
│   ├── stage2/               # Modelo LSTM distribucional (train/infer/eval)
│   └── stats/                # Análisis descriptivos y de features
├── stage2_results/           # Checkpoints, trayectorias y métricas del Escalón 2
├── test/                     # Scripts de diagnóstico ad-hoc
└── requirements.txt
```

---

## 6. Ejecución rápida

```bash
# Etapa 0 (armonización + etiquetado + QC)
python src/stage0/run_stage0.py --dry-run            # validar sin escribir
python src/stage0/run_stage0.py                      # ejecución completa

# Escalón 1 (estructura del riesgo)
python src/stage0/escalon1_experiment.py

# Escalón 2 (train + inferencia + evaluación)
python src/stage2/run_stage2.py --mode all
```

> Nota: `requirements.txt` cubre la Etapa 0/Escalón 1. El Escalón 2 requiere además `torch`, `lightgbm`, `scikit-learn`, `matplotlib` y `seaborn`.

---

## 7. Siguientes pasos (situación actual)

1. **Completar la evaluación externa en MIMIC** (generar `trajectories_mimic*.parquet` y ampliar `metrics_summary.csv`).
2. **Reducir el MAE en tramos tempranos**, donde hoy se concentra el error (predicción puntual imprecisa pese a buena calibración de intervalos).
3. Consolidar dependencias de ambos escalones en `requirements.txt`.
