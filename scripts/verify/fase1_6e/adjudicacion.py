#!/usr/bin/env python3
"""
scripts/verify/fase1_6e/adjudicacion.py
=======================================
Fase 1.6e — paquete de **adjudicacion visual ciega** de parejas de cursos
partidos (D-f revisada, decision del 13/10: variante d).

Que hace
--------
1. Busca en el indice las parejas consecutivas del mismo box que cumplen la
   **variante d** (|dPEEP| <= 3, |dFiO2| <= 20, |dTV| <= 50 mL, |dRR| <= 2,
   hueco <= 12 h y la region siguiente vuelve con ventilacion activa).
2. Anade **controles** (pacientes distintos): hueco > 24 h, mismo box, con TV y
   FR medibles a ambos lados, elegidos al azar con semilla FIJA.
3. Baraja candidatas y controles con semilla fija, asigna codigo anonimo
   (P01...Pnn) y pinta **un PNG por pareja**: 6 h ANTES y 6 h DESPUES pegadas,
   sin revelar la duracion real del hueco ni fechas/horas de reloj, con la tabla
   de medianas antes/despues debajo.
4. Escribe ``hoja.csv`` (codigo, decision, comentario) y ``LEEME.md``.
   La **clave** codigo -> pareja va FUERA del repo (D:) y su SHA-256 se guarda
   en el informe para fijarla.

La decision de unir NO la toma este script: lo hace `union_adjudicada.py` con la
hoja rellena por el usuario.

Uso:
    # solo cuenta (no lee D:, sirve para comprobar el numero de candidatas)
    python scripts/verify/fase1_6e/adjudicacion.py --cohort clinic --solo-lista
    # paquete completo
    python scripts/verify/fase1_6e/adjudicacion.py --cohort clinic
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.common.paths import config_path  # noqa: E402
from src.common.vital_signals import read_track_series  # noqa: E402
from src.create_dataset.build_signal_cases import (  # noqa: E402
    SPECS,
    scan_source_files,
)
from src.stage0.io.versioning import load_config  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from annotate_index import (  # noqa: E402
    EPS,
    MAX_GAP_H,
    RETURN_MIN,
    _starts_ventilated,
)

CONFIG = ROOT / "src/stage0/config/harmonize.yaml"
REPORT = ROOT / "reports" / "fase1_6e"
SEMILLA = 20261013
N_CONTROLES = 12
MARGEN_H = 6.0            # ventana antes/despues
GAP_CONTROL_H = 24.0      # controles: pacientes distintos
# Variante d (13/10)
D_PEEP = 3.0
D_FIO2 = 20.0
D_TV = 50.0
D_RR = 2.0

MONITOR: dict[str, tuple[str, ...]] = {
    "FC": ("Intellivue/ECG_HR", "Intellivue/PLETH_HR", "Intellivue/HR"),
    "SpO2": ("Intellivue/PLETH_SAT_O2",),
    "PAM": ("Intellivue/ABP_MEAN", "Intellivue/NIBP_MEAN",
            "Intellivue/ART_MEAN"),
}
VENT: dict[str, tuple[str, ...]] = {
    "TV_EXP": ("Intellivue/TV_EXP",),
    "VENT_RR": ("Intellivue/VENT_RR",),
    "PEEP": ("Intellivue/PEEP_CMH2O",),
    "FiO2": ("Intellivue/FIO2",),
    "PIP": ("Intellivue/PIP",),
}


def _recorta(files: list, t0_unix: float, variante: str, lo_h: float,
             hi_h: float) -> tuple[np.ndarray, np.ndarray] | None:
    """Serie (horas desde t0, valor) recortada a la ventana, para el grafico."""
    if not files:
        return None
    series = read_track_series([str(f.path) for f in files],
                               list(variante), t0_unix=t0_unix)
    for name in variante:
        t, v = series.get(name, (None, None))
        if t is None or len(t) == 0:
            continue
        h = t / 3600.0
        sel = (h >= lo_h) & (h <= hi_h)
        if sel.any():
            return h[sel], v[sel]
    return None


def _ficheros_de(e: dict, meta: dict) -> list:
    return [meta[n] for n in (e.get("source_files") or []) if n in meta]


def _dos_ultimos(files: list) -> list:
    return sorted(files, key=lambda f: f.dt_unix)[-2:]


def _dos_primeros(files: list) -> list:
    return sorted(files, key=lambda f: f.dt_unix)[:2]


def _desde_cascada(path: Path, idx_por_id: dict) -> dict:
    """Candidatas y controles desde la cascada, con identificadores ESTABLES.

    La poblacion NO exige apagado simultaneo <= 15 min (la cascada informa del
    desfase como columna). Se empareja por los times/ficheros estables, no por
    `event_id` posicional.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    cands, ctrls = [], []
    for tipo, clave in (("candidata", "candidatas"),
                        ("control", "controles_disponibles")):
        for p in data.get(clave) or []:
            par = {**p, "tipo": tipo,
                   "gap_h": p.get("gap_h"),
                   "desfase_vent_monitor_h": p.get("desfase_vent_monitor_h"),
                   "deltas": p.get("deltas") or {}}
            (cands if tipo == "candidata" else ctrls).append(par)
    return {"candidatas": cands, "controles": ctrls,
            "cascada": data.get("cascada"), "notas": data.get("notas") or []}


def _parejas(apag: list[dict], idx_por_id: dict) -> dict:
    """Candidatas (variante d) y controles a partir de los casos de 2c.1.

    NO recalcula nada: usa `hueco_h` y las diferencias medianas
    (d_peep/d_fio2/d_tv/d_rr) que ya calculo `apagado_simultaneo.py`.

    OJO con "vuelve con ventilacion activa": se evalua con el criterio del
    INDICE (`_starts_ventilated`, o sea el inicio del intento respecto al t0),
    NO con el `empieza_activa` de la sonda: ese se mide con la actividad por
    minuto dentro de la ventana de analisis de 2c.1, que para el evento
    siguiente puede estar recortada y da falsos negativos. Por eso el numero de
    candidatas puede diferir del `candidatos_cumplen["d"]` del control negativo
    (que ademas no exigia "vuelve ventilada").
    """
    cands: list[dict] = []
    ctrls: list[dict] = []
    for c in apag:
        nxt_id = c.get("siguiente")
        gap = c.get("hueco_h")
        if nxt_id is None or gap is None:
            continue
        nxt = idx_por_id.get(nxt_id)
        if nxt is None or not _starts_ventilated(nxt, RETURN_MIN):
            continue
        d = {k: c.get(k) for k in ("d_peep", "d_fio2", "d_tv", "d_rr")}
        par = {"box": c.get("box"), "prev": c["event_id"], "next": nxt_id,
               "gap_h": gap, "dPEEP": d["d_peep"], "dFiO2": d["d_fio2"],
               "dTV": d["d_tv"], "dRR": d["d_rr"],
               "clase_2c1": c.get("clase"),
               "tiene_ficheros": c.get("tiene_ficheros")}
        if gap > GAP_CONTROL_H and d["d_tv"] is not None and d["d_rr"] is not None:
            ctrls.append({**par, "tipo": "control"})
        elif (0.0 <= gap <= MAX_GAP_H + EPS
              and all(d[k] is not None for k in d)
              and abs(d["d_peep"]) <= D_PEEP
              and abs(d["d_fio2"]) <= D_FIO2
              and abs(d["d_tv"]) <= D_TV
              and abs(d["d_rr"]) <= D_RR):
            cands.append({**par, "tipo": "candidata"})
    return {"candidatas": cands, "controles": ctrls}


def _mediana(serie: tuple[np.ndarray, np.ndarray] | None) -> float | None:
    return None if serie is None else float(np.nanmedian(serie[1]))


