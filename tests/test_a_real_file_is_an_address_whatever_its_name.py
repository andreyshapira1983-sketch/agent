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
  nine-case behaviour table so the disease cannot regress.

  Root choice: we use Path(__file__).resolve().parents[1] instead of a
  conftest fixture. Rationale: this test is a pure unit test of the
  predicate and must not depend on conftest wiring; the workspace root
  is unambiguously the parent of tests/. A fixture would add an
  indirection that could silently point elsewhere if conftest changes.
"""

from pathlib import Path

from core.placeholder_text import looks_like_unfilled_path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


def test_existing_organ_is_not_placeholder():
    # 1. The guard's own module exists -> it is an address, not a placeholder.
    assert looks_like_unfilled_path("core/placeholder_text.py", base_dir=WORKSPACE_ROOT) is False


def test_this_test_file_is_not_placeholder():
    # 2. This very file exists -> it is an address, not a placeholder.
    assert looks_like_unfilled_path(
        "tests/test_a_real_file_is_an_address_whatever_its_name.py",
        base_dir=WORKSPACE_ROOT,
    ) is False


def test_angle_bracket_insert_path_is_placeholder():
    # 3. '<insert path>' is an unfilled template marker.
    assert looks_like_unfilled_path("<insert path>", base_dir=WORKSPACE_ROOT) is True


def test_generic_your_file_path_is_placeholder():
    # 4. 'path/to/your_file.py' is a template, not a real file.
    assert looks_like_unfilled_path("path/to/your_file.py", base_dir=WORKSPACE_ROOT) is True


def test_double_brace_module_is_placeholder():
    # 5. '{{module}}' is an unfilled template variable.
    assert looks_like_unfilled_path("{{module}}", base_dir=WORKSPACE_ROOT) is True


def test_xxx_test_file_is_placeholder():
    # 6. 'test_XXX.py' is a template, not a real file.
    assert looks_like_unfilled_path("test_XXX.py", base_dir=WORKSPACE_ROOT) is True


def test_new_organ_draft_without_template_markers_is_not_placeholder():
    # 7. A non-existent path WITHOUT template markers is a new file for
    #    writing, not an unfilled placeholder.
    assert looks_like_unfilled_path("core/new_organ_draft.py", base_dir=WORKSPACE_ROOT) is False


def test_legacy_behaviour_without_base_dir_is_preserved():
    # 8. Without base_dir, the old substring-based behaviour is kept:
    #    'core/placeholder_text.py' still looks like a placeholder.
    assert looks_like_unfilled_path("core/placeholder_text.py") is True


def test_old_segment_law_still_holds_for_docs_path():
    # 9. 'docs/path/tools.md' is a real, existing file -> not a placeholder.
    assert looks_like_unfilled_path("docs/path/tools.md") is False
