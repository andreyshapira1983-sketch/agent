"""EXPERIMENTAL validator for the first physical `.qm` specimen. Not production.

Nothing in the agent's runtime imports this. It is a standalone probe answering
one question: can another physical file identify a specific semantic object
inside `main.qm`, resolve the external boundary it is linked to, and verify
that the asserted relationship is still TRUE?

The specimen asserts; this validator compares those assertions against FACTS
extracted by `scripts/qm_ps_anchor.ps1`, which uses PowerShell's own parser.
Neither half is allowed to do the other's job: the bridge never asserts, and
this file never reads the target's text.

Four outcomes, deliberately distinct, because the audit that produced the
specimen found that a signal firing on *any* edit cannot be counted as
behavioural evidence:

    0  GREEN        the link resolves and every asserted property holds
    1  BROKEN       a semantic property is FALSE -- the relationship is wrong
    2  STALE        every property still holds, but the target's bytes moved;
                    re-verification is owed, the relationship is not broken
    3  UNRESOLVABLE the specimen, the entity, the target file or the anchor
                    could not be found -- nothing was verified either way

A run that merely parses `main.qm` exits 3, not 0. Syntax is not evidence.

Usage:  python scripts/qm_link_check.py [path/to/specimen.qm]
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

GREEN, BROKEN, STALE, UNRESOLVABLE = 0, 1, 2, 3

# Exit codes are not a severity order: STALE is 2 and BROKEN is 1, so `max`
# over several links would rank "target moved" above "relationship is false".
_SEVERITY = {GREEN: 0, STALE: 1, BROKEN: 2, UNRESOLVABLE: 3}

ROOT = Path(__file__).resolve().parent.parent
_MISSING = object()


def _worse(a: int, b: int) -> int:
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


def _sha_prefix(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _extract_ast_facts(bridge: Path, target: Path, anchor_name: str) -> dict | None:
    """Run the parser bridge. Returns None if it could not be run at all."""
    exe = shutil.which("pwsh") or shutil.which("powershell")
    if exe is None:
        return None
    proc = subprocess.run(
        [exe, "-NoProfile", "-File", str(bridge),
         "-Path", str(target), "-Function", anchor_name],
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def _read_path(facts: dict, dotted: str):
    """Resolve a dotted path into the AST facts, or `_MISSING`."""
    node = facts
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _check_assertion(a: dict, facts: dict) -> tuple[bool, str]:
    path, op = a["path"], a["op"]
    actual = _read_path(facts, path)
    if actual is _MISSING:
        return False, f"{path}: the resolver produced no such fact"
    if op == "equals":
        ok = actual == a["value"]
        return ok, f"{path} = {actual!r}" + ("" if ok else f", asserted {a['value']!r}")
    if op == "at_least":
        ok = isinstance(actual, int) and actual >= a["value"]
        return ok, f"{path} = {actual!r}" + ("" if ok else f", asserted >= {a['value']!r}")
    if op == "count_of_value":
        if not isinstance(actual, list):
            return False, f"{path} is not a list ({actual!r})"
        found = sum(1 for x in actual if x == a["value"])
        ok = found == a["expected"]
        return ok, (f"{path} contains {found} x {a['value']!r}"
                    + ("" if ok else f", asserted {a['expected']}"))
    return False, f"{path}: unknown operator {op!r}"


def main(argv: list[str]) -> int:
    specimen_path = ROOT / (argv[1] if len(argv) > 1 else "main.qm")

    if not specimen_path.is_file():
        print(f"UNRESOLVABLE: no specimen at {specimen_path}")
        return UNRESOLVABLE
    try:
        specimen = json.loads(specimen_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"UNRESOLVABLE: {specimen_path.name} is not readable -- {exc}")
        return UNRESOLVABLE

    links = specimen.get("external_links") or []
    if not links:
        print("UNRESOLVABLE: the specimen declares no external link")
        return UNRESOLVABLE

    worst = GREEN
    for link in links:
        lid = link.get("link_id", "?")
        print(f"\n=== link {lid}: {link.get('relation')} ===")

        entity_id = link.get("from_entity")
        entity = (specimen.get("semantic_entities") or {}).get(entity_id)
        if entity is None:
            print(f"UNRESOLVABLE: {lid} names entity {entity_id!r}, absent from the specimen")
            worst = _worse(worst, UNRESOLVABLE)
            continue
        print(f"  entity {entity_id} resolved: {entity.get('kind')}, "
              f"evidence_status={entity.get('evidence_status')}")

        target_spec = link.get("target", {})
        target_path = ROOT / target_spec.get("file", "")
        if not target_path.is_file():
            print(f"UNRESOLVABLE: target {target_spec.get('file')!r} does not exist")
            worst = _worse(worst, UNRESOLVABLE)
            continue

        anchor = target_spec.get("anchor", {})
        resolver = link.get("resolver", {})
        if anchor.get("kind") != "powershell_function" or resolver.get("kind") != "powershell_ast":
            print(f"UNRESOLVABLE: no resolver for anchor kind {anchor.get('kind')!r}")
            worst = _worse(worst, UNRESOLVABLE)
            continue

        bridge = ROOT / resolver.get("bridge", "")
        if not bridge.is_file():
            print(f"UNRESOLVABLE: resolver bridge {resolver.get('bridge')!r} is missing")
            worst = _worse(worst, UNRESOLVABLE)
            continue

        facts = _extract_ast_facts(bridge, target_path, anchor["name"])
        if facts is None:
            print("UNRESOLVABLE: the parser bridge could not be run")
            worst = _worse(worst, UNRESOLVABLE)
            continue
        print(f"  target {target_spec.get('file')} parsed; anchor {anchor['name']!r} "
              f"at lines {facts.get('anchor_first_line')}-{facts.get('anchor_last_line')}")

        broken = False
        for prop in link.get("verified_properties", []):
            results = [_check_assertion(a, facts) for a in prop.get("assertions", [])]
            failed = [why for ok, why in results if not ok]
            if failed:
                broken = True
                print(f"  {prop['id']} FAIL: {prop['claim']}")
                for why in failed:
                    print(f"      {why}")
            else:
                print(f"  {prop['id']} PASS: {prop['claim']}")
                for _, why in results:
                    print(f"      {why}")

        recorded = target_spec.get("sha256_prefix")
        actual = _sha_prefix(target_path)
        stale = recorded != actual
        if stale:
            print(f"  FRESHNESS: STALE -- recorded {recorded}, actual {actual}")
        else:
            print(f"  FRESHNESS: current ({actual})")

        if broken:
            print(f"  VERDICT {lid}: BROKEN -- the asserted relationship is false")
            worst = _worse(worst, BROKEN)
        elif stale:
            print(f"  VERDICT {lid}: STALE -- properties hold, target moved, re-verify")
            worst = _worse(worst, STALE)
        else:
            print(f"  VERDICT {lid}: GREEN")

    print({GREEN: "\nGREEN", BROKEN: "\nBROKEN", STALE: "\nSTALE",
           UNRESOLVABLE: "\nUNRESOLVABLE"}[worst])
    return worst


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
