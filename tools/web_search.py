"""Web Search tool — read-only, no API key (DuckDuckGo via ddgs).

Schema (matches the architecture's Tool Catalog contract):
  Input:
    - query: str
    - max_results: int  (1..MAX_RESULTS_CAP)
  Output (list[dict]):
    - title: str
    - url:   str
    - snippet: str
    - source: str   (search provider that produced the row)

Tool Result Validation (§5):
  - hard fail: output is not a list, or every item is malformed
  - warnings: empty result set, missing snippets, count > cap
"""
from __future__ import annotations

from typing import Any

from tools.base import Tool


MAX_RESULTS_CAP = 10
DEFAULT_MAX_RESULTS = 5


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the public web via DuckDuckGo and return a list of "
        "{title, url, snippet, source} results. Read-only, no auth."
    )
    risk = "read_only"

    def __init__(self, default_max_results: int = DEFAULT_MAX_RESULTS):
        self.default_max_results = default_max_results

    def run(self, query: str, max_results: int | None = None) -> list[dict[str, str]]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")

        requested = max_results if max_results is not None else self.default_max_results
        n = max(1, min(int(requested), MAX_RESULTS_CAP))

        try:
            from ddgs import DDGS
        except ImportError as exc:
            raise RuntimeError(
                "ddgs is not installed. Run `pip install ddgs`."
            ) from exc

        results: list[dict[str, str]] = []
        try:
            with DDGS() as client:
                for item in client.text(query.strip(), max_results=n):
                    title = (item.get("title") or "").strip()
                    url = (item.get("href") or item.get("url") or "").strip()
                    snippet = (item.get("body") or item.get("snippet") or "").strip()
                    source = (item.get("source") or "duckduckgo").strip()
                    if not url:
                        continue  # drop unusable rows early
                    results.append(
                        {
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": source,
                        }
                    )
        except Exception as exc:
            raise RuntimeError(f"web_search API failure: {type(exc).__name__}: {exc}") from exc

        return results

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, list):
            return False, [f"expected list, got {type(output).__name__}"]

        issues: list[str] = []

        if not output:
            issues.append("no results returned by web search")
            return True, issues  # empty is a legitimate "nothing found" — let LLM acknowledge it

        if len(output) > MAX_RESULTS_CAP:
            issues.append(f"result count {len(output)} exceeds cap {MAX_RESULTS_CAP}")

        well_formed = 0
        for i, item in enumerate(output):
            if not isinstance(item, dict):
                issues.append(f"result[{i}] is not a dict")
                continue
            if not item.get("url"):
                issues.append(f"result[{i}] missing url")
                continue
            if not item.get("title"):
                issues.append(f"result[{i}] missing title")
                continue
            if not item.get("snippet"):
                issues.append(f"result[{i}] empty snippet")
            well_formed += 1

        if well_formed == 0:
            return False, issues + ["no well-formed results (no row with both title and url)"]

        return True, issues
