"""Training harness — обучение модели на текстовых данных.

Простой training loop поверх BareMetalTransformer:
    - Causal language modeling (next token prediction)
    - AdamW optimizer с gradient clipping
    - Поддержка checkpoint save/load
"""

from __future__ import annotations

import random
import time

import torch
import torch.nn.functional as F

from .config import BrainConfig
from .model import BareMetalTransformer
from .tokenizer import BPETokenizer


class Trainer:
    def __init__(
        self,
        model: BareMetalTransformer,
        tokenizer: BPETokenizer,
        config: BrainConfig,
        lr: float = 3e-4,
        weight_decay: float = 0.1,
    ):
        self.model = model
        self.tokenizer = tokenizer
        self.config = config
        self.optimizer = torch.optim.AdamW(
            model.parameters(), lr=lr, weight_decay=weight_decay,
        )
        self._step_count = 0

    def train_on_text(
        self,
        text: str,
        epochs: int = 1,
        batch_size: int = 4,
        seq_len: int = 512,
        stride: int | None = None,
        verbose: bool = False,
    ) -> dict:
        """Обучает модель на сыром тексте.

        Args:
            text: корпус для обучения.
            epochs: количество эпох.
            batch_size: размер батча.
            seq_len: длина контекстного окна.
            stride: шаг между окнами (default: seq_len // 2).
            verbose: печатать loss каждые N батчей.

        Returns:
            {'loss': float, 'batches': int, 'tokens': int, 'elapsed': float}
        """
        tokens = self.tokenizer.encode(text)
        if len(tokens) < seq_len + 1:
            return {'loss': 0.0, 'batches': 0, 'tokens': 0, 'elapsed': 0.0}

        _stride = stride or max(1, seq_len // 2)

        # Все возможные стартовые позиции
        starts = list(range(0, len(tokens) - seq_len, _stride))

        self.model.train()
        total_loss = 0.0
        n_batches = 0
        t0 = time.time()

        for _epoch in range(epochs):
            random.shuffle(starts)

            for i in range(0, len(starts), batch_size):
                batch_starts = starts[i:i + batch_size]
                if not batch_starts:
                    continue

                x = torch.stack([
                    torch.tensor(tokens[s:s + seq_len], dtype=torch.long)
                    for s in batch_starts
                ])
                y = torch.stack([
                    torch.tensor(tokens[s + 1:s + seq_len + 1], dtype=torch.long)
                    for s in batch_starts
                ])

                logits = self.model(x)
                loss = F.cross_entropy(
                    logits.view(-1, self.config.vocab_size),
                    y.view(-1),
                )

                self.optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                self.optimizer.step()

                total_loss += loss.item()
                n_batches += 1
                self._step_count += 1

                if verbose and n_batches % 10 == 0:
                    avg = total_loss / n_batches
                    print(f'  step {self._step_count}  loss={avg:.4f}')

        self.model.eval()
        elapsed = time.time() - t0
        avg_loss = total_loss / max(n_batches, 1)

        return {
            'loss': round(avg_loss, 4),
            'batches': n_batches,
            'tokens': n_batches * batch_size * seq_len,
            'elapsed': round(elapsed, 2),
        }

    @property
    def step_count(self) -> int:
        return self._step_count
