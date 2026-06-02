"""RSS / Atom fetch tool.

RSS is a good no-key source for autonomous agents: cheap, structured, and
schedule-friendly. The tool is read-only and follows the same network safety
shape as `web_fetch`: HTTPS by default, plain HTTP only by explicit host
allowlist, ASCII URL, DNS/IP/redirect checks, egress allow/deny policy,
post-gzip size cap, timeout, and redaction before output.
"""
from __future__ import annotations

import hashlib
import socket
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from core.redaction import redact_text
from tools.base import Risk, Tool
from tools.network_safety import (
    NetworkSafetyPolicy,
    build_safe_opener,
    decompress_gzip_limited,
    host_patterns_from_env,
)


DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_BYTES = 1 * 1024 * 1024
DEFAULT_MAX_ENTRIES = 20
MAX_ENTRIES_CAP = 50
MAX_URL_LEN = 2048
USER_AGENT = "AutonomousAgent/RSS (+source-registry)"

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_CONTENT_TYPES = (
    "application/rss+xml",
    "application/atom+xml",
    "application/xml",
    "application/xhtml+xml",
    "text/xml",
    "text/html",
    "text/plain",
)


class RssFetchTool(Tool):
    name = "rss_fetch"
    description = (
        "Fetch one RSS/Atom feed URL and return parsed entries with title, "
        "url, summary, published_at, fetched_at, and content_hash. "
        "Read-only; HTTPS by default; refuses local/private network targets "
        "and egress-denied hosts."
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
        self._opener = opener
        self._network_policy = NetworkSafetyPolicy(
            tool_name=self.name,
            allowed_schemes=ALLOWED_SCHEMES,
            max_url_len=MAX_URL_LEN,
            resolver=resolver,
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

    def run(self, url: str, max_entries: int | None = None) -> dict[str, Any]:
        self._network_policy.validate_url(url, role="rss_fetch url")
        entry_limit = max(1, min(int(max_entries or DEFAULT_MAX_ENTRIES), MAX_ENTRIES_CAP))

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "application/rss+xml, application/atom+xml, application/xml, "
                    "text/xml, text/html, */*;q=0.5"
                ),
                "Accept-Encoding": "gzip, identity",
            },
        )
        started = time.monotonic()
        opener = self._opener or build_safe_opener(self._network_policy)
        final_url = url
        try:
            with opener.open(req, timeout=self.timeout_seconds) as resp:
                final_url = str(getattr(resp, "geturl", lambda: url)())
                self._network_policy.validate_url(final_url, role="rss_fetch final url")
                status_code = int(getattr(resp, "status", 200))
                content_type = resp.headers.get("Content-Type", "")
                content_encoding = (resp.headers.get("Content-Encoding") or "").lower()
                raw = resp.read(self.max_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise ValueError(f"HTTP {exc.code} fetching {url!r}: {exc.reason}") from None
        except urllib.error.URLError as exc:
            raise ValueError(f"URL error fetching {url!r}: {exc.reason}") from None
        except socket.timeout:
            raise ValueError(f"timeout fetching {url!r}") from None

        elapsed_ms = int((time.monotonic() - started) * 1000)
        truncated = len(raw) > self.max_bytes
        if truncated:
            raw = raw[: self.max_bytes]
        if content_encoding == "gzip":
            try:
                raw, decompressed_truncated = decompress_gzip_limited(raw, self.max_bytes)
                truncated = truncated or decompressed_truncated
            except OSError:
                pass

        self._check_content_type(content_type)
        text_raw = raw.decode(self._extract_charset(content_type) or "utf-8", errors="replace")
        content_hash = hashlib.sha256(text_raw.encode("utf-8", errors="replace")).hexdigest()
        feed_title, feed_type, entries = _parse_feed(text_raw, limit=entry_limit)
        safe_title, _ = redact_text(feed_title)
        safe_entries = []
        for entry in entries:
            safe_entry = dict(entry)
            for key in ("title", "summary"):
                safe_entry[key], _ = redact_text(str(safe_entry.get(key) or ""))
            safe_entries.append(safe_entry)

        return {
            "url": final_url,
            "requested_url": url,
            "status_code": status_code,
            "content_type": content_type,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": content_hash,
            "title": safe_title,
            "feed_type": feed_type,
            "entries": safe_entries,
            "text_truncated": truncated,
            "bytes": len(raw),
            "elapsed_ms": elapsed_ms,
            "compensation_plan": {
                "id": "noop",
                "actions": [{"kind": "noop", "description": "rss_fetch is read-only"}],
                "tool_name": self.name,
                "description": "rss_fetch makes no changes; no rollback needed",
            },
        }

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, dict):
            return False, ["rss_fetch output must be a dict"]
        required = {
            "url", "status_code", "content_type", "fetched_at", "content_hash",
            "title", "feed_type", "entries", "text_truncated", "bytes",
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
        if output["feed_type"] not in {"rss", "atom", "unknown"}:
            return False, ["feed_type must be rss, atom, or unknown"]
        if not isinstance(output["entries"], list):
            return False, ["entries must be a list"]
        if not isinstance(output["text_truncated"], bool):
            return False, ["text_truncated must be a bool"]
        for key in ("bytes", "elapsed_ms"):
            if not isinstance(output[key], int) or output[key] < 0:
                return False, [f"{key} must be a non-negative int"]

        warnings: list[str] = []
        if not output["entries"]:
            warnings.append("feed has no parsed entries")
        for idx, entry in enumerate(output["entries"]):
            if not isinstance(entry, dict):
                return False, [f"entry[{idx}] is not a dict"]
            if not entry.get("title") and not entry.get("url"):
                warnings.append(f"entry[{idx}] has neither title nor url")
        return True, warnings

    @staticmethod
    def _validate_url(url: Any) -> None:
        NetworkSafetyPolicy(
            tool_name="rss_fetch",
            allowed_schemes=ALLOWED_SCHEMES,
            max_url_len=MAX_URL_LEN,
            resolve_dns=False,
        ).validate_url(url, role="rss_fetch url")

    @staticmethod
    def _check_host_not_local(hostname: str | None) -> None:
        NetworkSafetyPolicy(tool_name="rss_fetch", resolve_dns=False).validate_host(hostname)

    @staticmethod
    def _check_content_type(content_type: str) -> None:
        ct_lower = (content_type or "").split(";", 1)[0].strip().lower()
        if not ct_lower:
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


def _parse_feed(text: str, *, limit: int) -> tuple[str, str, list[dict[str, str]]]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"feed XML parse failed: {exc}") from None

    root_name = _local_name(root.tag).lower()
    if root_name == "rss":
        channel = root.find("channel")
        if channel is None:
            channel = root
        title = _child_text(channel, "title")
        entries = [_rss_item(item) for item in channel.findall("item")[:limit]]
        return title, "rss", entries
    if root_name == "feed":
        title = _child_text(root, "title")
        entries = [_atom_entry(entry) for entry in _children(root, "entry")[:limit]]
        return title, "atom", entries

    entries = [_rss_item(item) for item in root.findall(".//item")[:limit]]
    return _child_text(root, "title"), "unknown", entries


