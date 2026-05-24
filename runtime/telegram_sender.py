"""
runtime/telegram_sender.py — Send replies through the Telegram Bot API.

The `TelegramIntakeChannel` already long-polls messages. This module is
its outbound twin — a tiny `sendMessage` wrapper that the chat handler
uses to answer the user without exposing technical detail (no JSON,
no reasoning blocks, no policy verdicts).

We deliberately ignore Markdown / formatting parsing on the way out:
the agent's reply is plain text. This avoids the entire class of
"agent leaks structured fields into chat" bugs.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from brain.secrets import SecretsVault

logger = logging.getLogger(__name__)

_TG_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_CHARS = 3500   # Telegram's hard cap is 4096; leave room for trailers.


class TelegramSender:
    """Outbound side of the Telegram channel.

    Stateless — instances are cheap, share one per process.
    """

    def __init__(
        self,
        *,
        vault: SecretsVault,
        http=None,
        timeout: float = 15.0,
    ) -> None:
        self._vault = vault
        self._http = http or _default_http
        self._timeout = float(timeout)

    # ────────────────────────────────────────────────────────────────

    def send_text(self, chat_id: int | str, text: str) -> bool:
        """Send a chat message. Returns True on success, False on any error.

        Empty strings are intentionally a no-op (return False) — callers
        who want literal silence pass `""`. Long messages are split into
        multiple sends; we never raise on the caller side because a
        failed reply must not crash the agent loop.
        """
        token = self._token()
        if not token:
            logger.warning("[TelegramSender] no token — skipping reply")
            return False

        text = (text or "").strip()
        if not text:
            return False

        ok_all = True
        for chunk in _chunk_text(text, _MAX_CHARS):
            ok = self._send_chunk(token, chat_id, chunk)
            ok_all = ok_all and ok
            if not ok:
                break
        return ok_all

    # ────────────────────────────────────────────────────────────────

    def _send_chunk(self, token: str, chat_id: int | str, chunk: str) -> bool:
        params = {
            "chat_id":                  str(chat_id),
            "text":                     chunk,
            "disable_web_page_preview": "true",
        }
        try:
            reply = self._http(token, "sendMessage", params, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 — outbound failure is data, not a crash
            logger.warning("[TelegramSender] send failed: %s", exc)
            return False
        if not isinstance(reply, dict) or not reply.get("ok", False):
            logger.warning(
                "[TelegramSender] API returned not-ok: %s",
                (reply or {}).get("description", "<empty>"),
            )
            return False
        return True

    def _token(self) -> str:
        if self._vault.has("TELEGRAM_BOT_TOKEN"):
            return self._vault.reveal("TELEGRAM_BOT_TOKEN")
        return ""


# ════════════════════════════════════════════════════════════════════
# HTTP transport — separable for tests
# ════════════════════════════════════════════════════════════════════

def _default_http(
    token: str,
    method: str,
    params: dict[str, Any],
    *,
    timeout: float,
) -> dict:
    url = _TG_API.format(token=token, method=method)
    encoded = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        url, data=encoded, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


def _chunk_text(text: str, max_len: int) -> list[str]:
    if len(text) <= max_len:
        return [text]
    out: list[str] = []
    cursor = 0
    while cursor < len(text):
        end = min(cursor + max_len, len(text))
        if end < len(text):
            split = text.rfind("\n", cursor, end)
            if split == -1 or split <= cursor + max_len // 2:
                split = text.rfind(" ", cursor, end)
            if split != -1 and split > cursor:
                end = split
        out.append(text[cursor:end].strip())
        cursor = end
    return [c for c in out if c]
