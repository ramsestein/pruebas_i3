# Fase 1.6e, punto 2 — ¿separan los umbrales fijos?

## vitaldb

- Afectados 50 · controles 20
- Minutos activos (afectados): {'n': 50, 'p5': 0.0, 'p25': 17.25, 'mediana': 76.5, 'p75': 451.75, 'p95': 721.0}
- Minutos en espera (`vent_standby`): 5169 {'TV_EXP': 3, 'MV_EXP': 1, 'AWP_WAV': 5164, 'VENT_RR': 1}

### TV_EXP (umbral 100.0 mL)
- **Clara** (controles, minutos activos): {'n': 6708, 'p5': 259.0, 'p25': 322.0, 'mediana': 399.0, 'p75': 481.0, 'p95': 580.65}
- **Sospechosa** (afectados, minutos sin actividad): {'n': 3, 'p5': 6.5, 'p25': 32.5, 'mediana': 65.0, 'p75': 78.0, 'p95': 88.4}
- % ≥ umbral → clara **99.8** vs sospechosa **0.0**

### MV_EXP (umbral 1.0 L/min)
- **Clara** (controles, minutos activos): {'n': 6708, 'p5': 3.96, 'p25': 5.15, 'mediana': 6.3, 'p75': 7.69, 'p95': 12.873}
- **Sospechosa** (afectados, minutos sin actividad): {'n': 1, 'p5': 0.6, 'p25': 0.6, 'mediana': 0.6, 'p75': 0.6, 'p95': 0.6}
- % ≥ umbral → clara **100.0** vs sospechosa **0.0**

### AWP_WAV (umbral 5.0 cmH2O)
- **Clara** (controles, minutos activos): {'n': 3432, 'p5': 205.752, 'p25': 400.0, 'mediana': 662.0, 'p75': 980.25, 'p95': 1035.0}
- **Sospechosa** (afectados, minutos sin actividad): {'n': 5164, 'p5': 0.0, 'p25': 0.0, 'mediana': 0.0, 'p75': 0.0, 'p95': 0.0}
- % ≥ umbral → clara **100.0** vs sospechosa **3.0**