def _rss_item(item: ET.Element) -> dict[str, str]:
    return {
        "title": _child_text(item, "title"),
        "url": _child_text(item, "link"),
        "summary": _child_text(item, "description"),
        "published_at": _child_text(item, "pubDate"),
        "id": _child_text(item, "guid"),
    }


def _atom_entry(entry: ET.Element) -> dict[str, str]:
    link = ""
    for child in _children(entry, "link"):
        href = child.attrib.get("href")
        rel = child.attrib.get("rel", "alternate")
        if href and rel == "alternate":
            link = href
            break
        if href and not link:
            link = href
    return {
        "title": _child_text(entry, "title"),
        "url": link,
        "summary": _child_text(entry, "summary") or _child_text(entry, "content"),
        "published_at": _child_text(entry, "published") or _child_text(entry, "updated"),
        "id": _child_text(entry, "id"),
    }


def _child_text(parent: ET.Element, local_name: str) -> str:
    for child in list(parent):
        if _local_name(child.tag).lower() == local_name.lower():
            return " ".join("".join(child.itertext()).split())
    return ""


def _children(parent: ET.Element, local_name: str) -> list[ET.Element]:
    return [
        child
        for child in list(parent)
        if _local_name(child.tag).lower() == local_name.lower()
    ]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag
