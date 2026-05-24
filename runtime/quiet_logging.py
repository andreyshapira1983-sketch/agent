"""
runtime/quiet_logging.py — Centralised, quiet logging configuration.

Design goals
────────────
1. **Silent when idle.** Empty poll cycles must not print anything.
   Background pollers may log DEBUG, but the root level stays at INFO.

2. **Single human-friendly format.** One short timestamp, the logger
   name, and the message. No threadpool noise, no module paths.

3. **PII safe.** Every handler runs through the project's `PIIFilter`
   so secrets / emails / phones never reach stdout, files, or syslog.

4. **Override via env.** Operator can flip a single env var to debug:

        $env:AGENT_LOG_LEVEL = "DEBUG"

Call `configure_quiet_logging(level)` exactly once at process start,
before any module logs.
"""

from __future__ import annotations

import logging
import sys

from brain.privacy import PIIFilter, PIIRedactor

_CONFIGURED = False


def configure_quiet_logging(
    level: str | int = "INFO",
    *,
    redactor: PIIRedactor | None = None,
    quiet_libs: bool = True,
) -> None:
    """Idempotent: safe to call multiple times — only the first wins.

    Args:
        level:      Root level. Anything `"DEBUG"`/`"INFO"`/`"WARNING"`/...
        redactor:   Optional shared PIIRedactor whose `mask()` is applied
                    to every record. When None, a fresh redactor is used.
        quiet_libs: When True (default), shut up the chatty stdlib + 3rd
                    party loggers (urllib3, openai, httpx, imaplib, ...)
                    so the operator only sees agent activity.
    """
    global _CONFIGURED
    if _CONFIGURED:
        logging.getLogger().setLevel(_normalize(level))
        return

    # Best-effort: force stdout to UTF-8 so Cyrillic chat replies and
    # masked secrets don't render as `?` on Windows consoles.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s  %(name)s  %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    handler.addFilter(PIIFilter(redactor or PIIRedactor()))

    root = logging.getLogger()
    root.setLevel(_normalize(level))
    root.handlers.clear()
    root.addHandler(handler)

    if quiet_libs:
        # Third-party libs go to WARNING — they never need to chatter at us.
        for noisy in (
            "urllib3",
            "openai",
            "httpx", "httpcore",
            "imaplib", "smtplib",
            "asyncio",
            "chromadb", "chromadb.api", "chromadb.telemetry",
        ):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        # Internal subsystems: keep INFO for events that matter (job
        # transitions, chat messages, errors) but hide setup chatter and
        # per-cycle reasoning traces that only the developer cares about.
        for boot_noise in (
            # Boot / setup chatter
            "brain.policy",
            "brain.memory.episodic_memory",
            "brain.memory.semantic_memory",
            "brain.memory.memory_manager",
            "brain.adapters.openai_adapter",
            "brain.uncertainty",
            "brain.skills.registry",
            "brain.skills.workflow_runner",
            "brain.context_builder",
            "tools.registry",
            "tools.executor",
            "channels.email_intake",
            "runtime.config",
            # Per-cycle reasoning trace — useful in DEBUG, noise in chat
            "brain.core",
            "brain.planner",
            "brain.goal_stack",
            "brain.interpreter",
        ):
            logging.getLogger(boot_noise).setLevel(logging.WARNING)

    _CONFIGURED = True


def _normalize(level: str | int) -> int:
    if isinstance(level, int):
        return level
    return getattr(logging, str(level).upper(), logging.INFO)
