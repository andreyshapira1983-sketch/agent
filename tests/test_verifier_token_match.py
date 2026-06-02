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

import pytest

from core.evidence import (
    Evidence,
    ProvenanceChain,
    evidence_from_tool_result,
    make_evidence,
)
from core.verifier import (
    _MIN_TOKEN_LEN,
    _NO_TOKEN_FALLBACK_PREFIXES,
    _TOKEN_STOPWORDS,
    Citation,
    _tokenise_citation_body,
    match_citation,
    verify,
)


# ===========================================================================
# Tokeniser
# ===========================================================================

class TestTokeniseCitationBody:
    def test_empty_returns_empty(self):
        assert _tokenise_citation_body("") == []

    def test_splits_on_colon(self):
        assert _tokenise_citation_body("run_tests:bug_lab") == [
            "run_tests", "bug_lab"
        ]

    def test_splits_on_slash(self):
        assert _tokenise_citation_body("tests/bug_lab/case") == [
            "tests", "bug_lab", "case"
        ]

    def test_splits_on_backslash(self):
        assert _tokenise_citation_body(r"tests\bug_lab\case") == [
            "tests", "bug_lab", "case"
        ]

    def test_splits_on_whitespace(self):
        assert _tokenise_citation_body("python -m pytest tests") == [
            "python", "pytest", "tests"
        ]

    def test_splits_on_comma_only(self):
        # `,` IS a separator; `-` is NOT (it's an identifier character
        # like `_`). So `a-b,c` -> ["a-b", "c"]; the `c` is too short.
        assert _tokenise_citation_body("foo-bar,baz") == ["foo-bar", "baz"]

    def test_preserves_underscores_and_dashes(self):
        # The whole reason we exist: `run_tests` and `bug_lab` are
        # ONE token each, not 2+2.
        assert _tokenise_citation_body("run_tests:bug_lab:case-3") == [
            "run_tests", "bug_lab", "case-3"
        ]

    def test_lowercases(self):
        assert _tokenise_citation_body("PYTEST:BUG_LAB") == [
            "pytest", "bug_lab"
        ]

    def test_min_length_filters_short_tokens(self):
        # `vs`, `m`, `a` all below MIN_TOKEN_LEN=3 -> dropped.
        assert _MIN_TOKEN_LEN == 3
        assert _tokenise_citation_body("a:vs:m:bug_lab") == ["bug_lab"]

    def test_stopwords_filtered(self):
        # `test`, `shell`, `web` are stopwords (citation-prefix copies).
        # `http`, `https`, `www` likewise. So the body
        # "test:run_tests:bug_lab" yields only ["run_tests", "bug_lab"].
        assert "test" in _TOKEN_STOPWORDS
        assert "https" in _TOKEN_STOPWORDS
        assert _tokenise_citation_body("test:run_tests:bug_lab") == [
            "run_tests", "bug_lab"
        ]
        assert _tokenise_citation_body("https://example.com") == [
            "example.com"
        ]

    def test_all_stopwords_returns_empty(self):
        assert _tokenise_citation_body("test:web:shell:file") == []

    def test_idempotent_on_already_clean(self):
        assert _tokenise_citation_body("alpha:beta") == ["alpha", "beta"]


# ===========================================================================
# match_citation — token-overlap fallback
# ===========================================================================

def _chain(*evs: Evidence) -> ProvenanceChain:
    ch = ProvenanceChain()
    for ev in evs:
        ch.add(ev)
    return ch


def _shell_ev(argv0: str, short_cmd: str) -> Evidence:
    return make_evidence(
        kind="shell_output",
        source_id=f"shell_output:shell_exec:{argv0}:{short_cmd}",
        obtained_via="shell_exec",
        claim=f"Ran `{short_cmd}`",
        excerpt="ok",
    )


def _test_ev(target: str = "tests") -> Evidence:
    return make_evidence(
        kind="test_result",
        source_id=f"test_result:run_tests:pytest:{target}",
        obtained_via="run_tests",
        claim="Test run verdict: passed=1, failed=0, errors=0",
        excerpt="1 passed",
    )


def _web_ev(url: str) -> Evidence:
    return make_evidence(
        kind="web_page",
        source_id=f"web_page:{url}",
        obtained_via="web_fetch",
        claim=f"Fetched page {url}",
        excerpt="page body",
    )


