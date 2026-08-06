"""The probe that breaks code on purpose must itself be trustworthy.

Census item C2. The probe's whole value is that its output cannot be argued
with — pytest prints `FAILED` or it does not, and no reasoning of mine can
forge that. But the probe decides WHICH breaks to try, and a probe that
silently tries nothing would report a clean bill of health forever.

So these tests hold the two ends: it must produce the mutations it claims, and
it must not produce the ones it says it skips. They run on synthetic sources
and never invoke pytest, so they stay fast enough to live in the ordinary
suite; the slow part — actually running the suite under a mutation — is what
the script does when a human asks it to.

First real run, `core/low_evidence_policy.py` against its four test files, ten
mutations: **six survived**. That is the module deciding whether an answer is
truncated for insufficient evidence — the one whose failure, measured in census
item A2, hands a user a confident unsupported claim. Its thresholds are largely
unpinned. Recorded rather than fixed here: closing them is its own job, and
naming it is what stops it being forgotten.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from mutation_probe import (  # noqa: E402
    _default_value_nodes,
    enumerate_mutations,
)


def _descriptions(source: str) -> list[str]:
    return [m.description for m in enumerate_mutations(source)]


# ---------------------------------------------------------------------------
# It produces the breaks it claims
# ---------------------------------------------------------------------------

def test_a_comparison_is_flipped():
    source = "def f(n):\n    return n > 3\n"

    mutations = enumerate_mutations(source)

    assert any("comparison Gt -> GtE" in m.description for m in mutations)
    flipped = next(m for m in mutations if "Gt -> GtE" in m.description)
    assert "n >= 3" in flipped.source


def test_a_boundary_moves_by_one():
    source = "LIMIT = 8\n"

    mutations = enumerate_mutations(source)

    assert [m.description for m in mutations] == ["number 8 -> 9"]
    assert "LIMIT = 9" in mutations[0].source


def test_a_truth_value_is_inverted():
    source = "def f():\n    return True\n"

    mutations = enumerate_mutations(source)

    assert [m.description for m in mutations] == ["boolean True -> False"]
    assert "return False" in mutations[0].source


def test_every_mutation_still_parses():
    """A mutant that will not import proves nothing — the tests fail on syntax.

    Cheap to check and easy to break: `ast.unparse` on a tree whose locations
    were not fixed produces text Python will not accept.
    """
    source = "def f(n):\n    if n > 3 and True:\n        return 1\n    return 0\n"

    for mutation in enumerate_mutations(source):
        ast.parse(mutation.source)


def test_each_mutation_changes_exactly_one_thing():
    """Two changes at once would make a survivor unattributable."""
    source = "def f(n):\n    return n > 3 or n < 1\n"

    for mutation in enumerate_mutations(source):
        differences = sum(
            1 for a, b in zip(source.split(), mutation.source.split(), strict=False)
            if a != b
        )
        assert differences <= 2, mutation.description


# ---------------------------------------------------------------------------
# It skips what it says it skips
# ---------------------------------------------------------------------------

def test_signature_defaults_are_not_mutated():
    """Measured noise, not a rule of taste.

    The first real run reported five survivors in `core/low_evidence_policy.py`
    and three of them were parameter defaults every caller overrides —
    unobservable changes reported as gaps. A probe that flags what cannot matter
    stops being read, which is the failure this project keeps finding in its own
    checks.
    """
    source = "def f(a: bool = False, b: int = 0):\n    return a\n"

    assert _descriptions(source) == []


def test_dataclass_field_defaults_are_not_mutated():
    source = (
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class C:\n"
        "    n: int = 0\n"
        "    flag: bool = True\n"
    )

    assert _descriptions(source) == []


def test_a_default_is_skipped_but_a_real_constant_beside_it_is_not():
    """The skip must be surgical, or it hides the case the probe exists for."""
    source = (
        "def f(limit: int = 8):\n"
        "    return limit > 3\n"
    )

    descriptions = _descriptions(source)

    assert "number 8 -> 9" not in descriptions, "the default should be skipped"
    assert "number 3 -> 4" in descriptions, "the comparison operand should not"


def test_the_skip_set_is_built_from_the_tree_that_is_walked():
    """The bug this nearly shipped with, pinned so it cannot return.

    A first version parsed the source twice — once for the skip set, once for
    the walk — and matched nodes by `id()`. The two parses produce different
    objects, so the skip set referred to nodes the mutator never saw and did
    nothing at all. An inert guard reporting success is the exact shape the
    census kept finding; it deserved a test rather than a memory.
    """
    source = "def f(a: bool = False):\n    return a\n"
    tree = ast.parse(source)

    skip = _default_value_nodes(tree)

    assert skip, "the default should have been collected"
    assert any(id(node) in skip for node in ast.walk(tree)), (
        "the skip set does not refer to nodes of the tree it was built from"
    )


# ---------------------------------------------------------------------------
# A refusal is not a finding
# ---------------------------------------------------------------------------

def test_a_refusal_and_a_survivor_get_different_exit_codes(monkeypatch, capsys):
    """`probe` returns -1 when it refuses: the selection was already red, so a
    survivor would prove nothing. `main` used to map every non-zero value to 1,
    which is the exit code for "mutations survived" — a caller could not tell a
    refusal from a finding.
    """
    import mutation_probe as mp

    monkeypatch.setattr(mp.subprocess, "run", lambda *a, **k: _CleanGit())

    codes = {}
    for label, value in (("refused", -1), ("all caught", 0), ("survivors", 3)):
        monkeypatch.setattr(mp, "probe", lambda *a, _v=value, **k: _v)
        codes[label] = mp.main(["core/loop.py", "tests/test_cli.py"])
    capsys.readouterr()

    assert codes == {"refused": 2, "all caught": 0, "survivors": 1}, codes


def test_the_two_refusals_share_one_code(monkeypatch, capsys):
    """Uncommitted changes and an already-red selection are both "the probe did
    not run", which is the single thing a caller acts on."""
    import mutation_probe as mp

    monkeypatch.setattr(mp.subprocess, "run", lambda *a, **k: _DirtyGit())
    dirty = mp.main(["core/loop.py", "tests/test_cli.py"])

    monkeypatch.setattr(mp.subprocess, "run", lambda *a, **k: _CleanGit())
    monkeypatch.setattr(mp, "probe", lambda *a, **k: -1)
    already_red = mp.main(["core/loop.py", "tests/test_cli.py"])
    capsys.readouterr()

    assert dirty == already_red == 2


class _CleanGit:
    stdout = ""


class _DirtyGit:
    stdout = " M core/loop.py"
