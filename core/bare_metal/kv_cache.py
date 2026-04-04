"""KV-cache для авторегрессивного инференса.

Хранит кеш Key/Value тензоров для каждого слоя,
чтобы при генерации нового токена не перевычислять предыдущие.
"""

from __future__ import annotations

import torch


class KVCache:
    __slots__ = ('k', 'v')

    def __init__(self, batch: int, max_len: int, n_kv_heads: int, head_dim: int):
        self.k = torch.zeros(batch, max_len, n_kv_heads, head_dim)
        self.v = torch.zeros(batch, max_len, n_kv_heads, head_dim)

    def update(
        self,
        new_k: torch.Tensor,
        new_v: torch.Tensor,
        start_pos: int,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        seq = new_k.shape[1]
        self.k[:, start_pos:start_pos + seq] = new_k
        self.v[:, start_pos:start_pos + seq] = new_v
        return self.k[:, :start_pos + seq], self.v[:, :start_pos + seq]

    def reset(self) -> None:
        self.k.zero_()
        self.v.zero_()
