"""MVP-14.2 — `web_fetch` tool: turn a web pointer into a verifiable source.

`web_search` returns a list of (title, url, snippet) hits — POINTERS,
not sources. The Verifier (MVP-14.4) cannot give a high confidence to
a claim grounded only on a search snippet, because the snippet is
adversarially short and could mis-represent the actual page.

`web_fetch` is the upgrade path: given a URL, it performs ONE HTTP GET,
strips HTML to plain text, applies the same redaction pipeline as every
other tool, and returns a payload that the Evidence factory promotes to
`kind="web_page"` with the document's content_hash and fetched_at. The
Verifier can then say "this claim is supported by https://X fetched
at T with sha256(content)=H".

Safety model (defence in depth):

  - **scheme allow-list**: only `https` by default; plain `http` is
    accepted only for explicitly allowlisted hosts. No `file://`, no
    `data:`, no `ftp:`.
  - **ASCII URL**: the planner sanitiser enforces this earlier, but the
    tool re-checks because tests bypass the planner.
  - **egress policy + local-network block-list**: optional allow/deny host
    patterns, DNS resolution checks, redirect re-validation, localhost,
    127.0.0.0/8, 0.0.0.0, 10/8, 192.168/16, 172.16-31/12, link-local
    169.254/16, and IPv6 loopback / unique-local are refused. Stops the
    agent from being abused into a SSRF tool against internal services.
  - **size cap**: read at most `max_bytes` (default 1 MiB), and cap again
    after gzip decompression. Larger bodies are truncated with a marker.
  - **timeout**: short (default 10 s); the loop's overall budget is
    finite.
  - **content-type allow-list**: text/html, text/plain, application/json
    or xml. A binary blob is refused with a clean error.
  - **redaction**: text is passed through `redact_text` before leaving
    the tool — same defence-in-depth promise as every other tool.

Risk: read_only. No file or process side effect.
"""
from __future__ import annotations

