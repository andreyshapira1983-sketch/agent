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

Block 5 (audit G1, 2026-09-03): the first detector matched the receiver's
TEXT against `"core/"` — a spelling this repository never uses; every pin is
spelled `REPO_ROOT / "core" / "x.py"` or `Path(__file__).parent.parent / …`.
It found 0 while an independent count found 34, and it had no negative
control. The detector now walks the receiver's string constants and its
anchor; a fixture-anchored path (`tmp_path / "core" / "foo.py"`) is a
fixture, not the program. The 23 pins it found on that day are listed below
as a RATCHET — new ones go red, the listed ones wait for their migration.
"""
from __future__ import annotations

import ast
import pathlib

#: Directories holding shipped code. A test may of course read files it created
#: itself under `tmp_path`; those are fixtures, not the program.
PRODUCTION_DIRS = ("core", "cli", "app", "scripts", "tools")

#: Names that anchor a path in a FIXTURE tree, not in the repository.
#: `worktree` добавлено 2026-09-18: принимающий (`core/burn_in_supervisor.py`)
#: отдаёт батарее СВЕЖЕЕ рабочее дерево под tmp, и проверка «батарея видела
#: код кандидата» обязана прочитать файл именно там. Это ровно тот случай,
#: ради которого список и заведён: дерево создал тест, а не репозиторий.
_FIXTURE_ANCHORS = frozenset({
    "tmp_path", "tmp", "ws", "workspace", "repo", "root", "target_dir", "worktree",
    # 2026-09-17: дерево следующего цикла опыта самоприменения. Как и
    # `worktree`, это git-worktree ВРЕМЕННОГО синтетического репозитория, и
    # `core/widget.py` в нём — выдуманный файл, а не поставляемый код.
    # Цена названа честно: настоящий закол под именем `cycle` пройдёт мимо
    # сенсора — ровно как уже условлено для `root`, `repo` и `workspace`.
    "cycle",
})

#: Known pins on 2026-09-03 — `file :: pinned path`. Ratchet, not amnesty:
#: until: 2026-09-30 — migrate each to `Path(module.__file__)` /
#: `inspect.getsource(module)` and delete its line; a new pin anywhere is red
#: today.
_KNOWN_PINS = frozenset({
    "test_command_surface_snapshot.py::cli/command_dispatch.py",
    "test_command_surface_snapshot.py::cli/intent_bridge.py",
    "test_command_surface_snapshot.py::cli/app.py",
    "test_command_surface_snapshot.py::cli/repl.py",
    "test_main_patch_seams.py::cli/app.py",
    "test_a_registered_tool_is_not_silently_dead.py::core/step_sanitizer.py",
    "test_a_suppression_names_its_removal_condition.py::scripts/architecture_invariants.py",
    "test_a_suppression_names_its_removal_condition.py::core/loop_step_execution.py",
    "test_catalogue_permissions_name_their_own_sink.py::core/loop.py",
    "test_command_registry.py::cli/app.py",
    "test_command_registry.py::cli/command_dispatch.py",
    "test_command_registry.py::cli",
    "test_commands_map_check.py::cli/command_dispatch.py",
    "test_help_render.py::cli/help.py",
    "test_no_dead_copy_of_a_live_guard.py::tools/web_fetch.py",
    "test_the_goal_reaches_the_hands.py::core/self_build_producer.py",
    "test_the_goal_reaches_the_hands.py::core/best_next_action.py",
    "test_the_lab_measures_the_runtime.py::app/bootstrap.py",
    "test_the_mismatch_sensor_was_measured.py::app/bootstrap.py",
    "test_the_mismatch_sensor_was_measured.py::core/reasoning_action_check.py",
    "test_the_stagnation_signal_is_still_observational.py::core/loop_attempt.py",
    "test_verification_summary.py::core/verifier_core.py",
    "test_verify_replan_cap.py::core/loop_verify_replan.py",
})


def _pinned_path(receiver: ast.AST) -> str:
    """`core/x.py` when the receiver's string constants spell a shipped path."""
    # Source order, not walk order: `ast.walk` visits the outer `/`'s right
    # operand ('loop.py') before the inner one ('core').
    constants = sorted(
        (n for n in ast.walk(receiver)
         if isinstance(n, ast.Constant) and isinstance(n.value, str)),
        key=lambda n: (n.lineno, n.col_offset),
    )
    parts = [n.value.replace("\\", "/").strip("/") for n in constants]
    joined = "/".join(p for p in parts if p)
    head = joined.split("/", 1)[0]
    return joined if head in PRODUCTION_DIRS else ""


