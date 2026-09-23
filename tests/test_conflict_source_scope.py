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

import pathlib

import pytest

from core.knowledge_pipeline import ConflictResolver
from core.source_registry import ClaimRecord, SourceRecord, SourceRegistry
from core.state_integrity import read_state_jsonl_unlocked

_LIVE = pathlib.Path(__file__).resolve().parents[1] / "data" / "source_registry.jsonl"


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


# ==========================================================================
# The acceptance criterion, stated by the operator: zero of the 16 survive.
# ==========================================================================
@pytest.mark.skipif(not _LIVE.exists(), reason="no live registry in this checkout")
def test_the_live_registry_reports_no_conflicts() -> None:
    reg = SourceRegistry()
    for row in read_state_jsonl_unlocked(_LIVE):
        payload = row["payload"]
        if row["kind"] == "source":
            reg.add_source(SourceRecord(**payload))
        else:
            reg.add_claim(ClaimRecord(**payload))

    found = _conflicts(reg)

    assert found == [], (
        "false conflicts remain on the live registry: "
        + ", ".join(f"{c.subject!r}({len(c.claim_ids)} claims)" for c in found)
    )


# ==========================================================================
# Why each class was false.
# ==========================================================================
def test_two_dialogue_turns_do_not_conflict() -> None:
    """Реплики разговора — не спорящие источники.

    Замерено на живом реестре 2026-09-23: 27 ложных конфликтов по предмету
    `user asked claude`, значения ('claude', 'снова claude'). Все утверждения
    приходили из ходов беседы с адресом `session_dialogue:turn_N:…`, а
    «user asked: X» читалось как утверждение «user asked ЕСТЬ X». Повтор
    вопроса — это ход беседы, а не второй источник, спорящий с первым.
    """
    reg = _registry([
        ("session_dialogue:turn_6:abc", "dialogue", "d1", "user asked: [Claude] Это Claude."),
        ("session_dialogue:turn_7:def", "dialogue", "d2", "user asked: [Claude] Это снова Claude."),
    ])

    assert _conflicts(reg) == []


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


def test_a_dialogue_turn_gets_its_own_source_type_at_the_root() -> None:
    """Улика из реплики разговора типизируется `dialogue`, а не `unknown`.

    Это и есть корень: пока тип проваливался в `unknown`, реплики попадали в
    разрешитель конфликтов как полноценные источники о мире.
    """
    from core.evidence import evidence_from_prior_turn
    from core.source_registry import source_type_from_evidence

    # Через настоящую фабрику, а не через самодельный двойник: 2026-09-23 я уже
    # обжёгся тем, что тест проверял мою подделку и пропустил живую ошибку.
    ev = evidence_from_prior_turn(
        question="[Claude] Это Claude.",
        answer="Да.",
        turn_index=6,
        turn_id="abc",
    )

    assert ev.kind == "session_dialogue"
    assert ev.source_id.startswith("session_dialogue:")
    assert source_type_from_evidence(ev) == "dialogue"


def test_two_files_assigning_one_variable_do_not_conflict() -> None:
    reg = _registry([
        ("core/checkpoint.py", "file", "c1", "path = self._path_for(trace_id)"),
        ("cli/commands_approval.py", "file", "c2", "path = (workspace / DEFAULT_APPROVAL_INBOX_PATH)"),
    ])

    assert _conflicts(reg) == []


def test_two_modules_describing_themselves_do_not_conflict() -> None:
    """`this module` is an anaphor: each file means a different module."""
    reg = _registry([
        ("core/smart_memory.py", "file", "c1", "This module is intentionally local and auditable"),
        ("app/daemon.py", "file", "c2", "This module is the foundation for continuous service"),
    ])

    assert _conflicts(reg) == []


def test_two_recorded_goals_do_not_conflict() -> None:
    """Memory holds the agent's own prior output; two goals are two tasks."""
    reg = _registry([
        ("memory:mem_a", "memory", "c1", "objective = find one concrete bug class in the repository"),
        ("memory:mem_b", "memory", "c2", "objective = identify tests that cover recovery"),
    ])

    assert _conflicts(reg) == []


def test_citing_your_own_memory_is_not_a_second_source() -> None:
    reg = _registry([
        ("core/evidence.py", "file", "c1", "Evidence is built outside the tool"),
        ("memory:working_turn_2", "memory", "c2", "Evidence is per-run proof"),
    ])

    assert _conflicts(reg) == []


# ==========================================================================
# GUARD — a real disagreement between prose sources must still be raised.
# ==========================================================================
def test_two_articles_disagreeing_still_conflict() -> None:
    reg = _registry([
        ("https://a.example/au", "article", "c1", "The capital of Australia is Canberra"),
        ("https://b.example/au", "web_page", "c2", "The capital of Australia is Sydney"),
    ])

    found = _conflicts(reg)

    assert len(found) == 1, "a genuine contradiction between independent sources must survive"
    assert found[0].subject == "capital of australia"


