"""Run isolated repair passes; automatically verify and adopt the next agent.

The operator starts this controller once. Candidate code runs in child processes
and cannot replace the controller's already imported authority or verifier.
The source checkout is read only; a separate local repository holds the work.
This is repository isolation, not an operating-system security sandbox.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values

from core.approval_inbox import ApprovalInbox
from core.bounded_subprocess import run_with_tree_kill
from core.burn_in_sandbox import SANDBOX_ENV_FLAG, SANDBOX_MARKER
from core.burn_in_supervisor import (
    SupervisorError,
    adopt_offer,
    experiment_head,
    materialise_next_cycle,
    offer_verified_commit,
)
from scripts.burn_in_supervisor import _battery, _pending_offers

_TRAILER = "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
_MEMORY_DIRS = ("data", "logs")


@dataclass(frozen=True)
class RepairLimits:
    passes: int = 3
    seconds: int = 900
    cycles: int = 6
    llm_calls: int = 6
    cost_units: int = 100
    applies_per_day: int = 20

    def __post_init__(self) -> None:
        if any(value <= 0 for value in vars(self).values()):
            raise ValueError("all repair limits must be positive")


def _git(repo: Path, *args: str) -> str:
    out, err, code, timed_out = run_with_tree_kill(
        ["git", *args], cwd=repo, env=None, timeout=120,
    )
    if timed_out or code != 0:
        detail = err.decode("utf-8", "replace").strip()
        raise SupervisorError(f"git {args[0]} failed (timeout={timed_out}): {detail}")
    return out.decode("utf-8")


def _copy_file(source: Path, target: Path, *, root: Path) -> None:
    if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
        raise SupervisorError(f"refusing a linked file outside the snapshot: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _copy_memory(source: Path, target: Path) -> None:
    """Carry memories and spend, never code, supervisor state or authority."""
    for directory in _MEMORY_DIRS:
        base = source / directory
        if not base.exists():
            continue
        if base.is_symlink() or not base.resolve().is_relative_to(source.resolve()):
            raise SupervisorError(f"refusing linked runtime directory: {base}")
        for path in base.rglob("*"):
            if path.is_symlink():
                raise SupervisorError(f"refusing linked runtime state: {path}")
            if path.is_file() and path.suffix != ".lock":
                _copy_file(path, target / path.relative_to(source), root=source)


def _snapshot(source: Path, destination: Path) -> None:
    """Snapshot current files, including edits; do not move the source branch."""
    names = _git(source, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    destination.mkdir()
    for name in sorted(set(names.split("\0")) - {""}):
        path = source / name
        if path.is_file():
            _copy_file(path, destination / name, root=source)
    _git(destination, "init", "--quiet", "--initial-branch=sandbox")
    # Private repository excludes also cover older source versions.
    exclude = destination / ".git" / "info" / "exclude"
    with exclude.open("a", encoding="utf-8") as stream:
        stream.write("\ndata/\nlogs/\nstate/\nconfig/burn_in_sandbox.json\n"
                     "config/budget_limits.json\n")
    _git(destination, "add", "-A")
    _git(
        destination, "-c", "user.name=Autonomous Repair",
        "-c", "user.email=autonomous-repair@localhost",
        "commit", "--quiet", "-m", f"Isolated operator snapshot\n\n{_TRAILER}",
    )


def _grant(repo: Path, limits: RepairLimits, expires_at: str) -> None:
    inbox = ApprovalInbox(path=repo / "data" / "approval_inbox.jsonl")
    item = inbox.add(
        operation="autonomous_runtime.standing_grant",
        summary="Operator started bounded, isolated autonomous repair",
        risk="irreversible",
        reasons=("operator invoked scripts/autonomous_repair.py",),
        payload={"max_runs_per_day": limits.applies_per_day},
        expires_at=expires_at,
    )
    inbox.approve(
        item.id, actor="operator:autonomous_repair",
        reason="Bounded runs in the isolated copy; no per-file human approval",
    )


def _prepare_tree(
    repo: Path, tree: Path, *, limits: RepairLimits, expires_at: str,
) -> str:
    head = materialise_next_cycle(repo, tree)
    # The lane restores a BRANCH after verification. On detached HEAD, checking
    # out "HEAD" would leave it on its candidate instead of its starting code.
    _git(tree, "checkout", "--quiet", "-b", tree.name, head)
    _copy_memory(repo, tree)
    _copy_file(repo / "config" / "budget_limits.json",
               tree / "config" / "budget_limits.json", root=repo)
    marker = tree / SANDBOX_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "sandbox": True, "workspace": str(tree.resolve()),
        "expires_at": expires_at, "max_applies_per_day": limits.applies_per_day,
        "reason": "operator-started isolated autonomous repair",
    }), encoding="utf-8")
    return head


def _campaign(tree: Path, limits: RepairLimits, env: dict[str, str], seconds: float) -> int:
    child_env = dict(env)
    child_env[SANDBOX_ENV_FLAG] = "on"
    # The workspace-local config is already copied. A global override also
    # reaches pytest and replaces test fixtures' independent budget configs.
    child_env.pop("AGENT_BUDGET_CONFIG_PATH", None)
    child_env["PYTHONIOENCODING"] = "utf-8"
    out, err, code, timed_out = run_with_tree_kill(
        [sys.executable, str(tree / "agent_tick.py"),
         "--workspace", str(tree), "--campaign", "--charter", "--allow-effects",
         "--max-cycles", str(limits.cycles), "--max-llm-calls", str(limits.llm_calls),
         "--max-cost-units", str(limits.cost_units),
         "--max-wall-clock-seconds", str(max(1, int(seconds))),
         "--max-unproductive-streak", "3"],
        cwd=tree, env=child_env, timeout=seconds,
    )
    (tree / "logs").mkdir(exist_ok=True)
    (tree / "logs" / "repair-pass.stdout").write_bytes(out)
    (tree / "logs" / "repair-pass.stderr").write_bytes(err)
    if timed_out:
        raise SupervisorError("repair pass timed out; candidate not adopted")
    return int(code) if code is not None else 1


def _adopt_candidates(repo: Path, tree: Path, deadline: float) -> list[dict]:
    decisions = []
    verification_env = dict(os.environ)
    verification_env.pop("AGENT_BUDGET_CONFIG_PATH", None)
    for sha in _pending_offers(tree, experiment_head(repo)):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SupervisorError("session time limit reached before verification")
        offer_verified_commit(repo, sha=sha, proposal_id=f"{tree.name}:{sha}")
        verdict = adopt_offer(
            repo, sha=sha,
            battery=lambda candidate: _battery(
                candidate, timeout_seconds=min(3600, max(0.1, deadline - time.monotonic())),
                env=verification_env,
            ),
        )
        decisions.append(verdict.to_dict())
    return decisions


def run_session(source: Path, limits: RepairLimits) -> dict:
    """One operator start -> isolated passes -> accepted code on the next pass."""
    source = source.resolve()
    budget = source / "config" / "budget_limits.json"
    if not budget.is_file():
        raise SupervisorError(
            "config/budget_limits.json is missing; set explicit spend limits before launch"
        )
    # Do not inherit a budget path pointing back into the operator's checkout.
    env = {k: v for k, v in dotenv_values(source / ".env").items() if v is not None}
    env.update(os.environ)
    session = source / ".autonomous-repair" / uuid4().hex
    session.mkdir(parents=True)
    repo = session / "repository"
    _snapshot(source, repo)
    _copy_file(budget, repo / "config" / "budget_limits.json", root=source)
    _copy_memory(source, repo)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=limits.seconds)).isoformat()
    _grant(repo, limits, expires_at)
    deadline = time.monotonic() + limits.seconds
    report = {"session": str(session), "status": "running", "passes": [], "accepted": 0}
    _save_report(session, report)
    print(f"isolated repair session: {session}", flush=True)
    for number in range(1, limits.passes + 1):
        if time.monotonic() >= deadline:
            report.update(status="stopped", reason="session time limit reached")
            break
        tree = session / f"pass-{number:04d}"
        row = {"pass": number, "workspace": str(tree)}
        report["passes"].append(row)
        _save_report(session, report)
        try:
            head = _prepare_tree(repo, tree, limits=limits, expires_at=expires_at)
            row["started_from"] = head
            _save_report(session, report)
            print(f"pass {number}: started from {head}", flush=True)
            code = _campaign(tree, limits, env, max(0.1, deadline - time.monotonic()))
            row["exit_code"] = code
            _copy_memory(tree, repo)
            if code != 0:
                report.update(status="stopped", reason=f"campaign exited with code {code}")
                break
            row["adoptions"] = _adopt_candidates(repo, tree, deadline)
            report["accepted"] += sum(v["accepted"] for v in row["adoptions"])
            row["next_head"] = experiment_head(repo)
        except (SupervisorError, OSError) as exc:
            report.update(status="stopped", reason=str(exc))
            break
        except KeyboardInterrupt:
            report.update(status="stopped", reason="operator interrupted the session")
            break
        finally:
            _save_report(session, report)
    if report["status"] == "running":
        report["status"] = "completed"
    report["next_head"] = experiment_head(repo)
    _save_report(session, report)
    return report


def _save_report(session: Path, report: dict) -> None:
    target = session / "report.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    for name, value in vars(RepairLimits()).items():
        parser.add_argument("--" + name.replace("_", "-"), type=int, default=value)
    args = parser.parse_args(argv)
    try:
        limits = RepairLimits(**{name: getattr(args, name) for name in vars(RepairLimits())})
        report = run_session(args.workspace, limits)
    except (SupervisorError, OSError, ValueError) as exc:
        print(f"autonomous repair refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
