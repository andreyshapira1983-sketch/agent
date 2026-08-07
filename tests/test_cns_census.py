"""Census of the central nervous system: its nodes, its edges, what is proven.

The perimeter is `core/loop.py` and its mixins — the code deciding WHEN a
signal goes WHERE. Organs it talks to (detectors, planner, memory, verifier,
tools) sit outside it; their insides are taken apart separately and later.

WHAT THIS PROVES, AND WHAT IT DOES NOT. The snapshot in `knowledge/maps/cns_census.json`
is machine-discovered under one rule (`_collect_nodes` / `_collect_edges`
below). It proves completeness RELATIVE TO THAT RULE and nothing else. A
function that takes part in the cycle without matching the rule — dispatched
through `getattr`, held in a table, reached via a collaborator instead of
`self` — is invisible here, and these ratchets stay green while the map goes
stale. The rule itself is therefore UNPROVEN, and saying so is the point.

TWO CENSUSES, NOT ONE. A node count cannot hold "all transitions are known":
a new edge between two existing nodes changes the nervous system without
changing the count. Both are guarded.

WHAT IS STILL UNGUARDED. Neither census notices a node whose MEANING changed
while its name, its callers and its edges stayed put. Guarding against that
needs a fingerprint of the normalised implementation, which does not exist
yet — so `certified` cannot yet mean "still the same system".

PROPERTIES, NOT ONE STAMP. A node can have a proven caller, a proven output
and entirely unproven failure semantics, so status is recorded per property.
Only established properties are listed; anything absent is UNPROVEN by
convention. That convention is exactly why the node itself must always be
present, even with an empty list — a missing entry would be indistinguishable
from "there is nothing there".

Moving a property into the snapshot is a claim like any other: name the test
and the mutation it catches, in the commit that adds it.
"""
from __future__ import annotations

import ast
import json
import pathlib

CORE = pathlib.Path(__file__).resolve().parent.parent / "core"
SNAPSHOT = CORE.parent / "knowledge" / "maps" / "cns_census.json"

#: Properties a node can have proven about it, from the operator's definition
#: of DONE (2026-08-07). Absence means UNPROVEN — there is no N/A.
PROPERTIES = frozenset({
    "discovery",          # found by the rule and named in the snapshot
    "callers",            # every caller known, not assumed from the name
    "inputs",             # what it reads, including off `self`
    "outputs",            # what it returns AND what it mutates in place
    "control_semantics",  # why it lets a signal through or stops it
    "mutations",          # state it changes
    "owner",              # who owns the state it touches
    "failure_semantics",  # failure / cancel / retry paths through it
    "behavioral_test",    # a test asserting the consequence, not the shape
    "mutation_bite",      # that test demonstrably reddens on the defect
})

#: The perimeter as a number. A twentieth mixin widens the nervous system
#: without widening anything that says so.
EXPECTED_MIXINS = 19


def _load() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _collect_nodes() -> dict[str, int]:
    """THE DISCOVERY RULE for nodes: module functions and class methods.

    Nested helpers are excluded deliberately — they are detail of the node
    owning them. Dunders are plumbing. This rule is what the census is
    complete relative to; it is not proven to find everything acting as a node
    at runtime.
    """
    found: dict[str, int] = {}
    for path in sorted(CORE.glob("loop*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("__"):
                found[f"{path.name}::{node.name}"] = node.end_lineno - node.lineno + 1
            elif isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, ast.FunctionDef) and not member.name.startswith("__"):
                        key = f"{path.name}::{node.name}.{member.name}"
                        found[key] = member.end_lineno - member.lineno + 1
    return found


def _collect_edges() -> set[tuple[str, str]]:
    """THE DISCOVERY RULE for edges: a `self.<name>` call inside a node body.

    Blind to dispatch through `getattr`, to callbacks handed to another
    object, and to anything reached via a collaborator rather than `self`.
    Same caveat: complete relative to the rule, not to reality.
    """
    owner: dict[str, str] = {}
    bodies: dict[str, ast.FunctionDef] = {}
    for path in sorted(CORE.glob("loop*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("__"):
                bodies[f"{path.name}::{node.name}"] = node
            elif isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, ast.FunctionDef) and not member.name.startswith("__"):
                        key = f"{path.name}::{node.name}.{member.name}"
                        bodies[key] = member
                        owner.setdefault(member.name, key)
    found: set[tuple[str, str]] = set()
    for name, body in bodies.items():
        for sub in ast.walk(body):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "self"):
                target = owner.get(sub.func.attr)
                if target and target != name:
                    found.add((name, target))
    return found


def test_every_discovered_node_is_in_the_census() -> None:
    """A new node must be classified, not silently absent from the map."""
    unlisted = sorted(set(_collect_nodes()) - set(_load()["nodes"]))
    assert not unlisted, (
        f"nodes with no entry in the census: {unlisted}. An empty property "
        "list is the honest default; omission is not."
    )


def test_the_census_names_no_node_that_is_gone() -> None:
    stale = sorted(set(_load()["nodes"]) - set(_collect_nodes()))
    assert not stale, f"in the census but no longer in the code: {stale}"


def test_every_discovered_edge_is_in_the_census() -> None:
    """A transition between two KNOWN nodes still changes the system."""
    known = {tuple(e) for e in _load()["edges"]}
    fresh = sorted(_collect_edges() - known)
    assert not fresh, f"transitions absent from the census: {fresh}"


def test_the_census_names_no_transition_that_is_gone() -> None:
    known = {tuple(e) for e in _load()["edges"]}
    stale = sorted(known - _collect_edges())
    assert not stale, f"in the census but no longer called: {stale}"


def test_every_listed_property_is_a_known_one() -> None:
    bad = {n: sorted(set(p) - PROPERTIES)
           for n, p in _load()["nodes"].items() if set(p) - PROPERTIES}
    assert not bad, f"unknown property names: {bad}"


def test_the_perimeter_has_not_quietly_moved() -> None:
    mixins = {p.name for p in CORE.glob("loop_*.py")}
    assert len(mixins) == EXPECTED_MIXINS, (
        f"perimeter changed: {len(mixins)} mixin files, expected "
        f"{EXPECTED_MIXINS}; update docs/PROJECT_MAP.ru.md in the same commit"
    )