def test_documentation_and_a_book_still_conflict() -> None:
    reg = _registry([
        ("https://docs.example", "documentation", "c1", "The default timeout is 30 seconds"),
        ("isbn:123", "book", "c2", "The default timeout is 60 seconds"),
    ])

    assert len(_conflicts(reg)) == 1


def test_a_prose_source_is_not_contradicted_by_code() -> None:
    """Code cannot refute an article; it is not a claim about the world."""
    reg = _registry([
        ("https://a.example", "article", "c1", "The default timeout is 30 seconds"),
        ("core/config.py", "file", "c2", "The default timeout is 5 seconds"),
    ])

    assert _conflicts(reg) == []


# ==========================================================================
# MIR-076 — two new false classes, measured on the operator's live session
# (2026-08-03): deictic noun phrases and same-statement-in-other-words.
# ==========================================================================
def test_two_documents_describing_themselves_do_not_conflict() -> None:
    """`this document` is deictic: each file means ITSELF. The old code only
    survived the `.py` variant because code locators are excluded wholesale;
    markdown docs walked straight into the false conflict (measured live)."""
    reg = _registry([
        ("docs/a.md", "file", "c1", "This document is the corrected, authoritative version of the audit."),
        ("docs/b.md", "file", "c2", "This document is the logical home for the long-horizon plan."),
    ])
    assert _conflicts(reg) == []


def test_russian_deictic_subject_does_not_conflict() -> None:
    """«Этот документ» — тот же дейксис; раньше «этот» просто срезался и
    подлежащим становилось «документ», группируя чужие самоописания."""
    reg = _registry([
        ("docs/a.md", "file", "c1", "Этот документ является исправленной версией аудита"),
        ("docs/b.md", "file", "c2", "Этот документ является домом длинного плана"),
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


def test_genuinely_different_values_still_conflict() -> None:
    reg = _registry([
        ("docs/a.md", "file", "c1", "Request timeout is 5 seconds"),
        ("docs/b.md", "file", "c2", "Request timeout is 10 seconds"),
    ])
    found = _conflicts(reg)
    assert len(found) == 1, "настоящее противоречие обязано остаться конфликтом"


def test_short_value_containment_is_still_a_conflict() -> None:
    """The equivalence rule must not glue short prefixes: «good» ⊄ «good for
    nothing» — a 1-token prefix is no evidence of agreement."""
    reg = _registry([
        ("docs/a.md", "file", "c1", "The plan is good"),
        ("docs/b.md", "file", "c2", "The plan is good for nothing"),
    ])
    found = _conflicts(reg)
    assert len(found) == 1


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


def test_a_real_disagreement_between_prose_sources_still_fires() -> None:
    """Улов не отдан: настоящие разногласия двух прозаических источников
    по-прежнему поднимаются.
    """
    reg = _registry([
        ("docs/A.md", "article", "c1", "The default timeout is 30 seconds."),
        ("docs/B.md", "article", "c2", "The default timeout is 60 seconds."),
    ])

    assert len(_conflicts(reg)) == 1


def test_the_extractor_does_not_mint_claims_from_truncated_fragments() -> None:
    from core.evidence import ProvenanceChain, make_evidence
    from core.knowledge_pipeline import KnowledgePipeline
    from core.source_ranker import rank_chain

    chain = ProvenanceChain()
    chain.add(make_evidence(
        kind="file", source_id="file:docs/X.md", obtained_via="file_read",
        claim="Contents of workspace file X.md",
        excerpt=("The registry keeps every source row. "
                 "If a command is not here, ...[truncated]"),
        confidence=0.9,
    ))
    registry, _ = KnowledgePipeline().build_registry(
        chain, ranking=rank_chain(chain, question="what does the doc say"))

    texts = " ".join(c.text for c in registry.claims)
    assert "[truncated]" not in texts


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


def test_what_the_search_found_is_not_what_is_missing() -> None:
    """Живой прогон 2026-09-20 (RFC 9110): «упоминаний определения не найдено:
    поиск дал три совпадения (core/injection_guard.py, docs/CODE_NOTES.md,
    tests/…), но ни одно не содержит формулировки» — именами НАЙДЕННЫХ файлов
    верное утверждение и объявили ложью, эпизод не попал в опыт."""
    from core.verifier_absence import absence_subjects

    found_list = ("В рабочей папке docs/ и knowledge/ упоминаний именно этого определения не "
                  "найдено: поиск «RFC 9110» дал три совпадения (core/injection_guard.py, "
                  "docs/CODE_NOTES.md, tests/test_an_email.py), но ни одно не содержит формулировки")
    assert absence_subjects(found_list) == set()
    assert "data_v2" in absence_subjects("Файла data_v2.csv нет в рабочей папке")
    assert "parse_config" in absence_subjects("Функции parse_config нет в core/loader.py")
