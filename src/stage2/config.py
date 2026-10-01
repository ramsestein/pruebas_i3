"""
Configuración centralizada para el Escalón 2:
Predicción dinámica del tiempo restante hasta extubación exitosa
con red LSTM + cabeza distribucional log-normal.
"""

import os

# ── Paths ──────────────────────────────────────────────────────────────
DATASET_VERSION = "v0.1.0_fbb5b280"
MIMIC_SURVIVAL_VERSION = "v0.1.0_e9d2ab7c"  # MIMIC survival está aquí

BASE_DIR = os.path.join("datasets", "harmonized", DATASET_VERSION)
NUMERICS_DIR = os.path.join(BASE_DIR, "numerics")
SURVIVAL_PATH = os.path.join(BASE_DIR, "survival_48h.parquet")
MIMIC_SURVIVAL_PATH = os.path.join(
    "datasets", "harmonized", MIMIC_SURVIVAL_VERSION, "survival_48h.parquet"
)

OUTPUT_DIR = "stage2_results"
TRAJECTORY_DIR = os.path.join(OUTPUT_DIR, "trajectories")
CHECKPOINT_DIR = os.path.join(OUTPUT_DIR, "checkpoints")

# ── Cohorte ────────────────────────────────────────────────────────────
# Solo eICU (train/val) y MIMIC (test externo). VitalDB y Clinic se ignoran.
TRAIN_COHORT = "eicu"
TEST_COHORT = "mimic"

# ── Features ───────────────────────────────────────────────────────────
# Features vitales básicas (se leen de los archivos parquet).
BASE_FEATURES = ["RR", "HR", "SpO2", "PEEP", "MAP", "FiO2", "TV", "PIP"]

# Índices derivados que se computan a nivel de ventana.
DERIVED_FEATURES = [
    "RSBI",            # Rapid Shallow Breathing Index = RR / TV
    "SF_ratio",        # SpO2 / FiO2 ratio (proxy de P/F)
    "compliance",      # TV / (PIP - PEEP)
    "driving_pressure",# PIP - PEEP
    "MAP_FiO2",        # MAP / FiO2
]

# Ya no hay obligatorias/secundarias fijas.
# Una ventana es válida si tiene >= MIN_FEATURES_PER_WINDOW features no-NaN.
ALL_FEATURES = BASE_FEATURES + DERIVED_FEATURES
MIN_FEATURES_PER_WINDOW = 5   # Mínimo de features presentes para validar ventana

N_FEATURES = len(ALL_FEATURES)                        # 13
N_INPUT_DIM = N_FEATURES * 2                           # 26 (valor + missingness)

# ── Ventanas y Landmarks ──────────────────────────────────────────────
WINDOW_SIZE_MINUTES = 15       # Duración de cada ventana
MIN_GAP_MINUTES = 5            # Separación mínima entre ventanas consecutivas
SEQ_LEN = 4                    # Número de ventanas en la secuencia de entrada
LANDMARK_STRIDE_MINUTES = 15   # Cada cuánto se muestrea un landmark

# LOCF (Last Observation Carried Forward) para variables esparsas como
# PEEP/PIP/FiO2. Si la última medición tiene más de MAX_AGE, se marca missing.
LOCF_MAX_AGE_HOURS = 4.0       # Antigüedad máxima para forward-fill

# ── Arquitectura ──────────────────────────────────────────────────────
WINDOW_MLP_HIDDEN = [64, 32]   # Capas ocultas del encoder de ventana
LSTM_HIDDEN = 186              # Dimensión oculta de la LSTM
LSTM_NUM_LAYERS = 3            # Capas de la LSTM
DROPOUT = 0.4                  # Dropout en LSTM (regularización fuerte)

# ── Entrenamiento ─────────────────────────────────────────────────────
BATCH_SIZE = 128
MAX_EPOCHS = 100
LEARNING_RATE = 1e-4
WEIGHT_DECAY = 1e-4            # Regularización L2 fuerte
LR_SCHEDULER_FACTOR = 0.5      # Factor de reducción en plateau
LR_SCHEDULER_PATIENCE = 3      # Reducir LR rápido ante plateau
LR_MIN = 1e-6                   # LR mínimo
EARLY_STOPPING_PATIENCE = 10   # Early stop agresivo
VAL_SPLIT = 0.15               # 15% de pacientes eICU para validación
RANDOM_SEED = 42

# ── Estabilización numérica ───────────────────────────────────────────
MU_CLAMP = 10.0                 # Clamp para |mu|
LOG_SIGMA_MIN = -2.0            # sigma mínimo ≈ 0.135
LOG_SIGMA_MAX = 3.0             # sigma máximo ≈ 20
T_EPS = 1e-6                    # Épsilon para log(t)
GRAD_CLIP_VALUE = 1.0           # Clip de valor de gradiente (además del clip de norma)
LANDMARK_T_LOG = True           # Normalizar landmark_t: usar log(t+1) en vez de t bruto
