"""A plan that carries a template where an address belongs never arrives.

Background: docs/CODE_NOTES.md, "A path is an address, not a template".
"""
from __future__ import annotations

import pytest

from core.placeholder_text import (
    looks_like_unfilled_content,
    looks_like_unfilled_path,
)

#: The three measured live on 2026-08-15, one per tool, one class.
_MEASURED = [
    "your_file_path_here.txt",          # file_write, after «используй file_write»
    "core/<identified_file>.py",        # file_read, right after listing core/
]

#: The third measured shape is CONTENT, not an address — the guard that already
#: existed. Kept beside its siblings because the class is one: a plan carrying a
#: template where the concrete thing belongs.
_MEASURED_CONTENT = "<updated content for core/loop.py with experience_block integration>"

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

#: Real paths from this repository. A guard that stops these is worse than none.
_REAL = [
    "core/loop_attempt.py",
    "data/causal_observations.jsonl",
    "docs/CODE_NOTES.md",
    "tests/conftest.py",
    "knowledge/maps/COMMANDS_MAP.md",
    "scripts/gen_anatomy.py",
    "core/loop_memory_write.py",
    "probe/x.txt",
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


@pytest.mark.parametrize("path", _REAL)
def test_a_real_path_is_never_refused(path: str):
    assert looks_like_unfilled_path(path) is False


def test_an_empty_path_is_not_this_defect():
    """Emptiness has its own error already; two guards for one fault confuse
    the reader about which one fired.
    """
    assert looks_like_unfilled_path("") is False
    assert looks_like_unfilled_path("   ") is False


def test_the_content_shape_stays_with_its_own_guard():
    """One class, two guards, and they must not be merged: an unfilled address
    sends the agent hunting for a file, an unfilled body creates a junk one.
    """
    assert looks_like_unfilled_content(_MEASURED_CONTENT) is True
    assert looks_like_unfilled_content("# настоящий код\nimport os\n") is False
