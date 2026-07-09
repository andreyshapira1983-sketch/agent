"""Semantic Scholar Academic Search tool.

Uses the free Semantic Scholar Graph API (api.semanticscholar.org) to return
peer-reviewed papers with titles, abstracts, authors, year, and ar5iv URLs.

Set SEMANTIC_SCHOLAR_API_KEY in .env for higher rate limits (free key at
https://www.semanticscholar.org/product/api).
Without a key the tool retries on 429 with exponential back-off,
then falls back to DuckDuckGo scoped to semanticscholar.org.

Output per paper:
  title, url, ar5iv_url, abstract, year, authors, venue, citation_count, source
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

from tools.base import Tool

MAX_RESULTS_CAP = 10
DEFAULT_MAX_RESULTS = 5
_API_BASE = "https://api.semanticscholar.org/graph/v1"
_FIELDS = "title,abstract,year,authors,venue,citationCount,externalIds,url"
_TIMEOUT = 15


def _ar5iv(arxiv_id: str) -> str:
    return f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}" if arxiv_id else ""


def _paper_dict(paper: dict) -> dict[str, Any]:
    ext_ids = paper.get("externalIds") or {}
    authors = [a.get("name", "") for a in (paper.get("authors") or [])]
    return {
        "title": (paper.get("title") or "").strip(),
        "url": (paper.get("url") or "").strip(),
        "ar5iv_url": _ar5iv(ext_ids.get("ArXiv", "")),
        "abstract": (paper.get("abstract") or "").strip(),
        "year": paper.get("year"),
        "authors": authors,
        "venue": (paper.get("venue") or "").strip(),
        "citation_count": paper.get("citationCount", 0),
        "source": "semantic_scholar",
    }


class SemanticScholarSearchTool(Tool):
    name = "semantic_scholar_search"
    description = (
        "Search Semantic Scholar for peer-reviewed academic papers. "
        "Returns {title, url, ar5iv_url, abstract, year, authors, venue, "
        "citation_count}. Use this INSTEAD of web_search when the user asks "
        "to find or read a scientific/academic article. "
        "Pass ar5iv_url directly to web_fetch for full article text. "
        "Read-only; set SEMANTIC_SCHOLAR_API_KEY in .env for higher rate limits."
    )
    risk = "read_only"

    def __init__(self, default_max_results: int = DEFAULT_MAX_RESULTS) -> None:
        self.default_max_results = default_max_results

    # ------------------------------------------------------------------
    def run(
        self,
        query: str,
        max_results: int | None = None,
        fields_of_study: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")

        import urllib.request
        import urllib.parse
        import json as _json

        n = max(1, min(int(max_results or self.default_max_results), MAX_RESULTS_CAP))
        api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()

        params: dict[str, str] = {
            "query": query.strip(),
            "limit": str(n),
            "fields": _FIELDS,
        }
        if fields_of_study:
            params["fieldsOfStudy"] = fields_of_study.strip()

        url = f"{_API_BASE}/paper/search?" + urllib.parse.urlencode(params)
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "agent-research-tool/1.0 (academic use)",
        }
        if api_key:
            headers["x-api-key"] = api_key

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                    raw = _json.loads(resp.read().decode("utf-8"))
                return [_paper_dict(p) for p in raw.get("data", [])]
            except Exception as exc:
                last_exc = exc
                code = getattr(exc, "code", None)
                if code == 429:
                    time.sleep(2 ** (attempt + 1))  # 2s, 4s, 8s
                    continue
                break

        # Graceful fallback via DuckDuckGo scoped to semanticscholar.org
        try:
            return self._ddg_fallback(query, n)
        except Exception:
            raise RuntimeError(
                f"Semantic Scholar API unavailable after retries "
                f"({type(last_exc).__name__}: {last_exc}). "
                "Set SEMANTIC_SCHOLAR_API_KEY in .env for a free key."
            ) from last_exc

    # ------------------------------------------------------------------
    def _ddg_fallback(self, query: str, n: int) -> list[dict[str, Any]]:
        try:
            from ddgs import DDGS
        except ImportError:
            raise RuntimeError(
                "ddgs package not installed; cannot fall back to DuckDuckGo. "
                "Install it with: pip install ddgs"
            )

        results: list[dict[str, Any]] = []
        ddg_query = f"{query} site:semanticscholar.org"
        try:
            with DDGS() as client:
                for item in client.text(ddg_query, max_results=n):
                    url = (item.get("href") or item.get("url") or "").strip()
                    title = (item.get("title") or "").strip()
                    # Strip site suffix added by search engines to page titles
                    title = re.sub(r"\s*[|\-]\s*Semantic Scholar\s*$", "", title).strip()
                    snippet = (item.get("body") or "").strip()
                    if not url or "semanticscholar.org" not in url:
                        continue
                    m = re.search(r"arXiv[:\s]+(\d{4}\.\d{4,5})", snippet, re.IGNORECASE)
                    results.append({
                        "title": title,
                        "url": url,
                        "ar5iv_url": _ar5iv(m.group(1)) if m else "",
                        "abstract": snippet,
                        "year": None,
                        "authors": [],
                        "venue": "",
                        "citation_count": 0,
                        "source": "semantic_scholar_via_ddg",
                    })
        except Exception:
            pass
        return results

    # ------------------------------------------------------------------
    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, list):
            return False, [f"expected list, got {type(output).__name__}"]
        issues: list[str] = []
        if not output:
            issues.append("no papers returned")
            return True, issues
        well_formed = 0
        for i, item in enumerate(output):
            if not isinstance(item, dict):
                issues.append(f"result[{i}] is not a dict")
                continue
            if not item.get("title"):
                issues.append(f"result[{i}] missing title")
                continue
            well_formed += 1
        if well_formed == 0:
            return False, issues + ["no well-formed results"]
        return True, issues

    def compensation_plan(self, arguments: dict[str, Any], output: Any) -> dict:
        return {
            "actions": [{"kind": "noop", "description": "read-only tool"}],
            "description": "read-only; no rollback needed",
        }