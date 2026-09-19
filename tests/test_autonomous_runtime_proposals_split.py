"""The proposal cluster left `core/autonomous_runtime` verbatim — pinned.

The operator's rule, restated 2026-08-22: a file too large to read through
produces mistakes. `AutonomousRuntime` was 1251 lines even after its data
carriers moved out, so the proposal / self-build concern moved into a mixin —
the same treatment `core/loop.py` received, and the same guard.

This test is not about behaviour; the rest of the suite holds that. It is about
the HONESTY of the move: every relocated body must match what it replaced,
character for character by AST, checked against git history rather than against
my memory of it. The one thing I retyped during the earlier data-carrier move —
two `Literal` vocabularies — I got wrong from memory, which is exactly why this
guard exists.
"""
from __future__ import annotations

import ast
import subprocess  # nosec B404 — fixed argv, reading our own history
from pathlib import Path

import pytest

import core.autonomous_runtime as runtime_mod
import core.autonomous_runtime_proposals as prop_mod
from core.autonomous_runtime import AutonomousRuntime

#: The commit whose tree still had every moved body inside autonomous_runtime.
_BEFORE = "144a35f"

MOVED_METHODS = [
    "_task_propose",
    "_parse_proposals",
    "_existing_proposal_fingerprints",
    "_proposal_digest",
    "_has_pending_self_build_proposal",
]
#: Moved verbatim and since legitimately evolved IN PLACE — the relocation pin
#: no longer applies to these, but every other guard here still does. Each
#: entry must name its change; an unnamed entry is a smuggled edit.
EVOLVED_AFTER_MOVE = [
    # 2026-08-28, MIR-185 (заказ второго экзаменатора): событие
    # self_build_proposal теперь несёт reason — отказ оставляет улику в момент
    # отказа; свидетель tests/test_autonomous_self_build.py.
    "_run_self_build_proposal",
]
MOVED_FUNCTIONS = [
    "_proposal_stem",
    "_proposal_tokens",
    "_proposal_canonical_signature",
    "_proposal_jaccard",
]


def _history_source() -> str | None:
    """The pre-split file, or None when history is unavailable (shallow clone)."""
    try:
        out = subprocess.run(  # noqa: S603 — fixed argv
            ["git", "show", f"{_BEFORE}:core/autonomous_runtime.py"],  # noqa: S607 — the test drives a real binary on purpose
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    # NOT text=True: that decodes with the locale codec, which on this Windows
    # host is cp1251 and chokes on the Russian comments in the source. The file
    # is UTF-8 and must be read as UTF-8 — the same lesson as needing
    # PYTHONIOENCODING for Cyrillic output, met again at the subprocess seam.
    source = out.stdout.decode("utf-8", errors="strict")
    return source if source.strip() else None


def _index(source: str) -> dict[str, ast.AST]:
    tree = ast.parse(source)
    found: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.setdefault(node.name, node)
    return found


@pytest.mark.parametrize("name", MOVED_METHODS + MOVED_FUNCTIONS)
def test_the_body_matches_history_node_for_node(name: str) -> None:
    """A relocation may not edit. If this reddens, something was rewritten on
    the way out and the diff is no longer a pure move."""
    history = _history_source()
    if history is None:
        pytest.skip("git history unavailable — cannot pin the move")
    before = _index(history)
    after = _index(Path(prop_mod.__file__).read_text(encoding="utf-8"))
    assert name in before, f"{name} was not in the pre-split file — wrong baseline"
    assert name in after, f"{name} did not arrive in the mixin"
    assert ast.dump(before[name]) == ast.dump(after[name]), (
        f"{name} differs from its pre-split body — the move was not verbatim"
    )


def test_nothing_moved_stayed_behind() -> None:
    """Two definitions of one method is worse than none: the reader cannot tell
    which one runs."""
    still_there = _index(Path(runtime_mod.__file__).read_text(encoding="utf-8"))
    duplicated = [
        n for n in MOVED_METHODS + EVOLVED_AFTER_MOVE + MOVED_FUNCTIONS
        if n in still_there
    ]
    assert not duplicated, f"left behind in the original: {duplicated}"


@pytest.mark.parametrize("name", MOVED_METHODS + EVOLVED_AFTER_MOVE)
def test_the_consumer_still_sees_one_class(name: str) -> None:
    """The split must be invisible from outside: `AutonomousRuntime` still
    answers for every moved method, so no caller had to change."""
    assert hasattr(AutonomousRuntime, name), f"{name} vanished from the runtime"


def test_the_mixin_holds_no_state_of_its_own() -> None:
    """State lives on the composed runtime. A mixin with its own `__init__` or
    class attributes would quietly become a second owner of the truth."""
    assert "__init__" not in prop_mod.AutonomousRuntimeProposals.__dict__
    data_attrs = [
        k for k, v in prop_mod.AutonomousRuntimeProposals.__dict__.items()
        if not k.startswith("__") and not callable(v)
    ]
    assert not data_attrs, f"the mixin grew state: {data_attrs}"


def test_the_patch_seam_lives_where_the_code_does() -> None:
    """Tests monkeypatch the producer; after a move the address must name the
    module that actually looks the name up. Six patch sites were repointed when
    this split landed, and this pins the target so the next move notices."""
    assert hasattr(prop_mod, "produce_self_apply_proposal")
    assert not hasattr(runtime_mod, "produce_self_apply_proposal"), (
        "the old address is back — two patch targets means one of them is a lie"
    )
