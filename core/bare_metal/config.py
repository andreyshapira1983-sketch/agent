"""Конфигурация модели — все гиперпараметры в одном месте.

Дефолты оптимизированы для CPU: ~16 M params, ~65 MB FP32.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass


@dataclass
class BrainConfig:
    vocab_size: int = 16384
    dim: int = 384
    n_layers: int = 8
    n_heads: int = 8
    n_kv_heads: int = 4       # GQA: 2× меньше KV голов
    max_seq_len: int = 2048
    ffn_multiplier: float = 2.667   # SwiGLU hidden = 2/3 * mult * dim
    norm_eps: float = 1e-5
    rope_theta: float = 10000.0
    dropout: float = 0.0

    # ── derived ───────────────────────────────────────────────────────────

    @property
    def head_dim(self) -> int:
        return self.dim // self.n_heads

    @property
    def ffn_hidden(self) -> int:
        raw = int(2 * self.dim * self.ffn_multiplier / 3)
        return 64 * ((raw + 63) // 64)          # align to 64

    # ── persistence ───────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: str) -> BrainConfig:
        with open(path, encoding='utf-8') as f:
            return cls(**json.load(f))
