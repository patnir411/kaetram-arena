#!/usr/bin/env python3
"""
model.py — Kaetram World Model: Small Transformer Forward Dynamics Predictor.

Predicts state deltas, rewards (XP gain), and terminal signals (death)
from (state, action) input pairs.

Architecture: ~25M params, runs comfortably on RTX 3060 12GB.
"""

import math
import torch
import torch.nn as nn
from world.schema import STATE_DIM, ACTION_DIM


class PositionalEncoding(nn.Module):
    """Sinusoidal positional encoding for the sequence dimension."""

    def __init__(self, d_model: int, max_len: int = 128):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer("pe", pe.unsqueeze(0))  # (1, max_len, d_model)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.pe[:, :x.size(1)]


class KaetramWorldModel(nn.Module):
    """
    Small Transformer that predicts:
        (state_t, action_t) → (delta_state, reward, terminal)

    Input:  state_vec (STATE_DIM) + action_vec (ACTION_DIM) = 90 floats
    Output: predicted delta (STATE_DIM), reward (1), terminal prob (1)
    """

    def __init__(
        self,
        d_model: int = 256,
        n_layers: int = 4,
        n_heads: int = 4,
        d_ff: int = 512,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.d_model = d_model
        input_dim = STATE_DIM + ACTION_DIM

        # Positional encoding (treat flattened input as a 1-token sequence,
        # but we chunk it into patches for richer attention patterns)
        self.patch_size = 16  # split input into patches of 16 floats
        n_patches = math.ceil(input_dim / self.patch_size)
        self.patch_proj = nn.Linear(self.patch_size, d_model)
        self.pos_enc = PositionalEncoding(d_model, max_len=n_patches + 1)

        # CLS token for aggregation
        self.cls_token = nn.Parameter(torch.randn(1, 1, d_model) * 0.02)

        # Transformer encoder
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=d_ff,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

        # Output heads
        self.delta_head = nn.Sequential(
            nn.Linear(d_model, d_model),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(d_model, STATE_DIM),
        )

        self.reward_head = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.GELU(),
            nn.Linear(d_model // 2, 1),
        )

        self.terminal_head = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.GELU(),
            nn.Linear(d_model // 2, 1),
        )

        self._init_weights()

    def _init_weights(self):
        """Xavier uniform initialization for all linear layers."""
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    def _patchify(self, x: torch.Tensor) -> torch.Tensor:
        """Split flat input into patches and project to d_model.

        Args:
            x: (batch, input_dim) flat state+action vector
        Returns:
            (batch, n_patches, d_model)
        """
        B, D = x.shape
        # Pad to multiple of patch_size
        pad = (self.patch_size - D % self.patch_size) % self.patch_size
        if pad > 0:
            x = torch.nn.functional.pad(x, (0, pad))
        # Reshape to patches
        x = x.view(B, -1, self.patch_size)  # (B, n_patches, patch_size)
        return self.patch_proj(x)  # (B, n_patches, d_model)

    def forward(
        self,
        state: torch.Tensor,
        action: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Forward pass.

        Args:
            state:  (batch, STATE_DIM) normalized state vector
            action: (batch, ACTION_DIM) one-hot action + args

        Returns:
            delta:    (batch, STATE_DIM) predicted state change
            reward:   (batch, 1) predicted XP reward (normalized)
            terminal: (batch, 1) death probability (sigmoid applied)
        """
        # Concatenate state and action
        x = torch.cat([state, action], dim=-1)  # (B, STATE_DIM + ACTION_DIM)

        # Patchify and add positional encoding
        patches = self._patchify(x)  # (B, n_patches, d_model)

        # Prepend CLS token
        B = patches.size(0)
        cls = self.cls_token.expand(B, -1, -1)  # (B, 1, d_model)
        tokens = torch.cat([cls, patches], dim=1)  # (B, 1+n_patches, d_model)
        tokens = self.pos_enc(tokens)

        # Transformer encode
        encoded = self.encoder(tokens)  # (B, 1+n_patches, d_model)

        # Use CLS token as aggregate representation
        cls_out = encoded[:, 0]  # (B, d_model)

        # Predict outputs
        delta = self.delta_head(cls_out)  # (B, STATE_DIM)
        reward = self.reward_head(cls_out)  # (B, 1)
        terminal = torch.sigmoid(self.terminal_head(cls_out))  # (B, 1)

        return delta, reward, terminal

    def predict(self, state: torch.Tensor, action: torch.Tensor) -> dict:
        """Convenience method for inference. Returns dict with numpy arrays."""
        self.eval()
        with torch.no_grad():
            delta, reward, terminal = self.forward(state, action)
        return {
            "delta": delta.cpu().numpy(),
            "reward": reward.cpu().numpy(),
            "terminal": terminal.cpu().numpy(),
        }

    @property
    def num_params(self) -> int:
        return sum(p.numel() for p in self.parameters())


def count_params():
    """Quick check of model size."""
    model = KaetramWorldModel()
    total = model.num_params
    print(f"KaetramWorldModel: {total:,} parameters ({total * 4 / 1e6:.1f} MB fp32)")
    return total


if __name__ == "__main__":
    count_params()
