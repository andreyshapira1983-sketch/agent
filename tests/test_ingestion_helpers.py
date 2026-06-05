"""Unit tests for core.ingestion pure helpers + _ingest_paths skip branches.

ingestion.py was at 81% only because it is exercised *indirectly* through the
CLI / online-ingestion integration tests. The load-bearing seams that had no
direct test were:

  - the workspace path-traversal guard (`_ensure_inside_workspace` /
    `_resolve_inside_workspace`) — a security boundary,
  - the project file walker (`_iter_project_files`) — skip-dir + extension
    filtering,
  - the chunker (`_chunk_text`) — paragraph merge / oversize split / cap,
  - the per-file input-validation skip branches inside `_ingest_paths`
    (not-a-file, unsupported extension, too large, empty, non-UTF-8).

These run with a tiny fake agent (no network, no real LLM).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core import ingestion
from core.ingestion import (
    CHUNK_CHARS,
    MAX_FILE_BYTES,
    ingest_files,
    _chunk_text,
    _ensure_inside_workspace,
    _iter_project_files,
    _resolve_inside_workspace,
)


# ── _ensure_inside_workspace ─────────────────────────────────────────────────

class TestEnsureInsideWorkspace:
    def test_path_inside_workspace_is_allowed(self, tmp_path: Path):
        inside = tmp_path / "sub" / "file.txt"
        # Must not raise.
        _ensure_inside_workspace(tmp_path, inside)

    def test_path_outside_workspace_raises_permission_error(self, tmp_path: Path):
        outside = tmp_path.parent / "elsewhere" / "secret.txt"
        with pytest.raises(PermissionError, match="escapes workspace"):
            _ensure_inside_workspace(tmp_path, outside)


# ── _resolve_inside_workspace ────────────────────────────────────────────────

class TestResolveInsideWorkspace:
    def test_empty_path_raises_value_error(self, tmp_path: Path):
        with pytest.raises(ValueError, match="path is required"):
            _resolve_inside_workspace(tmp_path, "   ")

    def test_relative_path_resolves_under_workspace(self, tmp_path: Path):
        resolved = _resolve_inside_workspace(tmp_path, "docs/readme.md")
        assert resolved == (tmp_path / "docs" / "readme.md").resolve()

    def test_strips_surrounding_quotes(self, tmp_path: Path):
        resolved = _resolve_inside_workspace(tmp_path, '"docs/a.txt"')
        assert resolved == (tmp_path / "docs" / "a.txt").resolve()

    def test_absolute_path_inside_workspace_is_returned(self, tmp_path: Path):
        target = (tmp_path / "x.txt").resolve()
        resolved = _resolve_inside_workspace(tmp_path, str(target))
        assert resolved == target

    def test_traversal_escape_raises_permission_error(self, tmp_path: Path):
        with pytest.raises(PermissionError, match="escapes workspace"):
            _resolve_inside_workspace(tmp_path, "../../etc/passwd")


# ── _iter_project_files ──────────────────────────────────────────────────────

class TestIterProjectFiles:
    def _seed(self, root: Path) -> None:
        (root / "a.txt").write_text("a", encoding="utf-8")
        (root / "b.md").write_text("b", encoding="utf-8")
        (root / "image.png").write_text("notext", encoding="utf-8")  # skipped ext
        skip = root / "__pycache__"
        skip.mkdir()
        (skip / "cached.txt").write_text("noise", encoding="utf-8")  # skip-dir
        nested = root / "pkg"
        nested.mkdir()
        (nested / "c.py").write_text("print(1)", encoding="utf-8")

    def test_yields_only_text_files_outside_skip_dirs(self, tmp_path: Path):
        self._seed(tmp_path)
        found = {p.name for p in _iter_project_files(tmp_path, limit=100)}
        assert found == {"a.txt", "b.md", "c.py"}
        # png excluded by extension, __pycache__ content excluded by skip-dir.
        assert "image.png" not in found
        assert "cached.txt" not in found

    def test_respects_limit(self, tmp_path: Path):
        self._seed(tmp_path)
        found = list(_iter_project_files(tmp_path, limit=2))
        assert len(found) == 2

    def test_results_are_sorted_casefold(self, tmp_path: Path):
        (tmp_path / "Zeta.txt").write_text("z", encoding="utf-8")
        (tmp_path / "alpha.txt").write_text("a", encoding="utf-8")
        names = [p.name for p in _iter_project_files(tmp_path, limit=100)]
        assert names == ["alpha.txt", "Zeta.txt"]


# ── _chunk_text ──────────────────────────────────────────────────────────────

class TestChunkText:
    def test_empty_text_returns_no_chunks(self):
        assert _chunk_text("", max_chunks=8) == []
        assert _chunk_text("\n\n   \n\n", max_chunks=8) == []

    def test_small_text_is_a_single_chunk(self):
        chunks = _chunk_text("hello world", max_chunks=8)
        assert chunks == ["hello world"]

    def test_paragraphs_merge_until_chunk_limit(self):
        # Two short paragraphs fit in one chunk.
        text = "para one\n\npara two"
        chunks = _chunk_text(text, max_chunks=8)
        assert chunks == ["para one\n\npara two"]

    def test_oversize_paragraph_is_split_into_pieces(self):
        big = "x" * (CHUNK_CHARS * 2 + 50)
        chunks = _chunk_text(big, max_chunks=8)
        assert len(chunks) == 3
        assert all(len(c) <= CHUNK_CHARS for c in chunks)

    def test_oversize_after_current_flushes_current_first(self):
        # A small paragraph accumulates in `current`, then an oversize
        # paragraph arrives → current must be flushed before the split.
        small = "intro paragraph"
        big = "z" * (CHUNK_CHARS + 20)
        chunks = _chunk_text(f"{small}\n\n{big}", max_chunks=8)
        assert chunks[0] == small
        assert len(chunks) == 3  # intro + 2 split pieces

    def test_max_chunks_cap_is_enforced_on_oversize_split(self):
        big = "y" * (CHUNK_CHARS * 5)
        chunks = _chunk_text(big, max_chunks=2)
        assert len(chunks) == 2

    def test_paragraph_overflow_flushes_current_and_starts_new(self):
        # current fills near the limit, next paragraph would overflow → flush.
        p1 = "a" * (CHUNK_CHARS - 10)
        p2 = "b" * 100
        chunks = _chunk_text(f"{p1}\n\n{p2}", max_chunks=8)
        assert chunks[0] == p1
        assert chunks[1] == p2

    def test_merge_overflow_respects_max_chunks_cap(self):
        # Two near-full paragraphs can't merge; with max_chunks=1 the loop
        # must return as soon as the first chunk is flushed on overflow.
        p1 = "a" * (CHUNK_CHARS - 50)
        p2 = "b" * (CHUNK_CHARS - 50)
        chunks = _chunk_text(f"{p1}\n\n{p2}", max_chunks=1)
        assert chunks == [p1]


# ── _ingest_paths skip branches via ingest_files ─────────────────────────────

class _FakeKnowledgePipeline:
    def run(self, chain, *, ranking, source_store, remember, auto_write_memory):
        registry = SimpleNamespace(
            sources=[],
            claims=[],
            to_log_payload=lambda: {},
        )
        return SimpleNamespace(
            registry=registry,
            source_store={},
            memory_saved=0,
            memory_rejected=0,
            memory_skipped=0,
            conflicts=SimpleNamespace(count=0),
            to_log_payload=lambda: {},
        )


def _fake_agent() -> SimpleNamespace:
    return SimpleNamespace(
        knowledge_auto_write=False,
        knowledge_pipeline=_FakeKnowledgePipeline(),
        source_registry_store=None,
        _remember_from_knowledge=None,
        log=None,
    )


class TestIngestFilesSkipBranches:
    def test_directory_is_skipped_as_not_a_file(self, tmp_path: Path):
        (tmp_path / "adir").mkdir()
        report = ingest_files(agent=_fake_agent(), workspace=tmp_path, paths=["adir"], dry_run=True)
        assert report.files_seen == 1
        assert report.files_ingested == 0
        assert any("not a file" in s for s in report.skipped_paths)

    def test_unsupported_extension_is_skipped(self, tmp_path: Path):
        (tmp_path / "pic.png").write_text("data", encoding="utf-8")
        report = ingest_files(agent=_fake_agent(), workspace=tmp_path, paths=["pic.png"], dry_run=True)
        assert any("unsupported extension" in s for s in report.skipped_paths)

    def test_oversized_file_is_skipped(self, tmp_path: Path, monkeypatch):
        big = tmp_path / "big.txt"
        big.write_text("z" * 100, encoding="utf-8")
        # Shrink the cap rather than write a 1 MB file.
        monkeypatch.setattr(ingestion, "MAX_FILE_BYTES", 10)
        report = ingest_files(agent=_fake_agent(), workspace=tmp_path, paths=["big.txt"], dry_run=True)
        assert any("too large" in s for s in report.skipped_paths)

    def test_empty_file_is_skipped(self, tmp_path: Path):
        (tmp_path / "empty.txt").write_text("   \n\n", encoding="utf-8")
        report = ingest_files(agent=_fake_agent(), workspace=tmp_path, paths=["empty.txt"], dry_run=True)
        assert any("empty file" in s for s in report.skipped_paths)

    def test_non_utf8_file_is_skipped(self, tmp_path: Path):
        bad = tmp_path / "bad.txt"
        bad.write_bytes(b"\xff\xfe\x00\x01 not utf8")
        report = ingest_files(agent=_fake_agent(), workspace=tmp_path, paths=["bad.txt"], dry_run=True)
        assert any("not valid UTF-8" in s for s in report.skipped_paths)

    def test_valid_text_file_is_ingested(self, tmp_path: Path):
        (tmp_path / "good.txt").write_text("a real paragraph of text.", encoding="utf-8")
        report = ingest_files(agent=_fake_agent(), workspace=tmp_path, paths=["good.txt"], dry_run=True)
        assert report.files_ingested == 1
        assert report.chunks >= 1
        assert "good.txt" in report.ingested_paths
        # dry_run forces auto_write_memory off regardless of the flag.
        assert report.auto_write_memory is False
