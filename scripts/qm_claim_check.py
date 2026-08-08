"""EXPERIMENTAL validator for ONE claim certificate. Not production, not a schema.

The property under test is AUTHORITY: the semantic decisions live in the `.qm`
file and are executed here, not duplicated here. This file knows *how* to run an
evaluation, bind evidence to it, resolve environment sources and apply an ordered
rule list. It does not know which command, which carrier, which event, which
projection, which environment keys, or in what precedence -- all of that is read.
Change a fact in the certificate and this program behaves differently.

Three checks, in order, because they fail differently:

  1. EVIDENCE BINDING -- the observation must come from the evaluation this run
     launched, and must be the record the certificate names. A workspace can
     already hold journals from earlier runs; reading the wrong one would make the
     verdict a coincidence.
  2. DEPENDENCY MODEL -- the certificate's ordered rules predict a value before
     the observation is compared. If prediction and observation disagree, the
     CERTIFICATE is defective, and that is reported even when the claim holds.
  3. THE CLAIM -- only then is the observed value compared with the expected one.

Exit codes follow the certificate's own vocabulary:
    0 VALID   1 INVALID   2 OUT_OF_DOMAIN   3 PRECEDENCE_VIOLATED   4 UNREADABLE

Usage:  python scripts/qm_claim_check.py knowledge/quantum/c2.claim.qm [--workspace DIR]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

VALID, INVALID, OUT_OF_DOMAIN, PRECEDENCE_VIOLATED, UNREADABLE = 0, 1, 2, 3, 4
ROOT = Path(__file__).resolve().parent.parent
_ABSENT = object()


def _dotenv(workspace: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    env_file = workspace / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def _resolve_env(cert: dict, workspace: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Build the effective environment in the ORDER the certificate declares."""
    sources = cert["environment_resolution"]["sources"]
    effective: dict[str, str] = {}
    origin: dict[str, str] = {}
    for spec in reversed(sources):           # lowest precedence first, so the
        if spec["source"] == "process_env":  # highest overwrites it
            table = dict(os.environ)
        elif spec["source"] == "workspace_dotenv":
            table = _dotenv(workspace)
        else:
            continue
        for k, v in table.items():
            effective[k] = v
            origin[k] = spec["id"]
    return effective, origin


def _predict(cert: dict, env: dict[str, str]) -> tuple[str, object]:
    """Apply the certificate's ordered rules. First match wins."""
    for rule in cert["dependency_model"]["rules"]:
        when = rule.get("when", {})
        if "always" in when:
            hit = bool(when["always"])
        elif "key_present" in when:
            hit = when["key_present"] in env
        elif "key_equals" in when:
            hit = all(env.get(k) == v for k, v in when["key_equals"].items())
        else:
            hit = False
        if not hit:
            continue
        src = rule["value_from"]
        if "literal" in src:
            return rule["id"], src["literal"]
        value = env.get(src["key"], src.get("default", _ABSENT))
        return rule["id"], value
    return "<no rule matched>", _ABSENT


def _dig(record: object, path: list[str]) -> object:
    node = record
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return _ABSENT
        node = node[part]
    return node


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("UNREADABLE: no certificate given")
        return UNREADABLE
    cert_path = Path(argv[1])
    try:
        cert = json.loads(cert_path.read_text(encoding="utf-8"))
        obs = cert["observation"]
        expected = cert["claim"]["expected"]
        command = cert["evaluation_domain"]["command"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"UNREADABLE: {cert_path} -- {exc}")
        return UNREADABLE

    workspace = (Path(argv[argv.index("--workspace") + 1]).resolve()
                 if "--workspace" in argv else Path(tempfile.mkdtemp()) / "ws")
    workspace.mkdir(parents=True, exist_ok=True)

    env, origin = _resolve_env(cert, workspace)
    rule_id, predicted = _predict(cert, env)

    # Evidence binding, part 1: what existed BEFORE this evaluation.
    before = set(workspace.glob(obs["carrier_glob"]))
    proc = subprocess.run(
        [sys.executable, *command, "--workspace", str(workspace)],
        capture_output=True, text=True, encoding="utf-8", timeout=600, cwd=str(ROOT),
    )
    after = set(workspace.glob(obs["carrier_glob"]))
    fresh = sorted(after - before)

    print(f"  claim        : {cert['claim_id']} -- expected {expected!r}")
    print(f"  workspace    : {workspace}")
    print(f"  carriers     : {len(before)} before, {len(after)} after, {len(fresh)} new")
    print(f"  predicted    : {predicted!r} via rule {rule_id}"
          + (f" (AGENT_MODEL from {origin.get('AGENT_MODEL')})" if "AGENT_MODEL" in env else ""))

    if obs.get("bind_to_current_evaluation", True) and len(fresh) != 1:
        print(f"\nOUT_OF_DOMAIN: evidence cannot be bound to this evaluation -- "
              f"expected exactly 1 new carrier, found {len(fresh)} "
              f"(process exited {proc.returncode})")
        return OUT_OF_DOMAIN

    source = fresh[0] if fresh else (sorted(after)[0] if after else None)
    if source is None:
        print("\nOUT_OF_DOMAIN: no carrier at all")
        return OUT_OF_DOMAIN

    wanted_event = obs.get("require_event")
    record = None
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if wanted_event is None or row.get("event") == wanted_event:
            record = row
            break
    if record is None:
        print(f"\nOUT_OF_DOMAIN: {source.name} holds no record with event "
              f"{wanted_event!r}")
        return OUT_OF_DOMAIN

    observed = _dig(record, obs["projection_path"])
    if observed is _ABSENT:
        print(f"\nOUT_OF_DOMAIN: the {wanted_event!r} record has no "
              f"{'.'.join(obs['projection_path'])}")
        return OUT_OF_DOMAIN

    print(f"  evidence     : {source.name}, event {wanted_event!r}")
    print(f"  observed     : {observed!r}")

    if predicted is not _ABSENT and observed != predicted:
        print(f"\nPRECEDENCE_VIOLATED: rule {rule_id} predicted {predicted!r}, "
              f"observed {observed!r} -- the certificate's dependency model is wrong"
              + (", and the claim is false as well" if observed != expected else
                 ", while the claim itself still holds"))
        return PRECEDENCE_VIOLATED

    if observed == expected:
        print(f"\nVALID (active dependency this evaluation: {rule_id})")
        return VALID
    print(f"\nINVALID: expected {expected!r}, observed {observed!r} via {rule_id}")
    return INVALID


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
