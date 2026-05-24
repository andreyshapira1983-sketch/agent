"""
runtime/config.py — Single config loader.

What this does
──────────────
1. Read the project's `.env` file via `SecretsVault.load_dotenv()`.
2. Normalize legacy variable names (e.g. `TELEGRAM` → `TELEGRAM_BOT_TOKEN`)
   so downstream code can rely on canonical keys.
3. Derive IMAP credentials from EMAIL_* gmail-style settings when the
   user didn't bother to set IMAP_* explicitly.
4. Build an `AgentConfig` struct that summarises *which* channels are
   ready to run without leaking secret values.

The vault itself remains the single source of truth for actual values.
`AgentConfig` only reports presence / configuration, never raw secrets.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from brain.secrets import SecretsVault

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
# Legacy-name aliasing
# ════════════════════════════════════════════════════════════════════
#
# The repo's .env predates the rest of the codebase and uses slightly
# different variable names than the modules expect. We patch this in
# one place rather than littering rename hacks across channels/tools.
#
_LEGACY_ALIASES: dict[str, str] = {
    # user wrote     → canonical name code expects
    "TELEGRAM":       "TELEGRAM_BOT_TOKEN",
    "GITHUB":         "GITHUB_TOKEN",
    "EMAIL":          "EMAIL_USERNAME",
}


# Gmail defaults — used only when nothing better is configured.
_GMAIL_IMAP_HOST = "imap.gmail.com"
_GMAIL_IMAP_PORT = "993"
_GMAIL_SMTP_HOST = "smtp.gmail.com"
_GMAIL_SMTP_PORT = "587"


# ════════════════════════════════════════════════════════════════════
# Public dataclass
# ════════════════════════════════════════════════════════════════════

@dataclass
class AgentConfig:
    """Snapshot of which channels are ready to come online.

    Built once at startup from the SecretsVault. Contains no raw secrets
    — every sensitive value stays inside the vault.
    """

    # Where the brain stores its persistent state
    workspace:          Path = field(default_factory=lambda: Path.cwd())
    data_dir:           Path = field(default_factory=lambda: Path.cwd() / "data")
    attachments_dir:    Path = field(default_factory=lambda: Path.cwd() / "data" / "attachments")
    professions_dir:    Path = field(default_factory=lambda: Path.cwd() / "professions")

    # Capabilities — each True means the corresponding channel can start
    openai_ready:       bool = False
    telegram_ready:     bool = False
    email_ready:        bool = False
    imap_ready:         bool = False

    # Non-secret config the runtime needs at runtime
    openai_model:       str  = "gpt-4o"
    telegram_alert_chat: str | None = None
    imap_host:          str  = _GMAIL_IMAP_HOST
    imap_port:          int  = int(_GMAIL_IMAP_PORT)
    smtp_host:          str  = _GMAIL_SMTP_HOST
    smtp_port:          int  = int(_GMAIL_SMTP_PORT)

    # Runtime tuning — kept gentle so the agent is quiet when idle
    telegram_poll_seconds:  int  = 25     # long-poll on Telegram side
    email_poll_seconds:     int  = 60     # one IMAP cycle per minute
    log_level:              str  = "INFO"
    autonomy_level:         int  = 2

    # Status / dry-run flags
    dry_run_send:           bool = True   # True ⇒ EmailTool defaults to dry-run

    def summary(self) -> dict:
        """Operator-friendly readout — never contains secrets."""
        return {
            "workspace":           str(self.workspace),
            "data_dir":            str(self.data_dir),
            "attachments_dir":     str(self.attachments_dir),
            "openai_ready":        self.openai_ready,
            "telegram_ready":      self.telegram_ready,
            "email_ready":         self.email_ready,
            "imap_ready":          self.imap_ready,
            "openai_model":        self.openai_model,
            "telegram_alert_chat": self.telegram_alert_chat,
            "imap_host":           self.imap_host,
            "imap_port":           self.imap_port,
            "smtp_host":           self.smtp_host,
            "smtp_port":           self.smtp_port,
            "telegram_poll_s":     self.telegram_poll_seconds,
            "email_poll_s":        self.email_poll_seconds,
            "autonomy_level":      self.autonomy_level,
            "dry_run_send":        self.dry_run_send,
        }


# ════════════════════════════════════════════════════════════════════
# Loader
# ════════════════════════════════════════════════════════════════════

def load_config(
    *,
    env_path: Path | str | None = None,
    vault: SecretsVault | None = None,
    autonomy_level: int = 2,
    dry_run_send: bool | None = None,
) -> tuple[AgentConfig, SecretsVault]:
    """Load `.env` into a SecretsVault and build a sibling AgentConfig.

    Args:
        env_path:       Override the .env path (defaults to ./.env).
        vault:          Reuse an existing vault (tests). When None, a fresh
                        one is created.
        autonomy_level: Initial agent autonomy. 2 = approval for tool calls.
        dry_run_send:   When True, outbound email defaults to dry-run.
                        When None, derived from AGENT_DRY_RUN env var,
                        defaulting to True.

    Returns:
        (config, vault) — pass both to `build_runtime(...)`.
    """
    vault = vault or SecretsVault()
    if env_path is None:
        env_path = Path.cwd() / ".env"
    env_path = Path(env_path)
    if env_path.exists():
        loaded = vault.load_dotenv(env_path)
        logger.info("[config] loaded %d entries from %s", loaded, env_path)
    else:
        logger.warning("[config] .env not found at %s — using env vars only", env_path)

    # Pull anything still missing from os.environ — convenient when the
    # operator exported keys in their shell instead of using .env. We
    # only ask for keys the vault hasn't already learned, otherwise the
    # vault would log a noisy "missing" warning for every value the .env
    # already supplied.
    missing_from_vault = [
        name for name in (
            "OPENAI_API_KEY", "OPENAI_MODEL",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALERTS_CHAT_ID",
            "EMAIL_USERNAME", "EMAIL_PASSWORD",
            "EMAIL_SMTP_HOST", "EMAIL_SMTP_PORT",
            "IMAP_USERNAME", "IMAP_PASSWORD", "IMAP_HOST", "IMAP_PORT",
            "AGENT_LOG_LEVEL", "AGENT_DRY_RUN",
        )
        if not vault.has(name) and os.environ.get(name)
    ]
    if missing_from_vault:
        vault.load_from_env(*missing_from_vault, required=False)

    _apply_legacy_aliases(vault)
    _derive_imap_from_email(vault)

    cfg = AgentConfig(autonomy_level=int(autonomy_level))
    cfg.openai_ready   = vault.has("OPENAI_API_KEY")
    cfg.telegram_ready = vault.has("TELEGRAM_BOT_TOKEN")
    cfg.email_ready    = vault.has("EMAIL_USERNAME") and vault.has("EMAIL_PASSWORD")
    cfg.imap_ready     = vault.has("IMAP_USERNAME") and vault.has("IMAP_PASSWORD")

    if vault.has("OPENAI_MODEL"):
        cfg.openai_model = vault.reveal("OPENAI_MODEL")
    if vault.has("TELEGRAM_ALERTS_CHAT_ID"):
        cfg.telegram_alert_chat = vault.reveal("TELEGRAM_ALERTS_CHAT_ID")
    if vault.has("IMAP_HOST"):
        cfg.imap_host = vault.reveal("IMAP_HOST")
    if vault.has("IMAP_PORT"):
        try:
            cfg.imap_port = int(vault.reveal("IMAP_PORT"))
        except (TypeError, ValueError):
            pass
    if vault.has("EMAIL_SMTP_HOST"):
        cfg.smtp_host = vault.reveal("EMAIL_SMTP_HOST")
    if vault.has("EMAIL_SMTP_PORT"):
        try:
            cfg.smtp_port = int(vault.reveal("EMAIL_SMTP_PORT"))
        except (TypeError, ValueError):
            pass

    if vault.has("AGENT_LOG_LEVEL"):
        cfg.log_level = vault.reveal("AGENT_LOG_LEVEL").upper().strip() or "INFO"
    if dry_run_send is None:
        cfg.dry_run_send = _bool_from_env(vault.reveal("AGENT_DRY_RUN") if vault.has("AGENT_DRY_RUN") else "true")
    else:
        cfg.dry_run_send = bool(dry_run_send)

    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.attachments_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "[config] ready: openai=%s telegram=%s email=%s imap=%s",
        cfg.openai_ready, cfg.telegram_ready, cfg.email_ready, cfg.imap_ready,
    )
    return cfg, vault


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════

def _apply_legacy_aliases(vault: SecretsVault) -> None:
    """Copy legacy-named secrets under their canonical names.

    Idempotent: if the canonical key is already set, we leave it alone.
    """
    for legacy, canonical in _LEGACY_ALIASES.items():
        if vault.has(canonical):
            continue
        if vault.has(legacy):
            vault.set(canonical, vault.reveal(legacy))
            logger.info("[config] aliased %s → %s", legacy, canonical)


def _derive_imap_from_email(vault: SecretsVault) -> None:
    """If only EMAIL_USERNAME / EMAIL_PASSWORD were provided, derive IMAP.

    Convenience for Gmail-style setups where one app password unlocks both
    SMTP and IMAP.
    """
    if not vault.has("IMAP_USERNAME") and vault.has("EMAIL_USERNAME"):
        vault.set("IMAP_USERNAME", vault.reveal("EMAIL_USERNAME"))
        logger.info("[config] IMAP_USERNAME derived from EMAIL_USERNAME")
    if not vault.has("IMAP_PASSWORD") and vault.has("EMAIL_PASSWORD"):
        vault.set("IMAP_PASSWORD", vault.reveal("EMAIL_PASSWORD"))
        logger.info("[config] IMAP_PASSWORD derived from EMAIL_PASSWORD")
    if not vault.has("IMAP_HOST"):
        vault.set("IMAP_HOST", _GMAIL_IMAP_HOST)
    if not vault.has("IMAP_PORT"):
        vault.set("IMAP_PORT", _GMAIL_IMAP_PORT)


def _bool_from_env(raw: str | None) -> bool:
    if raw is None:
        return True
    value = str(raw).strip().lower()
    return value not in {"0", "false", "no", "off", "нет"}


def mask(value: str | None, *, keep: int = 4) -> str:
    """Return a masked preview of a secret for human-readable logs.

    `"sk-proj-abcdefghij"` -> `"sk-p..."`. Never leaks more than `keep`
    leading characters and always pads with the same fixed mask so log
    output never reveals key length.
    """
    if not value:
        return "<unset>"
    head = value[:keep]
    return f"{head}..."
