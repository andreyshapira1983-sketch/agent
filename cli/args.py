"""The command-line surface of ``python main.py`` -- flags, defaults, help text.

Seven public flags: ``--ask``, ``--file``, ``--workspace``, ``--auto-approve``,
``--resume``, ``--reason``, ``--expect``. Operators and scripts depend on their
names, defaults and choices, and ``--help`` is the authoritative rendering of
all three, so ``tests/characterization/test_cli_argparse_surface.py`` reads the
text argparse itself produces rather than re-deriving the parser.

Moved verbatim out of ``main()``: same description, same order, same help
strings, same ``metavar`` and ``choices``. ``prog`` is not set here any more than
it was there -- argparse takes it from ``sys.argv[0]``, which is what keeps the
usage line reading ``usage: main.py [-h] ...`` both under ``python main.py`` and
under the tests that patch ``sys.argv``.

Building the parser has no side effects, so nothing patches it and nothing here
needs the ``main`` compatibility seam that ``cli/one_shot.py`` and
``cli/repl.py`` document.
"""
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser. Pure construction -- no parsing, no I/O."""
    parser = argparse.ArgumentParser(
        description=(
            "Autonomous agent MVP-4 — LLM picks tools, sessions have working memory."
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
            "deep request downgrades to the standard model — the agent never "
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
