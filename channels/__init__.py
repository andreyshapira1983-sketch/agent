"""
channels/ — Inbound communication channels.

Each channel polls or listens on an external surface (email, telegram, etc.)
and converts incoming messages into `brain.skills.Job` objects that are
handed to `Brain.intake_job(job)`.

Channels are thin: they own connection state and idempotency, nothing else.
All routing, profession matching, and policy enforcement happens inside the
Brain.
"""

from .email_intake import (
    EmailIntakeChannel,
    EmailIntakeError,
    IMAPClientProtocol,
    PolledEmail,
)
from .telegram_intake import (
    PolledMessage,
    TelegramIntakeChannel,
    TelegramIntakeError,
)

__all__ = [
    "EmailIntakeChannel",
    "EmailIntakeError",
    "IMAPClientProtocol",
    "PolledEmail",
    "PolledMessage",
    "TelegramIntakeChannel",
    "TelegramIntakeError",
]
