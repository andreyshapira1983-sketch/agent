"""EXPERIMENTAL validator for the first physical `.qm` specimen. Not production.

Nothing in the agent's runtime imports this. It is a standalone probe answering
one question: can another physical file identify a specific semantic object
inside `main.qm`, resolve the external boundary it is linked to, and verify
that the asserted relationship is still TRUE?

The specimen asserts; this validator compares those assertions against FACTS
extracted by `scripts/qm_ps_anchor.ps1`, which uses PowerShell's own parser.
Neither half is allowed to do the other's job: the bridge never asserts, and
this file never reads the target's text.

It also walks the specimen's `certified_claims` bindings, which connect it to
claim certificates that decide their own validity by re-evaluation. A binding
carries no value: the specimen names a carrier its boundary emits and gates the
dependent state on the certificate's verdict.

Five outcomes, deliberately distinct, because the audit that produced the
specimen found that a signal firing on *any* edit cannot be counted as
behavioural evidence:

    0  GREEN        every link's properties hold and every bound state is usable
    1  BROKEN       a semantic property is FALSE -- the relationship is wrong
    2  STALE        properties still hold, but a target's bytes moved;
                    re-verification is owed, the relationship is not broken
    3  UNRESOLVABLE a specimen, entity, target, anchor or certificate could not
                    be found -- nothing was verified either way
    5  DEPENDENT_UNAVAILABLE
                    the graph is intact and a certificate REFUSED. The bound
                    state may not be read. This is the system working.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qm_gate_claim import gate

GREEN, BROKEN, STALE, UNRESOLVABLE, DEPENDENT_UNAVAILABLE = 0, 1, 2, 3, 5

# A certificate that refuses is the graph WORKING, not failing: the link is
# intact and the dependent state is simply not usable. It gets its own code so
# a refusal is never confused with a broken relationship. The mapping from a
# certificate's verdict to that decision lives in scripts/qm_gate_claim.py.

# Exit codes are not a severity order: STALE is 2 and BROKEN is 1, so `max`
# over several links would rank "target moved" above "relationship is false".
_SEVERITY = {GREEN: 0, STALE: 1, DEPENDENT_UNAVAILABLE: 2, BROKEN: 3, UNRESOLVABLE: 4}

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
    if op == "contains":
        if not isinstance(actual, list):
            return False, f"{path} is not a list ({actual!r})"
        ok = a["value"] in actual
        return ok, (f"{path} contains {a['value']!r}" if ok
                    else f"{path} does not contain {a['value']!r}; it has {actual!r}")
    return False, f"{path}: unknown operator {op!r}"


def main(argv: list[str]) -> int:
    specimens = argv[1:] or ["main.qm", "app.qm"]
    if len(specimens) > 1:
        worst_all = GREEN
        for one in specimens:
            print(f"\n########## {one} ##########")
            worst_all = _worse(worst_all, main([argv[0], one]))
        return worst_all
    specimen_path = ROOT / specimens[0]

    if not specimen_path.is_file():
        print(f"UNRESOLVABLE: no specimen at {specimen_path}")
        return UNRESOLVABLE
    try:
        specimen = json.loads(specimen_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"UNRESOLVABLE: {specimen_path.name} is not readable -- {exc}")
        return UNRESOLVABLE

    links = specimen.get("external_links") or []
    if not links and not specimen.get("producer_bindings"):
        print("UNRESOLVABLE: the specimen declares no link of any kind")
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

    worst = _worse(worst, _check_producer_bindings(specimen))
    worst = _worse(worst, _check_certified_claims(specimen))

    print({GREEN: "\nGREEN", BROKEN: "\nBROKEN", STALE: "\nSTALE",
           UNRESOLVABLE: "\nUNRESOLVABLE",
           DEPENDENT_UNAVAILABLE: "\nDEPENDENT_UNAVAILABLE"}[worst])
    return worst


def _python_facts(bridge: Path, module_file: Path, function: str) -> dict | None:
    if not bridge.is_file() or not module_file.is_file():
        return None
    proc = subprocess.run(
        [sys.executable, str(bridge), str(module_file), function],
        capture_output=True, text=True, encoding="utf-8", timeout=300, cwd=str(ROOT),
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None

def _check_producer_bindings(specimen: dict) -> int:
    """Walk bindings that assert THIS boundary produces a carrier.

    A producer binding is verified with the same discipline as an external link:
    the anchor is resolved by the language's own parser, the specimen supplies the
    assertions, and the certificate it defers to is resolved but never copied.
    """
    bindings = specimen.get("producer_bindings") or []
    if not bindings:
        return GREEN
    worst = GREEN
    for binding in bindings:
        bid = binding.get("binding_id", "?")
        print(f"\n=== producer binding {bid}: {binding.get('relationship')} ===")

        resolver = binding.get("resolver", {})
        if resolver.get("kind") != "python_ast":
            print(f"UNRESOLVABLE: no resolver for kind {resolver.get('kind')!r}")
            worst = _worse(worst, UNRESOLVABLE)
            continue
        bridge = ROOT / resolver.get("bridge", "")
        module_file = ROOT / resolver.get("module_file", "")
        if not bridge.is_file() or not module_file.is_file():
            print(f"UNRESOLVABLE: bridge or module file missing "
                  f"({resolver.get('bridge')!r}, {resolver.get('module_file')!r})")
            worst = _worse(worst, UNRESOLVABLE)
            continue

        proc = subprocess.run(
            [sys.executable, str(bridge), str(module_file), resolver.get("function", "")],
            capture_output=True, text=True, encoding="utf-8", timeout=300, cwd=str(ROOT),
        )
        try:
            facts = json.loads(proc.stdout)
        except json.JSONDecodeError:
            print("UNRESOLVABLE: the Python anchor bridge produced no facts")
            worst = _worse(worst, UNRESOLVABLE)
            continue

        if not facts.get("anchor_found"):
            print(f"UNRESOLVABLE: {resolver.get('function')!r} is not a module-level "
                  f"function in {resolver.get('module_file')}")
            worst = _worse(worst, UNRESOLVABLE)
            continue
        print(f"  anchor {resolver.get('function')!r} resolved at lines "
              f"{facts.get('anchor_first_line')}-{facts.get('anchor_last_line')}")

        broken = False
        for prop in binding.get("verified_properties", []):
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
        if broken:
            print(f"  VERDICT {bid}: BROKEN -- this boundary does not produce what "
                  f"the binding asserts")
            worst = _worse(worst, BROKEN)
            continue

        for check in binding.get("consumer_checks", []):
            cres = check.get("resolver", {})
            cfacts = _python_facts(ROOT / cres.get("bridge", ""),
                                   ROOT / cres.get("module_file", ""),
                                   cres.get("function", "__module__"))
            if cfacts is None:
                print(f"UNRESOLVABLE: regime {check.get('regime')} module "
                      f"{cres.get('module_file')!r} produced no facts")
                broken = True
                worst = _worse(worst, UNRESOLVABLE)
                break
            for prop in check.get("properties", []):
                results = [_check_assertion(a, cfacts) for a in prop.get("assertions", [])]
                failed = [why for ok, why in results if not ok]
                if failed:
                    broken = True
                    print(f"  {prop['id']} FAIL [{check.get('regime')}]: {prop['claim']}")
                    for why in failed:
                        print(f"      {why}")
                else:
                    print(f"  {prop['id']} PASS [{check.get('regime')}]: {prop['claim']}")
        if broken:
            print(f"  VERDICT {bid}: BROKEN -- a declared consumer regime no longer holds")
            worst = _worse(worst, BROKEN)
            continue

        cres = binding.get("consumer_resolver")
        if cres:
            cfacts = _python_facts(ROOT / cres.get("bridge", ""),
                                   ROOT / cres.get("module_file", ""),
                                   cres.get("function", "__module__"))
            if cfacts is None:
                print(f"UNRESOLVABLE: the consumer module {cres.get('module_file')!r} "
                      f"produced no facts")
                worst = _worse(worst, UNRESOLVABLE)
                continue
            for prop in binding.get("consumer_properties", []):
                results = [_check_assertion(a, cfacts) for a in prop.get("assertions", [])]
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
            if broken:
                print(f"  VERDICT {bid}: BROKEN -- the named consumer no longer reads it")
                worst = _worse(worst, BROKEN)
                continue

        deferred = binding.get("certified_by") or {}
        status, detail = gate(ROOT / deferred.get("certificate", ""),
                              deferred.get("claim_id"))
        print(f"  the carrier is produced; gate -> {status} ({detail})")
        if status == "USABLE":
            print(f"  VERDICT {bid}: GREEN -- production proven, state certified")
        elif status == "UNAVAILABLE":
            print(f"  VERDICT {bid}: structurally resolved, but {deferred.get('projection')} "
                  f"is UNAVAILABLE")
            worst = _worse(worst, DEPENDENT_UNAVAILABLE)
        else:
            worst = _worse(worst, UNRESOLVABLE)
    return worst


def _check_certified_claims(specimen: dict) -> int:
    """Walk the bindings that connect this specimen to claim certificates.

    A binding is not a copy. The specimen names a carrier its own boundary emits
    and delegates that carrier's certified STATE to a certificate that decides for
    itself, by re-evaluation. So this walker resolves the certificate, runs it,
    and gates the dependent state on the status it returns -- it never reads a
    value out of the binding, because there is none to read.
    """
    bindings = specimen.get("certified_claims") or []
    if not bindings:
        return GREEN
    worst = GREEN
    for binding in bindings:
        bid = binding.get("binding_id", "?")
        print(f"\n=== binding {bid}: {binding.get('relationship')} ===")

        status, detail = gate(ROOT / binding.get("certificate", ""),
                              binding.get("claim_id"))
        print(f"  gate -> {status} ({detail})")
        if status == "UNRESOLVABLE":
            print(f"UNRESOLVABLE: {detail}")
            worst = _worse(worst, UNRESOLVABLE)
            continue

        usable = status == "USABLE"
        state = binding.get("dependent_state", "<unnamed>")
        if usable:
            print(f"  VERDICT {bid}: {state} is USABLE")
        else:
            print(f"  VERDICT {bid}: {state} is UNAVAILABLE -- the specimen asserts "
                  f"nothing about it on its own")
            worst = _worse(worst, DEPENDENT_UNAVAILABLE)
    return worst


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
