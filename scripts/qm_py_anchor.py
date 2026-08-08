"""EXPERIMENTAL parser bridge for Python anchors. Not production.

The PowerShell side of this lab resolves its anchor with PowerShell's own parser,
on the ruling that a semantic link may not depend on a parser weaker than the
language it observes. This is the same instrument for Python: it resolves a
function by name with `ast` and emits FACTS about what that function emits.

It asserts nothing. Every assertion lives in a `.qm` file; `scripts/qm_link_check.py`
is what compares the two.

Facts emitted, as JSON on stdout:
    parse_ok            the module parsed
    anchor_found        the named function exists at module level
    anchor_first_line / anchor_last_line
    logged_events       every string-literal event name logged inside the anchor
    payload_keys        for each such event, the literal keys of its payload dict

Usage:  python scripts/qm_py_anchor.py <module.py> <function>
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


def _string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(json.dumps({"parse_ok": False, "error": "usage: <module.py> <function>"}))
        return 0
    path, want = Path(argv[1]), argv[2]
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        print(json.dumps({"parse_ok": False, "error": str(exc)[:200]}))
        return 0

    getattr_names = sorted({
        name for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "getattr"
        and len(node.args) >= 2
        for name in (_string(node.args[1]),) if name is not None
    })

    # Attributes read as `self.NAME` anywhere in the module. A consumer that
    # reads a field off itself is invisible to the getattr fact above, and the
    # first version of the app.qm binding was silently blind to exactly that.
    self_attribute_reads = sorted({
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
        and isinstance(node.value, ast.Name) and node.value.id == "self"
    })

    # Per-FUNCTION getattr names. Module-wide membership was proved false
    # protection: two regimes reading the same field from the same module make a
    # module-scoped assertion survive the removal of either one.
    getattr_names_by_function = {
        fn.name: sorted({
            nm for sub in ast.walk(fn)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
            and sub.func.id == "getattr" and len(sub.args) >= 2
            for nm in (_string(sub.args[1]),) if nm is not None
        })
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    anchor = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == want:
            anchor = node
            break
    if anchor is None:
        print(json.dumps({"parse_ok": True, "anchor_found": False,
                          "getattr_names": getattr_names,
                          "getattr_names_by_function": getattr_names_by_function,
                          "self_attribute_reads": self_attribute_reads}))
        return 0

    events: list[str] = []
    payload_keys: dict[str, list[str]] = {}
    for node in ast.walk(anchor):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        name = _string(node.args[0])
        if name is None:
            continue
        # A logging call is `something.log("event", {...})`. The bridge does not
        # care what the receiver is called -- naming it here would put a fact in
        # the instrument instead of in the .qm file.
        payload = node.args[1]
        if not isinstance(payload, ast.Dict):
            continue
        events.append(name)
        payload_keys[name] = sorted(
            k for k in (_string(key) for key in payload.keys) if k is not None
        )

    # Calls inside the anchor whose arguments are all literals, keyed by callee
    # name. This is what makes "two projections share one source" checkable: if
    # a field is read twice from one variable with one default, both reads show
    # up here identically, and changing either one changes the fact.
    literal_calls: dict[str, list[list[object]]] = {}
    for node in ast.walk(anchor):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.keywords or not node.args:
            continue
        if not all(isinstance(a, ast.Constant) for a in node.args):
            continue
        literal_calls.setdefault(node.func.id, []).append([a.value for a in node.args])

    print(json.dumps({
        "parse_ok": True,
        "anchor_found": True,
        "getattr_names": getattr_names,
        "getattr_names_by_function": getattr_names_by_function,
        "self_attribute_reads": self_attribute_reads,
        "literal_calls": literal_calls,
        "anchor_first_line": anchor.lineno,
        "anchor_last_line": getattr(anchor, "end_lineno", anchor.lineno),
        "logged_events": sorted(set(events)),
        "payload_keys": payload_keys,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
