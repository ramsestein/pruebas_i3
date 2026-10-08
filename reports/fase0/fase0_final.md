# Fase 0 — Resultados y descripción de datos final

> Generado el 2026-10-02 09:06:53

## 1. Tests de completitud

**Estado global:** ✅ TODO VERDE — 10/10 verdes.

| Resultado | Comprobación | Detalle |
|---|---|---|
| ✅ | C1_clinic_index |  |
| ✅ | C2_clinic_t0_fields |  |
| ✅ | C3_clinic_t0_vs_file | max offset 0.0s |
| ✅ | V1_vitaldb_index |  |
| ✅ | V2_vitaldb_t0_fields |  |
| ✅ | V3_vitaldb_t0_vs_file | max offset 0.0s |
| ✅ | M1_mimic_clinical_tables |  |
| ✅ | M2_mimic_labels |  |
| ✅ | G1_harmonized_outputs |  |
| ✅ | G2_channel_availability |  |

## 2. Descripción de datos final

### Clínic

- **Casos:** 50
- **t0_unix/tend_unix en índice:** sí
- **Duración mediana del episodio:** 41.8 h (mín 2.6 h, máx 256.1 h)

### VitalDB SICU

- **Casos:** 83
- **t0_unix/tend_unix en índice:** sí
- **Duración mediana del episodio:** 42.0 h (mín 0.1 h, máx 1171.0 h)

### MIMIC-III

- **Pacientes:** 82
- **Censurados (sin extubación):** 0
- **Fuente:** tablas clínicas `datasets/mimic3wdb/clinical/` + `.vital` enriquecidos existentes.

## 3. Disponibilidad de canales

Versión harmonized: `v0.1.0_df652b7b`

| Cohorte | Casos | ECG | PPG | ABP | HR | SBP | DBP | MAP | SpO2 | RR | FiO2 | PEEP | TV | MV | PIP |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| clinic | 50 | 94.0% | 100.0% | 92.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 80.0% | 100.0% |
| vitaldb | 83 | 98.8% | 98.8% | 90.4% | 97.6% | 96.4% | 96.4% | 96.4% | 97.6% | 96.4% | 0.0% | 0.0% | 92.8% | 90.4% | 0.0% |
| mimic | 82 | 0.0% | 0.0% | 3.7% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 100.0% | 98.8% | 100.0% | 100.0% | 100.0% |

> Disponibilidad de ondas calculada sobre la rama `get_waveforms`; numéricas sobre `get_numerics` (≥1 valor no-NaN).

## 4. Etiquetas de supervivencia

| Cohorte | Total | Éxito | Fallo | Censurados |
|---|---|---|---|---|
| clinic | 50 | 50 | 0 | 0 |
| vitaldb | 83 | 83 | 0 | 0 |
| mimic | 82 | 82 | 0 | 0 |

## 5. Offset t0 (adapter vs primer timestamp)

| Cohorte | Offset máx. absoluto (s) |
|---|---|
| clinic | 0.0 |
| vitaldb | 0.0 |
| mimic | — |

## 6. Artefactos

- Tests de completitud: `reports/fase0/completeness.json`
- Casos Clínic: `datasets/clinic_vitals/clinic_full_cases/` + índice
- Casos VitalDB: `datasets/vitaldb_sicu/vitaldb_full_cases/` + índice
- Tablas clínicas MIMIC-III: `datasets/mimic3wdb/clinical/`
- Salidas harmonized: `C:\Users\Ramsés\Desktop\Proyectos\iprove3\datasets\harmonized\v0.1.0_df652b7b`
