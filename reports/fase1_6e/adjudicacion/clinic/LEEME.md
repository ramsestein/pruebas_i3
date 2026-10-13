# Adjudicacion visual ciega — clinic (181 eventos)

> **CANCELADO (13/10/2026): NO adjudiques esta hoja.** La union de cursos queda
> **desactivada** (opcion a). Motivo en `CANCELADO.md` y en
> `reports/fase1_6e/decision_union.md`. Este LEEME se conserva solo como traza.

Tienes **34 parejas** (22 candidatas + 12 controles) en
PNG, en orden aleatorio y con codigo anonimo. **No sabes cuales son controles**
(de pacientes distintos) ni los huecos ni las clases: es a proposito.

## Que mirar

Cada PNG tiene 6 h ANTES y 6 h DESPUES de un corte (linea negra discontinua).
Las dos mitades estan PEGADAS: el eje x son horas relativas al corte, asi que
**la duracion real del hueco esta oculta**. Arriba el monitor (FC, SpO2, PAM),
abajo el ventilador (TV_EXP, VENT_RR, PEEP, FiO2, PIP). Debajo, la diferencia de
medianas despues - antes.

## Que decidir (columna `decision` de `hoja.csv`)

- `mismo`   → parece el MISMO paciente y el MISMO curso: la ventilacion continua
              (PEEP, FiO2, TV y FR compatibles), la FC/SpO2/PAM son coherentes a
              los dos lados del corte.
- `distinto`→ parecen pacientes distintos o cursos distintos (cambia el patron
              de ventilacion de forma incompatible, o la senal fisiologica no
              encaja).
- `dudoso`  → no te atreves a decidir. **Se tratara como no unir.**

## Como rellenarlo (paso a paso)

1. Abre `hoja.csv` (VSCode, Excel o LibreOffice). Tiene **una fila por pareja**
   con las columnas `codigo,decision,comentario`; `decision` y `comentario`
   estan VACIAS.
2. Abre el PNG con ese mismo codigo (`P01.png`, `P02.png`, ...) y mira el corte.
3. Escribe en la columna `decision` UNA de estas tres palabras exactas:
   `mismo`, `distinto` o `dudoso` (minusculas, sin acentos, sin espacios).
4. Si quieres, anota el motivo en `comentario` (texto libre, no se parsea).
5. Guarda el fichero **como CSV** (Excel en espanol: "CSV UTF-8 (delimitado por
   comas)"; el lector acepta tambien punto y coma).

Ejemplo de las tres primeras filas ya rellenas:

```
codigo,decision,comentario
P01,mismo,TV y FR identicos a los dos lados
P02,distinto,cambia el patron de ventilacion por completo
P03,dudoso,mitad izquierda con muy pocos datos
```

- Si dejas la decision EN BLANCO se cuenta como `dudoso` (no se une).
- **No cambies la columna `codigo` ni anadas/quites filas:** es lo unico que
  dice a que pareja corresponde cada decision.
- Con 34 filas son unos 20-30 minutos. Marca sin miedo: la duda ya es una
  respuesta valida.

## Importante

- No se une nada automaticamente: solo se uniran (en `union_adjudicada.py`) las
  parejas que marques `mismo`.
- Si marcas como `mismo` alguna pareja que en realidad era CONTROL, el script
  avisara y **no unira nada** hasta que lo revises: seria un falso positivo del
  adjudicador.
