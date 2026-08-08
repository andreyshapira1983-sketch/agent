"""EXPERIMENTAL validator for ONE claim certificate. Not production, not a schema.

It answers one question mechanically: *is this claim valid right now, in the
evaluation its certificate declares?* It does not consult a cached verdict, and it
holds no repository hash -- QT3 measured that the complete tracked snapshot is
unchanged by a mutation that falsifies the claim, so a file hash would be a
currency signal blind to the claim's active dependency.

It also checks the certificate against itself: the certificate names a precedence
rule over its dependencies, so the validator predicts which dependency should be
active and reports PRECEDENCE_VIOLATED when the observation disagrees. A
certificate that is wrong about its own dependency model fails differently from a
claim that has become false.

Exit codes match the certificate's own vocabulary:
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


def _dotenv_value(workspace: Path, key: str) -> str | None:
    env_file = workspace / ".env"
    if not env_file.is_file():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("UNREADABLE: no certificate given")
        return UNREADABLE
    cert_path = Path(argv[1])
    try:
        cert = json.loads(cert_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"UNREADABLE: {cert_path} -- {exc}")
        return UNREADABLE

    workspace = None
    if "--workspace" in argv:
        workspace = Path(argv[argv.index("--workspace") + 1]).resolve()
    if workspace is None:
        workspace = Path(tempfile.mkdtemp()) / "ws"
    workspace.mkdir(parents=True, exist_ok=True)

    key = "AGENT_MODEL"
    from_process = os.environ.get(key)
    from_dotenv = _dotenv_value(workspace, key)

    # The certificate's own dependency model, applied BEFORE observing, so the
    # prediction cannot be fitted to the result.
    predicted_source, predicted_value = ("D3 core/llm.py default", None)
    if from_dotenv is not None:
        predicted_source, predicted_value = ("D1 workspace .env", from_dotenv)
    if from_process is not None:
        predicted_source, predicted_value = ("D2 process environment", from_process)

    domain = cert["evaluation_domain"]["command"]
    proc = subprocess.run(
        [sys.executable, *domain, "--workspace", str(workspace)],
        capture_output=True, text=True, encoding="utf-8", timeout=600, cwd=str(ROOT),
    )

    logs = sorted((workspace / "logs").glob("run_*.jsonl"))
    if not logs:
        print(f"OUT_OF_DOMAIN: no run journal in {workspace}\\logs "
              f"(process exited {proc.returncode})")
        return OUT_OF_DOMAIN
    try:
        record = json.loads(logs[0].read_text(encoding="utf-8").splitlines()[0])
        observed = record["payload"]["llm_model"]
    except (OSError, ValueError, KeyError, IndexError) as exc:
        print(f"OUT_OF_DOMAIN: the journal exists but the projection failed -- {exc}")
        return OUT_OF_DOMAIN

    expected = cert["claim"]["observable"]["expected"]
    print(f"  claim      : {cert['claim_id']} -- payload.llm_model == {expected!r}")
    print(f"  workspace  : {workspace}")
    print(f"  D2 process env AGENT_MODEL : {from_process!r}")
    print(f"  D1 .env     AGENT_MODEL    : {from_dotenv!r}")
    print(f"  predicted active dependency: {predicted_source}")
    print(f"  observed    : {observed!r}")

    if predicted_value is not None and observed != predicted_value:
        print(f"\nPRECEDENCE_VIOLATED: {predicted_source} predicted {predicted_value!r}, "
              f"observed {observed!r} -- the certificate's dependency model is wrong")
        return PRECEDENCE_VIOLATED

    if observed == expected:
        print(f"\nVALID (active dependency this evaluation: {predicted_source})")
        return VALID
    print(f"\nINVALID: expected {expected!r}, observed {observed!r} "
          f"via {predicted_source}")
    return INVALID


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
