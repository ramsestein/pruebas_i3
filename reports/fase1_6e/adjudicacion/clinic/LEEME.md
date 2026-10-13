# Adjudicacion visual ciega — clinic (181 eventos)

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

Escribe la decision en `hoja.csv` (y un comentario breve si quieres). **No
cambies la columna `codigo`.**

## Importante

- No se une nada automaticamente: solo se uniran (en `union_adjudicada.py`) las
  parejas que marques `mismo`.
- Si marcas como `mismo` alguna pareja que en realidad era CONTROL, el script
  avisara y **no unira nada** hasta que lo revises: seria un falso positivo del
  adjudicador.
