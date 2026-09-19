"""What the agent qualified on the web is kept — with its quote, address and date.

Operator 2026-09-19, the chain: found something new outside → checked the
source → decided it is worth keeping → recorded it with provenance → recalled it
next run → re-checked the source when needed. Before: 7 web tasks that day,
0 records — «the internet is a source that must be qualified» had no qualifier.
Qualification here is this turn's own check: a fact with a verbatim quote that
the verifier confirmed on the opened page.
"""
from __future__ import annotations

from pathlib import Path

from core.learned_conclusion import conclusion_memory, web_knowledge_memory
from core.smart_memory import EpisodeRecord
from tests.test_a_verified_conclusion_is_remembered import _loop

URL = "https://plato.stanford.edu/entries/goedel-incompleteness/"
_ANSWER = (
    "Conclusion:\nПервая теорема Гёделя о неполноте: в непротиворечивой формальной системе с "
    f"достаточной арифметикой есть неразрешимые утверждения [verified:web:{URL}].\n"
    "Facts:\n"
    "- SEP: «in any consistent formal system F within which a certain amount of arithmetic can be "
    f"carried out, there are statements of the language of F which can neither be proved nor disproved in F» [verified:web:{URL}].\n"
    "- Страница доступна [unverified:insufficient_for_realtime].\n"
    f"Sources:\n1. web:{URL} — Stanford Encyclopedia of Philosophy\n"
    "Confidence: high\nUnverified: nothing\n"
)


def _episode(**kw) -> EpisodeRecord:
    base = {
        "goal": "q",
        "question": ("Найти в интернете первоисточник о теореме Гёделя о неполноте"
                     "\n\nThis is your own chosen goal. Do the work yourself with your tools"),
        "outcome": "success", "summary": "s", "full_answer": _ANSWER, "completion_state": "achieved",
        "verified_chunks": 2, "unverified_chunks": 1, "tools_used": ["web_search", "web_fetch"],
        "source_labels": ["web:теорема Гёделя", f"web_fetch:{URL[:50]}"],
        "defect_signals": [], "usage_eligible": True, "created_at": "2026-09-19T19:55:00+00:00",
    }
    base.update(kw)
    return EpisodeRecord(**base)


def test_a_qualified_web_conclusion_keeps_quote_address_and_date() -> None:
    text = web_knowledge_memory(_episode())
    assert text is not None
    assert text.startswith("Вопрос: Найти в интернете первоисточник о теореме Гёделя о неполноте\n")
    assert "This is your own chosen goal" not in text, "обёртка кампании — не вопрос"
    assert "Цитата: «in any consistent formal system F" in text
    assert f"Источник: {URL} (прочитан 2026-09-19)" in text
    assert conclusion_memory(_episode()) is None, "как факт библиотеки сеть по-прежнему не пишется"


def test_an_unqualified_web_answer_is_not_kept() -> None:
    unverified = _ANSWER.replace(f"[verified:web:{URL}]", "[unverified:insufficient_for_realtime]")
    assert web_knowledge_memory(_episode(full_answer=unverified)) is None, "цитата не подтверждена по странице"
    assert web_knowledge_memory(_episode(source_labels=["web:теорема Гёделя"])) is None, "страницу не открывали"
    assert web_knowledge_memory(_episode(usage_eligible=False)) is None


def test_the_loop_writes_it_tagged_as_web_knowledge(workspace: Path) -> None:
    loop = _loop(workspace)
    loop._remember_conclusion(_episode())
    records = loop.persistent_store.load()
    assert len(records) == 1 and "web-knowledge" in records[0].tags
    assert URL in records[0].content
    loop._remember_conclusion(_episode())
    assert len(loop.persistent_store.load()) == 1, "тот же вывод дважды — дубль"


def test_the_prompt_line_keeps_the_source_and_the_planner_knows_how_to_recheck() -> None:
    """Chain step 6: records were cut at 400 chars and the «Источник» line — the
    only thing that makes a re-check possible — was always the part cut off."""
    from core.memory_policy import cut_keeping_provenance
    from core.planner_prompt import PLANNER_SYSTEM

    text = web_knowledge_memory(_episode())
    assert text is not None and len(text) > 400
    shown = cut_keeping_provenance(text, 400)
    assert len(shown) <= 400
    assert shown.endswith(f"Источник: {URL} (прочитан 2026-09-19)")
    assert "Вывод: Первая теорема Гёделя" in shown
    assert "ONE web_fetch of\nthat exact URL" in PLANNER_SYSTEM
