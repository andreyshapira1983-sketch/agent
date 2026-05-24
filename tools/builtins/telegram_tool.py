"""
tools/builtins/telegram_tool.py — Telegram Bot Tool (S4.3.3)

Позволяет агенту отправлять и получать сообщения через Telegram Bot API.

Конфигурация через .env:
    TELEGRAM                  = <bot_token>          (обязательно)
    TELEGRAM_ALERTS_CHAT_ID   = <chat_id>            (для send_alert)
    TELEGRAM_CHANNEL_ID       = <channel_id>         (для send_to_channel)

Поддерживаемые действия (параметр action):
    send_message     — отправить сообщение в любой чат
    send_to_channel  — отправить в канал (TELEGRAM_CHANNEL_ID)
    send_alert       — отправить алерт (TELEGRAM_ALERTS_CHAT_ID)
    get_updates      — получить последние сообщения боту
    get_me           — информация о боте

Безопасность:
    - Токен берётся только из переменных окружения / vault
    - Все запросы через HTTPS
    - Тексты обрезаются до 4096 символов (лимит Telegram)
    - parse_mode по умолчанию HTML (не Markdown — меньше edge cases)
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from tools.base import ToolBase, ToolResult, ToolSpec

try:
    from brain.secrets import SecretsVault
except ImportError:
    SecretsVault = None  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/{method}"
_MAX_TEXT = 4096          # Telegram hard limit
_TIMEOUT  = 15            # seconds

_VALID_ACTIONS = {
    "send_message",
    "send_to_channel",
    "send_alert",
    "get_updates",
    "get_me",
}


class TelegramTool(ToolBase):
    """
    Telegram Bot API tool.

    Параметры execute():
        action       (str)  : одно из send_message / send_to_channel /
                              send_alert / get_updates / get_me
        text         (str)  : текст сообщения (для send_*)
        chat_id      (str)  : chat_id получателя (только для send_message)
        parse_mode   (str)  : "HTML" | "MarkdownV2" (по умолч. "HTML")
        disable_preview (bool): отключить предпросмотр ссылок (умолч. True)
        offset       (int)  : для get_updates — смещение (умолч. 0)
        limit        (int)  : для get_updates — количество (умолч. 10)
        dry_run      (bool) : показать параметры запроса без реальной отправки
    """

    def __init__(self, vault: "SecretsVault | None" = None) -> None:
        self._vault = vault

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="telegram",
            description=(
                "Interact with Telegram Bot API. "
                "Actions: send_message, send_to_channel, send_alert, "
                "get_updates, get_me."
            ),
            parameters={
                "action":          "str — send_message | send_to_channel | send_alert | get_updates | get_me",
                "text":            "str — message text (for send_* actions)",
                "chat_id":         "str — target chat_id (only for send_message)",
                "parse_mode":      "str — HTML or MarkdownV2 (default HTML)",
                "disable_preview": "bool — disable link preview (default True)",
                "offset":          "int — update offset for get_updates",
                "limit":           "int — max updates to fetch (default 10)",
                "dry_run":         "bool — preview without sending (default False)",
            },
            requires_approval=False,
            is_destructive=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        action = str(params.get("action", "")).strip().lower()
        if not action:
            return self._fail("Parameter 'action' is required.")
        if action not in _VALID_ACTIONS:
            return self._fail(
                f"Unknown action '{action}'. Valid: {sorted(_VALID_ACTIONS)}"
            )

        token = self._get_token()
        if not token:
            return self._fail(
                "Telegram bot token not found. "
                "Set TELEGRAM env var or provide via SecretsVault."
            )

        dry_run: bool = bool(params.get("dry_run", False))

        try:
            if action == "send_message":
                return self._send_message(token, params, dry_run)
            if action == "send_to_channel":
                return self._send_preset(token, params, dry_run, env_key="TELEGRAM_CHANNEL_ID")
            if action == "send_alert":
                return self._send_preset(token, params, dry_run, env_key="TELEGRAM_ALERTS_CHAT_ID")
            if action == "get_updates":
                return self._get_updates(token, params, dry_run)
            if action == "get_me":
                return self._get_me(token, dry_run)
        except Exception as exc:
            logger.warning("[TelegramTool] Unexpected error in action '%s': %s", action, exc)
            return self._fail(f"Unexpected error: {exc}")

        return self._fail(f"Action '{action}' not implemented.")  # unreachable

    # ──────────────────────────────────────────────────────────────────
    # Actions
    # ──────────────────────────────────────────────────────────────────

    def _send_message(
        self,
        token: str,
        params: dict,
        dry_run: bool,
    ) -> ToolResult:
        chat_id = str(params.get("chat_id", "")).strip()
        if not chat_id:
            return self._fail("Parameter 'chat_id' is required for send_message.")
        return self._do_send(token, chat_id, params, dry_run)

    def _send_preset(
        self,
        token: str,
        params: dict,
        dry_run: bool,
        env_key: str,
    ) -> ToolResult:
        chat_id = self._get_env(env_key)
        if not chat_id:
            return self._fail(
                f"Target chat_id not configured. Set {env_key} in .env."
            )
        return self._do_send(token, chat_id, params, dry_run)

    def _do_send(
        self,
        token: str,
        chat_id: str,
        params: dict,
        dry_run: bool,
    ) -> ToolResult:
        text = str(params.get("text", "")).strip()
        if not text:
            return self._fail("Parameter 'text' is required for send actions.")
        if len(text) > _MAX_TEXT:
            logger.warning(
                "[TelegramTool] Text truncated from %d to %d chars", len(text), _MAX_TEXT
            )
            text = text[:_MAX_TEXT]

        parse_mode      = str(params.get("parse_mode", "HTML"))
        disable_preview = bool(params.get("disable_preview", True))

        payload = {
            "chat_id":                  chat_id,
            "text":                     text,
            "parse_mode":               parse_mode,
            "disable_web_page_preview": disable_preview,
        }

        if dry_run:
            return self._ok({"dry_run": True, "payload": payload})

        result, elapsed = self._timed(
            self._post, token, "sendMessage", payload
        )
        if result.get("ok"):
            msg = result["result"]
            logger.info(
                "[TelegramTool] Sent to chat_id=%s message_id=%s (%.0f ms)",
                chat_id, msg.get("message_id"), elapsed,
            )
            return self._ok(
                {
                    "message_id": msg.get("message_id"),
                    "chat_id":    chat_id,
                    "text":       text[:80] + ("..." if len(text) > 80 else ""),
                },
                duration_ms=round(elapsed),
            )
        return self._fail(
            f"Telegram API error: {result.get('description', 'unknown')}",
            error_code=result.get("error_code"),
        )

    def _get_updates(
        self,
        token: str,
        params: dict,
        dry_run: bool,
    ) -> ToolResult:
        offset = int(params.get("offset", 0))
        limit  = min(int(params.get("limit", 10)), 100)

        payload = {"offset": offset, "limit": limit}
        if dry_run:
            return self._ok({"dry_run": True, "payload": payload})

        result, elapsed = self._timed(
            self._post, token, "getUpdates", payload
        )
        if result.get("ok"):
            updates = result["result"]
            logger.info(
                "[TelegramTool] Got %d updates (%.0f ms)", len(updates), elapsed
            )
            # Return compact representation to avoid flooding context
            compact = []
            for u in updates:
                msg = u.get("message") or u.get("channel_post") or {}
                compact.append({
                    "update_id":  u.get("update_id"),
                    "from":       (msg.get("from") or {}).get("username"),
                    "chat_id":    (msg.get("chat") or {}).get("id"),
                    "text":       (msg.get("text") or "")[:200],
                    "date":       msg.get("date"),
                })
            return self._ok(
                {"count": len(updates), "updates": compact},
                duration_ms=round(elapsed),
            )
        return self._fail(
            f"Telegram API error: {result.get('description', 'unknown')}",
            error_code=result.get("error_code"),
        )

    def _get_me(self, token: str, dry_run: bool) -> ToolResult:
        if dry_run:
            return self._ok({"dry_run": True, "method": "getMe"})
        result, elapsed = self._timed(self._post, token, "getMe", {})
        if result.get("ok"):
            bot = result["result"]
            return self._ok(
                {
                    "id":         bot.get("id"),
                    "username":   bot.get("username"),
                    "first_name": bot.get("first_name"),
                },
                duration_ms=round(elapsed),
            )
        return self._fail(f"Telegram API error: {result.get('description', 'unknown')}")

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _post(token: str, method: str, payload: dict) -> dict:
        url = _API_BASE.format(token=token, method=method)
        resp = requests.post(url, json=payload, timeout=_TIMEOUT)
        try:
            data = resp.json()
        except Exception:
            data = {"ok": False, "description": resp.text[:200]}
        if not resp.ok:
            logger.debug("[TelegramTool] %s payload=%s response=%s", method, payload, data)
        return data

    def _get_token(self) -> str | None:
        if self._vault:
            try:
                secret = self._vault.get("TELEGRAM")
                if secret:
                    return secret
            except Exception:
                pass
        return self._get_env("TELEGRAM")

    def _get_env(self, key: str) -> str | None:
        val = os.environ.get(key, "").strip()
        return val if val else None
