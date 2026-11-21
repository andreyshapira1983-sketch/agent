"""Что руки прочитали, то видит и тот, кто пишет ответ.

Эпизод 2026-09-21 08:54, вопрос «открой core/smart_memory.py целиком»: агент
прочитал все 1756 строк, а синтезатору дошло 32 000 знаков — 26 из 36 блоков
урезаны до 444 знаков, и ответ честно писал «строки 111–323… не были показаны
в уликах дословно». Потолок стоял под окна в 8–32 k токенов; синтезатор теперь
deepseek-v4-pro с окном в миллион.
"""
from __future__ import annotations

from core.evidence_budget import apply_total_budget


def _clean_env(monkeypatch) -> None:
    for name in ("AGENT_EVIDENCE_FILE_CHARS", "AGENT_EVIDENCE_TOTAL_CHARS",
                 "AGENT_EVIDENCE_SELF_DOC_CHARS"):
        monkeypatch.delenv(name, raising=False)


def test_the_ceiling_still_holds_beyond_it(monkeypatch) -> None:
    """Потолок не снят, а поднят: сверх него режется, как прежде."""
    _clean_env(monkeypatch)
    blocks = [(f"file:f{i}.py", "z" * 40_000) for i in range(4)]
    kept, trimmed = apply_total_budget(blocks)
    assert trimmed
    assert sum(len(text) for _label, text in kept) <= 100_000 + 2_000
