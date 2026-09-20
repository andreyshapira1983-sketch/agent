"""What the agent qualified on the web is kept — with its quote, address and date.

Operator 2026-09-19, the chain: found something new outside → checked the
source → decided it is worth keeping → recorded it with provenance → recalled it
next run → re-checked the source when needed. Before: 7 web tasks that day,
0 records — «the internet is a source that must be qualified» had no qualifier.

Qualification is this turn's own check, taken from the VERIFIER'S REPORT: one
claim with a verbatim quote whose verdict is `verified` and whose evidence is
the opened page. Not from the answer text — `[verified:…]` is stripped from the
answer a human reads (`core/answer_format`), and the first version of this rule
looked for it there: 146 episodes on 2026-09-20, 0 records.
"""
from __future__ import annotations

from pathlib import Path

from core.evidence import ProvenanceChain, make_evidence
from core.learned_conclusion import conclusion_memory, web_knowledge_memory
from core.smart_memory import EpisodeRecord
from core.verifier_models import ClaimChunk, VerificationReport
from tests.test_a_verified_conclusion_is_remembered import _loop

URL = "https://plato.stanford.edu/entries/goedel-incompleteness/"
QUOTE = ("in any consistent formal system F within which a certain amount of arithmetic can be "
         "carried out, there are statements of the language of F which can neither be proved nor disproved in F")
_ANSWER = (
    "Conclusion:\nПервая теорема Гёделя о неполноте: в непротиворечивой формальной системе с "
    "достаточной арифметикой есть неразрешимые утверждения.\n"
    "Facts:\n"
    f"- SEP: «{QUOTE}».\n"
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


def _page_evidence():
    return make_evidence(kind="web_page", source_id=f"web_page:{URL}", obtained_via="web_fetch",
                         claim=f"Fetched page {URL}", excerpt=QUOTE)


def _chain(*evs) -> ProvenanceChain:
    chain = ProvenanceChain()
    for ev in evs:
        chain.add(ev)
    return chain


def _report(*chunks: ClaimChunk) -> VerificationReport:
    return VerificationReport(
        total_chunks=len(chunks), verified_chunks=sum(c.verdict == "verified" for c in chunks),
        unverified_chunks=sum(c.verdict != "verified" for c in chunks), cited_but_unmatched_chunks=0,
        self_declared_chunks=0, structural_chunks=0, chunks=tuple(chunks),
        annotated_answer=_ANSWER, fully_unverified=False, chain_was_empty=False,
    )


def _verified_case():
    page = _page_evidence()
    chunk = ClaimChunk(text=f"SEP: «{QUOTE}».", citations=(), matched_evidence_ids=(page.id,), verdict="verified")
    return _report(chunk), _chain(page)


def test_a_qualified_web_conclusion_keeps_quote_address_and_date() -> None:
    report, chain = _verified_case()
    text = web_knowledge_memory(_episode(), report, chain)
    assert text is not None
    assert text.startswith("Вопрос: Найти в интернете первоисточник о теореме Гёделя о неполноте\n")
    assert "This is your own chosen goal" not in text, "обёртка кампании — не вопрос"
    assert f"Цитата: «{QUOTE}»" in text
    assert f"Источник: {URL} (прочитан 2026-09-19)" in text
    assert conclusion_memory(_episode()) is None, "как факт библиотеки сеть по-прежнему не пишется"


def test_an_unqualified_web_answer_is_not_kept() -> None:
    report, chain = _verified_case()
    page = _page_evidence()
    topic_only = ClaimChunk(text=f"SEP: «{QUOTE}».", citations=(), matched_evidence_ids=(page.id,),
                            verdict="topic_supported_but_claim_unverified")
    assert web_knowledge_memory(_episode(), _report(topic_only), _chain(page)) is None, "цитата не подтверждена"
    local = make_evidence(kind="file", source_id="file:core/x.py", obtained_via="file_read", claim="c", excerpt="x")
    own_file = ClaimChunk(text=f"В коде: «{QUOTE}».", citations=(), matched_evidence_ids=(local.id,),
                          verdict="verified")
    assert web_knowledge_memory(_episode(), _report(own_file), _chain(local)) is None, "улика не страница"
    assert web_knowledge_memory(_episode(), None, None) is None, "без отчёта проверки — не сохраняем"
    assert web_knowledge_memory(_episode(source_labels=["web:теорема"]), *_verified_case()) is None, "не открывали"
    assert web_knowledge_memory(_episode(usage_eligible=False), report, chain) is None


def test_the_loop_writes_it_tagged_as_web_knowledge(workspace: Path) -> None:
    loop = _loop(workspace)
    loop.last_verification, loop.last_provenance = _verified_case()
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

    text = web_knowledge_memory(_episode(), *_verified_case())
    assert text is not None and len(text) > 400
    shown = cut_keeping_provenance(text, 400)
    assert len(shown) <= 400
    assert shown.endswith(f"Источник: {URL} (прочитан 2026-09-19)")
    assert "Вывод: Первая теорема Гёделя" in shown
    assert "ONE web_fetch of\nthat exact URL" in PLANNER_SYSTEM
