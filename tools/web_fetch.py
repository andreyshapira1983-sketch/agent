"""`web_fetch` tool: turn a web pointer (a search hit) into a verifiable, hashed source."""
from __future__ import annotations

import hashlib
import re
import subprocess
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
    reserve_egress,
)
from tools.reddit_feed import feed_as_text, reddit_feed_url

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_BYTES = 1 * 1024 * 1024            # 1 MiB
MAX_URL_LEN = 2048
USER_AGENT = "AutonomousAgent/MVP-14.2 (+evidence-layer)"

#: 404/410 mean "no such resource" (RFC 9110 §15.5.5, §15.5.11): an observation, not a tool
#: failure. Other codes (401/403/429/5xx) stay errors: they say nothing about existence.
_NOT_FOUND_CODES = frozenset({404, 410})


def _not_found_result(url: str, code: int, reason: str, started: float) -> dict[str, Any]:
    text = (f"HTTP {code} {reason or 'Not Found'}: {url} — the origin server has no current "
            f"representation of this resource (RFC 9110).")
    return {
        "url": url, "requested_url": url, "status_code": code, "not_found": True,
        "content_type": "", "fetched_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text": text, "text_truncated": False, "full_length": len(text), "bytes": 0,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "compensation_plan": {
            "id": "noop", "actions": [{"kind": "noop", "description": "web_fetch is read-only"}],
            "tool_name": "web_fetch", "description": "web_fetch makes no changes; no rollback needed",
        },
    }

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

ALLOWED_CONTENT_TYPES: tuple[str, ...] = (
    "text/html",
    "text/plain",
    "text/xml",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    # Primary sources are often PDFs; only their text is extracted (see _pdf_text).
    "application/pdf",
)

#: PDF pages read: the evidence keeps only an excerpt, so more pages cost time for nothing.
PDF_MAX_PAGES = 40

#: PDF download cap: a truncated PDF cannot be opened at all (its cross-reference table is
#: at the END), so PDFs get their own cap, large enough for a textbook.
PDF_MAX_BYTES = 32 * 1024 * 1024

#: Scanned-PDF pages to OCR: minutes, not seconds, in the convert_file sandbox.
PDF_OCR_MAX_PAGES = 10

#: OCR program runner; None means the convert_file sandbox. Replaced only in tests.
_ocr_runner = None

# Blocks we strip together with their content.
_BLOCK_NAMES = ("script", "style", "noscript", "template", "svg")
_BLOCK_OPEN_RE = re.compile(r"<(" + "|".join(_BLOCK_NAMES) + r")\b", re.IGNORECASE)
_BLOCK_CLOSE_RES = {
    name: re.compile(rf"</{name}\s*>", re.IGNORECASE) for name in _BLOCK_NAMES
}
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")
_SPACE_ONLY_LINES_RE = re.compile(r"\n[ \t]+(?=\n)")


def _strip_blocks(text: str) -> str:
    """Replace each script/style/... block, content included, with a space.

    One forward pass: an opener with no closer is remembered per tag name, so
    a page of unclosed tags is not rescanned to the end from every opener.
    """
    out: list[str] = []
    pos = 0
    no_closer: set[str] = set()
    search_from = 0
    open_end = -1
    while len(no_closer) < len(_BLOCK_NAMES):
        opener = _BLOCK_OPEN_RE.search(text, search_from)
        if opener is None:
            break
        if open_end < opener.end():
            open_end = text.find(">", opener.end())
        if open_end < 0:
            break
        name = opener.group(1).lower()
        closer = None if name in no_closer else _BLOCK_CLOSE_RES[name].search(text, open_end + 1)
        if closer is None:
            no_closer.add(name)
            search_from = opener.end()
            continue
        out.append(text[pos:opener.start()])
        out.append(" ")
        pos = search_from = closer.end()
    out.append(text[pos:])
    return "".join(out)


def _strip_tags(text: str) -> str:
    """Replace each `<...>` tag with a space; a `<` with no `>` after it stays."""
    out: list[str] = []
    pos = 0
    while True:
        lt = text.find("<", pos)
        gt = text.find(">", lt + 1) if lt >= 0 else -1
        if gt < 0:
            break
        if gt == lt + 1:  # "<>" is not a tag
            out.append(text[pos:gt])
            pos = gt
            continue
        out.append(text[pos:lt])
        out.append(" ")
        pos = gt + 1
    out.append(text[pos:])
    return "".join(out)


def _strip_blocks_and_tags(text: str) -> str:
    """Markup removal of `_strip_html`: blocks with content, then bare tags."""
    return _strip_tags(_strip_blocks(text))


#: Characters shown around each match, and the number of match windows shown.
FIND_RADIUS = 600
FIND_MAX_WINDOWS = 6


