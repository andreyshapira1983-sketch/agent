"""The command-line surface of ``python main.py`` -- flags, defaults, help
text.
"""
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser. Pure construction -- no parsing, no I/O."""
    parser = argparse.ArgumentParser(
        description=(
            # ASCII only: the console this ships on is cp1251, and an em
            # dash here printed as a replacement char in the FIRST line of
            # --help on every run. No version number either - it was MVP-4
            # here and MVP-5 in main.py, and both were years behind.
            "Autonomous agent - the LLM picks tools; sessions keep working memory."
        )
    )
    parser.add_argument(
        "--ask",
        help="One-shot question (no memory). Omit to enter the interactive REPL.",
    )
    parser.add_argument(
        "--file",
        help=(
            "Optional file hint. The planner MAY call file_read with it. "
            "Without this hint, file_read is never used."
        ),
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root (default: current directory).",
    )
    parser.add_argument(
        "--auto-approve",
        choices=["off", "approve", "deny"],
        default="off",
        help=(
            "Approval policy for escalated (irreversible / external) actions: "
            "'off' (default) = interactive prompts in the REPL, deny in one-shot; "
            "'approve' = auto-approve everything (use only in tests / scripts); "
            "'deny' = auto-deny everything."
        ),
    )
    parser.add_argument(
        "--resume",
        metavar="TRACE_ID",
        default=None,
        help=(
            "Resume a previous run by trace ID. If the run completed synthesis, "
            "the cached answer is printed immediately (no LLM call). "
            "Budget-paused runs resume with saved phase/step context; "
            "crash-partial runs are re-run from scratch."
        ),
    )
    parser.add_argument(
        "--reason",
        default=None,
        help=(
            "Deep/Opus escalation reason (one-shot --ask only). Without it, a "
            "deep request downgrades to the standard model - the agent never "
            "opens Opus for itself. Valid: operator_explicitly_requested_opus, "
            "planner_multi_file_architecture_change."
        ),
    )
    parser.add_argument(
        "--expect",
        default=None,
        help=(
            "Expected deep output (used with --reason). Valid: minimal_patch_plan, "
            "architecture_tradeoff, cross_file_synthesis, final_answer_high_stakes."
        ),
    )
    return parser
