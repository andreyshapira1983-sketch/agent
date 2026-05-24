"""
tools/builtins/web_fetch.py — Fetch a single URL and extract readable text.

This is the "live knowledge" leg of the agent's information diet. When
the LLM decides a specific URL would help (e.g. a documentation page, an
RFC, a client-supplied reference), the Brain emits a `web_fetch` tool
call and gets back clean Markdown-ish text.

Design choices
──────────────
- **No API key.** Pure `urllib` so the agent works offline-of-cloud.
- **HTML-only.** Other content types return their first 4KB raw so the
  LLM can at least see headers/snippets.
- **Hard cap on output.** 30KB to prevent overflowing LLM context.
- **Strict timeouts.** 10s connect + 15s read, never blocks forever.
- **Local SSRF guard.** Refuses `file://`, `localhost`, and RFC 1918
  ranges unless explicitly allowed.

Not in this file
────────────────
- `web_search` — that needs an API key (Brave/Tavily). Skipped until
  Phase 3 when BudgetController gates it.
"""

from __future__ import annotations

import html
import ipaddress
import logging
import re
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from tools.base import ToolBase, ToolResult, ToolSpec

logger = logging.getLogger(__name__)


_MAX_OUTPUT_CHARS = 30_000
_CONNECT_TIMEOUT = 10
_READ_TIMEOUT = 15
_DEFAULT_UA = (
    "AutonomousAgent/1.0 (+contact: ops@example.invalid)"
)


# ════════════════════════════════════════════════════════════════════
# Tool
# ════════════════════════════════════════════════════════════════════

class WebFetchTool(ToolBase):
    """Download a single URL and return readable text (Markdown-ish).

    Parameters
    ──────────
        url             (str)  — HTTPS or HTTP URL to fetch.
        allow_private   (bool) — when True, allows fetching localhost
                                 and RFC 1918 ranges (default False).
        user_agent      (str)  — override the default UA string.

    Returns
    ───────
        ToolResult.output is a dict:
            {
                "url":          "<final URL after redirects>",
                "status":       200,
                "content_type": "text/html; charset=utf-8",
                "title":        "...",
                "text":         "<cleaned readable text>",
                "truncated":    bool,
                "bytes":        <raw response size>,
            }
    """

    def __init__(self, *, allow_private_default: bool = False) -> None:
        self._allow_private_default = bool(allow_private_default)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="web_fetch",
            description=(
                "Fetch a single URL and return its readable text content "
                "(stripped of tags). For HTML pages returns title + body text. "
                "Returns up to 30 KB of text. Safe by default — refuses "
                "localhost / private IPs unless allow_private=true."
            ),
            parameters={
                "url":           "str — full http(s):// URL",
                "allow_private": "bool (optional) — allow private/localhost (default false)",
                "user_agent":    "str (optional) — override User-Agent header",
            },
            is_destructive=False,
            requires_approval=False,
        )

    # ────────────────────────────────────────────────────────────────

    def execute(self, **params: Any) -> ToolResult:
        url = str(params.get("url", "")).strip()
        if not url:
            return self._fail("`url` is required")

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return self._fail(f"unsupported scheme: {parsed.scheme!r}")
        if not parsed.netloc:
            return self._fail(f"URL is missing host: {url!r}")

        allow_private = self._parse_bool(
            params.get("allow_private", self._allow_private_default)
        )
        if not allow_private and _is_private(parsed.hostname or ""):
            return self._fail(
                f"refusing to fetch private/localhost host '{parsed.hostname}'. "
                "Pass allow_private=true to override."
            )

        user_agent = str(params.get("user_agent") or _DEFAULT_UA).strip()
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})

        try:
            with urllib.request.urlopen(req, timeout=_READ_TIMEOUT) as response:  # noqa: S310
                final_url = response.geturl()
                status = response.status
                content_type = response.headers.get("Content-Type", "") or ""
                raw = response.read(_MAX_OUTPUT_CHARS * 4)  # extra room before extraction
        except urllib.error.HTTPError as exc:
            return self._fail(f"HTTP {exc.code} from {url}: {exc.reason}")
        except urllib.error.URLError as exc:
            return self._fail(f"network error fetching {url}: {exc.reason}")
        except (TimeoutError, socket.timeout):
            return self._fail(f"timeout fetching {url}")
        except OSError as exc:
            return self._fail(f"OS error fetching {url}: {exc}")

        if "text/html" in content_type.lower():
            title, body = _extract_html(raw, content_type)
        else:
            title, body = "", _decode_safely(raw, content_type)

        truncated = len(body) > _MAX_OUTPUT_CHARS
        if truncated:
            body = body[:_MAX_OUTPUT_CHARS]

        return self._ok({
            "url":          final_url,
            "status":       status,
            "content_type": content_type,
            "title":        title,
            "text":         body,
            "truncated":    truncated,
            "bytes":        len(raw),
        })

    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in {"true", "1", "yes", "on", "y"}
        return bool(value)


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════

_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_WHITESPACE_RE = re.compile(r"[ \t\u00a0]+")
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n+")


def _extract_html(raw: bytes, content_type: str) -> tuple[str, str]:
    text = _decode_safely(raw, content_type)
    title_match = _TITLE_RE.search(text)
    title = (
        html.unescape(title_match.group(1)).strip()
        if title_match
        else ""
    )
    body = _SCRIPT_STYLE_RE.sub("", text)
    body = _TAG_RE.sub(" ", body)
    body = html.unescape(body)
    body = _WHITESPACE_RE.sub(" ", body)
    body = _BLANK_LINE_RE.sub("\n\n", body).strip()
    return title, body


def _decode_safely(raw: bytes, content_type: str) -> str:
    # Try charset hint from Content-Type, fall back to utf-8 with replacement.
    encoding = "utf-8"
    if "charset=" in content_type.lower():
        try:
            encoding = content_type.lower().split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        except IndexError:
            encoding = "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except (LookupError, ValueError):
        return raw.decode("utf-8", errors="replace")


def _is_private(host: str) -> bool:
    if not host:
        return True
    if host.lower() in {"localhost", "ip6-localhost", "ip6-loopback"}:
        return True
    # Try parse as IP first
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private or ip.is_loopback
        or ip.is_link_local or ip.is_reserved
        or ip.is_unspecified
    )