def find_windows(text: str, find: str) -> str:
    """Page text around the matches of `find` (terms separated by |), with a header line.

    No match: a marker line, then the page text as before.
    """
    terms = [t.strip() for t in (find or "").split("|") if t.strip()][:5]
    hits = sorted({m.start() for t in terms for m in re.finditer(re.escape(t), text, re.IGNORECASE)})
    if not hits:
        return f"[find {find!r}: no match in {len(text)} chars; page head follows]\n" + text
    spans: list[list[int]] = []
    for pos in hits:
        lo, hi = max(0, pos - FIND_RADIUS), min(len(text), pos + FIND_RADIUS)
        if spans and lo <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], hi)
        else:
            spans.append([lo, hi])
    shown = spans[:FIND_MAX_WINDOWS]
    head = (f"[find {find!r}: {len(hits)} match(es) in {len(text)} chars; "
            f"{len(shown)} window(s) shown, at chars {', '.join(str(lo) for lo, _ in shown)}]")
    return head + "\n" + "\n…\n".join(text[lo:hi] for lo, hi in shown)

def _pdf_text(raw: bytes) -> str:
    """Text of a PDF via pdftotext; a broken file is a named refusal, never an empty string.

    A PDF without a text layer is a scan and goes to OCR.
    """
    import tempfile as _tempfile
    from pathlib import Path as _Path

    with _tempfile.TemporaryDirectory(prefix="web_fetch_pdf_") as root:
        src = _Path(root, "source.pdf")
        src.write_bytes(raw)
        out = _Path(root, "result.txt")
        argv = ["pdftotext", "-layout", "-nopgbrk", "-f", "1", "-l", str(PDF_MAX_PAGES), str(src), str(out)]
        try:
            proc = subprocess.run(  # noqa: S603
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"PDF could not be read: pdftotext timed out after {DEFAULT_TIMEOUT_SECONDS}s") from exc
        except FileNotFoundError as exc:
            raise ValueError("PDF could not be read: pdftotext not installed") from exc
        if proc.returncode != 0:
            raise ValueError(f"PDF could not be read: pdftotext exited {proc.returncode}")
        text = out.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return _ocr_scanned_pdf(raw)
    return text


def _mostly_latin(text: str) -> bool:
    """True when some letters are Cyrillic but under 10%: Latin text with look-alike swaps."""
    letters = [ch for ch in text if ch.isalpha()]
    cyrillic = sum(1 for ch in letters if "Ѐ" <= ch <= "ӿ")
    return bool(cyrillic) and cyrillic < 0.10 * len(letters)


