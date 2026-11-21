"""MVP-14.5b — unit tests for the token-overlap fallback in
`match_citation` + the richer source_id formats for `test_result` and
`shell_output`.

The bug this fixes was discovered in live REPL test 1: the LLM cited
`[test:run_tests:bug_lab]` but the Verifier marked it
`cited_but_unmatched` because the source_id contained the full pytest
command (incl. an absolute Windows python.exe path) and the literal
string `run_tests:bug_lab` didn't appear in it. The fix has two
layers:

  1. **Richer source_id** — `test_result:run_tests:pytest:<paths>` and
     `shell_output:shell_exec:<argv0_basename>:<short_cmd>` — so the
     keywords LLMs naturally use (`run_tests`, `pytest`, `shell_exec`,
     program names) are already in the source_id and substring match
     succeeds.

  2. **Token-overlap fallback** — when a single-substring match still
     fails (composite bodies like `run_tests:bug_lab`), we tokenise
     the body and accept the candidate with the most meaningful tokens
     present in its source_id. Web / file / search prefixes are
     deliberately EXCLUDED from this fallback because URL fragments
     and path components match too liberally across unrelated records.
"""
from __future__ import annotations

from core.evidence import (
    Evidence,
    ProvenanceChain,
    evidence_from_tool_result,
    make_evidence,
)
from core.verifier import (
    Citation,
    _tokenise_citation_body,
    match_citation,
)

# ===========================================================================
# Tokeniser
# ===========================================================================

class TestTokeniseCitationBody:
    def test_empty_returns_empty(self):
        assert _tokenise_citation_body("") == []


# ===========================================================================
# match_citation — token-overlap fallback
# ===========================================================================

def _chain(*evs: Evidence) -> ProvenanceChain:
    ch = ProvenanceChain()
    for ev in evs:
        ch.add(ev)
    return ch


class TestMatchCitationTokenFallback:
    """The whole reason this module exists — composite bodies match."""


    def test_token_fallback_refuses_zero_meaningful_tokens(self):
        """Body that tokenises to ONLY stopwords -> no fallback hit.
        Uses a target without `test`/`pytest` substrings so the direct
        substring match doesn't fire first."""
        ev = make_evidence(
            kind="test_result",
            source_id="test_result:run_tests:pytest:smoke",
            obtained_via="run_tests",
            claim="x", excerpt="y",
        )
        cit = Citation(
            prefix="test", body="web:shell", raw="[test:web:shell]",
            expected_kind="test_result",
        )
        # Body tokens "web" and "shell" are both stopwords -> empty
        # token list -> fallback returns None.
        assert match_citation(cit, _chain(ev)) is None

    def test_token_fallback_below_min_len_no_match(self):
        """Body that tokenises to only short tokens -> no fallback hit.
        Same precaution: target avoids any substring that the body
        could match directly."""
        ev = make_evidence(
            kind="test_result",
            source_id="test_result:run_tests:pytest:smoke",
            obtained_via="run_tests",
            claim="x", excerpt="y",
        )
        cit = Citation(
            prefix="test", body="x:y:z", raw="[test:x:y:z]",
            expected_kind="test_result",
        )
        assert match_citation(cit, _chain(ev)) is None


# ===========================================================================
# evidence_from_tool_result — richer source_ids
# ===========================================================================

class TestRunTestsSourceId:
    def test_default_target(self):
        ev = evidence_from_tool_result(
            tool_name="run_tests",
            arguments={},  # no `paths` -> default "tests"
            output={
                "passed": 1, "failed": 0, "errors": 0,
                "command": ["python", "-m", "pytest"],
                "stdout_tail": "1 passed",
            },
        )
        assert ev is not None
        assert ev.source_id == "test_result:run_tests:pytest:tests"
        # Substring matches for typical citations all succeed.
        sid = ev.source_id.lower()
        assert "run_tests" in sid
        assert "pytest" in sid
        assert "tests" in sid


    def test_paths_string_form_accepted(self):
        ev = evidence_from_tool_result(
            tool_name="run_tests",
            arguments={"paths": "tests/foo"},
            output={
                "passed": 1, "failed": 0, "errors": 0,
                "command": ["python", "-m", "pytest"],
                "stdout_tail": "ok",
            },
        )
        assert ev is not None
        assert ev.source_id == "test_result:run_tests:pytest:tests/foo"


class TestShellExecSourceId:
    def test_argv0_basename_extracted(self):
        ev = evidence_from_tool_result(
            tool_name="shell_exec",
            arguments={"argv": [r"C:\Python311\python.exe", "-m", "pytest"]},
            output={
                "argv": [r"C:\Python311\python.exe", "-m", "pytest"],
                "exit_code": 0, "stdout": "ok", "stderr": "",
            },
        )
        assert ev is not None
        # Drive letter + .exe stripped — `python` is what the LLM cites.
        assert ev.source_id.startswith("shell_output:shell_exec:python:")
        assert "shell_exec" in ev.source_id
        assert "python" in ev.source_id


    def test_full_windows_path_not_in_source_id_prefix(self):
        """The 3-segment prefix is normalised — `python`, not the full
        `C:\\Python311\\python.exe`. The trailing <short_cmd> field
        still carries the full path for forensic purposes."""
        ev = evidence_from_tool_result(
            tool_name="shell_exec",
            arguments={"argv": [r"C:\Python311\python.exe", "-V"]},
            output={
                "argv": [r"C:\Python311\python.exe", "-V"],
                "exit_code": 0, "stdout": "Python 3.11.0", "stderr": "",
            },
        )
        assert ev is not None
        # First 3 segments (split on `:`, maxsplit=3, take 3).
        prefix_segments = ev.source_id.split(":", 3)[:3]
        assert prefix_segments == ["shell_output", "shell_exec", "python"]
