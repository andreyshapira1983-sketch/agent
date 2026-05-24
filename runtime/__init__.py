"""
runtime/ — Live agent assembly.

This package wires the Brain, memory, registries, channels, and intake
loops into a single live process. Everything below this line is plumbing
— no new agent intelligence lives here.

Public surface:

    from runtime import AgentConfig, build_runtime, run_forever

The CLI entrypoint is `run_live.py` at the repo root.
"""

from .config import AgentConfig, load_config, mask
from .quiet_logging import configure_quiet_logging
from .agent_runtime import AgentRuntime, build_runtime
from .chat import ChatHandler, ChatReply
from .email_sender import EmailSender
from .telegram_sender import TelegramSender
from .welcome_book import WelcomeBook
from .live_loop import LiveLoop, run_forever

__all__ = [
    "AgentConfig",
    "AgentRuntime",
    "ChatHandler",
    "ChatReply",
    "EmailSender",
    "LiveLoop",
    "TelegramSender",
    "WelcomeBook",
    "build_runtime",
    "configure_quiet_logging",
    "load_config",
    "mask",
    "run_forever",
]
