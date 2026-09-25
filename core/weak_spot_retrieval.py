"""Слабое место рефлексии → улики → файлы кода (MIR-106).

Два приёма из литературы, оба открыты 2026-09-25 до правки:

* **Обоснование уликами.** Обзор памяти агентов (arXiv 2603.07670, §4.3,
  «reflection grounding»): вывод рефлексии обязан ссылаться на конкретные
  случаи; ошибочный вывод в долго живущем агенте опасен тем, что влияет на
  тысячи последующих решений. У нашего урока улика — шаблон ошибки из
  журналов (`Lesson.pattern`: тип события, инструмент, текст, число случаев).
  Нет улики — слабое место не изучается.
* **Поиск файлов по описанию.** SWE-bench (Jimenez et al., ICLR 2024, §4.1
  «Sparse retrieval»): файлы кода к задаче на естественном языке ищут BM25;
  запрос — описание задачи, документы — файлы. Там же предел: примерно в
  половине случаев BM25 не находит ни одного нужного файла — это базовый
  способ, не точный. BM25 здесь тот же, что у памяти (core/memory_policy.py).

Прежде тема вроде «planner interface design» не сверялась ни с чем: план был
тем же, что без слабого места и что для бессмыслицы.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.ingestion import SKIP_DIR_NAMES
from core.memory_policy import _bm25_scores, _term_counts, _tokens

#: Сколько файлов поиск отдаёт в план: столько же, сколько план берёт обычно.
TOP_K = 5


def evidence_query(lesson: Any) -> str:
    """Запрос по улике урока; пусто — улики нет, и изучать нечего."""
    pattern = getattr(lesson, "pattern", None)
    if pattern is None or int(getattr(pattern, "count", 0) or 0) < 1:
        return ""
    parts = (getattr(lesson, "focus_area", ""), getattr(pattern, "tool_name", ""),
             getattr(pattern, "event_type", ""), getattr(pattern, "sample_message", ""))
    return " ".join(str(p) for p in parts if p).strip()


def retrieve_files(workspace: Path, query: str, *, top_k: int = TOP_K) -> list[str]:
    """Файлы `.py`, лучшие по BM25 против запроса; только с ненулевым баллом."""
    root = Path(workspace).resolve()
    q = _tokens(query)
    if not q:
        return []
    rels: list[str] = []
    docs = []
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIR_NAMES for part in path.parts) or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith("tests/"):
            # Замер 2026-09-25: тесты дословно цитируют тексты ошибок и забирали
            # все пять мест; изучать слабое место — это читать код, который
            # ошибается (в SWE-bench нужные файлы — исходники, не тесты).
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Путь — часть документа: имя модуля говорит о нём не меньше текста.
        rels.append(rel)
        docs.append(_term_counts(rel.replace("/", " ").replace("_", " ") + " " + text, []))
    scores = _bm25_scores(q, docs)
    ranked = sorted((s, r) for s, r in zip(scores, rels, strict=True) if s > 0)
    return [r for _s, r in sorted(ranked, key=lambda x: (-x[0], x[1]))[:top_k]]
