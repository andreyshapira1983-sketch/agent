"""A plan that carries a template where an address belongs never arrives.

Background: docs/CODE_NOTES.md, "A path is an address, not a template".
"""
from __future__ import annotations

import pytest

from core.placeholder_text import (
    looks_like_unfilled_path,
)

#: The three measured live on 2026-08-15, one per tool, one class.
_MEASURED = [
    "your_file_path_here.txt",          # file_write, after «используй file_write»
    "core/<identified_file>.py",        # file_read, right after listing core/
]


#: Unseen shapes of the same class. The operator's rule: a repair counts only
#: when a form it was NOT fitted to is stopped.
_UNSEEN = [
    "path/to/your/file.py",
    "core/{{module}}.py",
    "docs/FILL_ME.md",
    "core/TODO_replace_me.py",
    "<insert path>",
    "core/${MODULE}.py",
    "tests/test_XXX.py",
]


@pytest.mark.parametrize("path", _MEASURED)
def test_the_three_measured_shapes_are_refused(path: str):
    """Measured live, one per tool, all in a single day.

    `file_write` already refused unfilled CONTENT; nothing looked at the
    address. So the agent listed `core/`, saw `loop_attempt.py` in the result,
    and read `core/<identified_file>.py` — then honestly reported the file was
    missing. The wall stands exactly between OBSERVED and EXPLAINED: it could
    not reach the code that raises the signal it was investigating.
    """
    assert looks_like_unfilled_path(path) is True


@pytest.mark.parametrize("path", _UNSEEN)
def test_unseen_shapes_of_the_same_class_are_refused(path: str):
    assert looks_like_unfilled_path(path) is True


def test_an_empty_path_is_not_this_defect():
    """Emptiness has its own error already; two guards for one fault confuse
    the reader about which one fired.
    """
    assert looks_like_unfilled_path("") is False
    assert looks_like_unfilled_path("   ") is False
