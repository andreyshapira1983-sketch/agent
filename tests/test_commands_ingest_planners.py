"""The deterministic planners and scanners behind the ingestion commands.

The second half of `cli/commands_ingest.py`: `:source-review-plan`,
`:implementation-plan`, `:patch-proposal-plan`, the `:self-build-propose`
scanner, and the pure helpers they are built from. #167 covered the command
surface and said this part was left; this is that part.

Two behaviours here are worth more than their line count:

* **the ungrounded read-first guard.** When the operator says "read these files
  first" and names files that are NOT in the Source Registry, the planners must
  refuse with `insufficient_source_evidence` instead of emitting a plan that
  looks source-grounded while resting on nothing. Both the trigger and the three
  ways out of it are pinned.
* **the large-file scanner walks only safe roots.** It must never descend into
  `.git`, `.venv`, `data/`, `logs/`, `config/`, `__pycache__` or any dotted
  directory — a scan that wanders into runtime state or a virtualenv reports
  nonsense and reads files it has no business reading.

Real `SourceRecord` / `ClaimRecord` / `SourceRegistry` objects are used so the
tests cannot drift from the registry's actual shape.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli.commands_ingest as mod
from cli.commands_ingest import (
    LARGE_FILE_LINE_THRESHOLD,
    _format_large_file_report,
    _handle_implementation_plan,
    _handle_patch_proposal_plan,
    _handle_self_build_propose,
    _handle_source_registry,
    _handle_source_review_plan,
    _insufficient_source_evidence_payload,
    _large_file_report,
    _propose_self_build_operator_intent_patch,
    _requests_explicit_file_read,
    _safe_python_file,
    _safe_scan_dir,
    _self_build_propose_payload,
)
from core.source_registry import ClaimRecord, SourceRecord, SourceRegistry


class FakeLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def log(self, event, payload=None):
        self.events.append((event, payload))

    def payload(self, event):
        for name, data in self.events:
            if name == event:
                return data
        raise AssertionError(f"{event!r} not logged; got {[e for e, _ in self.events]}")


def registry_with(*sources_and_claims):
    """Build a real registry through its own API (sources/claims are tuples)."""
    reg = SourceRegistry()
    for item in sources_and_claims:
        if isinstance(item, SourceRecord):
            reg.add_source(item)
        else:
            reg.add_claim(item)
    return reg


def source(sid="src-1", *, title="Parser notes", locator="docs/parser.md", stype="document"):
    return SourceRecord(id=sid, type=stype, title=title, locator=locator, trust_level=0.8)


def claim(cid="cl-1", *, source_id="src-1", text="the parser drops empty input"):
    return ClaimRecord(id=cid, source_id=source_id, text=text, confidence=0.9, status="extracted")


def agent_with(registry=None):
    return SimpleNamespace(log=FakeLog(), last_source_registry=registry)


# ── :source-registry — the human rendering ───────────────────────────────────

def test_source_registry_lists_sources_and_their_claims(tmp_path, capsys):
    agent = agent_with(registry_with(source(), claim()))

    assert _handle_source_registry("--claims", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "=== source registry ===" in err
    assert "sources=1 claims=1" in err
    assert "src-1 [document] claims=1 trust=0.80" in err
    assert "Parser notes" in err, "the title line is shown"
    assert "- [extracted 0.90] the parser drops empty input" in err


def test_source_registry_falls_back_to_the_locator_when_there_is_no_title(tmp_path, capsys):
    agent = agent_with(registry_with(source(title="")))

    assert _handle_source_registry("", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "docs/parser.md" in err
    assert "- [" not in err, "claims are only shown with --claims"


def test_source_registry_says_when_there_are_no_sources(tmp_path, capsys):
    assert _handle_source_registry("", agent_with(registry_with()), tmp_path) is True
    assert "(no ingested sources)" in capsys.readouterr().err


# ── the read-first guard ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "goal,expected",
    [
        ("read core/foo.py first", True),
        ("сначала прочитай core/foo.py", True),
        ("review only these files: core/foo.py", True),
        ("read the docs", False),          # verb, no qualifier
        ("first, tell me the plan", False),  # qualifier, no verb
        ("", False),
    ],
)
def test_explicit_read_request_needs_both_a_verb_and_a_qualifier(goal, expected):
    assert _requests_explicit_file_read(goal) is expected


def test_no_block_when_the_goal_is_not_a_read_first_request():
    assert _insufficient_source_evidence_payload(
        "improve the parser", {"requested_mentions": ["core/foo.py"]}, requested_kind="x"
    ) is None


def test_no_block_when_no_files_were_named():
    assert _insufficient_source_evidence_payload(
        "read these files first", {"requested_mentions": []}, requested_kind="x"
    ) is None


def test_no_block_when_the_named_files_are_grounded():
    assert _insufficient_source_evidence_payload(
        "read core/foo.py first",
        {"requested_mentions": ["core/foo.py"], "matched_sources": [{"id": "src-1"}]},
        requested_kind="x",
    ) is None


def test_ungrounded_read_first_request_is_blocked_with_its_reason():
    payload = _insufficient_source_evidence_payload(
        "read core/foo.py first",
        {
            "requested_mentions": ["core/foo.py"],
            "matched_sources": [],
            "missing_mentions": ["core/foo.py"],
            "registry": {"sources": 0, "claims": 0},
        },
        requested_kind="source_review_plan",
    )

    assert payload is not None
    assert payload["kind"] == "insufficient_source_evidence"
    assert payload["requested_kind"] == "source_review_plan"
    assert "core/foo.py" in json.dumps(payload, ensure_ascii=False)
    assert payload["constraints"], "the blocked payload states what it did NOT do"


def test_source_review_plan_reports_the_block_instead_of_a_plan(tmp_path, capsys):
    """The whole point: no plan that pretends to be grounded."""
    agent = agent_with(registry_with())  # empty registry

    assert _handle_source_review_plan("read core/foo.py first", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "=== insufficient source evidence (blocked) ===" in err
    assert "requested files NOT read/verified:" in err
    assert "core/foo.py" in err
    assert "next actions:" in err and "constraints:" in err

    logged = agent.log.payload("operator_source_review_plan")
    assert logged["kind"] == "insufficient_source_evidence"


def test_source_review_plan_json_mode_carries_the_block(tmp_path, capsys):
    agent = agent_with(registry_with())
    assert _handle_source_review_plan("read core/foo.py first --json", agent, tmp_path) is True
    assert json.loads(capsys.readouterr().err)["kind"] == "insufficient_source_evidence"


# ── the planners on grounded input ───────────────────────────────────────────

def test_source_review_plan_uses_the_matched_sources(tmp_path, capsys):
    agent = agent_with(registry_with(source(locator="core/parser.py"), claim()))

    assert _handle_source_review_plan("review core/parser.py", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "core/parser.py" in err
    logged = agent.log.payload("operator_source_review_plan")
    assert logged["registry"]["sources"] == 1
    assert logged.get("kind") != "insufficient_source_evidence", "a grounded goal is not blocked"


def test_source_review_plan_without_mentions_shows_what_is_in_the_registry(tmp_path, capsys):
    agent = agent_with(registry_with(source(), claim()))

    assert _handle_source_review_plan("plan the next review", agent, tmp_path) is True

    logged = agent.log.payload("operator_source_review_plan")
    assert logged["registry"]["sources"] == 1
    assert capsys.readouterr().err.strip()


def test_source_review_plan_with_no_registry_at_all(tmp_path, capsys):
    assert _handle_source_review_plan("plan a review", agent_with(None), tmp_path) is True
    assert capsys.readouterr().err.strip()


@pytest.mark.parametrize(
    "handler,event",
    [
        (_handle_implementation_plan, "operator_implementation_plan"),
        (_handle_patch_proposal_plan, "operator_patch_proposal_plan"),
    ],
)
def test_the_two_plan_commands_render_and_log(handler, event, tmp_path, capsys):
    agent = agent_with(registry_with(source(locator="core/parser.py"), claim()))

    assert handler("make the parser handle empty input", agent, tmp_path) is True

    assert capsys.readouterr().err.strip()
    logged = agent.log.payload(event)
    assert logged["goal"] == "make the parser handle empty input"


@pytest.mark.parametrize(
    "handler", [_handle_source_review_plan, _handle_implementation_plan, _handle_patch_proposal_plan]
)
@pytest.mark.parametrize("rest", ["--limit", "--limit lots"])
def test_a_bad_limit_stops_a_plan_command_before_it_plans(handler, rest, tmp_path, capsys):
    """The only refusal these three have: a --limit that is not a number."""
    agent = agent_with(registry_with())

    assert handler(rest, agent, tmp_path) is True

    assert "--limit requires a number" in capsys.readouterr().err
    assert agent.log.events == [], "a refused command logs nothing"


@pytest.mark.parametrize(
    "handler,event",
    [
        (_handle_source_review_plan, "operator_source_review_plan"),
        (_handle_implementation_plan, "operator_implementation_plan"),
        (_handle_patch_proposal_plan, "operator_patch_proposal_plan"),
    ],
)
def test_an_empty_goal_is_allowed_and_gets_a_default(handler, event, tmp_path, capsys):
    """Pinned because it is surprising: these commands do not require a goal."""
    agent = agent_with(registry_with())

    assert handler("", agent, tmp_path) is True

    assert capsys.readouterr().err.strip()
    assert agent.log.payload(event)["goal"], "a default goal is substituted, never empty"


@pytest.mark.parametrize(
    "handler,event",
    [
        (_handle_implementation_plan, "operator_implementation_plan"),
        (_handle_patch_proposal_plan, "operator_patch_proposal_plan"),
    ],
)
def test_the_two_plan_commands_have_a_json_mode(handler, event, tmp_path, capsys):
    agent = agent_with(registry_with(source(), claim()))

    assert handler("improve the parser --json", agent, tmp_path) is True

    payload = json.loads(capsys.readouterr().err)
    assert payload["goal"] == "improve the parser"
    assert agent.log.payload(event)["goal"] == "improve the parser"


# ── the large-file scanner ───────────────────────────────────────────────────

def _write(path: Path, lines: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n" * lines, encoding="utf-8")


def test_the_scanner_reports_only_oversized_files_biggest_first(tmp_path):
    _write(tmp_path / "core" / "huge.py", LARGE_FILE_LINE_THRESHOLD + 10)
    _write(tmp_path / "cli" / "bigger.py", LARGE_FILE_LINE_THRESHOLD + 20)
    _write(tmp_path / "core" / "small.py", 10)
    _write(tmp_path / "top_level.py", LARGE_FILE_LINE_THRESHOLD + 5)

    report = _large_file_report(tmp_path)

    assert [item["path"] for item in report] == [
        "cli/bigger.py",
        "core/huge.py",
        "top_level.py",
    ], "sorted by size, then path"
    assert all(int(item["lines"]) > LARGE_FILE_LINE_THRESHOLD for item in report)


def test_the_scanner_never_descends_into_runtime_or_tooling_directories(tmp_path):
    for unsafe in ("data", "logs", "config", ".venv", ".git", "__pycache__"):
        _write(tmp_path / "core" / unsafe / "huge.py", LARGE_FILE_LINE_THRESHOLD + 50)
    _write(tmp_path / "core" / ".hidden" / "huge.py", LARGE_FILE_LINE_THRESHOLD + 50)
    _write(tmp_path / "unlisted_root" / "huge.py", LARGE_FILE_LINE_THRESHOLD + 50)
    _write(tmp_path / "core" / "nested" / "deep" / "huge.py", LARGE_FILE_LINE_THRESHOLD + 1)

    report = _large_file_report(tmp_path)

    assert [item["path"] for item in report] == ["core/nested/deep/huge.py"], (
        "only safe roots are walked, and nesting inside them is followed"
    )


def test_the_scanner_ignores_non_python_files(tmp_path):
    big = tmp_path / "core" / "notes.txt"
    big.parent.mkdir(parents=True)
    big.write_text("line\n" * (LARGE_FILE_LINE_THRESHOLD + 10), encoding="utf-8")

    assert _large_file_report(tmp_path) == []


def test_scan_guards_reject_what_they_should(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / ".cache").mkdir()
    (tmp_path / "core" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "core" / "b.txt").write_text("x\n", encoding="utf-8")

    assert _safe_scan_dir(tmp_path / "core") is True
    assert _safe_scan_dir(tmp_path / "data") is False
    assert _safe_scan_dir(tmp_path / ".cache") is False
    assert _safe_scan_dir(tmp_path / "missing") is False
    assert _safe_scan_dir(tmp_path / "core" / "a.py") is False, "a file is not a scan dir"

    assert _safe_python_file(tmp_path / "core" / "a.py") is True
    assert _safe_python_file(tmp_path / "core" / "b.txt") is False
    assert _safe_python_file(tmp_path / "core" / "missing.py") is False


def test_the_report_formats_empty_and_populated(tmp_path):
    assert _format_large_file_report([]) == "NO_LARGE_FILES"

    text = _format_large_file_report([{"path": "core/huge.py", "lines": 4200}])
    assert text.splitlines()[0] == "LARGE_FILE_REPORT"
    assert f"threshold_lines={LARGE_FILE_LINE_THRESHOLD}" in text
    assert "core/huge.py lines=4200" in text


def test_the_large_files_flag_prints_the_report(tmp_path, capsys):
    _write(tmp_path / "core" / "huge.py", LARGE_FILE_LINE_THRESHOLD + 3)

    assert _handle_self_build_propose("--large-files", agent_with(), tmp_path) is True

    err = capsys.readouterr().err
    assert "LARGE_FILE_REPORT" in err
    assert "core/huge.py" in err


# ── :self-build-propose — the routing-guard patch ────────────────────────────

GUARD = "    if _looks_like_meta_instruction(normalized):\n        return None\n"
HELPER_ANCHOR = "\n\ndef _matches_inbox_task_request"


def test_no_patch_when_the_target_file_is_absent(tmp_path, capsys):
    assert _handle_self_build_propose("", agent_with(), tmp_path) is True
    assert capsys.readouterr().err.strip() == "NO_PATCH"


def test_no_patch_when_the_guard_anchor_is_missing(tmp_path):
    target = tmp_path / "core" / "operator_intent.py"
    target.parent.mkdir(parents=True)
    target.write_text("def route():\n    return None\n", encoding="utf-8")

    payload = _self_build_propose_payload(tmp_path)

    assert payload["diff"] == "NO_PATCH"
    assert payload["risk"] == "none; no patch is proposed."


def test_no_patch_when_the_helper_anchor_is_missing():
    assert _propose_self_build_operator_intent_patch(GUARD) is None


def test_the_patch_adds_both_the_guard_call_and_the_helper():
    original = GUARD + HELPER_ANCHOR + "():\n    return False\n"

    patched = _propose_self_build_operator_intent_patch(original)

    assert patched is not None
    assert "if _looks_like_self_build_request(normalized):" in patched
    assert "def _looks_like_self_build_request(" in patched
    assert patched.index("if _looks_like_self_build_request") < patched.index(
        "def _looks_like_self_build_request"
    ), "the call is added at the guard, the helper below it"


def test_an_already_patched_file_yields_no_patch():
    original = (
        GUARD
        + "    if _looks_like_self_build_request(normalized):\n        return None\n"
        + "\n\ndef _looks_like_self_build_request(text: str) -> bool:\n    return False\n"
        + HELPER_ANCHOR
        + "():\n    return False\n"
    )
    assert _propose_self_build_operator_intent_patch(original) == original


def test_a_real_diff_is_proposed_and_printed(tmp_path, capsys):
    target = tmp_path / "core" / "operator_intent.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "def route(normalized):\n" + GUARD + "    return 1\n" + HELPER_ANCHOR + "():\n    return False\n",
        encoding="utf-8",
    )

    payload = _self_build_propose_payload(tmp_path)
    assert payload["diff"].startswith("--- ")
    assert "\n@@ " in payload["diff"]
    assert payload["file"] == "core/operator_intent.py"
    assert payload["risk"].startswith("low;")

    assert _handle_self_build_propose("", agent_with(), tmp_path) is True
    err = capsys.readouterr().err
    assert "=== self-build proposal ===" in err
    assert "diagnosis:" in err and "diff:" in err
    assert "tests:" in err and "risk:" in err


def test_a_malformed_diff_is_never_published_as_a_patch(tmp_path, monkeypatch):
    """Defensive guard: a diff without the `---` / `@@` markers is not a patch.

    `difflib` should never produce one, which is exactly why the guard needs a
    test — otherwise nothing would notice if it were removed and a garbage
    "patch" reached the operator as if it were applicable.
    """
    target = tmp_path / "core" / "operator_intent.py"
    target.parent.mkdir(parents=True)
    target.write_text("original\n", encoding="utf-8")
    monkeypatch.setattr(mod, "_propose_self_build_operator_intent_patch", lambda text: "patched\n")
    monkeypatch.setattr(
        mod.difflib, "unified_diff", lambda *a, **k: ["not a diff at all\n"]
    )

    payload = _self_build_propose_payload(tmp_path)

    assert payload["diff"] == "NO_PATCH"
    assert payload["risk"] == "none; no patch is proposed."


def test_an_unchanged_patch_result_is_reported_as_no_patch(tmp_path, monkeypatch):
    target = tmp_path / "core" / "operator_intent.py"
    target.parent.mkdir(parents=True)
    target.write_text("anything\n", encoding="utf-8")
    monkeypatch.setattr(mod, "_propose_self_build_operator_intent_patch", lambda text: text)

    assert _self_build_propose_payload(tmp_path)["diff"] == "NO_PATCH"
