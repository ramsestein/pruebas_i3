# Fase 1.6f, punto 1 — de dónde sale `obs_end_h` (clinic)

## Qué es y de dónde sale

`obs_end_h` es el fin de la **observación fisiológica** (FC/SpO2 con
valores fisiológicos, unidas con 1 h de tolerancia) posterior al fin de
la ventilación, recortado a la región de paciente (D2). Lo calcula
`episodes.build_episodes`:

```python
tail = clip_spans(physiological, attempts[-1].end_h, region_end_h)
observation_end_h = tail[-1].end_h if tail else attempts[-1].end_h
```

La rama `else` es un **suelo**, no un tope: solo se toma cuando NO hay
ninguna muestra fisiológica después del fin de la ventilación, y entonces
vale exactamente lo mismo que `vent_end_h` (el evento queda censurado).
`observation_end_h` **nunca** puede ser menor que el fin de la ventilación
(el recorte es `[vent_end, region_end]` y el fallback es `vent_end`), así
que no hay ningún camino en el código que recorte una observación larga.
El respaldo `_region_end_h → episode.end_h` de `build_signal_cases` no es
alcanzable: `build_episodes` siempre deja `region_end_h` finito.

Prueba con test: `src/create_dataset/tests/test_obs_end.py`
(cola larga → se extiende; sin cola → igual a `vent_end_h` y censurado;
ondas sin fisiología → no alargan la observación).

## Auditoría del índice

- Índice: `datasets/clinic/cases_v0.6.0_cbf8b634/clinic_cases_index.json`
- Eventos: **143**
- Con `obs_end_h == vent_end_h`: **74**
- Con `monitor_tail_h == 0`: **74**
- ¿Toda igualdad implica cola cero? **sí**

| `end_reason` | eventos | de los que igualan |
|---|---|---|
| death_at_vent | 1 | 0 |
| end_of_record | 82 | 74 |
| extubation_observed | 60 | 0 |

