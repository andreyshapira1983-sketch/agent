"""Census of where the nervous system's state is created.

WHY THIS IS A SEPARATE CENSUS. The node rule skips dunders as plumbing, so
`AgentLoopInit.__init__` — creating 63 fields (64 until the planner cache was removed, 2026-08-08), the only place the
loop's state comes into being — is invisible to `tests/test_cns_census.py`.
Making it a 75th ordinary node would be the wrong repair: a constructor is a
different KIND of surface, and the questions asked of it (lifetime, origin,
ownership) are not the questions asked of a node.

WHAT IT PROVES. That every field is classified, that the counts in
`knowledge/maps/cns_model.json` still match what the rule finds, and that a
newly dead field cannot appear unnoticed.

WHAT IT DOES NOT PROVE. Ownership. Protocol item 6 is explicit: an assignment
site proves where a value is created or injected, never who owns it. The model
therefore records `owner: UNPROVEN` for all 63, and this file does not compute
one.

THE RULE'S OWN BLIND SPOTS, stated because they cost two wrong answers already:
attribute names assembled at runtime, `vars(self)` traversal, and access through
a collaborator holding the loop are all invisible here. An earlier version also
counted `self.X` in unrelated classes (making `llm`, `log`, `policy` look
per-run) and ignored `getattr(self, "X")` (making `durable_writes` and
`experience_retrieval` look dead — both are live).
"""
from __future__ import annotations

import ast
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORE = ROOT / "core"
MODEL = ROOT / "knowledge" / "maps" / "cns_model.json"

_SEARCH_DIRS = ("core", "cli", "api", "app", "tools", "tests", "scripts")


def _fields() -> set[str]:
    """THE DISCOVERY RULE for state: `self.X = ...` inside the constructor."""
    tree = ast.parse((CORE / "loop_init.py").read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name)
                        and tgt.value.id == "self"):
                    found.add(tgt.attr)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Attribute)
            and isinstance(node.target.value, ast.Name)
            and node.target.value.id == "self"
        ):
            found.add(node.target.attr)
    return found


def _uses(tree: ast.AST, fields: set[str]) -> tuple[set[str], set[str]]:
    """Written and read names, counting BOTH `self.X` and getattr(self, "X")."""
    written: set[str] = set()
    read: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "self" and node.attr in fields):
            (written if isinstance(node.ctx, ast.Store) else read).add(node.attr)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("getattr", "setattr", "hasattr")
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "self"
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in fields):
            (written if node.func.id == "setattr" else read).add(node.args[1].value)
    return written, read


def _classify() -> dict[str, list[str]]:
    fields = _fields()
    inner_w: set[str] = set()
    inner_r: set[str] = set()
    for path in sorted(CORE.glob("loop*.py")):
        if path.name == "loop_init.py":
            continue
        w, r = _uses(ast.parse(path.read_text(encoding="utf-8")), fields)
        inner_w |= w
        inner_r |= r

    outer: set[str] = set()
    for directory in _SEARCH_DIRS:
        for path in (ROOT / directory).rglob("*.py"):
            if "__pycache__" in str(path) or path.name.startswith("loop"):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in fields:
                    outer.add(node.attr)
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in ("getattr", "setattr", "hasattr")
                        and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant)
                        and node.args[1].value in fields):
                    outer.add(node.args[1].value)

    untouched = fields - inner_w - inner_r
    return {
        "per_run": sorted(inner_w),
        "read_only_in_perimeter": sorted(inner_r - inner_w),
        "outside_only": sorted(untouched & outer),
        "dead": sorted(untouched - outer),
    }


def _model_section() -> dict:
    return json.loads(MODEL.read_text(encoding="utf-8"))["state_initialization"]


def test_the_field_count_is_what_the_model_states() -> None:
    stated = _model_section()["_root"]["self_fields"]
    assert len(_fields()) == stated, (
        f"the constructor now creates {len(_fields())} fields, the model says "
        f"{stated}. A new field is new state — classify it, do not adjust the number"
    )


def test_every_lifetime_class_matches_the_model() -> None:
    found = _classify()
    stated = _model_section()["by_lifetime"]
    mismatch = {
        name: {"model": stated[name]["count"], "found": len(found[name])}
        for name in found if stated[name]["count"] != len(found[name])
    }
    assert not mismatch, f"lifetime classes drifted: {mismatch}; members found: {found}"


def test_no_field_became_dead_unnoticed() -> None:
    """A field nobody touches is either a defect or a deletion waiting to happen.

    Two are known: `_CompensationPlanCls` and `_last_step_failure`. The second
    is worse than unused — `core/replan.py:97` still documents it as the slot
    every step failure passes through, while the real carrier is a module-level
    thread-local. A third appearing must be seen, not absorbed.
    """
    stated = set(_model_section()["by_lifetime"]["dead"]["fields"])
    found = set(_classify()["dead"])
    assert found == stated, (
        f"dead fields changed: newly dead {sorted(found - stated)}, "
        f"no longer dead {sorted(stated - found)}"
    )


def test_ownership_is_not_claimed_for_any_field() -> None:
    """Protocol item 6: `self.X = ...` proves a site, never an owner."""
    section = _model_section()
    assert section["owner"] == "UNPROVEN for all 63", (
        "ownership was recorded without an experiment proving it; an "
        "assignment site is not an owner (operator's map, model error 4)"
    )