def _pintar(pareja: dict, meta: dict, idx_por_id: dict, png: Path,
            codigo: str, *, con_tabla: bool = True) -> bool:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    prev = idx_por_id[pareja["prev"]]
    nxt = idx_por_id[pareja["next"]]
    f_prev = _dos_ultimos(_ficheros_de(prev, meta))
    f_next = _dos_primeros(_ficheros_de(nxt, meta))
    t0_prev = float(prev["t0_unix"])
    t0_next = float(nxt["t0_unix"])
    vp = float((prev.get("attempts") or [{}])[-1].get("vent_end_h") or 0.0)
    att_next = nxt.get("attempts") or [{}]
    vn = float(att_next[0].get("vent_start_h") or 0.0)
    med: dict[str, tuple[float | None, float | None]] = {}

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    colores = {"FC": "tab:red", "SpO2": "tab:blue", "PAM": "tab:green"}
    for canon, tracks in MONITOR.items():
        s_a = _recorta(f_prev, t0_prev, tracks, vp - MARGEN_H, vp)
        s_b = _recorta(f_next, t0_next, tracks, vn, vn + MARGEN_H)
        med[canon] = (_mediana(s_a), _mediana(s_b))
        if s_a is not None:
            ax1.plot(s_a[0] - vp, s_a[1], ".", ms=1, color=colores[canon],
                     label=canon)
        if s_b is not None:
            ax1.plot(s_b[0] - vn, s_b[1], ".", ms=1, color=colores[canon])
    for canon, tracks in VENT.items():
        s_a = _recorta(f_prev, t0_prev, tracks, vp - MARGEN_H, vp)
        s_b = _recorta(f_next, t0_next, tracks, vn, vn + MARGEN_H)
        med[canon] = (_mediana(s_a), _mediana(s_b))
        if s_a is not None:
            ax2.plot(s_a[0] - vp, s_a[1], ".", ms=1, label=f"{canon} (antes)")
        if s_b is not None:
            ax2.plot(s_b[0] - vn, s_b[1], ".", ms=1, label=f"{canon} (despues)")
    for ax in (ax1, ax2):
        ax.axvline(0.0, color="k", ls="--", lw=1.4)
        ax.set_xlim(-MARGEN_H, MARGEN_H)
        ax.grid(alpha=0.25)
    ax1.set_ylabel("monitor")
    ax2.set_ylabel("ventilador")
    ax1.legend(loc="upper right", fontsize=7, ncol=3)
    ax2.legend(loc="upper right", fontsize=7, ncol=4)
    ax1.set_title(f"Pareja {codigo}: 6 h antes y 6 h despues del corte "
                  f"(eje en horas relativas; el corte no esta a escala)")
    ax2.set_xlabel("horas relativas al corte")
    if con_tabla:
        filas = []
        for k, (a, b) in med.items():
            if a is None and b is None:
                continue
            sa = "—" if a is None else f"{a:.2f}"
            sb = "—" if b is None else f"{b:.2f}"
            filas.append(f"{k:<7} {sa:>8} {sb:>8}")
        if filas:
            fig.text(0.01, 0.005,
                     "mediana en las 6 h        antes  despues\n" +
                     "\n".join(filas), fontsize=8, family="monospace")
    fig.tight_layout(rect=(0, 0.12, 1, 1))
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=110)
    plt.close(fig)
    return True


def _fin_de(e: dict) -> float | None:
    """Epoch del fin del ultimo tramo ventilado (t0 + vent_end)."""
    att = e.get("attempts") or []
    if not att:
        return None
    return (float(e["t0_unix"])
            + float(att[-1].get("vent_end_h") or 0.0) * 3600.0)


