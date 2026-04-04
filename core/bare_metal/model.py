"""Transformer целиком на чистом PyTorch — без HuggingFace, без внешних LLM.

Архитектура (LLaMA-like):
    - RMSNorm (pre-norm)
    - Rotary Positional Embedding (RoPE)
    - Grouped Query Attention (GQA)
    - SwiGLU FFN
    - Tied embedding ↔ output head
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from .config import BrainConfig
from .kv_cache import KVCache


# ═══════════════════════════════════════════════════════════════════════════════
# Нормализация
# ═══════════════════════════════════════════════════════════════════════════════

class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-5):
        super().__init__()
        self.weight = nn.Parameter(torch.ones(dim))
        self.eps = eps

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        rms = x.float().pow(2).mean(-1, keepdim=True).add(self.eps).rsqrt()
        return (x.float() * rms).type_as(x) * self.weight


# ═══════════════════════════════════════════════════════════════════════════════
# Rotary Positional Embedding
# ═══════════════════════════════════════════════════════════════════════════════

def precompute_rope(dim: int, max_len: int, theta: float = 10000.0) -> torch.Tensor:
    """(max_len, dim/2) complex-valued тензор частот RoPE."""
    freqs = 1.0 / (theta ** (torch.arange(0, dim, 2).float() / dim))
    t = torch.arange(max_len, dtype=torch.float32)
    angles = torch.outer(t, freqs)
    return torch.polar(torch.ones_like(angles), angles)


def apply_rotary(
    xq: torch.Tensor,
    xk: torch.Tensor,
    freqs: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Применяет RoPE к query и key тензорам.

    Args:
        xq: (B, L, n_heads, head_dim)
        xk: (B, L, n_kv_heads, head_dim)
        freqs: (L, head_dim/2) complex
    """
    # Reshape last dim → pairs → complex
    xq_c = torch.view_as_complex(xq.float().reshape(*xq.shape[:-1], -1, 2))
    xk_c = torch.view_as_complex(xk.float().reshape(*xk.shape[:-1], -1, 2))
    f = freqs.unsqueeze(0).unsqueeze(2)           # (1, L, 1, dim/2)
    xq_out = torch.view_as_real(xq_c * f).flatten(3)
    xk_out = torch.view_as_real(xk_c * f).flatten(3)
    return xq_out.type_as(xq), xk_out.type_as(xk)


# ═══════════════════════════════════════════════════════════════════════════════
# Multi-Head Attention с GQA и поддержкой KV-cache
# ═══════════════════════════════════════════════════════════════════════════════

class Attention(nn.Module):
    def __init__(self, cfg: BrainConfig):
        super().__init__()
        self.n_heads = cfg.n_heads
        self.n_kv = cfg.n_kv_heads
        self.hd = cfg.head_dim
        self.rep = self.n_heads // self.n_kv      # GQA repeat factor

        self.wq = nn.Linear(cfg.dim, cfg.n_heads * self.hd, bias=False)
        self.wk = nn.Linear(cfg.dim, cfg.n_kv_heads * self.hd, bias=False)
        self.wv = nn.Linear(cfg.dim, cfg.n_kv_heads * self.hd, bias=False)
        self.wo = nn.Linear(cfg.n_heads * self.hd, cfg.dim, bias=False)

    def forward(
        self,
        x: torch.Tensor,
        freqs: torch.Tensor,
        mask: torch.Tensor | None = None,
        cache: KVCache | None = None,
        start_pos: int = 0,
    ) -> torch.Tensor:
        B, L, _ = x.shape
        q = self.wq(x).view(B, L, self.n_heads, self.hd)
        k = self.wk(x).view(B, L, self.n_kv, self.hd)
        v = self.wv(x).view(B, L, self.n_kv, self.hd)

        q, k = apply_rotary(q, k, freqs[start_pos:start_pos + L])

        if cache is not None:
            k, v = cache.update(k, v, start_pos)

        # GQA → повторяем KV головы
        if self.rep > 1:
            k = k.repeat_interleave(self.rep, dim=2)
            v = v.repeat_interleave(self.rep, dim=2)

        # (B, heads, L, hd)
        q, k, v = (t.transpose(1, 2) for t in (q, k, v))

        scores = torch.matmul(q, k.transpose(-2, -1)) / math.sqrt(self.hd)
        if mask is not None:
            scores = scores + mask
        attn = F.softmax(scores.float(), dim=-1).type_as(q)

        out = torch.matmul(attn, v)
        out = out.transpose(1, 2).contiguous().view(B, L, -1)
        return self.wo(out)


# ═══════════════════════════════════════════════════════════════════════════════
# SwiGLU Feed-Forward
# ═══════════════════════════════════════════════════════════════════════════════

