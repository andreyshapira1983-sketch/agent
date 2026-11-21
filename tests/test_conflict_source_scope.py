"""MIR-054 — conflict detection belongs to prose sources, not to code or memory.

`_subject_value` reads `X = Y` as the proposition "X is Y". That is right for a
document and wrong for a program: two files assigning the same variable name
become "contradictory statements about *path*". On the live registry every one
of the 16 detected conflicts was false, across 52 claims, with subjects like
`path`, `payload`, `parsed`, `_valid_statuses` and `this module`.

The fix is not a smarter string heuristic — it is asking where a claim came
from. Measured on the live registry, all 52 conflicting claims originate from
`file` (36) and `memory` (16); none from `web_page`, `article` or `book`. That
is the real boundary:

  - `file` — source code and project documents. `X = Y` is an assignment, and
    two modules describing themselves both start "This module is …".
  - `memory` — the agent's own prior output. Two goals recorded by two runs are
    two tasks, not a contradiction, and citing yourself is not a second source.
  - prose sources — a book, article, documentation or page really does assert
    "X is Y", and two of them disagreeing is a genuine conflict worth raising.

This matters more since MIR-047 made a conflict quarantine the memory record it
produced: false conflicts now withdraw correct records from retrieval.

Status when written: the first test FAILS (16 conflicts on the live registry).
"""
from __future__ import annotations

from core.knowledge_pipeline import ConflictResolver
from core.source_registry import ClaimRecord, SourceRecord, SourceRegistry


def _registry(pairs: list[tuple[str, str, str, str]]) -> SourceRegistry:
    """pairs of (source_id, source_type, claim_id, claim_text)."""
    reg = SourceRegistry()
    seen: set[str] = set()
    for sid, stype, cid, text in pairs:
        if sid not in seen:
            reg.add_source(SourceRecord(
                id=sid, type=stype, title=sid, locator=sid, trust_level=0.8,  # type: ignore[arg-type]
            ))
            seen.add(sid)
        reg.add_claim(ClaimRecord(id=cid, source_id=sid, text=text, confidence=0.9))
    return reg


def _conflicts(reg: SourceRegistry) -> list:
    return list(ConflictResolver().resolve(reg)[1].conflicts)


def test_a_dialogue_turn_written_before_the_type_existed_still_does_not_conflict() -> None:
    """Старая строка с типом `unknown` тоже не должна спорить.

    Тип `dialogue` появился 2026-09-23, а строки на диске записаны раньше — с
    `unknown`. Починка у истока их не перепишет, поэтому природу источника
    приходится узнавать по адресу. Тот же приём, что для файлов кода.
    """
    reg = _registry([
        ("session_dialogue:turn_6:abc", "unknown", "d1", "user asked: [Claude] Это Claude."),
        ("session_dialogue:turn_7:def", "unknown", "d2", "user asked: [Claude] Это снова Claude."),
    ])

    assert _conflicts(reg) == []


def test_agreement_in_other_words_is_not_a_conflict() -> None:
    """Measured live: two doctrine files assert the SAME rule («never proof of
    implementation» vs «not proof of implementation») and the byte-comparison
    of values booked the agreement as a contradiction."""
    reg = _registry([
        ("docs/a.md", "file", "c1", "File existence is never proof of implementation — the cited"),
        ("docs/b.md", "file", "c2", "File existence is not proof of implementation;"),
    ])
    assert _conflicts(reg) == []


# ==========================================================================
# 2026-08-16: обрезок предложения — не суждение (живой ложный конфликт).
# ==========================================================================
def test_a_truncated_fragment_of_the_same_sentence_is_not_a_conflict() -> None:
    """Живой реестр 2026-08-16: «...capability is complete.» против
    «...capability is comp...[truncated]» из зеркального документа — резолвер
    прочёл обрезок как другое значение того же субъекта. Обрезанное
    предложение не утверждает ничего целого и в сравнение не допускается.
    """
    reg = _registry([
        ("docs/A.md", "article", "c1",
         "The existence of a module is not proof that a capability is complete."),
        ("docs/B.md", "article", "c2",
         "The existence of a module is not proof that a capability is comp...[truncated]"),
    ])

    assert _conflicts(reg) == []


def test_a_question_is_not_a_proposition() -> None:
    """Live registry 2026-09-20: two tables of contents, «1.1 What is a compiler?»
    (Mogensen) and «1.1 What is a plasma?» (Hutchinson), parsed as subject
    «11 what» with values «a compiler?» / «a plasma?» and were reported as a
    contradiction — and a conflict quarantines the memory record it came from."""
    from core.knowledge_pipeline import _subject_value

    assert _subject_value("1.1 What is a compiler?") is None
    assert _subject_value("1.1 What is a plasma?") is None
    assert _subject_value("What is a plasma") is None, "вопросительный предмет — не предмет"
    assert _subject_value("Entropy is a measure of disorder") == ("entropy", "a measure of disorder")
    assert _subject_value("Энтропия это мера беспорядка") == ("энтропия", "мера беспорядка")
