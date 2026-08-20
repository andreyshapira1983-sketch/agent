"""ONE operation: turn a claim certificate into a gate status for a bound state.

First file built under the one-operation rule. It holds exactly one behavioral
callable, `gate`. The `__main__` block below is an entry shim, not a second
operation: it parses two arguments and prints what `gate` returned.

    input / preconditions   a certificate path, and the claim id the caller
                            expects that certificate to carry
    operation               resolve the certificate, check its identity, and
                            re-evaluate it -- never read a cached verdict
    output / carrier        one status: USABLE, UNAVAILABLE or UNRESOLVABLE
    named consumer          scripts/qm_link_check.py, in both the certified-claim
                            walk and the producer-binding deferral

Why it exists as its own file: the two consumers above each had their own copy of
this logic, so a change to the gate had two places to go wrong and the graph had
no single answer to "may this state be used?". Extracting it makes the gate one
observable decision with one carrier.

The three statuses are not interchangeable.

    USABLE        the certificate mechanism ran and established the state.
    UNAVAILABLE   the mechanism RAN TO COMPLETION and did not establish a usable
                  state in this evaluation. That covers a false claim and an
                  evaluation outside the certificate's declared domain: both are
                  trustworthy answers about the state. It does NOT mean "the claim
                  was false", and it is never used when the mechanism itself was
                  unsound.
    UNRESOLVABLE  no trustworthy verdict exists. The certificate or its identity is
                  missing, the validator did not run or did not declare a verdict,
                  its declaration disagrees with its exit code, the certificate is
                  unreadable, or the certificate's own dependency model was
                  contradicted by the observation. The last case is a defect of the
                  CERTIFICATE, not a refusal by it, and collapsing it into
                  UNAVAILABLE would let a broken semantic model look like a decision.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: What the claim validator's exit codes mean. Kept here because this operation
#: is the only thing that interprets them.
_VERDICT_MARKER = "QM-VERDICT:"

_CLAIM_STATUS = {0: "VALID", 1: "INVALID", 2: "OUT_OF_DOMAIN",
                 3: "PRECEDENCE_VIOLATED", 4: "UNREADABLE"}

#: Every declared verdict maps here explicitly. Two of them used to fall through
#: to UNAVAILABLE and were measured doing so: PRECEDENCE_VIOLATED says the
#: certificate's own dependency model was contradicted, and UNREADABLE says no
#: verdict was formed at all. Neither is a refusal, so neither may look like one.
_GATE_MAPPING = {
    "VALID": "USABLE",
    "INVALID": "UNAVAILABLE",
    "OUT_OF_DOMAIN": "UNAVAILABLE",
    "PRECEDENCE_VIOLATED": "UNRESOLVABLE",
    "UNREADABLE": "UNRESOLVABLE",
}


def gate(certificate: Path, claim_id: str) -> tuple[str, str]:
    """Return (status, detail) for the state a binding defers to this certificate."""
    if not certificate.is_file():
        return "UNRESOLVABLE", f"certificate {certificate.name!r} does not exist"
    try:
        cert = json.loads(certificate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return "UNRESOLVABLE", f"{certificate.name} is not readable -- {exc}"
    if cert.get("claim_id") != claim_id:
        return ("UNRESOLVABLE",
                (f"the binding names claim {claim_id!r} but {certificate.name} "
                f"certifies {cert.get('claim_id')!r}"))

    validator = ROOT / "scripts" / "qm_claim_check.py"
    if not validator.is_file():
        return "UNRESOLVABLE", f"the validator {validator.name} is missing"
    proc = subprocess.run(
        [sys.executable, str(validator), str(certificate)],
        capture_output=True, text=True, encoding="utf-8", timeout=900, cwd=str(ROOT),
    )
    # PROVENANCE. An exit code proves nothing about where it came from: a crash, a
    # syntax error, a missing file and a deliberate refusal all arrive as integers.
    # UNAVAILABLE is reserved for "the mechanism ran and the certificate refused",
    # so the verdict must be DECLARED by the validator and must agree with its code.
    declared = [line.split(":", 1)[1].strip()
                for line in (proc.stdout or "").splitlines()
                if line.startswith(_VERDICT_MARKER)]
    if not declared:
        return ("UNRESOLVABLE",
                (f"the validator produced no {_VERDICT_MARKER} line (exit {proc.returncode}); "
                f"no verdict was reached"))
    verdict, _, tail = declared[-1].partition(" exit=")
    if tail.strip() != str(proc.returncode):
        return ("UNRESOLVABLE",
                (f"the validator declared {verdict!r} at exit={tail.strip()} but exited "
                f"{proc.returncode} -- the declaration and the code disagree"))
    if verdict not in _GATE_MAPPING:
        return "UNRESOLVABLE", f"the validator declared an unknown verdict {verdict!r}"
    return _GATE_MAPPING[verdict], f"certificate -> {verdict}"


if __name__ == "__main__":
    _status, _detail = gate(Path(sys.argv[1]), sys.argv[2])
    print(f"{_status}: {_detail}")
    raise SystemExit({"USABLE": 0, "UNAVAILABLE": 5, "UNRESOLVABLE": 3}[_status])
