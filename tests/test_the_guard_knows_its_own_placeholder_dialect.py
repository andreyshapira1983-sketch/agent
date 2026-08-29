"""TD-018 follow-up: the unfilled-content guard knows its own placeholder dialect.

The guard ``looks_like_unfilled_content`` in ``core/placeholder_text.py`` missed
four stub shapes today. Each of them must be caught. Legal files (a real
``# TODO:`` comment as the first line of a real multi-line file, a real test
with real imports/asserts) must NOT be caught.
"""

from __future__ import annotations

from core.placeholder_text import looks_like_unfilled_content


class TestUnfilledForms:
    def test_single_line_placeholder_with_dash(self):
        # Form 1: single-line prose, word "placeholder", dash separator.
        content = (
            "PLACEHOLDER — will be filled by executor "
            "(structure copied from tests/test_work_session_convergence.py)"
        )
        assert looks_like_unfilled_content(content) is True

    def test_hash_comment_placeholder(self):
        # Form 2: single-line "# placeholder — ..." comment.
        content = "# placeholder — will be replaced by the rewritten test targeting the live organ"
        assert looks_like_unfilled_content(content) is True

    def test_multiline_comments_then_assert_false(self):
        # Form 3: comment lines + a single "assert False, ..." statement.
        content = (
            "# placeholder — will be replaced\n"
            "# by the rewritten test\n"
            "assert False, 'stub'\n"
        )
        assert looks_like_unfilled_content(content) is True

    def test_multiline_comments_then_pass(self):
        # Form 4: comment lines + a single "pass" statement.
        content = (
            "# placeholder — will be replaced\n"
            "# by the rewritten test\n"
            "pass\n"
        )
        assert looks_like_unfilled_content(content) is True


class TestLegalForms:
    def test_todo_comment_first_line_of_real_file(self):
        # A "# TODO: ..." first line of an ordinary multi-line file is a legal
        # comment — the guard must not flag it.
        content = (
            "# TODO: refactor this later\n"
            "def f(x):\n"
            "    return x + 1\n"
        )
        assert looks_like_unfilled_content(content) is False

    def test_real_test_with_imports_and_asserts(self):
        # A real test file with real imports and asserts is not a stub.
        content = (
            "from __future__ import annotations\n"
            "\n"
            "def test_x():\n"
            "    assert 1 == 1\n"
        )
        assert looks_like_unfilled_content(content) is False

    def test_documentation_comments_only(self):
        # A file of only documentation comments, no statements at all, is NOT
        # a stub by our contract: it has no executable placeholder to trip the
        # executor, and flagging it would break legitimate doc-only modules.
        # Decision: leave it alone (returns False).
        content = (
            "# This module documents the public API.\n"
            "# See docs/ for details.\n"
        )
        assert looks_like_unfilled_content(content) is False
