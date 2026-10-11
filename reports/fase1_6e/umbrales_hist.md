# Fase 1.6e, punto 2 — ¿separan los umbrales fijos?

## clinic

- Afectados 115 · controles 20
- Minutos activos (afectados): {'n': 115, 'p5': 0.0, 'p25': 211.0, 'mediana': 601.0, 'p75': 711.0, 'p95': 721.0}
- Minutos en espera (`vent_standby`): 5175 {'TV_EXP': 477, 'MV_EXP': 6, 'AWP_WAV': 3261, 'VENT_RR': 477, 'PIP': 477, 'PEEP': 477}

### TV_EXP (umbral 100.0 mL)
- **Clara** (controles, minutos activos): {'n': 7883, 'p5': 334.0, 'p25': 412.0, 'mediana': 464.0, 'p75': 539.0, 'p95': 745.95}
- **Sospechosa** (afectados, minutos sin actividad): {'n': 477, 'p5': 0.0, 'p25': 13.0, 'mediana': 23.0, 'p75': 51.0, 'p95': 83.4}
- % ≥ umbral → clara **100.0** vs sospechosa **0.0**

### MV_EXP (umbral 1.0 L/min)
- **Clara** (controles, minutos activos): {'n': 5768, 'p5': 5.53, 'p25': 7.03, 'mediana': 8.22, 'p75': 10.6, 'p95': 12.769}
- **Sospechosa** (afectados, minutos sin actividad): {'n': 6, 'p5': 0.0, 'p25': 0.003, 'mediana': 0.02, 'p75': 0.142, 'p95': 0.577}
- % ≥ umbral → clara **100.0** vs sospechosa **0.0**

### AWP_WAV (umbral 5.0 cmH2O)
- **Clara** (controles, minutos activos): {'n': 719, 'p5': 1173.0, 'p25': 1193.0, 'mediana': 1240.0, 'p75': 1260.6, 'p95': 1320.0}
- **Sospechosa** (afectados, minutos sin actividad): {'n': 3261, 'p5': 0.0, 'p25': 0.0, 'mediana': 0.0, 'p75': 0.0, 'p95': 73.05}
- % ≥ umbral → clara **100.0** vs sospechosa **23.3**

