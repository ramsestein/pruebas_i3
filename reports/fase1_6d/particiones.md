# Particiones propuestas (Fase 1.6d, punto 7 — NO aplicadas)

## mimic

- Criterio: test 15% por subject_id + 5 pliegues por paciente
- Test: {'n': 1359, 'success': 1113, 'censored': 246, 'failures': 66}
- Train: {'n': 7734, 'success': 6342, 'censored': 1392, 'failures': 405}
- Pliegues de train: n=1564 (éx 1279/cens 285), n=1550 (éx 1267/cens 283), n=1536 (éx 1251/cens 285), n=1542 (éx 1253/cens 289), n=1542 (éx 1292/cens 250)
- Pacientes: 8160 (test 1224)

## eicu_b

- Criterio: test = 8 hospitales (fijos) + 5 pliegues por hospital del resto
- Test: {'n': 1781, 'success': 1332, 'censored': 449, 'failures': 335}
- Train: {'n': 7973, 'success': 6087, 'censored': 1886, 'failures': 758}
- Pliegues de train: n=2821 (éx 2175/cens 646), n=1122 (éx 907/cens 215), n=2639 (éx 2018/cens 621), n=841 (éx 609/cens 232), n=550 (éx 378/cens 172)
- Hospitales: test [67, 171, 227, 301, 303, 336, 392, 405], train 19

## clinic

- Criterio: test = último 40% del tiempo; train en 2 bloques temporales (ventana creciente: entrenar con el bloque 1, validar con el 2)
- Test: {'n': 72, 'success': 58, 'censored': 14, 'failures': 6}
- Train: {'n': 109, 'success': 82, 'censored': 27, 'failures': 1}
- Bloques de train: n=54 (éx 44/cens 10, fallos 0), n=55 (éx 38/cens 17, fallos 1)

## vitaldb

- Criterio: los 96 eventos = test (test externo; sin ajuste fino ni validación cruzada)
- Test: {'n': 96, 'success': 40, 'censored': 56, 'failures': 5}
- Train: {'n': 0, 'success': 0, 'censored': 0, 'failures': 0}

