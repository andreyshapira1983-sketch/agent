"""
run_live.py — Single, quiet entry point for the live agent.

What this does:
    1. Loads keys from .env (OPENAI, TELEGRAM, EMAIL, ...).
    2. Builds Brain, memory, tools, professions, channels.
    3. Starts the quiet long-poll loop:
         * answers Telegram chats in plain human language
         * processes Email jobs (DOCX attachments → workflow)
         * does NOTHING when no one is talking — true silence

Usage:

    py run_live.py                 # live, replies for real on Telegram
    py run_live.py --chat          # talk to the agent in this terminal
    py run_live.py --dry-run       # boots everything, then exits — smoke test
    py run_live.py --status        # one-line health check, then exits

Quiet by default. Set `AGENT_LOG_LEVEL=DEBUG` in `.env` for verbose output.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from runtime import (
    build_runtime,
    configure_quiet_logging,
    load_config,
    run_forever,
)
from runtime.config import mask


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    cfg, vault = load_config(
        env_path=args.env,
        autonomy_level=args.autonomy,
        dry_run_send=args.dry_run_send,
    )

    configure_quiet_logging(cfg.log_level)
    logger = logging.getLogger("run_live")

    if not cfg.openai_ready:
        logger.error(
            "OPENAI_API_KEY missing. Add it to your .env (or shell env) before running."
        )
        return 2

    logger.info(
        "ключи  : openai=%s  telegram=%s  email_in=%s  email_out=%s",
        _ready_mark(cfg.openai_ready, vault.get_optional("OPENAI_API_KEY")),
        _ready_mark(cfg.telegram_ready, vault.get_optional("TELEGRAM_BOT_TOKEN")),
        _ready_mark(cfg.imap_ready, vault.get_optional("IMAP_USERNAME")),
        _ready_mark(cfg.email_ready, vault.get_optional("EMAIL_USERNAME")),
    )

    rt = build_runtime(cfg, vault)
    logger.info(
        "брейн  : модель=%s  автономия=%s  job-store=%d  audit=%d",
        cfg.openai_model, cfg.autonomy_level,
        len(rt.job_store), len(rt.audit),
    )
    if rt.config.dry_run_send:
        logger.info("письма : DRY-RUN (только показываю что отправила бы; AGENT_DRY_RUN=false чтобы слать всерьёз)")
    else:
        logger.info("письма : РЕАЛЬНЫЕ отправки включены")

    if args.status:
        print(json.dumps(rt.status(), indent=2, ensure_ascii=False, default=str))
        return 0

    if args.dry_run:
        logger.info("dry-run: сборка прошла, петлю не запускаю.")
        return 0

    if args.chat:
        return _run_console_chat(rt)

    logger.info("слушаю — Ctrl+C чтобы остановить.")
    run_forever(rt)
    return 0


# ════════════════════════════════════════════════════════════════════
# Console chat — talk to the agent directly in the terminal.
# ════════════════════════════════════════════════════════════════════

def _run_console_chat(rt) -> int:
    """Read lines from stdin, print the agent's reply. No tech noise.

    Commands:
        /quit, /exit   — stop the chat
        /status        — print the runtime snapshot
        anything else  — sent to the Brain as a free-form message
    """
    # The chat handler itself already strips JSON envelopes, applies the
    # persona, and silently ignores empty input. The loop here is just
    # I/O: read a line, hand it over, print the reply, repeat.
    print()
    print("─" * 60)
    print(" Аня на связи. Пиши на любом языке. /quit чтобы выйти.")
    print("─" * 60)
    client_id = "console:operator"

    try:
        while True:
            try:
                line = input("you > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.lower() in {"/quit", "/exit", "/q"}:
                break
            if line.lower() == "/status":
                print(json.dumps(rt.status(), indent=2, ensure_ascii=False, default=str))
                continue
            try:
                reply = rt.chat.handle(
                    brief=line, client_id=client_id, source="console",
                )
            except Exception as exc:  # noqa: BLE001
                print(f"anya> (упс, внутренняя ошибка: {exc})\n")
                continue
            text = (reply.text or "").strip() or "(молчу)"
            print(f"anya> {text}\n")
    finally:
        close = getattr(rt, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
        print("─" * 60)
        print(" Чат завершён.")
    return 0


# ════════════════════════════════════════════════════════════════════
# CLI parsing
# ════════════════════════════════════════════════════════════════════

def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Live autonomous agent (Telegram + Email + Brain).",
    )
    parser.add_argument(
        "--env",
        default=Path.cwd() / ".env",
        type=Path,
        help="Path to .env file (default: ./.env).",
    )
    parser.add_argument(
        "--autonomy", type=int, default=2,
        help="Initial agent autonomy level (0..5). Default 2.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Build everything and exit. Useful as a smoke test.",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Print the runtime's current status as JSON, then exit.",
    )
    parser.add_argument(
        "--dry-run-send", dest="dry_run_send", action="store_true",
        default=None,
        help="Force EmailTool into dry-run mode regardless of .env.",
    )
    parser.add_argument(
        "--chat", action="store_true",
        help="Talk to the agent right in this terminal — no channels needed.",
    )
    return parser.parse_args(argv)


def _ready_mark(ready: bool, secret) -> str:
    """Return ``"YES (sk-pro…**)"`` or ``"no"`` — never the raw secret."""
    if not ready:
        return "no"
    try:
        preview = mask(secret.reveal())
    except Exception:  # noqa: BLE001
        preview = "set"
    return f"YES ({preview})"


if __name__ == "__main__":
    sys.exit(main())
