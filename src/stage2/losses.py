"""
Pérdida log-verosimilitud negativa (NLL) para la distribución log-normal
+ término de coherencia temporal (suavidad sobre el residuo).
"""

import numpy as np
import torch
import torch.nn as nn

from src.stage2.config import T_EPS


class LogNormalNLL(nn.Module):
    """
    Negative Log-Likelihood de LogNormal(mu, sigma).

    Target ya viene en log-space: y = log(time_remaining).
    Si log(T) ~ Normal(mu, sigma²), entonces:
    NLL = log(sigma) + 0.5 * ((y - mu) / sigma)² + 0.5 * log(2π)
    """

    def __init__(self, eps: float = T_EPS):
        super().__init__()
        self.eps = eps
        self.log_2pi = np.log(2 * np.pi)

    def forward(self, mu, log_sigma, y_true):
        """
        Args:
            mu:         (batch,) — mu predicha (en log-space)
            log_sigma:  (batch,) — log(sigma) predicha
            y_true:     (batch,) — log(time_remaining) real
        Returns:
            loss escalar
        """
        sigma = torch.exp(log_sigma) + self.eps
        diff = y_true - mu
        nll = log_sigma + 0.5 * (diff / sigma).pow(2) + 0.5 * self.log_2pi
        return nll.mean()


class CoherenceLoss(nn.Module):
    """
    Penalización de suavidad sobre el RESIDUO predicho entre landmarks
    consecutivos del MISMO paciente.

    AGNÓSTICO A LA DIRECCIÓN: penaliza saltos erráticos, no dicta signo.
    Permite repuntes clínicos, solo pide que sean suaves en vez de ruido.

    L_coh = mean( (ŷ(t2) - ŷ(t1))² / (Δt + 1) )

    donde ŷ es mu (la media predicha del residuo en log-space).
    El +1 evita división por cero y hace que Δt grandes atenúen la penalización.

    Implementación pura PyTorch (preserva gradientes).
    """

    def __init__(self, eps_dt: float = 1.0):
        super().__init__()
        self.eps_dt = eps_dt

    def forward(self, mu_pred, landmark_t, patient_ids):
        """
        Args:
            mu_pred:     (batch,) — residuo predicho en log-space (con gradiente)
            landmark_t:  (batch,) — tiempo del landmark (horas)
            patient_ids: list[str] — IDs de paciente por elemento del batch

        Returns:
            coh_loss escalar (diferenciable), o 0 si no hay pares consecutivos
        """
        if len(mu_pred) < 2:
            return torch.tensor(0.0, device=mu_pred.device)

        device = mu_pred.device
        n = len(mu_pred)

        # Matriz de comparación: pair_mask[i,j] = True si i,j son del mismo paciente
        # y j es el siguiente landmark después de i
        pids_arr = np.array(patient_ids)
        t_np = landmark_t.detach().cpu().numpy() if isinstance(landmark_t, torch.Tensor) else np.array(landmark_t)

        # Construir índices de pares consecutivos por paciente
        i_indices, j_indices = [], []
        unique_pids = np.unique(pids_arr)
        for pid in unique_pids:
            mask = pids_arr == pid
            if mask.sum() < 2:
                continue
            idx = np.where(mask)[0]
            sort_idx = idx[np.argsort(t_np[idx])]
            for k in range(len(sort_idx) - 1):
                i_indices.append(sort_idx[k])
                j_indices.append(sort_idx[k + 1])

        if not i_indices:
            return torch.tensor(0.0, device=device)

        i_t = torch.tensor(i_indices, device=device)
        j_t = torch.tensor(j_indices, device=device)

        # Δμ = μ_pred[j] - μ_pred[i]  (con gradiente)
        dmu = mu_pred[j_t] - mu_pred[i_t]

        # Δt (sin gradiente, es dato)
        dt = landmark_t[j_t] - landmark_t[i_t]

        pair_losses = (dmu ** 2) / (dt.abs() + self.eps_dt)
        return pair_losses.mean()


class CompositeLoss(nn.Module):
    """
    Pérdida compuesta: NLL + λ * coherencia temporal.

    Registra ambas componentes por separado para diagnóstico.
    """

    def __init__(self, lambda_coh: float = 0.05):
        super().__init__()
        self.nll = LogNormalNLL()
        self.coh = CoherenceLoss()
        self.lambda_coh = lambda_coh
        # Para logging
        self.last_nll = 0.0
        self.last_coh = 0.0

    def forward(self, mu, log_sigma, y_true, landmark_t, patient_ids):
        nll_loss = self.nll(mu, log_sigma, y_true)
        coh_loss = self.coh(mu, landmark_t, patient_ids)
        self.last_nll = nll_loss.item()
        self.last_coh = coh_loss.item()
        return nll_loss + self.lambda_coh * coh_loss