class TestMatchCitationDirectSubstringStillWorks:
    """Token fallback must not break the existing substring path."""

    def test_test_pytest_substring(self):
        ev = _test_ev(target="tests/bug_lab")
        cit = Citation(
            prefix="test", body="pytest", raw="[test:pytest]",
            expected_kind="test_result",
        )
        assert match_citation(cit, _chain(ev)) is ev

    def test_test_full_path_substring(self):
        ev = _test_ev(target="tests/bug_lab")
        cit = Citation(
            prefix="test", body="tests/bug_lab", raw="[test:tests/bug_lab]",
            expected_kind="test_result",
        )
        assert match_citation(cit, _chain(ev)) is ev

    def test_shell_program_substring(self):
        ev = _shell_ev("python", "python -m pytest tests")
        cit = Citation(
            prefix="shell", body="python", raw="[shell:python]",
            expected_kind="shell_output",
        )
        assert match_citation(cit, _chain(ev)) is ev


class TestMatchCitationTokenFallback:
    """The whole reason this module exists — composite bodies match."""

    def test_test_run_tests_bug_lab_composite(self):
        """The bug from live test 1: [test:run_tests:bug_lab] now
        matches the canonical source_id because `bug_lab` token is
        shared (substring of source_id), despite the literal string
        `run_tests:bug_lab` not appearing verbatim."""
        ev = _test_ev(target="tests/bug_lab")
        cit = Citation(
            prefix="test", body="run_tests:bug_lab",
            raw="[test:run_tests:bug_lab]",
            expected_kind="test_result",
        )
        # `run_tests` IS in source_id (we added it deliberately) and
        # `bug_lab` IS in source_id (part of target). Both tokens hit;
        # the fallback resolves the citation.
        assert match_citation(cit, _chain(ev)) is ev

    def test_token_fallback_picks_best_score(self):
        ev_a = _test_ev(target="tests/foo")
        ev_b = _test_ev(target="tests/bug_lab")
        cit = Citation(
            prefix="test", body="run_tests:bug_lab",
            raw="[test:run_tests:bug_lab]",
            expected_kind="test_result",
        )
        # ev_a has only `run_tests` (1 token); ev_b has both (2). Pick
        # the higher-score record.
        assert match_citation(cit, _chain(ev_a, ev_b)) is ev_b

    def test_shell_composite_body(self):
        ev = _shell_ev("git", "git status --porcelain")
        cit = Citation(
            prefix="shell", body="shell_exec:git:porcelain",
            raw="[shell:shell_exec:git:porcelain]",
            expected_kind="shell_output",
        )
        # `git` + `porcelain` both hit; `shell_exec` is also in source_id.
        assert match_citation(cit, _chain(ev)) is ev

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