class FeedForward(nn.Module):
    def __init__(self, cfg: BrainConfig):
        super().__init__()
        h = cfg.ffn_hidden
        self.gate = nn.Linear(cfg.dim, h, bias=False)
        self.up = nn.Linear(cfg.dim, h, bias=False)
        self.down = nn.Linear(h, cfg.dim, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.down(F.silu(self.gate(x)) * self.up(x))


# ═══════════════════════════════════════════════════════════════════════════════
# Transformer Block
# ═══════════════════════════════════════════════════════════════════════════════

class Block(nn.Module):
    def __init__(self, cfg: BrainConfig):
        super().__init__()
        self.attn = Attention(cfg)
        self.ffn = FeedForward(cfg)
        self.norm1 = RMSNorm(cfg.dim, cfg.norm_eps)
        self.norm2 = RMSNorm(cfg.dim, cfg.norm_eps)

    def forward(
        self,
        x: torch.Tensor,
        freqs: torch.Tensor,
        mask: torch.Tensor | None = None,
        cache: KVCache | None = None,
        start_pos: int = 0,
    ) -> torch.Tensor:
        x = x + self.attn(self.norm1(x), freqs, mask, cache, start_pos)
        x = x + self.ffn(self.norm2(x))
        return x


# ═══════════════════════════════════════════════════════════════════════════════
# Полная модель
# ═══════════════════════════════════════════════════════════════════════════════

class BareMetalTransformer(nn.Module):
    """Авторегрессивный трансформер с нуля.

    ~16 M параметров при дефолтном BrainConfig (384-dim, 8 слоёв).
    Inference: ~1-3 tok/s на CPU.
    """

    def __init__(self, cfg: BrainConfig):
        super().__init__()
        self.cfg = cfg
        self.embed = nn.Embedding(cfg.vocab_size, cfg.dim)
        self.layers = nn.ModuleList([Block(cfg) for _ in range(cfg.n_layers)])
        self.norm = RMSNorm(cfg.dim, cfg.norm_eps)
        self.head = nn.Linear(cfg.dim, cfg.vocab_size, bias=False)

        # Tied weights: embed ↔ output head
        self.head.weight = self.embed.weight

        # Precompute RoPE
        self.register_buffer(
            'freqs',
            precompute_rope(cfg.head_dim, cfg.max_seq_len, cfg.rope_theta),
            persistent=False,
        )

        self._init_weights()

    # ── Init ──────────────────────────────────────────────────────────────

    def _init_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, std=0.02)
            elif isinstance(m, nn.Embedding):
                nn.init.normal_(m.weight, std=0.02)

    # ── Forward ───────────────────────────────────────────────────────────

    def forward(
        self,
        tokens: torch.Tensor,
        start_pos: int = 0,
        caches: list[KVCache] | None = None,
    ) -> torch.Tensor:
        """
        Args:
            tokens: (B, L) int64 — ID токенов
            start_pos: позиция в последовательности (для KV-cache)
            caches: список KVCache по одному на слой

        Returns:
            logits: (B, L, vocab_size)
        """
        _B, L = tokens.shape
        h = self.embed(tokens)

        # Каузальная маска
        mask = None
        if L > 1:
            mask = torch.full((L, L), float('-inf'), device=tokens.device)
            mask = torch.triu(mask, diagonal=1)
            if start_pos > 0:
                # Расширяем маску для кешированных позиций
                mask = torch.hstack([
                    torch.zeros(L, start_pos, device=tokens.device),
                    mask,
                ])
            mask = mask.unsqueeze(0).unsqueeze(0)   # (1, 1, L, total_len)

        for i, layer in enumerate(self.layers):
            c = caches[i] if caches else None
            h = layer(h, self.freqs, mask, c, start_pos)

        return self.head(self.norm(h))

    # ── Утилиты ───────────────────────────────────────────────────────────

    @torch.no_grad()
    def embed_text(self, tokens: torch.Tensor) -> torch.Tensor:
        """Средний вектор последнего скрытого слоя → embedding для памяти.

        Args:
            tokens: (B, L)
        Returns:
            (B, dim)
        """
        h = self.embed(tokens)
        for layer in self.layers:
            h = layer(h, self.freqs)
        return self.norm(h).mean(dim=1)

    def create_caches(self, batch: int = 1) -> list[KVCache]:
        return [
            KVCache(batch, self.cfg.max_seq_len, self.cfg.n_kv_heads, self.cfg.head_dim)
            for _ in range(self.cfg.n_layers)
        ]

    def count_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters())

    def size_mb(self) -> float:
        return sum(p.numel() * p.element_size() for p in self.parameters()) / (1024 * 1024)
