"""Self-repair REPL commands (:repair, :propose-repair)."""
from __future__ import annotations

# main.py re-exports these handlers, so nothing here may import main (import cycle).
import sys
from typing import TYPE_CHECKING

from cli.parsers import _parse_repair_generation_args, _resolve_workspace_text_file
from core.self_repair import RepairProposal

if TYPE_CHECKING:
    from pathlib import Path

    from core.loop import AgentLoop


def _handle_repair(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """`:repair <target_path> [proposal_file] [test_path...] [--pattern PAT]`.

    With only <target_path> it generates a proposal and applies it at once.
    """
    tokens = rest.split()
    if not tokens:
        print(
            "Usage: :repair <target_path> [proposal_file] [test_path...] [--pattern PAT]\n"
            "  One-click: :repair core/foo.py [tests/test_foo.py ...]\n"
            "  Two-step:  :propose-repair core/foo.py  then  :repair core/foo.py tmp/foo.proposed.py",
            file=sys.stderr,
        )
        return True

    pattern: str | None = None
    if "--pattern" in tokens:
        i = tokens.index("--pattern")
        if i + 1 >= len(tokens):
            print("Usage: --pattern requires a value", file=sys.stderr)
            return True
        pattern = tokens[i + 1]
        tokens = tokens[:i] + tokens[i + 2:]

    target_path = tokens[0]
    remaining = tokens[1:]

    # The second token is a proposal file only if it is an existing .py outside tests/.
    proposal_path: str | None = None
    test_paths: tuple[str, ...]
    if remaining:
        candidate = remaining[0]
        candidate_p = workspace / candidate
        if (
            not candidate.startswith("tests")
            and candidate_p.exists()
            and candidate.endswith(".py")
        ):
            proposal_path = candidate
            test_paths = tuple(remaining[1:] or ["tests"])
        else:
            test_paths = tuple(remaining or ["tests"])
    else:
        test_paths = ("tests",)

    # ── One-click mode: generate proposal then apply it immediately ──────────
    if proposal_path is None:
        print(f"(repair: generating proposal for {target_path} …)", file=sys.stderr)
        try:
            gen_report = agent.propose_repair(
                target_path=target_path,
                workspace_root=workspace,
                test_paths=test_paths,
                test_pattern=pattern,
            )
        except Exception as exc:  # noqa: BLE001 — a command names its failure and returns
            print(f"repair: proposal generation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return True
        print(gen_report.user_summary(), file=sys.stderr)
        if gen_report.proposal is None:
            print("(repair: no proposal generated — stopping)", file=sys.stderr)
            return True
        try:
            report = agent.repair(
                gen_report.proposal,
                workspace_root=workspace,
            )
        except Exception as exc:  # noqa: BLE001 — a command names its failure and returns
            print(f"repair: apply failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return True
        print(report.user_summary(), file=sys.stderr)
        return True

    # ── Two-step mode: apply a pre-generated proposal file ───────────────────
    try:
        proposal_file = _resolve_workspace_text_file(
            workspace,
            proposal_path,
            role="proposal_file",
        )
        proposed_content = proposal_file.read_text(encoding="utf-8")
        report = agent.repair(
            RepairProposal(
                path=target_path,
                proposed_content=proposed_content,
                test_paths=test_paths,
                test_pattern=pattern,
                reason=f"REPL :repair from {proposal_path}",
            ),
            workspace_root=workspace,
        )
    except Exception as exc:  # noqa: BLE001 — a command names its failure and returns
        print(f"repair failed before controller run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return True

    print(report.user_summary(), file=sys.stderr)
    return True


def _handle_propose_repair(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    target_path, test_paths, pattern, trace_id, error = _parse_repair_generation_args(rest)
    if error:
        print(error, file=sys.stderr)
        return True

    report = agent.propose_repair(
        target_path=target_path or "",
        workspace_root=workspace,
        test_paths=test_paths,
        test_pattern=pattern,
        trace_id=trace_id,
    )
    print(report.user_summary(), file=sys.stderr)
    return True