def _eid_por_tiempo(idx_por_id: dict, par: dict) -> tuple[str, str]:
    """Localiza los event_id de la pareja por sus TIEMPOS estables (±15 min).

    Los `event_id` son posicionales y cambian entre versiones del indice; los
    tiempos (fin del primer tramo / inicio del segundo) no. Si alguno no casa
    devuelve "" y la pareja se reporta como no casada.
    """
    tol = 15 * 60.0
    t_end = par.get("prev_end_epoch")
    t_ini = par.get("next_start_epoch")
    prev = None
    if t_end is not None:
        prev = next((e["event_id"] for e in idx_por_id.values()
                     if _fin_de(e) is not None
                     and abs(_fin_de(e) - float(t_end)) <= tol), None)
    nxt = None
    if t_ini is not None:
        nxt = next((e["event_id"] for e in idx_por_id.values()
                    if abs(float(e["t0_unix"]) - float(t_ini)) <= tol), None)
    return (prev or "", nxt or "")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cohort", required=True, choices=sorted(SPECS))
    p.add_argument("--index", default=None)
    p.add_argument("--n-control", type=int, default=N_CONTROLES)
    p.add_argument("--semilla", type=int, default=SEMILLA)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--clave", default=r"D:\data\iprove3_work\adjudicacion_clave.json",
                   help="ruta FUERA del repo con la clave codigo -> pareja")
    p.add_argument("--desde-cascada", default=None,
                   help="JSON de cascada_candidatas.py (poblacion oficial).")
    p.add_argument("--excluir-clave", default=None,
                   help="Clave de una tanda anterior: sus parejas no se repiten.")
    p.add_argument("--solo-lista", action="store_true",
                   help="Solo cuenta candidatas y controles (no pinta PNG).")
    p.add_argument("--max-pares", type=int, default=None)
    p.add_argument("--config", default=str(CONFIG))
    args = p.parse_args()

    index_path = (Path(args.index) if args.index else
                  sorted((ROOT / "datasets" / args.cohort).glob(
                      f"cases_v*/{args.cohort}_cases_index.json"))[-1])
    idx = json.loads(index_path.read_text(encoding="utf-8"))
    print(f"Indice: {index_path.relative_to(ROOT)} ({idx['total_events']} eventos)")

    config = load_config(args.config)
    boxes = scan_source_files(
        config_path(config, "paths", f"{args.cohort}_raw_dir"), SPECS[args.cohort])
    meta_por_box = {b: {sf.path.name: sf for sf in fs} for b, fs in boxes.items()}

    idx_por_id = {e["event_id"]: e for e in idx["events"]}
    if args.desde_cascada:
        par = _desde_cascada(Path(args.desde_cascada), idx_por_id)
    else:
        par = _parejas(json.loads((REPORT / "apagado_simultaneo.json")
                                  .read_text(encoding="utf-8"))
                      .get(args.cohort) or [], idx_por_id)
    # Segunda tanda: las parejas ya adjudicadas (misma box + tiempos) no repiten.
    if args.excluir_clave and Path(args.excluir_clave).exists():
        prev = json.loads(Path(args.excluir_clave).read_text(encoding="utf-8"))
        usadas = {(v.get("box"), v.get("prev_end_epoch"), v.get("next_start_epoch"))
                  for v in (prev.get("clave") or {}).values()}
        par["candidatas"] = [c for c in par["candidatas"]
                             if (c.get("box"), c.get("prev_end_epoch"),
                                 c.get("next_start_epoch")) not in usadas]
        par["controles"] = [c for c in par["controles"]
                            if (c.get("box"), c.get("prev_end_epoch"),
                                c.get("next_start_epoch")) not in usadas]
        print(f"Excluidas {len(usadas)} parejas de la tanda anterior")
    print(f"- Candidatas variante d: {len(par['candidatas'])}")
    print(f"- Controles disponibles (hueco > {GAP_CONTROL_H:.0f} h, con TV y FR): "
          f"{len(par['controles'])}")
    if par["candidatas"]:
        gaps = sorted(c["gap_h"] for c in par["candidatas"])
        print(f"  hueco de las candidatas: p10 {gaps[int(0.1 * (len(gaps) - 1))]:.2f} "
              f"· p50 {gaps[len(gaps) // 2]:.2f} · "
              f"p90 {gaps[int(0.9 * (len(gaps) - 1))]:.2f} h")
    if args.solo_lista:
        return

    rnd = random.Random(args.semilla)
    ctrls = par["controles"][:]
    rnd.shuffle(ctrls)
    seleccion = par["candidatas"] + ctrls[: args.n_control]
    rnd.shuffle(seleccion)
    if args.max_pares:
        seleccion = seleccion[: args.max_pares]
    claves: dict[str, dict] = {}
    sin_casar: list[dict] = []
    out_dir = Path(args.out_dir) if args.out_dir else REPORT / "adjudicacion" / args.cohort
    for n, par_ in enumerate(seleccion, start=1):
        codigo = f"P{n:02d}"
        eid_prev, eid_next = _eid_por_tiempo(idx_por_id, par_)
        if not eid_prev or not eid_next:
            sin_casar.append({**par_, "codigo": codigo,
                              "prev_casado": eid_prev, "next_casado": eid_next})
            print(f"  {codigo}: NO CASA por tiempos (se reporta, no se pinta)",
                  flush=True)
            continue
        meta = meta_por_box.get(par_["box"], {})
        _pintar({**par_, "prev": eid_prev, "next": eid_next},
                meta, idx_por_id, out_dir / f"{codigo}.png", codigo)
        claves[codigo] = {
            "tipo": par_["tipo"],
            # Identificadores ESTABLES (nunca event_id posicional)
            "box": par_["box"],
            "prev_end_epoch": par_.get("prev_end_epoch"),
            "next_start_epoch": par_.get("next_start_epoch"),
            "prev_file": par_.get("prev_file"),
            "prev_file_sha256": par_.get("prev_file_sha256"),
            "next_file": par_.get("next_file"),
            "next_file_sha256": par_.get("next_file_sha256"),
            "gap_h": par_.get("gap_h"),
            "desfase_vent_monitor_h": par_.get("desfase_vent_monitor_h"),
            **(par_.get("deltas") or {}),
        }
        print(f"  {codigo}: PNG listo", flush=True)

    # Clave FUERA del repo + hash para fijarla.
    clave_path = Path(args.clave)
    clave_path.parent.mkdir(parents=True, exist_ok=True)
    contenido = json.dumps({"cohort": args.cohort, "semilla": args.semilla,
                            "clave": claves}, ensure_ascii=False, indent=2)
    clave_path.write_text(contenido, encoding="utf-8")
    sha = hashlib.sha256(contenido.encode("utf-8")).hexdigest()

    hoja = out_dir / "hoja.csv"
    with hoja.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["codigo", "decision", "comentario"])
        for codigo in sorted(claves):
            w.writerow([codigo, "", ""])

    n_cand = sum(1 for k in claves.values() if k["tipo"] == "candidata")
    n_ctrl = len(claves) - n_cand
    (out_dir / "LEEME.md").write_text(
        f"""# Adjudicacion visual ciega — {args.cohort} ({idx['total_events']} eventos)

Tienes **{len(claves)} parejas** ({n_cand} candidatas + {n_ctrl} controles) en
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
""", encoding="utf-8")

    informe = [f"# Paquete de adjudicacion — {args.cohort}", "",
               f"- Indice: `{index_path.relative_to(ROOT)}`",
               f"- Poblacion: cascada de candidatas (variante d), SIN exigir "
               f"apagado simultaneo (el desfase se informa por pareja)",
               f"- Candidatas variante d: **{n_cand}** "
               f"(de {len(par['candidatas'])} encontradas)",
               f"- Controles incluidos: **{n_ctrl}** "
               f"(de {len(par['controles'])} disponibles)",
               f"- Semilla: `{args.semilla}` · codigos `P01`…`P{n:02d}`",
               f"- Clave (FUERA del repo): `{clave_path}`",
               f"- SHA-256 de la clave: `{sha}`", "",
               "Identificadores ESTABLES por pareja (nunca `event_id` "
               "posicional): box + epoch del fin del primer tramo + epoch del "
               "inicio del segundo + hash del NOMBRE del fichero de origen de "
               "cada lado. Al aplicar la adjudicacion se empareja por esos "
               "tiempos (±15 min).", ""]
    if sin_casar:
        informe += [f"**{len(sin_casar)} parejas NO casaron por tiempos** y no se "
                    "pintaron (se listan abajo):", ""]
        for s in sin_casar:
            informe.append(f"- {s['codigo']}: {s['box']} "
                           f"prev_end={s.get('prev_end_epoch')} "
                           f"next_start={s.get('next_start_epoch')}")
        informe.append("")
    if par.get("cascada"):
        informe += [f"Cascada: {par['cascada']}", ""]
    (out_dir / "informe_adjudicacion.md").write_text("\n".join(informe),
                                                    encoding="utf-8")
    print(f"\nPNG: {out_dir}")
    print(f"Hoja: {hoja}")
    print(f"Clave (fuera del repo): {clave_path}")
    print(f"SHA-256 clave: {sha}")
    print("\nPARA AQUI: adjudica la hoja y vuelve a pasarmela.")


if __name__ == "__main__":
    main()
