# Calibración de G en MIMIC-MetaVision (Fase 1.6b punto 1 / 1.6c punto 3)

| G (h) | anotación | perdidos | fuera | fragmentos/int | inicio med (P90, ±2h) | fin med (P90, ±2h) | F1 reintub | etiq 48 h | Δ extubación med |
|---|---|---|---|---|---|---|---|---|---|
| 8 | 1 h | 3.1 % | 8.1 % | 1.07 | -0.07 (1.49, 91.9 %) | -1.25 (11.00, 56.8 %) | 0.398 | 85.2 % | -1.08 h |
| 8 | 2 h | 4.4 % | 8.0 % | 1.08 | -0.08 (1.50, 91.9 %) | -1.52 (11.03, 52.5 %) | 0.394 | 83.8 % | -1.43 h |
| 8 | 4 h | 13.1 % | 7.9 % | 1.26 | -0.08 (2.00, 90.3 %) | -2.35 (12.99, 37.0 %) | 0.246 | 75.7 % | -2.43 h |
| 10 | 1 h | 3.0 % | 8.2 % | 1.04 | -0.08 (1.52, 91.5 %) | -1.23 (12.38, 56.4 %) | 0.440 | 85.2 % | -1.03 h |
| 10 | 2 h | 4.4 % | 8.2 % | 1.04 | -0.08 (1.55, 91.5 %) | -1.50 (12.81, 52.1 %) | 0.438 | 83.9 % | -1.37 h |
| 10 | 4 h | 12.9 % | 8.1 % | 1.06 | -0.08 (1.88, 90.7 %) | -2.31 (14.79, 36.9 %) | 0.410 | 75.8 % | -2.13 h |
| 12 | 1 h | 3.0 % | 8.4 % | 1.03 | -0.08 (1.70, 91.0 %) | -1.20 (14.43, 55.9 %) | 0.457 | 85.2 % | -1.02 h |
| 12 | 2 h | 4.3 % | 8.4 % | 1.03 | -0.08 (1.73, 90.9 %) | -1.50 (14.82, 51.7 %) | 0.454 | 83.8 % | -1.33 h |
| 12 | 4 h | 12.9 % | 8.3 % | 1.04 | -0.08 (2.00, 90.3 %) | -2.27 (17.04, 36.6 %) | 0.445 | 75.8 % | -2.07 h |

## Elección

**G elegido = 10.0 h**

Regla: extra<=10% en todos los estratos; perdidos<=5% (anotacion<=2h) y <=15% (anotacion 4h); entre los que cumplen, max(min_estrato(score)); empate <0.02 -> G menor

| G (h) | cumple criterios | peor estrato (score) |
|---|---|---|
| 8 | sí | 0.506 |
| 10 | sí | 0.562 |
| 12 | sí | 0.571 |

## Error de etiqueta esperado con el G elegido

Traslado a eICU según la frecuencia de anotación del hospital

| Anotación | fin med (IQR) | fin P90 | fin ±2 h | reintub sens | reintub VPP | F1 | etiq 48 h |
|---|---|---|---|---|---|---|---|
| 1 h | -1.23 h (2.55) | 12.38 h | 56.4 % | 0.588 | 0.351 | 0.440 | 85.2 % |
| 2 h | -1.50 h (2.50) | 12.81 h | 52.1 % | 0.579 | 0.352 | 0.438 | 83.9 % |
| 4 h | -2.31 h (3.15) | 14.79 h | 36.9 % | 0.564 | 0.322 | 0.410 | 75.8 % |

## Desplazamiento del fin (con signo, G elegido)

Distribución del error de fin reconstruido − referencia (h):

| Anotación | P10 | P25 | mediana | P75 | P90 | % |err| ≤ 2 h |
|---|---|---|---|---|---|---|
| 1 h | n/a | n/a | -1.23 | n/a | n/a | 56.4 % |
| 2 h | n/a | n/a | -1.50 | n/a | n/a | 52.1 % |
| 4 h | n/a | n/a | -2.31 | n/a | n/a | 36.9 % |

## Matriz de confusión 3x3 de la etiqueta a 48 h

Filas = método de anotaciones; columnas = referencia.
Clases: éxito / éxito con fallo previo / censura.

**Estrato 1 h** (concordancia 81.3 %)

| | ref éxito | ref fallo+éxito | ref censura |
|---|---|---|---|
| **exito** | 6715 | 116 | 1061 |
| **exito_con_fallo_previo** | 238 | 205 | 107 |
| **censura** | 172 | 0 | 466 |

**Estrato 2 h** (concordancia 80.0 %)

| | ref éxito | ref fallo+éxito | ref censura |
|---|---|---|---|
| **exito** | 6602 | 117 | 1067 |
| **exito_con_fallo_previo** | 232 | 204 | 108 |
| **censura** | 291 | 0 | 459 |

## Corrección del fin por estrato

Desplazamientos medidos (mediana con signo): {'1 h': 1.23, '2 h': 1.5, '4 h': 2.31}

¿Mejora la concordancia en los estratos de 1 h y 2 h? **SÍ** ({'1 h': 2.09, '2 h': 2.53} pp)

| Anotación | etiq 48 h antes | etiq 48 h después | F1 antes | F1 después | fin ±2 h después |
|---|---|---|---|---|---|
| 1 h | 85.2 % | 87.3 % | 0.440 | 0.440 | 70.2 % |
| 2 h | 83.9 % | 86.4 % | 0.438 | 0.438 | 70.5 % |
| 4 h | 75.8 % | 79.0 % | 0.410 | 0.410 | 60.8 % |