import hashlib
import re
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from tools.base import Risk, Tool
from tools.network_safety import (
    NetworkSafetyPolicy,
    build_safe_opener,
    decompress_gzip_limited,
    host_patterns_from_env,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_BYTES = 1 * 1024 * 1024            # 1 MiB
MAX_URL_LEN = 2048
USER_AGENT = "AutonomousAgent/MVP-14.2 (+evidence-layer)"

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

ALLOWED_CONTENT_TYPES: tuple[str, ...] = (
    "text/html",
    "text/plain",
    "text/xml",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
)

# Tag tags we strip when there's no embedded text we want to keep.
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|template|svg)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class WebFetchTool(Tool):
    """Fetch a single web page and return its plain-text content."""

    name = "web_fetch"
    description = (
        "Fetch a single HTTPS web URL and return its plain-text "
        "content with a content_hash and fetched_at timestamp. Use this "
        "AFTER `web_search` to turn a search hit (a pointer) into a "
        "verifiable source the Verifier can cite. Plain HTTP requires an "
        "explicit host allowlist. Refuses local network targets, blocked "
        "egress hosts, non-text content types, and any URL > 2048 chars. "
        "Risk: read_only."
    )
    risk: Risk = "read_only"

    def __init__(
        self,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        opener: Any | None = None,
        resolver: Any | None = None,
        allow_http_hosts: tuple[str, ...] | None = None,
        egress_allow_hosts: tuple[str, ...] | None = None,
        egress_deny_hosts: tuple[str, ...] | None = None,
    ):
        if max_bytes <= 0:
            raise ValueError(f"max_bytes must be > 0, got {max_bytes}")
        if timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be > 0, got {timeout_seconds}")
        self.max_bytes = int(max_bytes)
        self.timeout_seconds = float(timeout_seconds)
        # Injectable for tests — production passes None and we build a
        # default opener that talks real HTTP.
        self._opener = opener
        self._network_policy = NetworkSafetyPolicy(
            tool_name=self.name,
            allowed_schemes=ALLOWED_SCHEMES,
            max_url_len=MAX_URL_LEN,
            resolver=resolver,
            # Stub openers in unit tests should not do real DNS. Production
            # fetches and resolver-injected tests do.
            resolve_dns=opener is None or resolver is not None,
            allow_http_hosts=(
                allow_http_hosts
                if allow_http_hosts is not None
                else host_patterns_from_env("AGENT_FETCH_ALLOW_HTTP_HOSTS")
            ),
            egress_allow_hosts=(
                egress_allow_hosts
                if egress_allow_hosts is not None
                else host_patterns_from_env("AGENT_FETCH_ALLOW_HOSTS")
            ),
            egress_deny_hosts=(
                egress_deny_hosts
                if egress_deny_hosts is not None
                else host_patterns_from_env("AGENT_FETCH_DENY_HOSTS")
            ),
        )

    def risk_for(self, arguments: dict[str, Any]) -> Risk:  # noqa: ARG002
        return "read_only"

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------

    def run(self, url: str) -> dict[str, Any]:
        self._network_policy.validate_url(url, role="web_fetch url")

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                # Prefer plain text / HTML — gives the server a hint.
                "Accept": "text/html, text/plain, application/json, */*;q=0.5",
                "Accept-Encoding": "gzip, identity",
            },
        )

        started = time.monotonic()
        opener = self._opener or build_safe_opener(self._network_policy)
        final_url = url
        try:
            with opener.open(req, timeout=self.timeout_seconds) as resp:
                final_url = str(getattr(resp, "geturl", lambda: url)())
                self._network_policy.validate_url(final_url, role="web_fetch final url")
                status_code = int(getattr(resp, "status", 200))
                content_type = resp.headers.get("Content-Type", "")
                content_encoding = (resp.headers.get("Content-Encoding") or "").lower()
                # Read at most max_bytes + 1 to detect truncation.
                raw = resp.read(self.max_bytes + 1)
        except urllib.error.HTTPError as e:
            raise ValueError(
                f"HTTP {e.code} fetching {url!r}: {e.reason}"
            ) from None
        except urllib.error.URLError as e:
            raise ValueError(f"URL error fetching {url!r}: {e.reason}") from None
        except socket.timeout:
            raise ValueError(f"timeout fetching {url!r}") from None

        elapsed_ms = int((time.monotonic() - started) * 1000)
        truncated = len(raw) > self.max_bytes
        if truncated:
            raw = raw[: self.max_bytes]

        # Decompress gzip if needed.
        if content_encoding == "gzip":
            try:
                raw, decompressed_truncated = decompress_gzip_limited(raw, self.max_bytes)
                truncated = truncated or decompressed_truncated
            except OSError:
                # Truncated gzip; fall back to raw bytes — the strip
                # below still produces SOME text.
                pass

        self._check_content_type(content_type)

        charset = self._extract_charset(content_type) or "utf-8"
        text_raw = raw.decode(charset, errors="replace")
        text_clean = self._strip_html(text_raw)

        # Defence-in-depth redaction on the way out.
        from core.redaction import redact_text

        text_safe, _ = redact_text(text_clean)

        # Hash the FULL content we read (post-decompression, pre-strip
        # but post-decode) — gives a stable "same page" handle even
        # when whitespace cleanup differs across runs.
        content_hash = hashlib.sha256(
            text_raw.encode("utf-8", errors="replace")
        ).hexdigest()

        return {
            "url": final_url,
            "requested_url": url,
            "status_code": status_code,
            "content_type": content_type,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": content_hash,
            "text": text_safe,
            "text_truncated": truncated,
            "bytes": len(raw),
            "elapsed_ms": elapsed_ms,
            "compensation_plan": {
                "id": "noop",
                "actions": [{"kind": "noop", "description": "web_fetch is read-only"}],
                "tool_name": self.name,
                "description": "web_fetch makes no changes; no rollback needed",
            },
        }

    # ------------------------------------------------------------------
    # validate_output
    # ------------------------------------------------------------------

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, dict):
            return False, ["web_fetch output must be a dict"]
        required = {
            "url", "status_code", "content_type", "fetched_at",
            "content_hash", "text", "text_truncated", "bytes",
            "elapsed_ms", "compensation_plan",
        }
        missing = required - output.keys()
        if missing:
            return False, [f"missing keys: {sorted(missing)}"]

        if not isinstance(output["url"], str) or not output["url"]:
            return False, ["url must be a non-empty string"]
        if not isinstance(output["status_code"], int):
            return False, ["status_code must be an int"]
        if not isinstance(output["content_hash"], str) or len(output["content_hash"]) != 64:
            return False, ["content_hash must be a 64-char sha256 hex digest"]
        if not isinstance(output["text_truncated"], bool):
            return False, ["text_truncated must be a bool"]
        for k in ("bytes", "elapsed_ms"):
            if not isinstance(output[k], int) or output[k] < 0:
                return False, [f"{k} must be a non-negative int"]
        if not isinstance(output["text"], str):
            return False, ["text must be a string"]
        if not isinstance(output["compensation_plan"], dict):
            return False, ["compensation_plan must be a dict"]

        warnings: list[str] = []
        if not output["text"].strip():
            warnings.append("fetched page has no extractable text")
        return True, warnings

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_url(url: Any) -> None:
        NetworkSafetyPolicy(
            tool_name="web_fetch",
            allowed_schemes=ALLOWED_SCHEMES,
            max_url_len=MAX_URL_LEN,
            resolve_dns=False,
        ).validate_url(url, role="web_fetch url")

    @staticmethod
    def _check_host_not_local(hostname: str | None) -> None:
        NetworkSafetyPolicy(tool_name="web_fetch", resolve_dns=False).validate_host(hostname)

    @staticmethod
    def _check_content_type(content_type: str) -> None:
        ct_lower = (content_type or "").split(";", 1)[0].strip().lower()
        if not ct_lower:
            # An empty content-type is unusual but seen in the wild;
            # accept and treat as text/plain.
            return
        if ct_lower not in ALLOWED_CONTENT_TYPES:
            raise PermissionError(
                f"content-type {content_type!r} not in allow-list "
                f"{ALLOWED_CONTENT_TYPES}"
            )

    @staticmethod
    def _extract_charset(content_type: str) -> str | None:
        if not content_type:
            return None
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                return part.split("=", 1)[1].strip().strip('"')
        return None

    @staticmethod
    def _strip_html(text: str) -> str:
        """Cheap, dependency-free HTML→text. Good enough for evidence."""
        # Remove script/style blocks (including their content).
        cleaned = _SCRIPT_STYLE_RE.sub(" ", text)
        # Replace remaining tags with a space so adjacent words don't merge.
        cleaned = _TAG_RE.sub(" ", cleaned)
        # Decode the most common entities.
        cleaned = (
            cleaned.replace("&amp;", "&")
                   .replace("&lt;", "<")
                   .replace("&gt;", ">")
                   .replace("&quot;", '"')
                   .replace("&#39;", "'")
                   .replace("&apos;", "'")
                   .replace("&nbsp;", " ")
        )
        # Collapse runs of whitespace within a line.
        cleaned = _WHITESPACE_RE.sub(" ", cleaned)
        # Squash >2 blank lines down to 2.
        cleaned = _BLANKLINES_RE.sub("\n\n", cleaned)
        return cleaned.strip()
