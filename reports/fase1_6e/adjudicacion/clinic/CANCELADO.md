# CANCELADO — este paquete NO se usa

**Decisión del 13/10/2026 (opción a): no se une ningún curso partido.**

Este paquete de adjudicación visual se generó para decidir qué parejas de
eventos unir. **Se descarta** y `hoja.csv` no hay que rellenarla. Motivo
completo en `reports/fase1_6e/decision_union.md`:

1. La pregunta ("¿es el mismo paciente?") no se puede responder con 12 h de
   constantes vitales: no hay firma que identifique a una persona.
2. La pregunta que de verdad importaba (¿el paciente salió del box aún
   intubado, o se extubó y lo reintubaron?) no la resuelve la señal, porque el
   hueco **no está grabado**.
3. El `gap_h` con el que se eligió esta población no refleja la señal: hay
   candidatas con "hueco 0,00 h" declarado y ~1 h sin FC/SpO2 justo en el corte.

Unir una extubación fallida con su reintubación borraría la variable del
estudio, así que ante la duda no se une nada.

Los 34 PNG se conservan solo como traza de lo explorado. **No los adjudiques.**
