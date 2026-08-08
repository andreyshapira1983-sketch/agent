"""One Quantum implementation file, one Python function definition.

The rule in `docs/AGENTS.md` is literal: a new Quantum implementation file defines
exactly ONE function. Not "one behavioral callable with helpers" -- one `def`. The
earlier wording let a 447-line file with eight private helpers call itself one
responsibility, which is how the debt below accumulated.

Existing files are DEBT with a recorded ceiling. They may only shrink. Nothing here
refactors them: a responsibility moves when the construction path reaches it and the
move can be proven behaviourally equivalent, not to make a counter go down.

The ratchet counts every `def` and `async def` in the module, nested ones included.
A helper hidden inside a function is still a second operation waiting to be found.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

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


@pytest.mark.parametrize("name", sorted(_DEBT_CEILING))
def test_declared_debt_never_grows(name: str) -> None:
    found = _definitions(_SCRIPTS / name)
    ceiling = _DEBT_CEILING[name]
    assert found <= ceiling, (
        f"{name} defines {found} functions, above its recorded ceiling of {ceiling}. "
        "A new operation belongs in a new file with exactly one definition."
    )


def test_a_new_quantum_file_defines_exactly_one_function() -> None:
    """Any `scripts/qm_*.py` not in the debt table is new, so the rule is absolute."""
    offenders = {
        path.name: _definitions(path)
        for path in sorted(_SCRIPTS.glob("qm_*.py"))
        if path.name not in _DEBT_CEILING and _definitions(path) != 1
    }
    assert not offenders, (
        f"new Quantum implementation files must define exactly one function: {offenders}"
    )


def test_the_debt_table_still_describes_reality() -> None:
    """A ceiling for a file that no longer exists would silently protect nothing."""
    missing = [name for name in _DEBT_CEILING if not (_SCRIPTS / name).is_file()]
    assert not missing, f"the debt table names files that are gone: {missing}"
