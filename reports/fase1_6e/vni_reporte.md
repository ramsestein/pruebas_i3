# Fase 1.6e, punto 3 — efecto de la regla de VNI post-extubación (D-b)

## vitaldb

- Eventos analizados: **70** (afectados)
- Tramos de VNI **confirmados** (reclasificados): **1** en 1 eventos
- Tramos **no verificables** (`posible_vni_no_verificable`, NO se reclasifican): **0** en 0 eventos
- Tramos posteriores con firma **verificable** que resulta **invasiva**: **46**
  (esta medida NO puede usar la pista de modo: la firma se calcula con `TV_INSP`; ver la nota de modo más abajo)
- **Sensibilidad de la etiqueta**: eventos que pasarían de censura (`end_of_record`) a éxito con la regla: **0**

**Nota sobre la pista de modo**: en Clínic **no hay** pista de modo (ver el inventario), así que todos los tramos con firma verificable se deciden por `TV_INSP`; en VitalDB sí existe `SET_SIMV` (SIMV = modalidad INVASIVA) y manda el modo cuando está presente: 1 tramos por modo y 46 por `TV_INSP`.

  - `vitaldb_SICU1_03_event_1`: tramos 3, VNI 1, no verificables 0, firma TV_INSP 0.5, cola con regla 1.539 h (sin regla 1.305 h)

