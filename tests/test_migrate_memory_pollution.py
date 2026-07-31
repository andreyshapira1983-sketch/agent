"""The pollution migration may only replay the writer's own rules — never guess.

`scripts/migrate_memory_pollution.py` archives persistent-memory rows that
today's knowledge gate would refuse to write: non-asserting sources
(MIR-054), claims extracted from code files, and raw code-fragment /
mojibake content banked before the extractor learned to reject it.

These tests pin four boundaries:

  1. the writer signature (`fact` + `knowledge` + `source-backed` tags AND a
     parseable `Source:` line) must be proved before any rule is applied —
     rows this writer did not produce come back untouched;
  2. the decision is the writer's own: the classifier imports the gate's
     predicates, so the report and the migration cannot disagree;
  3. the apply path archives (moves to `.archive.jsonl`), never deletes, and
     is idempotent — a second run finds nothing;
  4. dry-run mutates nothing at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.state_integrity import read_state_jsonl_unlocked
from scripts.migrate_memory_pollution import (
    archive_reason,
    apply_migration,
    main,
    plan_migration,
)


def _knowledge_row(content: str, source_tag: str, **over) -> dict:
    """A row shaped exactly like KnowledgeWritePolicy.memory_content/memory_tags."""
    row = {
        "id": "mem_test",
        "type": "semantic",
        "content": content,
        "tags": ["fact", "knowledge", "source-backed", source_tag,
                 "claim:claim_abc"],
        "owner": "self",
        "archived": False,
    }
    row.update(over)
    return row


def _log_row(**over) -> dict:
    return _knowledge_row(
        "strategy_classified: 2026-07-31T01:04:43+00:00\n"
        "Source: log:log_event:run_ab3f4bb672:36\n"
        "Confidence: 0.85",
        "log",
        **over,
    )


def _code_file_row(**over) -> dict:
    return _knowledge_row(
        'assert payload["multi_agent_state"] == "dry_run_executor_ready"\n'
        "Source: file:tests/test_architecture_audit.py\n"
        "Confidence: 0.85",
        "file",
        **over,
    )


def _prose_file_row(**over) -> dict:
    return _knowledge_row(
        "Agent tools require policy approval for irreversible actions.\n"
        "Source: file:docs/guide.md\n"
        "Confidence: 0.85",
        "file",
        **over,
    )


# ── classification: positives ────────────────────────────────────────────────

def test_log_sourced_row_is_archived_as_non_asserting():
    assert archive_reason(_log_row()) == "non-asserting source type: log"


@pytest.mark.parametrize("source_tag,locator", [
    ("test_result", "pytest:tests/test_loop.py"),
    ("memory", "mem_528f46c99825fb423c16396077d866fe"),
    ("tool_output", "current_time"),
    ("code_repository", "github.com/example/agent"),
])
def test_every_non_asserting_source_type_is_archived(source_tag, locator):
    row = _knowledge_row(
        f"Something observed once\nSource: {source_tag}:{locator}\nConfidence: 0.85",
        source_tag,
    )
    reason = archive_reason(row)
    assert reason is not None and source_tag in reason


def test_code_file_row_is_archived():
    assert archive_reason(_code_file_row()) == (
        "code file: tests/test_architecture_audit.py"
    )


def test_code_fragment_in_prose_file_is_archived():
    row = _knowledge_row(
        "assert first.signal_source == ARCHITECTURE_AUDIT_SOURCE\n"
        "Source: file:docs/README.md\nConfidence: 0.85",
        "file",
    )
    assert archive_reason(row) == "claim text is a code fragment"


def test_mojibake_row_is_archived():
    row = _knowledge_row(
        "������ �� �����������\nSource: file:docs/notes.md\nConfidence: 0.85",
        "file",
    )
    assert archive_reason(row) == "broken encoding (mojibake) in claim text"


# ── classification: fail-closed negatives ────────────────────────────────────

def test_prose_file_row_is_kept():
    assert archive_reason(_prose_file_row()) is None


def test_row_without_writer_signature_is_never_touched():
    """The guard the whole module exists for: lessons are not gate output."""
    lesson = {
        "id": "mem_lesson",
        "content": "Bug fixed in 'core/loop_methods2.py': …\n"
                   "Source: log:self_repair\nConfidence: 0.9",
        "tags": ["lesson", "bug-fix", "regression-guard", "insight"],
        "archived": False,
    }
    assert archive_reason(lesson) is None


def test_row_without_source_line_is_never_touched():
    row = _knowledge_row("A bare statement with no provenance line", "log")
    assert archive_reason(row) is None


def test_tag_and_source_line_must_agree():
    """A row whose tag says `file` but whose Source line says `log` was edited
    or corrupted — reclassifying it would be the guess MIR-057 forbids."""
    row = _knowledge_row(
        "strategy_classified: …\nSource: log:run_x:1\nConfidence: 0.85",
        "file",
    )
    assert archive_reason(row) is None


def test_already_archived_row_is_skipped():
    assert archive_reason(_log_row(archived=True)) is None


@pytest.mark.parametrize("tags", [
    None,
    "self-build",                      # string, not list
    ["fact", "knowledge", 42],         # non-string member
    ["fact", "knowledge"],             # signature incomplete
])
def test_malformed_or_foreign_tags_fail_closed(tags):
    row = _log_row()
    row["tags"] = tags
    assert archive_reason(row) is None


def test_user_owned_row_with_signature_tags_is_still_judged_by_source():
    # Owner is not part of the writer signature on purpose: the gate stamps
    # owner="self", but the signature is the tag triple + Source line. A
    # user-owned prose row keeps its place either way.
    row = _prose_file_row(owner="user")
    assert archive_reason(row) is None


# ── report and migration use the identical decision ──────────────────────────

def test_plan_is_exactly_the_rows_archive_reason_flags():
    payloads = [_log_row(id="a"), _prose_file_row(id="b"), _code_file_row(id="c")]
    plan = plan_migration(payloads)
    assert [(i, bool(r)) for i, r in plan] == [(0, True), (2, True)]
    assert all(archive_reason(payloads[i]) == r for i, r in plan)


# ── end-to-end through the real store format ─────────────────────────────────

def _seed_store(tmp_path: Path) -> tuple[Path, PersistentMemoryStore]:
    """Write records through the real store so rows carry _integrity envelopes."""
    store_path = tmp_path / "data" / "persistent_memory.jsonl"
    store = PersistentMemoryStore(store_path)
    rows = [
        _log_row(id="mem_log1"),
        _code_file_row(id="mem_code1"),
        _prose_file_row(id="mem_prose1"),
    ]
    store.save_many([MemoryRecord(**row) for row in rows])
    return store_path, store


def test_dry_run_touches_nothing(tmp_path: Path, capsys):
    store_path, _ = _seed_store(tmp_path)
    before = store_path.read_bytes()

    rc = main(["--workspace", str(tmp_path)])

    assert rc == 0
    assert store_path.read_bytes() == before
    assert not store_path.with_suffix(".archive.jsonl").exists()
    out = capsys.readouterr().out
    assert "would archive: 2" in out
    assert "dry run" in out


def test_apply_archives_moves_rows_and_backs_up(tmp_path: Path, capsys):
    store_path, store = _seed_store(tmp_path)

    rc = main(["--workspace", str(tmp_path), "--apply"])

    assert rc == 0
    # Active store: only the prose row remains, readable by the real store.
    remaining = store.load()
    assert [r.id for r in remaining] == ["mem_prose1"]
    # Archive store: both junk rows, marked archived, envelope intact.
    archived = store.load_archive()
    assert sorted(r.id for r in archived) == ["mem_code1", "mem_log1"]
    assert all(r.archived for r in archived)
    # Backup exists next to the active store.
    backups = list(store_path.parent.glob("persistent_memory.jsonl.*.bak"))
    assert len(backups) == 1
    # Backup preserves the pre-migration state (3 rows).
    assert len(read_state_jsonl_unlocked(backups[0])) == 3
    assert "archived 2 row(s)" in capsys.readouterr().out


def test_apply_is_idempotent(tmp_path: Path, capsys):
    store_path, store = _seed_store(tmp_path)
    assert main(["--workspace", str(tmp_path), "--apply"]) == 0
    capsys.readouterr()

    rc = main(["--workspace", str(tmp_path), "--apply"])

    assert rc == 0
    assert "would archive: 0" in capsys.readouterr().out
    assert [r.id for r in store.load()] == ["mem_prose1"]
    assert len(store.load_archive()) == 2  # not duplicated
    # No second backup was taken: the second run never reached the write path.
    assert len(list(store_path.parent.glob("persistent_memory.jsonl.*.bak"))) == 1


def test_missing_store_is_a_clean_noop(tmp_path: Path, capsys):
    assert main(["--workspace", str(tmp_path)]) == 0
    assert "nothing to do" in capsys.readouterr().out


def test_json_report_names_every_archived_row(tmp_path: Path, capsys):
    _seed_store(tmp_path)

    rc = main(["--workspace", str(tmp_path), "--json"])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["applied"] is False
    assert report["would_archive"] == 2
    assert {r["id"] for r in report["reasons"]} == {"mem_log1", "mem_code1"}


def test_apply_migration_reports_counts(tmp_path: Path):
    store_path, _ = _seed_store(tmp_path)
    payloads = read_state_jsonl_unlocked(store_path)
    plan = plan_migration(payloads)

    result = apply_migration(store_path, payloads, plan)

    assert result["archived"] == 2
    assert result["kept"] == 1
    assert Path(result["backup_active"]).exists()
