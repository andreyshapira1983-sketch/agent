"""No test may read a production source file by its literal path.

Census item C1, and measuring it shrank the item by an order of magnitude. The
first count said 96 files "judge code by its source text". Narrowed to what the
item is actually about — tests that break when a file MOVES — it was **six sites
in four files**:

    test_completion_contract.py:418        core/completion_contract.py
    test_profile_style_only.py:82          core/planner.py
    test_repair_routes_by_complexity.py    core/loop_repair.py  (x3)
    test_replan_audit.py:197               core/loop.py

Another 72 sites across 22 files also read source, through `module.__file__` or
`inspect.getsource`. Those follow the object and survive a relocation, so they
were never the problem. Counting them as such is how "96 files block Part B"
came about, when in truth **one file did** —
`test_repair_routes_by_complexity.py`, pinning the very module B1 wants to move.

Reading source is a weaker kind of test and this rule does not forbid it. Some
invariants genuinely live in the text: which modules another module may import,
whether a literal was reintroduced, which failure codes are hard-coded. What it
forbids is coupling that check to a PATH, because then a lawful move reddens a
test about something else entirely — and a test that fails for a reason it does
not describe is worse than no test.
"""
from __future__ import annotations

import ast
import pathlib

#: Directories holding shipped code. A test may of course read files it created
#: itself under `tmp_path`; those are fixtures, not the program.
PRODUCTION_DIRS = ("core/", "cli/", "app/", "scripts/", "tools/")


def _literal_production_reads() -> list[str]:
    """`Path("core/x.py").read_text()` and friends, anywhere in `tests/`."""
    offenders: list[str] = []
    for path in sorted(pathlib.Path("tests").rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "read_text"):
                continue
            receiver = ast.unparse(node.func.value)
            if any(d in receiver for d in PRODUCTION_DIRS):
                offenders.append(f"{path.name}:{node.lineno}  {receiver[:60]}")
    return offenders


def test_no_test_reads_shipped_code_by_a_literal_path():
    offenders = _literal_production_reads()

    assert offenders == [], (
        "these read shipped code by path, so moving the file reddens a test "
        "about something else. Read it through the module instead — "
        "`inspect.getsource(mod)` or `Path(mod.__file__)`:\n  "
        + "\n  ".join(offenders)
    )


def test_reading_source_through_the_module_is_still_allowed():
    """The rule is about coupling to a path, not about reading source at all.

    Without this the rule reads as "never inspect source", which would be wrong:
    some invariants live in the text and nowhere else — what a module may
    import, whether a banned literal returned. Those checks are legitimate and
    they travel with the object.
    """
    found = 0
    for path in sorted(pathlib.Path("tests").rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "getsource"):
                found += 1

    assert found > 0, "the rule would be vacuous if nothing read source this way"


def test_the_file_that_blocked_part_b_no_longer_pins_a_path():
    """Named, because it is the one that mattered.

    `core/loop_repair.py` has no caller inside the cycle — the census verified
    that by call sites — and B1 moves it out. This test pinned the module's
    routing rule by reading its path, so the move would have reddened it without
    touching behaviour.
    """
    # By CALL, not by string. A first version searched the text for
    # `Path("core/loop_repair.py")` and went red on the docstring that records
    # what the file used to do — a guard that punishes writing down history is
    # worse than the coupling it looks for. The census found exactly that shape
    # in `tests/test_one_task_store.py`; repeating it inside the fix for C1
    # would have been careless.
    offenders = [
        row for row in _literal_production_reads()
        if row.startswith("test_repair_routes_by_complexity.py")
    ]
    assert offenders == [], offenders

    src = pathlib.Path(
        "tests/test_repair_routes_by_complexity.py"
    ).read_text(encoding="utf-8")
    assert "inspect.getsource" in src, "it must find the source some other way"