def _ocr_scanned_pdf(raw: bytes) -> str:
    """OCR a scan without a text layer, only inside the convert_file sandbox.

    A foreign PDF is dangerous input: without the sandbox there is a named refusal, not OCR.
    """
    import tempfile as _tempfile
    from pathlib import Path as _Path

    from tools.convert_file import ConvertFileTool, ConvertRefused

    with _tempfile.TemporaryDirectory(prefix="web_fetch_ocr_") as root:
        _Path(root, "scan.pdf").write_bytes(raw)
        tool = ConvertFileTool(_Path(root), runner=_ocr_runner)
        try:
            out = tool.run(op="ocr", path="scan.pdf", max_pages=PDF_OCR_MAX_PAGES)
            # rus+eng OCR swaps look-alike Cyrillic into English scans: a mostly Latin
            # result is read again as English.
            if _mostly_latin(str(out.get("text") or "")):
                out = tool.run(op="ocr", path="scan.pdf", lang="eng", max_pages=PDF_OCR_MAX_PAGES)
        except ConvertRefused as exc:
            raise ValueError(f"PDF carries no extractable text (scanned images?) and OCR "
                             f"is unavailable: {exc}") from None
    text = str(out.get("text") or "").strip()
    if not text:
        raise ValueError("PDF carries no extractable text (scanned images?) and OCR read "
                         f"nothing: {out.get('error') or str(out.get('log_tail') or '')[-200:]}")
    return (f"[распознано со скана (OCR), первые {PDF_OCR_MAX_PAGES} страниц; "
            f"возможны ошибки распознавания]\n{text}")


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
        "Optional `find` (terms in the PAGE's language, separated by |) returns "
        "the text around each match instead of the page head. "
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
        budget_ledger: Any | None = None,
    ):
        if max_bytes <= 0:
            raise ValueError(f"max_bytes must be > 0, got {max_bytes}")
        if timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be > 0, got {timeout_seconds}")
        self.max_bytes = int(max_bytes)
        self.timeout_seconds = float(timeout_seconds)
        #: Persistent `web_fetches` meter; None means unmetered.
        self.budget_ledger = budget_ledger
        # Test seam; None builds the real HTTP opener.
        self._opener = opener
        self._network_policy = NetworkSafetyPolicy(
            tool_name=self.name,
            allowed_schemes=ALLOWED_SCHEMES,
            max_url_len=MAX_URL_LEN,
            resolver=resolver,
            # A stub opener alone (unit tests) skips real DNS; an injected resolver does not.
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

    def risk_for(self, arguments: dict[str, Any]) -> Risk:
        return "read_only"

    def _read_limit_for(self, content_type: str) -> int:
        """Read cap for this response; PDFs have their own (`PDF_MAX_BYTES`)."""
        if "application/pdf" in (content_type or "").lower():
            return max(self.max_bytes, PDF_MAX_BYTES)
        return self.max_bytes

    def run(self, url: str, find: str = "") -> dict[str, Any]:
        self._network_policy.validate_url(url, role="web_fetch url")
        # A Reddit page is built by script, so its feed is read instead (tools/reddit_feed.py).
        feed_url = reddit_feed_url(url)
        if feed_url:
            self._network_policy.validate_url(feed_url, role="web_fetch reddit feed url")
        reserve_egress(self.budget_ledger, tool_name="web_fetch", target=url)

        req = urllib.request.Request(  # noqa: S310 — validate_url above enforces the scheme allow-list and egress policy
            feed_url or url,
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
                # Read at most limit + 1 to detect truncation.
                limit = self._read_limit_for(content_type)
                raw = resp.read(limit + 1)
        except urllib.error.HTTPError as e:
            if e.code in _NOT_FOUND_CODES:
                return _not_found_result(url, e.code, str(e.reason or ""), started)
            raise ValueError(
                f"HTTP {e.code} fetching {url!r}: {e.reason}"
            ) from None
        except urllib.error.URLError as e:
            raise ValueError(f"URL error fetching {url!r}: {e.reason}") from None
        except TimeoutError:
            raise ValueError(f"timeout fetching {url!r}") from None

        elapsed_ms = int((time.monotonic() - started) * 1000)
        truncated = len(raw) > limit
        if truncated:
            raw = raw[:limit]

        if content_encoding == "gzip":
            try:
                raw, decompressed_truncated = decompress_gzip_limited(raw, limit)
                truncated = truncated or decompressed_truncated
            except OSError:
                # Truncated gzip: keep the raw bytes, the strip below still yields some text.
                pass

        if not (feed_url and "xml" in (content_type or "").lower()):
            self._check_content_type(content_type)

        if feed_url and "xml" in (content_type or "").lower():
            text_raw = raw.decode(self._extract_charset(content_type) or "utf-8", errors="replace")
            text_clean = feed_as_text(text_raw)
        elif "application/pdf" in (content_type or "").lower():
            if truncated:
                raise ValueError(
                    f"PDF is larger than the {limit} byte read cap and a "
                    f"truncated PDF cannot be opened at all: its table of "
                    f"contents lives at the END of the file"
                )
            text_raw = text_clean = _pdf_text(raw)
        else:
            charset = self._extract_charset(content_type) or "utf-8"
            text_raw = raw.decode(charset, errors="replace")
            text_clean = self._strip_html(text_raw)
        if "json" in content_type.lower():
            # API facts must not drown in a README field (tools/json_view.py).
            from tools.json_view import compact_json_view
            text_clean = compact_json_view(text_raw, truncated=truncated) or text_clean

        # Defence-in-depth redaction on the way out.
        from core.redaction import redact_text

        text_safe, _ = redact_text(text_clean)
        full_length = len(text_safe)
        found = find_windows(text_safe, find) if (find or "").strip() else None
        if found is not None:
            text_safe = found

        # Hash the decoded text before stripping: a stable "same page" handle across cleanups.
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
            "full_length": full_length,
            **({"find": find} if (find or "").strip() else {}),
            "bytes": len(raw),
            "elapsed_ms": elapsed_ms,
            "compensation_plan": {
                "id": "noop",
                "actions": [{"kind": "noop", "description": "web_fetch is read-only"}],
                "tool_name": self.name,
                "description": "web_fetch makes no changes; no rollback needed",
            },
        }

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

    @staticmethod
    def _check_content_type(content_type: str) -> None:
        ct_lower = (content_type or "").split(";", 1)[0].strip().lower()
        if not ct_lower:
            # An empty content-type is seen in the wild; treat it as text/plain.
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
        # Blocks go with their content; other tags become a space so adjacent words don't merge.
        cleaned = _strip_blocks_and_tags(text)
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
        # A line of only spaces counts as blank; then squash >2 blank lines down to 2.
        cleaned = _SPACE_ONLY_LINES_RE.sub("", cleaned)
        cleaned = _BLANKLINES_RE.sub("\n\n", cleaned)
        return cleaned.strip()
