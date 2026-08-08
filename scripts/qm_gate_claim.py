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

The three statuses are not interchangeable. UNRESOLVABLE means nothing was
decided -- the certificate or the identity is missing. UNAVAILABLE means the
graph is intact and the certificate REFUSED, which is the system working.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: What the claim validator's exit codes mean. Kept here because this operation
#: is the only thing that interprets them.
_CLAIM_STATUS = {0: "VALID", 1: "INVALID", 2: "OUT_OF_DOMAIN",
                 3: "PRECEDENCE_VIOLATED", 4: "UNREADABLE"}


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
                f"the binding names claim {claim_id!r} but {certificate.name} "
                f"certifies {cert.get('claim_id')!r}")

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "qm_claim_check.py"), str(certificate)],
        capture_output=True, text=True, encoding="utf-8", timeout=900, cwd=str(ROOT),
    )
    verdict = _CLAIM_STATUS.get(proc.returncode, f"UNKNOWN({proc.returncode})")
    return ("USABLE" if verdict == "VALID" else "UNAVAILABLE"), f"certificate -> {verdict}"


if __name__ == "__main__":
    _status, _detail = gate(Path(sys.argv[1]), sys.argv[2])
    print(f"{_status}: {_detail}")
    raise SystemExit({"USABLE": 0, "UNAVAILABLE": 5, "UNRESOLVABLE": 3}[_status])