def _reads_of(tree: ast.AST):
    """Receivers of `.read_text()/.read_bytes()/.open()` and `open(...)`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in (
            "read_text", "read_bytes", "open",
        ):
            yield node, node.func.value
        elif isinstance(node.func, ast.Name) and node.func.id == "open" and node.args:
            yield node, node.args[0]


def _pins_in(tree: ast.AST, name: str) -> list[tuple[str, str]]:
    """(key, human line) for every repository-anchored read of shipped code."""
    out: list[tuple[str, str]] = []
    for call, receiver in _reads_of(tree):
        pinned = _pinned_path(receiver)
        if not pinned:
            continue
        names = {n.id for n in ast.walk(receiver) if isinstance(n, ast.Name)}
        if names & _FIXTURE_ANCHORS:
            continue  # a fixture tree that happens to be named core/
        out.append((f"{name}::{pinned}", f"{name}:{call.lineno}  {ast.unparse(receiver)[:70]}"))
    return out


def _literal_production_reads() -> list[tuple[str, str]]:
    offenders: list[tuple[str, str]] = []
    for path in sorted(pathlib.Path("tests").rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        offenders.extend(_pins_in(tree, path.name))
    return offenders


def test_no_new_test_reads_shipped_code_by_a_literal_path():
    new = [line for key, line in _literal_production_reads() if key not in _KNOWN_PINS]

    assert new == [], (
        "these read shipped code by path, so moving the file reddens a test "
        "about something else. Read it through the module instead — "
        "`inspect.getsource(mod)` or `Path(mod.__file__)`:\n  "
        + "\n  ".join(new)
    )


def test_the_ratchet_only_goes_down():
    """A migrated pin must leave the list, or the list becomes an amnesty."""
    present = {key for key, _line in _literal_production_reads()}
    stale = sorted(_KNOWN_PINS - present)
    assert stale == [], "pins no longer found — delete them from _KNOWN_PINS:\n  " + "\n  ".join(stale)


def test_the_detector_catches_the_spelling_the_repo_uses():
    """Negative control (G1): the first detector found 0 of 34 because it
    looked for `"core/"` in the receiver's text."""
    snippet = (
        "from pathlib import Path\n"
        "REPO_ROOT = Path(__file__).resolve().parent.parent\n"
        "src = (REPO_ROOT / 'core' / 'loop.py').read_text()\n"
        "raw = open(Path(__file__).parent.parent / 'cli' / 'app.py').read()\n"
    )
    # Known blind spot, stated: a path built into a variable and read later
    # (`rel = Path('scripts') / 'x.py'; rel.read_text()`) is not followed.
    found = _pins_in(ast.parse(snippet), "synthetic.py")
    assert {k for k, _ in found} == {
        "synthetic.py::core/loop.py", "synthetic.py::cli/app.py",
    }, found


def test_a_fixture_tree_named_like_the_program_is_not_a_pin():
    snippet = (
        "def test_x(tmp_path):\n"
        "    (tmp_path / 'core' / 'foo.py').write_text('x')\n"
        "    repo = tmp_path\n"
        "    assert (repo / 'core' / 'foo.py').read_text() == 'x'\n"
    )
    assert _pins_in(ast.parse(snippet), "synthetic.py") == []


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
        line for key, line in _literal_production_reads()
        if key.startswith("test_repair_routes_by_complexity.py")
    ]
    assert offenders == [], offenders

    src = pathlib.Path(
        "tests/test_repair_routes_by_complexity.py"
    ).read_text(encoding="utf-8")
    assert "inspect.getsource" in src, "it must find the source some other way"
