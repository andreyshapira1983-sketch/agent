"""Action Loop — цикл Perceive → Reason → Act → Observe → Learn.

Это НЕ просто language model. Это reasoning agent:
    1. Восприятие: кодирует input, получает embedding.
    2. Вспоминание: ищет релевантные воспоминания.
    3. Рассуждение: генерирует ответ с контекстом воспоминаний.
    4. Действие: парсит structured output (<think>/<act>/<mem>).
    5. Запоминание: сохраняет опыт в working/episodic memory.
"""

from __future__ import annotations

import re

import torch

from .inference import InferenceEngine
from .memory import MemoryManager
from .tokenizer import BPETokenizer


class ActionLoop:
    """Один шаг = один полный цикл perception → action → learning."""

    def __init__(self, engine: InferenceEngine, memory: MemoryManager):
        self.engine = engine
        self.memory = memory
        self.turn: int = 0
        self.max_think_depth: int = 3     # limit chain-of-thought

    # ── Основной step ─────────────────────────────────────────────────────

    def step(self, user_input: str) -> str:
        """Полный цикл: input → response."""
        self.turn += 1

        # 1. Perceive: embed input
        query_emb = self._embed(user_input)

        # 2. Recall: ищем релевантные воспоминания
        memories = self.memory.recall(query_emb, k=3)
        mem_ctx = '\n'.join(
            f'[mem] {m.text[:200]}' for m in memories
        ) if memories else ''

        # 3. Build prompt с контекстом
        prompt = self._build_prompt(user_input, mem_ctx)

        # 4. Generate (с возможной цепочкой размышлений)
        response, response_emb = self._reason(prompt)

        # 5. Store experience
        self.memory.store_working(f'user: {user_input}', query_emb)
        self.memory.store_working(f'assistant: {response}', response_emb)

        # 6. Episodic: значимые взаимодействия
        if len(user_input) > 20 or self.turn % 5 == 0:
            combined = f'Q: {user_input[:200]}\nA: {response[:200]}'
            self.memory.store_episodic(combined, response_emb, importance=0.6)

        return response

    # ── Prompt building ───────────────────────────────────────────────────

    def _build_prompt(self, user_input: str, mem_context: str) -> str:
        parts: list[str] = []

        if mem_context:
            parts.append(f'Context from memory:\n{mem_context}\n')

        # Последние записи working memory → история разговора
        recent = list(self.memory.working)[-6:]
        if recent:
            parts.append('Recent:\n' + '\n'.join(
                m.text[:120] for m in recent
            ) + '\n')

        parts.append(f'User: {user_input}\nAssistant:')
        return '\n'.join(parts)

    # ── Reasoning chain ───────────────────────────────────────────────────

    def _reason(self, prompt: str) -> tuple[str, torch.Tensor]:
        """Генерирует ответ. Обрабатывает <think>/<act>/<mem> блоки."""
        text, emb = self.engine.generate(prompt, max_new_tokens=256)

        # Парсим структурированный вывод (после обучения модель научится
        # генерировать <think>...<act>... блоки; до обучения — raw text)
        parsed = parse_structured_output(text)

        # Если есть блок <mem> — сохраняем в semantic
        mem_list = parsed.get('mem', [])
        if isinstance(mem_list, list):
            for mem_text in mem_list:
                if mem_text.strip():
                    mem_emb = self._embed(mem_text)
                    self.memory.store_semantic(mem_text, mem_emb, importance=0.7)

        # Финальный ответ: <act respond:"..."> или весь текст
        act_list = parsed.get('act', [])
        if isinstance(act_list, list) and act_list:
            return act_list[-1], emb
        raw = parsed.get('raw', text)
        return (raw if isinstance(raw, str) else text), emb

    # ── Embedding helper ──────────────────────────────────────────────────

    def _embed(self, text: str) -> torch.Tensor:
        ids = self.engine.tokenizer.encode(text)
        if not ids:
            return torch.zeros(self.engine.cfg.dim)
        t = torch.tensor([ids], dtype=torch.long)
        if t.shape[1] > self.engine.cfg.max_seq_len:
            t = t[:, -self.engine.cfg.max_seq_len:]
        return self.engine.model.embed_text(t).squeeze(0)


# ═══════════════════════════════════════════════════════════════════════════════
# Парсер structured output
# ═══════════════════════════════════════════════════════════════════════════════

_TAG_RE = re.compile(
    r'<(think|act|obs|mem)>(.*?)</\1>',
    re.DOTALL,
)


def parse_structured_output(text: str) -> dict[str, list[str] | str]:
    """Извлекает <think>...<act>...<mem>... блоки из текста.

    Returns:
        {'think': [...], 'act': [...], 'mem': [...], 'obs': [...], 'raw': text_without_tags}
    """
    result: dict[str, list[str] | str] = {
        'think': [], 'act': [], 'obs': [], 'mem': [],
    }
    for m in _TAG_RE.finditer(text):
        tag = m.group(1)
        content = m.group(2).strip()
        if tag in result:
            lst = result[tag]
            if isinstance(lst, list):
                lst.append(content)

    # raw = текст без тегов
    raw = _TAG_RE.sub('', text).strip()
    result['raw'] = raw
    return result
