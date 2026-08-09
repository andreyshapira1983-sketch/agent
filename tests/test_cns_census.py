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
import re

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


def _method_owners() -> dict[str, set[str]]:
    """Every node a bare method name could refer to, kept as a SET.

    The set is the point. Resolving `self.foo()` needs one owner; when the
    perimeter holds two `foo`, the rule has no way to tell which one the call
    reaches, and picking either invents a transition that may not exist while
    hiding one that does.
    """
    owners: dict[str, set[str]] = {}
    for path in sorted(CORE.glob("loop*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, ast.FunctionDef) and not member.name.startswith("__"):
                        key = f"{path.name}::{node.name}.{member.name}"
                        owners.setdefault(member.name, set()).add(key)
    return owners


def _collect_edges() -> set[tuple[str, str]]:
    """THE DISCOVERY RULE for edges: a `self.<name>` call inside a node body.

    Blind to dispatch through `getattr`, to callbacks handed to another
    object, and to anything reached via a collaborator rather than `self`.
    Same caveat: complete relative to the rule, not to reality.

    FAIL-CLOSED ON AMBIGUITY. An ambiguous name yields NO edge here, and
    `test_no_method_name_has_two_owners` reddens instead. Deciding between
    two candidates needs MRO, override and mixin semantics this rule does not
    have; a silently chosen owner would enter the snapshot as a proven
    transition. Better a census that stops than a map that lies.
    """
    owners = _method_owners()
    bodies: dict[str, ast.FunctionDef] = {}
    for path in sorted(CORE.glob("loop*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("__"):
                bodies[f"{path.name}::{node.name}"] = node
            elif isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, ast.FunctionDef) and not member.name.startswith("__"):
                        bodies[f"{path.name}::{node.name}.{member.name}"] = member
    found: set[tuple[str, str]] = set()
    for name, body in bodies.items():
        for sub in ast.walk(body):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "self"):
                candidates = owners.get(sub.func.attr, set())
                if len(candidates) != 1:
                    continue
                target = next(iter(candidates))
                if target != name:
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


def test_no_method_name_has_two_owners() -> None:
    """The edge rule may not guess which of two same-named methods is called.

    Today the perimeter has none, and one reason is invisible until you look:
    hosts declare shared contracts under `if TYPE_CHECKING`, which the class
    walk skips. Move one such declaration out of that block, or add a real
    second `_sensor_failed`, and the name becomes ambiguous. That is the
    moment to define resolution semantics — deliberately, in its own commit —
    not the moment for the census to pick a winner.
    """
    ambiguous = {name: sorted(owners)
                 for name, owners in _method_owners().items() if len(owners) > 1}
    assert not ambiguous, (
        f"a method name owned by more than one node: {ambiguous}. Edges for it "
        "are NOT being recorded; define how `self.<name>` resolves before the "
        "census claims to know where the signal goes."
    )


def test_every_listed_property_is_a_known_one() -> None:
    bad = {n: sorted(set(p) - PROPERTIES)
           for n, p in _load()["nodes"].items() if set(p) - PROPERTIES}
    assert not bad, f"unknown property names: {bad}"


def test_the_map_states_the_numbers_the_census_computes() -> None:
    """Counts in the map are DERIVED. Kept by hand, they rot within days.

    They did: `46ba5ca` gave `_execute_step` its first properties, 14 became
    15, and five later commits touched the map without noticing. Nothing was
    red, because every ratchet guarded the snapshot and none guarded the prose
    about it.

    The numbers stay in the map on purpose — a person reading the map needs
    them there — so the machine is given the job of not letting them go stale.
    """
    census = _load()
    text = (CORE.parent / "docs" / "PROJECT_MAP.ru.md").read_text(encoding="utf-8")
    with_properties = sum(1 for props in census["nodes"].values() if props)
    expected = {
        "nodes": len(census["nodes"]),
        "edges": len(census["edges"]),
        "with_properties": with_properties,
        "without": len(census["nodes"]) - with_properties,
    }
    topology = re.search(r"находит \*\*(\d+) узл(?:а|ов) и (\d+) рёбер\*\*", text)
    status = re.search(
        r"\*\*(\d+) узлов из (\d+)\*\* имеют хотя бы одно доказанное свойство, "
        r"\*\*(\d+)\*\* — ни одного", text)
    assert topology and status, (
        "the sentences carrying the census numbers are gone from "
        "docs/PROJECT_MAP.ru.md; this ratchet reads them by shape, so rewording "
        "them means updating it in the same commit"
    )
    stated = {
        "nodes": int(topology.group(1)),
        "edges": int(topology.group(2)),
        "with_properties": int(status.group(1)),
        "without": int(status.group(3)),
    }
    assert int(status.group(2)) == expected["nodes"], (
        f"the map says {status.group(1)} of {status.group(2)} nodes; the census "
        f"holds {expected['nodes']}"
    )
    assert stated == expected, (
        f"docs/PROJECT_MAP.ru.md states {stated}, the census computes "
        f"{expected}. The census is the source; correct the prose."
    )


def test_the_perimeter_has_not_quietly_moved() -> None:
    mixins = {p.name for p in CORE.glob("loop_*.py")}
    assert len(mixins) == EXPECTED_MIXINS, (
        f"perimeter changed: {len(mixins)} mixin files, expected "
        f"{EXPECTED_MIXINS}; update docs/PROJECT_MAP.ru.md in the same commit"
    )
