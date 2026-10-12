# Diagnóstico de ventilación activa — resumen (Fase 1.6e, punto 1)

## clinic

- Afectados: **115** · controles: 20
- Con ventilación activa detectada: 102 (88.7 %)
- **No cambian** con el fix (fin activo ≈ fin actual): 65
- **Sí cambian** (la actividad acaba antes): 37 (mediana del recorte 0.7920000000000016 h, p90 5.021000000000001 h)
- **Sin actividad ninguna** (el evento desaparece): 13
- Pistas que prolongan sin actividad: {'FIO2': 4, 'AWP_WAV': 3, 'FLOW_WAV': 3, 'PEEP': 1, 'PIP': 1, 'TV_EXP': 1, 'TV_INSP': 1, 'VENT_RR': 1}
- Cola nueva ≥ 1 h: 9 · < 1 h: 93
- **Recuperados** (pasarían a éxito): 9 · siguen censurados: 93 · desaparecen: 13
- **Control**: cola mediana 27.646 h; 15/20 con cola ≥ 1 h

  - sin actividad: `clinic_box12_event_11` → FIO2=403min(max 51.0)
  - sin actividad: `clinic_box12_event_12` → FIO2=246min(max 31.0)
  - sin actividad: `clinic_box14_event_1` → AWP_WAV=10min(max 0.0) · FLOW_WAV=10min(max 0.0)
  - sin actividad: `clinic_box14_event_13` → AWP_WAV=706min(max 0.0) · FLOW_WAV=706min(max 0.0)
  - sin actividad: `clinic_box14_event_14` → AWP_WAV=314min(max 0.0) · FLOW_WAV=314min(max 0.0)
  - sin actividad: `clinic_box14_event_22` → FIO2=196min(max 35.0) · AWP_WAV=196min(max 243.0) · FLOW_WAV=196min(max 125.0)
  - sin actividad: `clinic_box14_event_3` → AWP_WAV=1min(max 0.0) · FLOW_WAV=1min(max 0.0)
  - sin actividad: `clinic_box14_event_39` → FIO2=71min(max 30.0) · AWP_WAV=71min(max 100.0) · FLOW_WAV=71min(max 1.0)
  - sin actividad: `clinic_box14_event_40` → FIO2=110min(max 21.0) · AWP_WAV=661min(max 760.0) · FLOW_WAV=661min(max 2313.0)
  - sin actividad: `clinic_box14_event_6` → AWP_WAV=436min(max 0.0) · FLOW_WAV=436min(max 0.0)
  - sin actividad: `clinic_box14_event_7` → AWP_WAV=19min(max 0.0) · FLOW_WAV=19min(max 0.0)
  - sin actividad: `clinic_box4_event_1` → FIO2=4min(max 61.0)
  - sin actividad: `clinic_box4_event_2` → FIO2=134min(max 50.0)

## vitaldb

- Afectados: **50** · controles: 20
- Con ventilación activa detectada: 43 (86.0 %)
- **No cambian** con el fix (fin activo ≈ fin actual): 14
- **Sí cambian** (la actividad acaba antes): 29 (mediana del recorte 8.607999999999976 h, p90 9.014 h)
- **Sin actividad ninguna** (el evento desaparece): 7
- Pistas que prolongan sin actividad: {'AWP_WAV': 3, 'FLOW_WAV': 3}
- Cola nueva ≥ 1 h: 29 · < 1 h: 14
- **Recuperados** (pasarían a éxito): 29 · siguen censurados: 14 · desaparecen: 7
- **Control**: cola mediana 24.925 h; 17/20 con cola ≥ 1 h

  - sin actividad: `vitaldb_SICU1_04_event_1` → AWP_WAV=241min(max 0.0) · FLOW_WAV=241min(max 0.0)
  - sin actividad: `vitaldb_SICU1_05_event_1` → AWP_WAV=721min(max 0.0) · FLOW_WAV=721min(max 0.0)
  - sin actividad: `vitaldb_SICU1_06_event_1` → AWP_WAV=721min(max 0.0) · FLOW_WAV=721min(max 0.0)
  - sin actividad: `vitaldb_SICU1_07_event_1` → FIO2=81min(max 100.0) · AWP_WAV=705min(max 1967.2) · FLOW_WAV=705min(max 3503.0)
  - sin actividad: `vitaldb_SICU1_08_event_1` → AWP_WAV=721min(max 0.0) · FLOW_WAV=721min(max 0.0)
  - sin actividad: `vitaldb_SICU1_09_event_3` → AWP_WAV=191min(max 0.0) · FLOW_WAV=191min(max 0.0)
  - sin actividad: `vitaldb_SICU2_09_event_3` → nada

