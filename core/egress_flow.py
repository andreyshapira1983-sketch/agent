"""Outbound data flow check: private data read this turn must not leave to an address taken from read material."""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

# Tools that send a request to the address in the `url` argument.
EGRESS_TOOLS = frozenset({"web_fetch", "rss_fetch"})
# Their output is private: workspace, logs, memory, code runs.
PRIVATE_TOOLS = frozenset({
    "file_read", "find_in_files", "list_dir", "read_logs", "diff_file", "convert_file",
    "memory_recall", "python_probe", "run_tests", "lesson_provenance", "model_roster",
})
# Their output legitimately picks the server: addresses from search results.
SEARCH_TOOLS = frozenset({"web_search", "semantic_scholar_search"})

_TOKEN = re.compile(r"[\w@.+-]{6,}")
_HOST = re.compile(r"(?<![\w.-])((?:[a-z0-9-]+\.)+[a-z]{2,})(?![\w-])", re.IGNORECASE)
_CAP = 2_000_000


class EgressLedger:
    """What the human asked and what was read this turn, for the gate."""

    def __init__(self, user_text: str = "") -> None:
        self.user = (user_text or "").lower()
        self.private: list[str] = []
        self.read: list[str] = []
        self.read_hosts: set[str] = set()
        self.search_hosts: set[str] = set()
        self._size = 0

    def note(self, tool_name: str, output: object) -> None:
        text = str(output or "")
        if not text:
            return
        hosts = {h.lower() for h in _HOST.findall(text)}
        if tool_name in SEARCH_TOOLS:
            self.search_hosts |= hosts
            return
        self.read_hosts |= hosts
        if self._size >= _CAP:
            return
        low = text.lower()[: _CAP - self._size]
        self._size += len(low)
        self.read.append(low)
        if tool_name in PRIVATE_TOOLS:
            self.private.append(low)

    def leak(self, tool_name: str, arguments: dict) -> str | None:
        """Why the request waits for a human, or None to let it pass."""
        if tool_name not in EGRESS_TOOLS:
            return None
        parts = urlsplit(str((arguments or {}).get("url") or ""))
        host = (parts.hostname or "").lower().rstrip(".")
        if not host or host in self.user or host in self.search_hosts:
            return None
        # A subdomain of the named server has the same owner: match on the dot boundary.
        if not any(host == seen or host.endswith("." + seen) for seen in self.read_hosts):
            return None
        link = unquote(f"{host}{parts.path}" + (f"?{parts.query}" if parts.query else "")).lower()
        if any(link in text for text in self.read):
            return None  # following a ready link from read material, not a built address
        carried = unquote(f"{parts.path} {parts.query} {parts.fragment}").lower()
        for token in _TOKEN.findall(carried):
            if token in host or token in self.user:
                continue
            if any(token in text for text in self.private):
                return (f"the URL host {host} comes from content read this turn, not from the request, "
                        f"and the URL carries private data read this turn ({token[:24]!r}) — "
                        "sending it out needs a human")
        return None
