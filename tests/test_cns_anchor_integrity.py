"""A map anchor must point at the thing it names, and a writer set must be whole.

`tests/test_cns_model.py` already checks that every `file:line` anchor resolves
to a line that exists. Its own docstring admits what it cannot do: tell an
anchor that drifted onto a different line from one that did not. This file
closes that gap and one more like it.

RULE 1 — AN ANCHOR NAMES ITS CARRIER. The anchored line must mention the carrier
it is filed under, or be the header of a `def`/`class` whose source names it. A
line number that survives a refactor while its content walks away is worse than
a missing anchor: it reads as evidence.

RULE 2 — A WRITER SET IS COMPLETE. Every site that mutates a carrier must appear
in that carrier's `writers`. A record may say "UNPROVEN"; it may not say
"PROVEN" about three of eleven. Derivation is declared per carrier below, not
guessed, because the three carriers have three different transports: a list, a
thread-local slot, and a field nothing touches.

WHAT BOTH RULES FOUND WHEN FIRST RUN, on the map as it stood:

  * four drifted anchors on `failure_history`, all in `core/loop_attempt.py`,
    all off by exactly the 24 lines that commit a990e48 removed from that file
    when it deleted the planner cache. One pointed at a blank line, one at a
    lone `)`, one at `action_spec={` inside a different method entirely. The
    commit updated census counts and the shape ratchet; it did not update these.

  * `_step_trigger_tls` recorded three writers where eleven exist, with
    `properties.writers` reading PROVEN. The three are exactly the
    `policy_blocked` family — the one path C08 had certified. The eight missing
    ones carry `verify_failed`, `web_empty`, `timeout`, `injection_blocked`,
    `unknown` twice and two variable codes, and every one of those codes is
    exercised by tests, so they are live sites, not dead code. Checked against
    history: eleven existed at b4017bb, the commit that wrote "three", and
    `core/loop_step_execution.py` has not changed since. The record was not
    stale. It was wrong when written.

WHAT THIS FILE DOES NOT PROVE. That the carriers listed are all the carriers, or
that a named consumer really consumes. It proves that what the map does say
about writers and anchors matches the code it points at.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_MODEL = _ROOT / "knowledge" / "maps" / "cns_model.json"
_PRODUCTION = ("core", "cli", "app", "api", "tools")


def _model() -> dict:
    return json.loads(_MODEL.read_text(encoding="utf-8"))


def _production_files() -> list[Path]:
    files = [p for d in _PRODUCTION for p in (_ROOT / d).rglob("*.py")]
    files += [_ROOT / "agent_tick.py", _ROOT / "main.py"]
    return [p for p in files if p.is_file()]


def _anchors(carrier: str, record: dict) -> list[tuple[str, str, dict]]:
    """(role, `path:line`, entry) for every anchor, whatever shape it is in.

    Entries appear both as dicts with a `site` key and as bare strings, and a
    bare string may carry a suffix (`...:110 (consume)`). The suffix form is why
    the existing guard skips these silently: `tail.isdigit()` is False, so the
    anchor is never checked at all.
    """
    out: list[tuple[str, str, dict]] = []
    for role in ("writers", "readers"):
        for entry in record.get(role, []) or []:
            if isinstance(entry, dict):
                anchor = entry.get("site", "")
                meta = entry
            else:
                anchor = str(entry).split(" ", 1)[0]
                meta = {}
            if ":" in anchor:
                out.append((role, anchor, meta))
    return out


def _line_at(anchor: str) -> tuple[Path, int, str, str]:
    head, _, tail = anchor.rpartition(":")
    path = _ROOT / head
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    number = int(tail)
    return path, number, (lines[number - 1] if 0 < number <= len(lines) else ""), source


def _names_carrier(carrier: str, line: str, number: int, source: str) -> bool:
    if carrier in line:
        return True
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.lineno == number):
            return carrier in (ast.get_source_segment(source, node) or "")
    return False


def _carriers() -> list[str]:
    return sorted(_model()["state_carriers"])


@pytest.mark.parametrize("carrier", _carriers())
def test_every_anchor_names_the_carrier_it_is_filed_under(carrier: str) -> None:
    record = _model()["state_carriers"][carrier]
    broken = []
    for role, anchor, meta in _anchors(carrier, record):
        path, number, line, source = _line_at(anchor)
        if not path.exists():
            broken.append(f"{role} {anchor}: no such file")
            continue
        if not _names_carrier(carrier, line, number, source):
            broken.append(f"{role} {anchor}: line reads {line.strip()[:56]!r}")
            continue
        call = meta.get("call")
        if call and f".{call}(" not in line:
            broken.append(f"{role} {anchor}: record says .{call}(), line reads "
                          f"{line.strip()[:48]!r}")
    assert not broken, (
        f"anchors filed under {carrier} that no longer describe their site: {broken}"
    )


def _writes_to_thread_local_slot(carrier: str) -> set[str]:
    """Assignments of a value INTO the slot. `= None` is the consume, not a write."""
    found: set[str] = set()
    for path in _production_files():
        source = path.read_text(encoding="utf-8")
        if carrier not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Assign) or isinstance(node.value, ast.Constant):
                continue
            for target in node.targets:
                if (isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == carrier):
                    found.add(f"{path.relative_to(_ROOT).as_posix()}:{node.lineno}")
    return found


def _mutations_of_list_field(carrier: str) -> set[str]:
    """`<anything>.<carrier>.append(...)` and friends — the calls that change it."""
    mutators = {"append", "extend", "insert", "remove", "pop", "clear"}
    found: set[str] = set()
    for path in _production_files():
        source = path.read_text(encoding="utf-8")
        if carrier not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr in mutators
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr == carrier):
                found.add(f"{path.relative_to(_ROOT).as_posix()}:{node.lineno}")
    return found


#: How each carrier's write sites are derived. Declared rather than inferred:
#: the three carriers have three transports and one rule would fit none of them.
_WRITE_RULES = {
    "failure_history": _mutations_of_list_field,
    "_step_trigger_tls": _writes_to_thread_local_slot,
    "_last_step_failure": _writes_to_thread_local_slot,
}


@pytest.mark.parametrize("carrier", sorted(_WRITE_RULES))
def test_the_writer_set_names_every_site_that_mutates_the_carrier(carrier: str) -> None:
    record = _model()["state_carriers"][carrier]
    recorded = {anchor for role, anchor, _ in _anchors(carrier, record)
                if role == "writers"}
    actual = _WRITE_RULES[carrier](carrier)
    missing = sorted(actual - recorded)
    assert not missing, (
        f"{carrier} is mutated at sites the map does not name: {missing}. "
        f"The record names {len(recorded)}; the code has {len(actual)}. A partial "
        "walk may be recorded as partial — it may not be recorded as the set."
    )


def test_the_derivation_can_actually_find_something() -> None:
    """A census that finds nothing would pass every completeness check above."""
    assert len(_writes_to_thread_local_slot("_step_trigger_tls")) >= 11
    assert len(_mutations_of_list_field("failure_history")) == 4
    assert _writes_to_thread_local_slot("_last_step_failure") == set()


def test_a_writer_set_may_not_be_proven_while_it_is_incomplete() -> None:
    """The failure that motivated rule 2: PROVEN over three of eleven."""
    model = _model()
    for carrier, rule in _WRITE_RULES.items():
        record = model["state_carriers"][carrier]
        verdict = (record.get("properties") or {}).get("writers")
        if verdict != "PROVEN":
            continue
        recorded = {a for role, a, _ in _anchors(carrier, record) if role == "writers"}
        assert not (rule(carrier) - recorded), (
            f"{carrier}.properties.writers says PROVEN over an incomplete set"
        )
