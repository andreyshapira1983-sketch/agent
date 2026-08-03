"""Pure command metadata for the operator (REPL/one-shot) command surface.

Phase 1 of the ``main.py`` extraction: **data only**. Nothing in the running
agent reads this module yet -- ``handle_meta_command`` still owns dispatch, and
``:help`` / the startup banner are still their own hand-written strings. Phase 2
is what switches those consumers over to this table.

Why it exists: the same command surface is currently spelled out in four
hand-maintained places (the dispatch chain in ``main.py``, the ``:help`` page,
the startup banner, and ``docs/COMMANDS_MAP.md``) and they already disagree --
see ``docs/refactor/CLI_BASELINE.md`` section 3.1.

Purity contract: this module imports **nothing** from ``core``, ``app``,
``cli.commands_*`` or any runtime module, and holds no behaviour. It must stay
importable with zero side effects. ``tests/test_command_registry.py`` enforces
both that and the agreement with the live code.

Field meanings (all recorded from the code at commit 72fc7a8):

- ``canonical`` -- the primary ``:token``. Chosen as the first token of the
  dispatch branch, which at this commit always matches the first token listed
  in ``:help`` and in ``docs/COMMANDS_MAP.md``.
- ``aliases`` -- the other tokens the same dispatch branch accepts.
- ``description`` -- taken from the live ``:help`` line for the canonical token,
  falling back to the ``COMMANDS_MAP`` row when ``:help`` omits it.
- ``usage`` -- the argument sketch documented for the command (``''`` if none).
- ``category`` -- the ``COMMANDS_MAP`` section the command is documented under.
- ``in_help`` -- whether the live ``:help`` page currently lists it.
- ``in_startup_summary`` -- whether the REPL startup banner currently lists it.
- ``modes`` -- where the token is accepted: every command works both one-shot
  (``--ask ':cmd'``) and in the REPL.
- ``phase`` -- ``'pre_dotenv'`` for the two commands that, **in one-shot only**,
  short-circuit before ``load_dotenv()`` and before any agent is built;
  ``'post_agent'`` for everything else. In the REPL both phases behave the same.
- ``handler_key`` -- a stable slug for the dispatch branch. Phase 3 maps these
  to the real handlers; nothing resolves them today.

Not modelled here on purpose (see CLI_BASELINE.md sections 3.1-3.2):

- REPL block/control tokens ``:task-begin``/``:task-end``/``:task-abort``/``:end``
  -- intercepted by the REPL loop, never dispatched through the head chain;
- the bare ``?`` help alias -- it lives outside the ``:token`` namespace;
- natural-language operator intents, four of which target handlers that have no
  ``:command`` equivalent at all.
"""
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

# The registry stays the single public surface it was before the split:
# the mode/phase constants and CommandSpec are re-exported here because
# tests and callers read them as attributes of THIS module.
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