class TestMatchCitationWebPrefixExcluded:
    """URL-bearing prefixes (`web`, `search`, `file`) MUST NOT use
    token-overlap fallback — that would let unrelated URLs match
    through shared TLDs or path components."""

    def test_web_unrelated_url_does_not_match(self):
        """The regression: `[web:https://unknown.example]` MUST NOT
        match a `web_page:https://known.example` record via any
        shared token."""
        ev = _web_ev("https://known.example/path")
        cit = Citation(
            prefix="web", body="https://unknown.example",
            raw="[web:https://unknown.example]",
            expected_kind="web_page",
        )
        assert match_citation(cit, _chain(ev)) is None

    def test_web_exact_substring_still_works(self):
        ev = _web_ev("https://example.com/a")
        cit = Citation(
            prefix="web", body="https://example.com/a",
            raw="[web:https://example.com/a]",
            expected_kind="web_page",
        )
        assert match_citation(cit, _chain(ev)) is ev

    def test_file_unrelated_path_does_not_match(self):
        ev = make_evidence(
            kind="file", source_id="src/core/loop.py",
            obtained_via="file_read", claim="x", excerpt="y",
        )
        cit = Citation(
            prefix="file", body="src/tools/web_fetch.py",
            raw="[file:src/tools/web_fetch.py]",
            expected_kind="file",
        )
        # Both paths share `src` but they're different files;
        # token-fallback must NOT bridge them.
        assert match_citation(cit, _chain(ev)) is None

    def test_search_unrelated_query_does_not_match(self):
        ev = make_evidence(
            kind="web_search_hit",
            source_id="web_search:python tutorial",
            obtained_via="web_search", claim="x", excerpt="y",
        )
        cit = Citation(
            prefix="search", body="java tutorial",
            raw="[search:java tutorial]",
            expected_kind="web_search_hit",
        )
        assert match_citation(cit, _chain(ev)) is None

    def test_no_token_fallback_prefixes_inventory(self):
        """Pin the exact set of prefixes excluded from token fallback
        so a future contributor who adds a new URL-ish prefix has to
        make an explicit call about whether it joins the exclusion."""
        assert _NO_TOKEN_FALLBACK_PREFIXES == frozenset(
            {"web", "search", "file"}
        )


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

    def test_explicit_paths_list(self):
        ev = evidence_from_tool_result(
            tool_name="run_tests",
            arguments={"paths": ["tests/bug_lab", "tests/core"]},
            output={
                "passed": 0, "failed": 1, "errors": 0,
                "command": ["python", "-m", "pytest", "tests/bug_lab"],
                "stdout_tail": "1 failed",
            },
        )
        assert ev is not None
        assert ev.source_id == (
            "test_result:run_tests:pytest:tests/bug_lab,tests/core"
        )

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

    def test_paths_truncated_at_60_chars(self):
        long_target = ",".join(f"tests/dir_{i}" for i in range(20))
        ev = evidence_from_tool_result(
            tool_name="run_tests",
            arguments={"paths": [f"tests/dir_{i}" for i in range(20)]},
            output={
                "passed": 1, "failed": 0, "errors": 0,
                "command": ["python", "-m", "pytest"],
                "stdout_tail": "ok",
            },
        )
        assert ev is not None
        # 60-char cap on the target segment; the prefix is unaffected.
        target_part = ev.source_id.split(":pytest:", 1)[1]
        assert len(target_part) <= 60

    def test_full_path_not_in_source_id(self):
        """Regression: the absolute python.exe path that pollutes
        argv on Windows must NOT end up in source_id any more."""
        ev = evidence_from_tool_result(
            tool_name="run_tests",
            arguments={"paths": ["tests"]},
            output={
                "passed": 1, "failed": 0, "errors": 0,
                "command": [
                    r"C:\Python311\python.exe", "-m", "pytest",
                    "-q", "tests",
                ],
                "stdout_tail": "ok",
            },
        )
        assert ev is not None
        assert "C:\\" not in ev.source_id
        assert "python.exe" not in ev.source_id.lower()


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

    def test_posix_path_basename(self):
        ev = evidence_from_tool_result(
            tool_name="shell_exec",
            arguments={"argv": ["/usr/bin/git", "status"]},
            output={
                "argv": ["/usr/bin/git", "status"],
                "exit_code": 0, "stdout": "clean", "stderr": "",
            },
        )
        assert ev is not None
        assert ev.source_id.startswith("shell_output:shell_exec:git:")

    def test_argv0_already_bare(self):
        ev = evidence_from_tool_result(
            tool_name="shell_exec",
            arguments={"argv": ["echo", "hi"]},
            output={
                "argv": ["echo", "hi"],
                "exit_code": 0, "stdout": "hi", "stderr": "",
            },
        )
        assert ev is not None
        assert ev.source_id.startswith("shell_output:shell_exec:echo:")

    def test_empty_argv0_falls_back_to_cmd(self):
        ev = evidence_from_tool_result(
            tool_name="shell_exec",
            arguments={},
            # argv=[""] is unusual but possible — first arg empty.
            output={
                "argv": ["", "noop"],
                "exit_code": 0, "stdout": "", "stderr": "",
            },
        )
        assert ev is not None
        # Falls back to `cmd` rather than crashing.
        assert ev.source_id.startswith("shell_output:shell_exec:cmd:")

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


# ===========================================================================
# End-to-end through verify()
# ===========================================================================

class TestVerifyEndToEndCompositeCitations:
    def test_composite_test_citation_now_verified(self):
        """The exact failure from live test 1, end-to-end."""
        chain = _chain(_test_ev(target="tests/bug_lab"))
        report = verify(
            answer=(
                "Conclusion: the test suite passed in tests/bug_lab "
                "[test:run_tests:bug_lab]."
            ),
            chain=chain,
        )
        assert report.verified_chunks == 1
        assert report.cited_but_unmatched_chunks == 0
        assert "[verified:test:run_tests:bug_lab]" in report.annotated_answer

    def test_composite_shell_citation_now_verified(self):
        chain = _chain(_shell_ev("git", "git status --porcelain"))
        report = verify(
            answer="The repo is clean [shell:shell_exec:git:porcelain].",
            chain=chain,
        )
        assert report.verified_chunks == 1
        assert (
            "[verified:shell:shell_exec:git:porcelain]"
            in report.annotated_answer
        )

    def test_web_unrelated_url_stays_unmatched(self):
        """Regression: web prefix MUST NOT exploit token fallback."""
        chain = _chain(_web_ev("https://known.example/page"))
        report = verify(
            answer="Cited [web:https://unknown.example/other].",
            chain=chain,
        )
        assert report.verified_chunks == 0
        assert report.cited_but_unmatched_chunks == 1
