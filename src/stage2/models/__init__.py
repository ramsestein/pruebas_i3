"""
Modelos del Escalón 2:
- WindowEncoder: MLP compartido que codifica cada ventana
- TimeDeltaLSTM: LSTM con Δt concatenado en cada paso
- LogNormalHead: cabeza distribucional → (mu, log_sigma)
- ExtubationModel: modelo completo que integra las 3 piezas
"""

import torch
import torch.nn as nn

from src.stage2.config import (
    N_INPUT_DIM, WINDOW_MLP_HIDDEN,
    LSTM_HIDDEN, LSTM_NUM_LAYERS, DROPOUT,
    MU_CLAMP, LOG_SIGMA_MIN, LOG_SIGMA_MAX,
)


class WindowEncoder(nn.Module):
    """
    MLP compartido que codifica una ventana (features + missingness)
    en una representación densa.

    Input:  (batch, N_INPUT_DIM)  → [16 para 8 features + 8 indicadores]
    Output: (batch, WINDOW_MLP_HIDDEN[-1])
    """

    def __init__(self):
        super().__init__()
        layers = []
        prev_dim = N_INPUT_DIM
        for h in WINDOW_MLP_HIDDEN:
            layers.append(nn.Linear(prev_dim, h))
            layers.append(nn.ReLU())
            prev_dim = h
        self.mlp = nn.Sequential(*layers)
        self.output_dim = WINDOW_MLP_HIDDEN[-1]

    def forward(self, x):
        """
        Args:
            x: (batch, seq_len, N_INPUT_DIM)
        Returns:
            (batch, seq_len, output_dim)
        """
        batch, seq_len, _ = x.shape
        x_flat = x.reshape(batch * seq_len, -1)
        encoded = self.mlp(x_flat)
        return encoded.reshape(batch, seq_len, -1)


class TimeDeltaLSTM(nn.Module):
    """
    LSTM que recibe la secuencia de ventanas codificadas + Δt en cada paso.

    En cada paso t:
      input_t = concat(encoded_window_t, delta_t)
    donde delta_t[0] se pone a 0 para la primera ventana.
    """

    def __init__(self):
        super().__init__()
        input_dim = WINDOW_MLP_HIDDEN[-1] + 1  # +1 para Δt
        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=LSTM_HIDDEN,
            num_layers=LSTM_NUM_LAYERS,
            batch_first=True,
            dropout=DROPOUT if LSTM_NUM_LAYERS > 1 else 0.0,
        )

    def forward(self, window_encoded, delta_t):
        """
        Args:
            window_encoded: (batch, seq_len, window_emb_dim)
            delta_t:        (batch, seq_len - 1)
        Returns:
            final_hidden: (batch, LSTM_HIDDEN)
        """
        batch, seq_len, _ = window_encoded.shape

        # Construir delta_t expandido: padding de 0 al inicio
        dt_padded = torch.zeros(batch, seq_len, 1, device=delta_t.device)
        dt_padded[:, 1:, 0] = delta_t  # primer Δt = 0

        # Concatenar en cada paso
        lstm_input = torch.cat([window_encoded, dt_padded], dim=-1)

        _, (h_n, _) = self.lstm(lstm_input)
        final_hidden = h_n[-1]  # última capa, último paso
        return final_hidden


class LogNormalHead(nn.Module):
    """
    Cabeza distribucional: [LSTM hidden | landmark_t_norm] → (mu, log_sigma).

    landmark_t se espera normalizado: log(t+1) para evitar scale mismatch
    con los hidden states de la LSTM (~[-1, 1]).
    """

    def __init__(self):
        super().__init__()
        self.fc = nn.Linear(LSTM_HIDDEN + 1, 2)  # +1 para landmark_t

    def forward(self, hidden, landmark_t_norm):
        """
        Args:
            hidden:           (batch, LSTM_HIDDEN)
            landmark_t_norm:  (batch,) — normalizado (ej. log(t+1))
        Returns:
            mu:        (batch,)
            log_sigma: (batch,)
        """
        combined = torch.cat([hidden, landmark_t_norm.unsqueeze(-1)], dim=-1)
        out = self.fc(combined)
        mu = torch.tanh(out[:, 0]) * MU_CLAMP
        log_sigma = torch.clamp(out[:, 1], LOG_SIGMA_MIN, LOG_SIGMA_MAX)
        return mu, log_sigma


class ExtubationModel(nn.Module):
    """
    Modelo completo del Escalón 2:
      WindowEncoder → TimeDeltaLSTM → LogNormalHead(landmark_t)
    """

    def __init__(self):
        super().__init__()
        self.encoder = WindowEncoder()
        self.lstm = TimeDeltaLSTM()
        self.head = LogNormalHead()

    def forward(self, x, delta_t, landmark_t):
        """
        Args:
            x:          (batch, SEQ_LEN, N_INPUT_DIM) — features + missingness
            delta_t:    (batch, SEQ_LEN - 1)           — Δt entre ventanas
            landmark_t: (batch,)                       — tiempo absoluto (horas)
        Returns:
            mu, log_sigma: (batch,)
        """
        encoded = self.encoder(x)
        hidden = self.lstm(encoded, delta_t)
        return self.head(hidden, landmark_t)
