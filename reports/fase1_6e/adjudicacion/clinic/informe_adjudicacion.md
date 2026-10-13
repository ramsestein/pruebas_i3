# Paquete de adjudicacion — clinic

- Indice: `datasets\clinic\cases_v0.5.0_eac1eb35\clinic_cases_index.json`
- Poblacion: cascada de candidatas (variante d), SIN exigir apagado simultaneo (el desfase se informa por pareja)
- Candidatas variante d: **22** (de 22 encontradas)
- Controles incluidos: **12** (de 12 disponibles)
- Semilla: `20261013` · codigos `P01`…`P34`
- Clave (FUERA del repo): `D:\data\iprove3_work\adjudicacion_clave.json`
- SHA-256 de la clave: `48a104a5d059af43ea448867ba95fe00bf19978af564766245e60bc2c2a3ee60`
- Identificadores por pareja: box + epoch del fin del primer tramo + epoch del inicio del segundo, y el fichero de origen que cubre el corte a cada lado con su **SHA-256 del CONTENIDO** (no del nombre) y su tamano. Sin `--sin-sha`.
- Paneles sin datos: **0** 


Identificadores ESTABLES por pareja (nunca `event_id` posicional): box + epoch del fin del primer tramo + epoch del inicio del segundo + SHA-256 del CONTENIDO del fichero de origen de cada lado. Al aplicar la adjudicacion se empareja por esos tiempos (±15 min).

Cascada: {'paso0': 168, 'paso1': 62, 'paso2': 62, 'paso3': 56, 'paso4': 22}
