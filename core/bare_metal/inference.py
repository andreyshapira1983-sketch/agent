"""Авторегрессивный движок генерации с KV-cache.

Sampling: temperature, top-k, top-p, repetition penalty.
Возвращает текст + embedding (для memory manager).
"""

from __future__ import annotations

import torch
import torch.nn.functional as F

from .config import BrainConfig
from .model import BareMetalTransformer
from .tokenizer import BPETokenizer


class InferenceEngine:
    """Генерирует текст по prompt-у авторегрессионно."""

    def __init__(
        self,
        model: BareMetalTransformer,
        tokenizer: BPETokenizer,
        cfg: BrainConfig,
    ):
        self.model = model
        self.tokenizer = tokenizer
        self.cfg = cfg
        self._total_tokens = 0

    # ── Основной generate ─────────────────────────────────────────────────

    @torch.no_grad()
    def generate(
        self,
        prompt: str,
        max_new_tokens: int = 256,
        temperature: float = 0.7,
        top_k: int = 50,
        top_p: float = 0.9,
        rep_penalty: float = 1.1,
    ) -> tuple[str, torch.Tensor]:
        """Генерирует продолжение prompt-а.

        Returns:
            (text, embedding) — сгенерированный текст и embedding всей
            последовательности (для memory manager).
        """
        self.model.eval()

        ids = self.tokenizer.encode_with_special(prompt, add_bos=True)
        # Truncate to leave room for generation
        max_prompt = self.cfg.max_seq_len - max_new_tokens - 1
        if max_prompt < 1:
            max_prompt = self.cfg.max_seq_len // 2
        if len(ids) > max_prompt:
            ids = ids[-max_prompt:]
        tokens = torch.tensor([ids], dtype=torch.long)

        caches = self.model.create_caches(1)

        # Prefill: обработать весь промпт за один forward
        logits = self.model(tokens, start_pos=0, caches=caches)

        generated: list[int] = []
        pos = tokens.shape[1]
        all_ids = list(ids)

        for _ in range(max_new_tokens):
            next_logits = logits[:, -1, :].clone()

            # Repetition penalty
            if rep_penalty != 1.0:
                for tok_id in set(all_ids):
                    if next_logits[0, tok_id] > 0:
                        next_logits[0, tok_id] /= rep_penalty
                    else:
                        next_logits[0, tok_id] *= rep_penalty

            # Temperature
            if temperature > 0:
                next_logits = next_logits / temperature

            # Top-k
            if top_k > 0:
                k = min(top_k, next_logits.size(-1))
                topk_vals, topk_idx = torch.topk(next_logits, k)
                filt = torch.full_like(next_logits, float('-inf'))
                filt.scatter_(1, topk_idx, topk_vals)
                next_logits = filt

            # Top-p (nucleus)
            if 0 < top_p < 1.0:
                sorted_logits, sorted_idx = torch.sort(next_logits, descending=True)
                cum_probs = torch.cumsum(F.softmax(sorted_logits, dim=-1), dim=-1)
                remove = (cum_probs - F.softmax(sorted_logits, dim=-1)) >= top_p
                sorted_logits[remove] = float('-inf')
                next_logits.scatter_(1, sorted_idx, sorted_logits)

            probs = F.softmax(next_logits, dim=-1)
            next_token = torch.multinomial(probs, num_samples=1)

            tok_id = int(next_token.item())
            if tok_id == self.tokenizer.EOS:
                break

            generated.append(tok_id)
            all_ids.append(tok_id)

            if pos >= self.cfg.max_seq_len - 1:
                break  # max context reached

            logits = self.model(next_token, start_pos=pos, caches=caches)
            pos += 1

        self._total_tokens += len(all_ids) + len(generated)

        text = self.tokenizer.decode(generated)

        # Embedding для memory manager
        all_t = torch.tensor([all_ids + generated], dtype=torch.long)
        if all_t.shape[1] > self.cfg.max_seq_len:
            all_t = all_t[:, -self.cfg.max_seq_len:]
        embedding = self.model.embed_text(all_t).squeeze(0)

        return text, embedding

    # ── Greedy (для eval/тестов) ──────────────────────────────────────────

    @torch.no_grad()
    def generate_greedy(self, prompt: str, max_new_tokens: int = 64) -> str:
        self.model.eval()
        ids = self.tokenizer.encode_with_special(prompt, add_bos=True)
        max_prompt = self.cfg.max_seq_len - max_new_tokens - 1
        if max_prompt < 1:
            max_prompt = self.cfg.max_seq_len // 2
        if len(ids) > max_prompt:
            ids = ids[-max_prompt:]
        tokens = torch.tensor([ids], dtype=torch.long)
        caches = self.model.create_caches(1)
        logits = self.model(tokens, start_pos=0, caches=caches)

        generated: list[int] = []
        pos = tokens.shape[1]

        for _ in range(max_new_tokens):
            tok_id = int(logits[:, -1, :].argmax(dim=-1).item())
            if tok_id == self.tokenizer.EOS:
                break
            generated.append(tok_id)
            if pos >= self.cfg.max_seq_len - 1:
                break
            logits = self.model(
                torch.tensor([[tok_id]], dtype=torch.long),
                start_pos=pos, caches=caches,
            )
            pos += 1

        return self.tokenizer.decode(generated)

    @property
    def total_tokens(self) -> int:
        return self._total_tokens
