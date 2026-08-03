"""Broad-except audit for ``core/`` (MIR-077) — read-only classifier.

The operator's standing rule: find the ROOT first. Review rounds #283 and
#286 both found the same defect class — a broad ``except Exception`` that
swallows a failure with no journal event, making the subsystem's breakage
invisible. This script maps EVERY broad handler in ``core/`` into classes:

* ``journaled``              — the handler logs/prints something;
* ``reraise``                — the handler re-raises;
* ``log_guard``              — the TRY body is itself only journaling, so a
                               silent handler is the deliberate last-resort
                               guard (the #283/#286 inner pattern);
* ``silent_noop``            — pass/continue/break only;
* ``silent_default_return``  — a bare default return;
* ``silent_other``           — swallows and does something else.

A silent handler WITHOUT a nearby comment naming its reason is the audit's
target class. The pytest ratchet (`tests/test_except_audit_ratchet.py`) pins
the baseline so the class can only shrink.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_LOGGY = ("log", "print", "warn", "stderr")


def _is_log_call(n: ast.AST) -> bool:
    if not isinstance(n, ast.Call):
        return False
    f = n.func
    name = ""
    while isinstance(f, ast.Attribute):
        name = f.attr + "." + name if name else f.attr
        f = f.value
    if isinstance(f, ast.Name):
        name = f.id + "." + name if name else f.id
    low = name.lower()
    return any(k in low for k in _LOGGY)


def _has_log_call(node: ast.AST) -> bool:
    return any(_is_log_call(n) for n in ast.walk(node))


def classify_file(path: Path) -> list[dict]:
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    out: list[dict] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Try):
            continue
        try_only_logs = len(node.body) >= 1 and all(
            isinstance(s, ast.Expr) and _is_log_call(s.value) for s in node.body
        )
        for h in node.handlers:
            t = h.type
            broad = t is None or (
                isinstance(t, ast.Name) and t.id in ("Exception", "BaseException")
            )
            if not broad:
                continue
            if _has_log_call(h):
                kind = "journaled"
            elif any(isinstance(n, ast.Raise) for n in ast.walk(h)):
                kind = "reraise"
            elif try_only_logs:
                kind = "log_guard"
            elif all(isinstance(s, (ast.Pass, ast.Continue, ast.Break)) for s in h.body):
                kind = "silent_noop"
            elif len(h.body) == 1 and isinstance(h.body[0], ast.Return):
                kind = "silent_default_return"
            else:
                kind = "silent_other"
            span = lines[h.lineno - 1 : min(h.body[0].lineno + 1, len(lines))]
            commented = any("#" in line for line in span)
            out.append({
                "file": path.relative_to(REPO).as_posix(),
                "line": h.lineno,
                "kind": kind,
                "commented": commented,
            })
    return out


def audit() -> list[dict]:
    rows: list[dict] = []
    for path in sorted((REPO / "core").glob("*.py")):
        rows.extend(classify_file(path))
    return rows


def unjustified_silent(rows: list[dict]) -> list[dict]:
    """The audit's target class: swallows silently, names no reason."""
    return [
        r for r in rows if r["kind"].startswith("silent") and not r["commented"]
    ]


def main() -> int:
    rows = audit()
    from collections import Counter

    kinds = Counter(r["kind"] for r in rows)
    bad = unjustified_silent(rows)
    print(f"broad except handlers in core/: {len(rows)}")
    for kind, count in sorted(kinds.items()):
        print(f"  {kind}: {count}")
    print(f"silent WITHOUT a stated reason (audit target): {len(bad)}")
    per_file = Counter(r["file"] for r in bad)
    for file, count in per_file.most_common():
        print(f"  {file}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
