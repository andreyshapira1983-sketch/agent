"""One Quantum implementation file, one Python function definition.

The rule in `docs/AGENTS.md` is literal: a new Quantum implementation file defines
exactly ONE function. Not "one behavioral callable with helpers" -- one `def`. The
earlier wording let a 447-line file with eight private helpers call itself one
responsibility, which is how the debt below accumulated.

Existing files are DEBT with a recorded ceiling. They may only shrink. Nothing here
refactors them: a responsibility moves when the construction path reaches it and the
move can be proven behaviourally equivalent, not to make a counter go down.

FILE SCOPE, stated so it cannot be bypassed by accident. The ratchet governs the
union of:

  * every `scripts/qm_*.py`, and
  * every bridge or validator path referenced from any `*.qm` specimen in the
    repository, whatever it is called.

The second half closes the rename hole: moving a new implementation file out of the
`qm_` prefix does not remove it from the rule, because a `.qm` file that points at
it drags it back into scope.

The count is every `def` and `async def` in the module, nested definitions and
methods included. A helper hidden inside a function is still a second operation
waiting to be found.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"

#: Highest number of function definitions each existing file may still contain.
#: Lower it when a file loses one. Never raise it -- a new operation gets a new
#: file, which is the entire point of the rule.
_DEBT_CEILING = {
    "qm_claim_check.py": 5,
    "qm_doc_coherence.py": 6,
    "qm_gate_claim.py": 1,
    "qm_link_check.py": 9,
    "qm_py_anchor.py": 2,
}


def _definitions(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(tree)
    )


def _files_in_scope() -> dict[str, Path]:
    """Prefix-matched scripts, plus anything a `.qm` specimen points at."""
    found = {path.name: path for path in _SCRIPTS.glob("qm_*.py")}
    for specimen in _ROOT.glob("*.qm"):
        blob = specimen.read_text(encoding="utf-8")
        for reference in re.findall(r'"((?:scripts|tools)/[^"]+\.py)"', blob):
            path = _ROOT / reference
            if path.is_file():
                found[path.name] = path
    return found


@pytest.mark.parametrize("name", sorted(_DEBT_CEILING))
def test_declared_debt_never_grows(name: str) -> None:
    found = _definitions(_SCRIPTS / name)
    ceiling = _DEBT_CEILING[name]
    assert found <= ceiling, (
        f"{name} defines {found} functions, above its recorded ceiling of {ceiling}. "
        "A new operation belongs in a new file with exactly one definition."
    )


def test_a_new_quantum_file_defines_exactly_one_function() -> None:
    """Anything in scope and not in the debt table is new, so the rule is absolute."""
    offenders = {
        name: _definitions(path)
        for name, path in sorted(_files_in_scope().items())
        if name not in _DEBT_CEILING and _definitions(path) != 1
    }
    assert not offenders, (
        f"new Quantum implementation files must define exactly one function: {offenders}"
    )


def test_the_debt_table_still_describes_reality() -> None:
    """A ceiling for a file that no longer exists would silently protect nothing."""
    missing = [name for name in _DEBT_CEILING if not (_SCRIPTS / name).is_file()]
    assert not missing, f"the debt table names files that are gone: {missing}"


def test_the_counter_sees_nested_and_async_definitions(tmp_path: Path) -> None:
    """Stated as a test because the rule is literal about what counts."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        '''
def outer():
    def inner():
        return 1
    return inner


async def other():
    return 2


class K:
    def method(self):
        return 3
''',
        encoding="utf-8",
    )
    assert _definitions(sample) == 4


def test_a_renamed_file_stays_in_scope_through_the_specimen_that_uses_it() -> None:
    """The prefix is a convenience; a `.qm` reference is what actually binds."""
    scope = _files_in_scope()
    referenced = {
        name for name, path in scope.items()
        if not name.startswith("qm_")
    }
    # No renamed file exists today; the assertion states the mechanism rather than
    # a count, so it keeps meaning when one appears.
    for name in referenced:
        assert name in _DEBT_CEILING or _definitions(scope[name]) == 1, (
            f"{name} is referenced by a .qm specimen and is therefore in scope"
        )
    assert scope, "the ratchet found nothing to govern -- its scope is broken"
