"""Brain — главный оркестратор.

Собирает всё вместе:
    BareMetalTransformer  → модель
    BPETokenizer          → токенизация
    InferenceEngine       → генерация
    MemoryManager         → рабочая/эпизодическая/семантическая память
    ActionLoop            → цикл reason/act/learn

Совместим с интерфейсом LocalNeuralBackend:
    brain.infer(prompt, context, system, history) → str
"""

from __future__ import annotations

import os
from typing import Any

import torch

from .action_loop import ActionLoop
from .config import BrainConfig
from .inference import InferenceEngine
from .memory import MemoryManager
from .model import BareMetalTransformer
from .tokenizer import BPETokenizer


class BareMetalBrain:
    """Полностью автономный мозг на чистом PyTorch."""

    def __init__(
        self,
        config: BrainConfig | None = None,
        checkpoint_dir: str | None = None,
    ):
        self.config = config or BrainConfig()
        self.tokenizer = BPETokenizer()
        self.model = BareMetalTransformer(self.config)
        self.engine = InferenceEngine(self.model, self.tokenizer, self.config)
        self.memory = MemoryManager(self.config.dim)
        self.action_loop = ActionLoop(self.engine, self.memory)
        self._total_tokens = 0

        if checkpoint_dir and os.path.isdir(checkpoint_dir):
            self.load_checkpoint(checkpoint_dir)

    # ── Основной интерфейс ────────────────────────────────────────────────

    def think(self, user_input: str) -> str:
        """Полный цикл мышления через ActionLoop."""
        response = self.action_loop.step(user_input)
        in_len = len(self.tokenizer.encode(user_input))
        out_len = len(self.tokenizer.encode(response))
        self._total_tokens += in_len + out_len
        return response

    def infer(
        self,
        prompt: str,
        context: Any = None,
        system: str | None = None,
        history: list[dict[str, str]] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Совместимый с LocalNeuralBackend интерфейс."""
        parts: list[str] = []
        if system:
            parts.append(str(system))
        if context:
            if isinstance(context, dict):
                parts.append('\n'.join(f'{k}: {v}' for k, v in context.items()))
            else:
                parts.append(str(context))
        if history:
            for msg in history[-10:]:
                role = msg.get('role', 'user')
                content = msg.get('content', '')
                parts.append(f'{role}: {content}')
        parts.append(str(prompt))
        full_prompt = '\n'.join(parts)

        text, _ = self.engine.generate(
            full_prompt, max_new_tokens=max_tokens or 256,
        )
        self._total_tokens += (
            len(self.tokenizer.encode(full_prompt))
            + len(self.tokenizer.encode(text))
        )
        return text

    # ── Health / stats ────────────────────────────────────────────────────

    def health(self) -> dict:
        return {
            'ok': True,
            'backend': 'bare_metal',
            'params': self.model.count_parameters(),
            'size_mb': round(self.model.size_mb(), 1),
            'memory': self.memory.stats(),
        }

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def total_cost_usd(self) -> float:
        return 0.0

    # ── Persistence ───────────────────────────────────────────────────────

    def save_checkpoint(self, dir_path: str) -> None:
        os.makedirs(dir_path, exist_ok=True)
        self.config.save(os.path.join(dir_path, 'config.json'))
        self.tokenizer.save(os.path.join(dir_path, 'tokenizer.json'))
        torch.save(
            self.model.state_dict(),
            os.path.join(dir_path, 'model.pt'),
        )
        self.memory.save(os.path.join(dir_path, 'memory.pt'))

    def load_checkpoint(self, dir_path: str) -> None:
        cfg_p = os.path.join(dir_path, 'config.json')
        if os.path.exists(cfg_p):
            self.config = BrainConfig.load(cfg_p)

        tok_p = os.path.join(dir_path, 'tokenizer.json')
        if os.path.exists(tok_p):
            self.tokenizer = BPETokenizer.load(tok_p)

        model_p = os.path.join(dir_path, 'model.pt')
        if os.path.exists(model_p):
            self.model = BareMetalTransformer(self.config)
            self.model.load_state_dict(
                torch.load(model_p, map_location='cpu', weights_only=True),
            )

        mem_p = os.path.join(dir_path, 'memory.pt')
        if os.path.exists(mem_p):
            self.memory = MemoryManager(self.config.dim)
            self.memory.load(mem_p)

        # Rebuild engine & action loop с новыми компонентами
        self.engine = InferenceEngine(self.model, self.tokenizer, self.config)
        self.action_loop = ActionLoop(self.engine, self.memory)

    # ── Train tokenizer ───────────────────────────────────────────────────

    def train_tokenizer(self, corpus: str, target_vocab: int | None = None) -> None:
        """Обучает BPE-токенизатор на тексте."""
        target = target_vocab or self.config.vocab_size
        self.tokenizer.train(corpus, target_vocab=target)
        # Rebuild engine с обновлённым tokenizer
        self.engine = InferenceEngine(self.model, self.tokenizer, self.config)
        self.action_loop = ActionLoop(self.engine, self.memory)
