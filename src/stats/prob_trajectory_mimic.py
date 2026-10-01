"""
Trayectoria de probabilidad predicha por SVM RBF (clinic) en pacientes MIMIC-III.
Adaptacion de _prob_trajectory.py para datos MIMIC.

Salida: results/mimic_evaluation/prob_trajectory_mixtos_mimic.png
"""
import json
import pathlib
import collections
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import joblib

BASE = pathlib.Path("datasets/mimic3wdb/windows_10min")
with open("datasets/mimic3wdb/windows_index.json") as f:
    idx = json.load(f)

pipe = joblib.load("results/models/svm_rbf_extubation_8h.joblib")

# Features del modelo clinic -> columnas MIMIC
FEAT_MAP = {
    "PEEP":    "PEEP",
    "PIP":     "PIP",
    "CO2":     None,   # no disponible -> NaN
    "ETCO2":   None,   # no disponible -> NaN
    "TV_INSP": "TV",
    "MV_EXP":  "MV",
    "RR":      "RR_V",
}
FEAT_ORDER = ["PEEP", "PIP", "CO2", "ETCO2", "TV_INSP", "MV_EXP", "RR", "SAFI"]


def win_features(df):
    f = {}
    for k, col in FEAT_MAP.items():
        if col is None or col not in df.columns:
            f[k] = np.nan
        else:
            s = df[col].dropna()
            f[k] = float(s.mean()) if len(s) > 0 else np.nan

    # SAFI
    fio2_s = df["FiO2"].dropna() if "FiO2" in df.columns else pd.Series([], dtype=float)
    spo2_s = df["SpO2"].dropna() if "SpO2" in df.columns else pd.Series([], dtype=float)
    if len(fio2_s) > 0 and len(spo2_s) > 0:
        fm = float(fio2_s.mean())
        sm = float(spo2_s.mean())
        safi = sm / (fm / 100.0) if fm > 0 else np.nan
        safi = min(max(safi, 0), 1000)
    else:
        safi = np.nan
    f["SAFI"] = safi
    return [f[k] for k in FEAT_ORDER]


# Agrupar por subject_id
by_patient = collections.defaultdict(list)
for w in idx["windows"]:
    by_patient[w["subject_id"]].append(w)

# Construir trayectorias
trajs = {}
for sid, ws in by_patient.items():
    ws_sorted = sorted(ws, key=lambda w: w["window_start"])
    rows, labels = [], []
    for w in ws_sorted:
        p = BASE / w["window_file"]
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        rows.append(win_features(df))
        # En MIMIC index: label=1=intubado, 0=extubado
        # Para consistencia con clinic (modelo predice 1=extubado):
        labels.append(1 - int(w["label"]))
    if not rows:
        continue
    X = np.array(rows, float)
    prob = pipe.predict_proba(X)[:, 1]  # P(extubado < 8h)
    trajs[sid] = dict(prob=prob, label=np.array(labels), n=len(prob))

# Pacientes mixtos (tienen ambas fases: label=1 lejos, label=0 cerca)
mixtos = {sid: t for sid, t in trajs.items() if (t["label"] == 0).any() and (t["label"] == 1).any()}
print(f"Pacientes totales: {len(trajs)}   Mixtos (transicion etiquetada): {len(mixtos)}")
print()

# Resumen numerico
if mixtos:
    print("MIXTOS — prob. media del modelo en cada fase del MISMO paciente:")
    print(f"{'paciente':<12}{'n':>5}{'prob_lejos':>12}{'prob_cerca':>12}{'sube?':>7}")
    subio = 0
    for sid, t in mixtos.items():
        p_far = t["prob"][t["label"] == 0].mean()   # fase intubado-lejos (label=0)
        p_near = t["prob"][t["label"] == 1].mean()  # fase extubable-cerca (label=1)
        up = "  SI" if p_near > p_far else "  no"
        if p_near > p_far:
            subio += 1
        print(f"{sid:<12}{t['n']:>5}{p_far:>12.3f}{p_near:>12.3f}{up:>7}")
    print(f"\n  -> En {subio}/{len(mixtos)} pacientes mixtos la prob. SUBE de fase-lejos a fase-cerca")
else:
    print("No hay pacientes mixtos en MIMIC (demasiado desbalanceado).")

# Plot
n = len(mixtos)
if n == 0:
    print("No se genera figura — no hay pacientes mixtos.")
else:
    ncol = 3
    nrow = int(np.ceil(n / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(15, 3.2 * nrow), squeeze=False)
    for ax, (sid, t) in zip(axes.ravel(), mixtos.items()):
        x = np.arange(t["n"])
        ax.plot(x, t["prob"], color="#1f77b4", lw=1.8)
        # sombrear fase cerca (label=1 = extubable en codificacion clinic)
        near = t["label"] == 1
        if near.any():
            first1 = np.argmax(near)
            ax.axvspan(first1 - 0.5, t["n"] - 0.5, color="#2ca02c", alpha=0.12)
            ax.axvline(first1 - 0.5, color="#2ca02c", ls="--", lw=1)
        ax.axhline(0.5, color="grey", ls=":", lw=0.8)
        ax.axhline(0.0, color="red", ls=":", lw=0.5, alpha=0.5)
        ax.set_ylim(-0.02, 1.02)
        ax.set_title(f"sid={sid}", fontsize=8)
        ax.set_xlabel("ventana (->tiempo)", fontsize=7)
        ax.set_ylabel("P(extubable<8h)", fontsize=7)
        ax.tick_params(labelsize=7)
    # apagar ejes sobrantes
    for ax in axes.ravel()[n:]:
        ax.axis("off")
    fig.suptitle("MIMIC-III — Probabilidad predicha (SVM clinic, SIN threshold)\n"
                 "Zona verde = ventanas etiquetadas extubable (<8h del fin de ventilacion)",
                 fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    out = "results/mimic_evaluation/prob_trajectory_mixtos_mimic.png"
    out_path = pathlib.Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120)
    print(f"\nFigura guardada: {out}")
