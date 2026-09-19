"""Regression guards for project-ingest memory pollution (core/ingestion.py,
core/knowledge_pipeline.py).

Background: ``:ingest-project`` auto-wrote every accepted sentence of every repo
file to persistent memory as a "fact", flooding it with ~1847 mid-sentence
source fragments (retrieval distractors). These tests pin the fixes so the
pollution cannot recur.

Tests A/B/C FAIL on the pre-fix code (proven by temporarily reverting).
Test D is a regression guard that must keep holding.

Archive/retrieval-exclusion and curated-protection tests live separately in
tests/test_archive_curated_protection.py (core/hygiene.py's responsibility).
"""
from __future__ import annotations

from pathlib import Path

from core.ingestion import ingest_project
from core.knowledge_pipeline import (
    ClaimExtractor,
    _is_broken_encoding,
    _looks_like_code_fragment,
)
from tests.test_cli import _build_agent

# ── A. repo tree is never mass-ingested into memory as facts ──────────────────

def test_ingest_project_never_persists_repo_tree_as_facts(workspace: Path):
    agent = _build_agent(workspace)
    (workspace / "notes.md").write_text(
        "The planner and synthesizer see both prior turns and any retrieved "
        "long-term records. Persistent memory stores long-term records on disk "
        "gated by a write policy.",
        encoding="utf-8",
    )
    before = len(agent.list_persistent())

    # Even with auto-write explicitly forced ON, project self-ingest must not
    # persist file fragments as facts. Evidence extraction itself must still
    # work (the source registry gets populated) — only the memory write is
    # suppressed.
    report = ingest_project(
        agent=agent, workspace=workspace, path=".", limit=10,
        auto_write_memory=True,
    )

    after = agent.list_persistent()
    assert report.claim_count > 0, "project evidence must still be extracted"
    assert report.memory_saved == 0
    assert not any("source-backed" in (r.tags or []) for r in after)
    assert len(after) == before


# ── B. raw file / code fragments are not accepted as standalone facts ─────────

def test_code_and_cli_fragments_rejected():
    ex = ClaimExtractor()
    # A genuine prose fact is still accepted.
    assert ex._accept_sentence(
        "The agent verifies each claim against evidence before answering."
    ) is True
    # Source-code / CLI / REPL / mid-sentence fragments are rejected.
    fragments = [
        "from core.loop import AgentLoop and helpers",
        "def answer(): return the cached project value",
        '>>> agent.run("hello world right now please")',
        "> :remember preference fact concise answers here",
        'python main.py --file "controlled report file"',
        "The planner reads both the prior turns and",  # dangling conjunction
    ]
    for frag in fragments:
        assert ex._accept_sentence(frag) is False, frag
        assert _looks_like_code_fragment(frag) is True, frag


# ── C. broken encoding (mojibake) is rejected ─────────────────────────────────

def test_broken_encoding_rejected():
    ex = ClaimExtractor()
    good = "The registry stores source-backed claims for later review."
    assert ex._accept_sentence(good) is True
    bad = "Interactive session ��� broken decode fragment from main"
    assert _is_broken_encoding(bad) is True
    assert ex._accept_sentence(bad) is False


# ── D. re-ingesting the same document does not create duplicates ──────────────

def test_reingesting_same_document_does_not_duplicate(workspace: Path):
    agent = _build_agent(workspace)
    (workspace / "k.txt").write_text(
        "The knowledge pipeline rejects conflicted or weak claims before saving.",
        encoding="utf-8",
    )
    from cli.commands_ingest import _handle_ingest_source
    _handle_ingest_source("k.txt --write-memory", agent, workspace)
    _handle_ingest_source("k.txt --write-memory", agent, workspace)
    contents = [str(r.content) for r in agent.list_persistent()]
    assert len(contents) == len(set(contents)), "duplicate persistent records created"


def test_a_read_window_header_and_a_cut_tail_are_not_facts():
    """Замер 2026-09-19: шапки окна чтения шли в память фактами с уверенностью
    0.85 (11 из 75 записей учебного опыта); одна пережила смену data.csv и
    сняла верный эпизод с опыта. Обрывки на «—» и «:» — та же семья."""
    for junk in (
        "[data.csv lines 1-20 of 61]",
        "[M12_complex_analysis.md lines 1-60 of 720]",
        "1.6 (2025), CC BY 4.0, library/txt/BeckEtAl_FirstCourseComplexAnalysis.txt —",
        "Карта строк txt по главам книги:",
    ):
        assert _looks_like_code_fragment(junk) is True, junk
    for fact in (
        "В data.csv две колонки: name и amount.",
        "Модуль M12 закрыт после независимой рецензии.",
        "The report lists 5 names (alice, bob, carol, dave, erin).",
    ):
        assert _looks_like_code_fragment(fact) is False, fact


def test_a_book_window_yields_sentences_not_its_lines():
    """Замер 2026-09-19 (опыт с библиотекой книг): 30 из 30 записей памяти были
    строками окна чтения — «320: Consequence is…», «5: Michaelmas term 2021/22»,
    «7: 1 Introduction 7», «5304: eβ(ℏ2k2/2m−µ) + 1»; их доставали в ответы до
    семи раз. Перенесённые строки склеиваются, фактом становится только
    законченное предложение или пункт списка."""
    from core.knowledge_pipeline import _statements

    window = (
        "[Hutchinson.txt lines 319-327 of 9000]\n"
        "319: 1 Introduction 7\n"
        "320: Consequence is that (fig 1.5) a self-consistent loop of dependencies occurs.\n"
        "321: This is one elementary example of the general principle of plasmas requiring a self-\n"
        "322: consistent solution of Maxwell’s equations plus the particle dynamics\n"
        "323: of the plasma.\n"
        "324: Michaelmas term 2021/22\n"
        "325: Figure 1.5: Self-consistent loop of dependencies\n"
        "326: 1.2.3 Debye Shielding\n"
        "327: eβ(ℏ2k2/2m−µ) + 1\n"
    )
    assert _statements(window) == [
        "Consequence is that (fig 1.5) a self-consistent loop of dependencies occurs.",
        "This is one elementary example of the general principle of plasmas requiring a "
        "self-consistent solution of Maxwell’s equations plus the particle dynamics of the plasma.",
    ]
    # Пункт списка — факт и без точки: так пишут документацию.
    assert _statements("- Default timeout is 30 seconds\nUniversity of Cambridge") == [
        "Default timeout is 30 seconds"]
