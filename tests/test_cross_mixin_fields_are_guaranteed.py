"""A field one mixin reads and another sets must exist by the time it is read.

Census item A6, and the measurement narrowed it sharply. Twenty places in the
loop layer read a sibling's field through `getattr(self, name, default)`. Nineteen
of them read something the CONSTRUCTOR guarantees — defensive habit, no hazard,
and not worth changing. Exactly one field was neither set in `core/loop_init.py`
nor reset per run in `core/loop.py`:

    _synthesis_expects_contract_headers

It is assigned ~370 lines into `_synthesize`, after 44 calls that can raise, and
the synthesis ladder catches exceptions. So a turn whose synthesis broke early
inherited the PREVIOUS turn's value, and the default in the `getattr` hid that
completely. Measured, both directions wrong:

    turn 1 task-specific contract -> False
    turn 2 generic contract, synthesis raises early
      -> verification reads False -> a genuinely malformed answer is NOT flagged

    turn 1 generic contract -> True
    turn 2 task-specific contract, synthesis raises early
      -> verification reads True -> a correct table-only answer IS flagged
         `malformed_output`

Fixed by making the field per-run state like its neighbours: reset to `True` in
`_run_inner` — the safe direction, since a turn whose contract was never
established should be judged by the generic one — declared in both readers'
host contracts, and read directly rather than through a default that turned an
absent connection into a quiet verdict under someone else's rule.

The guard below is about the CLASS. Fixing the one instance is what would leave
the next one open, which is the shape this census keeps finding.
"""
from __future__ import annotations

import ast
import pathlib
from collections import defaultdict


def _self_assignments(path: pathlib.Path) -> set[str]:
    """Every `self.<name>` this module assigns."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign)
            else []
        )
        for target in targets:
            if (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"):
                out.add(target.attr)
    return out


def _layer() -> list[pathlib.Path]:
    return sorted(pathlib.Path("core").glob("loop*.py"))


def _getattr_reads() -> list[tuple[str, int, str]]:
    """`getattr(self, "name", default)` sites: (file, line, name)."""
    found: list[tuple[str, int, str]] = []
    for path in _layer():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "getattr"
                    and len(node.args) == 3
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == "self"
                    and isinstance(node.args[1], ast.Constant)):
                found.append((path.name, node.lineno, node.args[1].value))
    return found


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

def test_no_default_hides_a_field_nobody_guarantees():
    """A default is only honest when the field is guaranteed to exist.

    Guaranteed means one of two things: the constructor sets it, so it exists
    for the life of the agent; or `_run_inner` resets it, so it exists for the
    life of the turn. A field with neither can hold a value from a previous run,
    and the default then reads as "not set" when the truth is "set by someone
    else, a turn ago".
    """
    constructor = _self_assignments(pathlib.Path("core/loop_init.py"))
    per_run = _self_assignments(pathlib.Path("core/loop.py"))
    owners: dict[str, set[str]] = defaultdict(set)
    for path in _layer():
        for name in _self_assignments(path):
            owners[name].add(path.name)

    offenders = [
        f"{filename}:{line} reads {name!r} (set by {sorted(owners[name])}), "
        "which neither the constructor guarantees nor the run resets"
        for filename, line, name in _getattr_reads()
        if owners.get(name)
        and filename not in owners[name]
        and name not in constructor
        and name not in per_run
    ]

    assert offenders == [], offenders


def test_the_field_that_failed_this_rule_is_now_reset_per_run():
    """Named explicitly, so the regression has an address and not just a rule."""
    per_run = _self_assignments(pathlib.Path("core/loop.py"))

    assert "_synthesis_expects_contract_headers" in per_run


def test_both_readers_declare_the_field_they_borrow():
    """The layer's own convention, applied to the field that skipped it."""
    for filename in ("core/loop_verification.py", "core/loop_verify_replan.py"):
        tree = ast.parse(pathlib.Path(filename).read_text(encoding="utf-8"))
        declared: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
                declared |= {
                    sub.target.id
                    for sub in ast.walk(node)
                    if isinstance(sub, ast.AnnAssign)
                    and isinstance(sub.target, ast.Name)
                }
        assert "_synthesis_expects_contract_headers" in declared, filename


def test_neither_reader_reads_it_through_a_default_any_more():
    reads = [
        (f, ln) for f, ln, name in _getattr_reads()
        if name == "_synthesis_expects_contract_headers"
    ]
    assert reads == [], reads


# ---------------------------------------------------------------------------
# What the rule deliberately does NOT forbid
# ---------------------------------------------------------------------------

def test_defaults_on_constructor_guaranteed_fields_are_left_alone():
    """Nineteen of the twenty sites are fine, and the rule must say so.

    `durable_writes`, `gateway_path`, `audit_read_only` and the rest are set in
    `core/loop_init.py`, so their defaults can never fire. Rewriting them would
    be churn dressed as a fix, and it would blunt the rule that catches the real
    case — a guard that flags everything stops being read.
    """
    constructor = _self_assignments(pathlib.Path("core/loop_init.py"))
    harmless = [
        name for _f, _ln, name in _getattr_reads()
        if name in constructor
    ]

    assert harmless, "the rule would be vacuous if no such site existed"
