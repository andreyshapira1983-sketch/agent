"""Operator command table: pure metadata, no runtime imports and no import side effects."""
from __future__ import annotations

from .command_specs import (
    BOTH_MODES,
    COMMANDS_CORE,
    ONE_SHOT,
    PHASE_POST_AGENT,
    PHASE_PRE_DOTENV,
    REPL,
    CommandSpec,
)
from .command_specs_ops import COMMANDS_OPS

# Re-exported: tests and callers read these as attributes of this module.
__all__ = [
    "BOTH_MODES",
    "BY_TOKEN",
    "COMMANDS",
    "ONE_SHOT",
    "PHASE_POST_AGENT",
    "PHASE_PRE_DOTENV",
    "REPL",
    "CommandSpec",
    "all_tokens",
    "lookup",
]


# Not modelled: REPL control tokens (:task-begin, :task-end, :task-abort, :end), the bare
# `?` help alias and natural-language operator intents.
COMMANDS: tuple[CommandSpec, ...] = COMMANDS_CORE + COMMANDS_OPS


BY_TOKEN: dict[str, CommandSpec] = {
    token: spec for spec in COMMANDS for token in spec.tokens
}
"""Every accepted token (canonical and alias) mapped to its command."""


def all_tokens() -> frozenset[str]:
    """Every ``:token`` the dispatch chain accepts."""
    return frozenset(BY_TOKEN)


def canonical_tokens() -> tuple[str, ...]:
    """The canonical token of every command, in registry order."""
    return tuple(spec.canonical for spec in COMMANDS)


def lookup(token: str) -> CommandSpec | None:
    """Resolve a token (canonical or alias) to its command, or ``None``."""
    return BY_TOKEN.get(token.strip().lower())


def in_category(category: str) -> tuple[CommandSpec, ...]:
    """Every command documented under ``category``, in registry order."""
    return tuple(spec for spec in COMMANDS if spec.category == category)
