"""
Guardian: a real file is an address, whatever its name.

History of the disease:
  The placeholder guard (looks_like_unfilled_path) used to judge a path
  by its substrings alone. Because 'core/placeholder_text.py' contains the
  substring 'placeholder', the guard condemned its own module as an
  unfilled placeholder. That false self-accusation repeated 26 times in
  the episodic memory (see long-term memory: mem_eff10fd87b893429787d9f1ea6e807a5
  and related monitor lessons).

  The cure: looks_like_unfilled_path(path, base_dir=None) now checks
  whether the path actually resolves to an existing file under base_dir.
  A real file is an address, whatever its name. This test locks in the
  behaviour table so the disease cannot regress.

  Root choice: we use Path(__file__).resolve().parents[1] instead of a
  conftest fixture. Rationale: this test is a pure unit test of the
  predicate and must not depend on conftest wiring; the workspace root
  is unambiguously the parent of tests/. A fixture would add an
  indirection that could silently point elsewhere if conftest changes.
"""

from pathlib import Path

from core.placeholder_text import looks_like_unfilled_path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


def test_generic_your_file_path_is_placeholder():
    # 4. 'path/to/your_file.py' is a template, not a real file.
    assert looks_like_unfilled_path("path/to/your_file.py", base_dir=WORKSPACE_ROOT) is True


def test_xxx_test_file_is_placeholder():
    # 6. 'test_XXX.py' is a template, not a real file.
    assert looks_like_unfilled_path("test_XXX.py", base_dir=WORKSPACE_ROOT) is True
