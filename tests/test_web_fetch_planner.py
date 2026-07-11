"""MVP-14.2 — planner sanitiser tests for `web_fetch`."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.planner import LLMPlanner
from tools.base import ToolRegistry
from tools.diff_file import DiffFileTool
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.read_logs import ReadLogsTool
from tools.rss_fetch import RssFetchTool
from tools.run_tests import RunTestsTool
from tools.shell_exec import ShellExecTool
from tools.web_fetch import WebFetchTool
from tools.web_search import WebSearchTool


def _planner(workspace: Path) -> LLMPlanner:
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    reg.register(WebSearchTool())
    reg.register(FileWriteTool(workspace_root=workspace))
    reg.register(ShellExecTool(workspace_root=workspace))
    reg.register(RunTestsTool(workspace_root=workspace))
    reg.register(ReadLogsTool(workspace_root=workspace))
    reg.register(DiffFileTool(workspace_root=workspace))
    reg.register(WebFetchTool())
    reg.register(RssFetchTool())

    class _StubLLM:
        def complete(self, **_kw):
            raise AssertionError("LLM must not be called")

    return LLMPlanner(llm=_StubLLM(), registry=reg)


def _run(
    workspace: Path, steps: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    sources, warnings, _dropped = _planner(workspace)._validate_steps(steps, file_hint=None)
    return sources, warnings


class TestWebFetchSanitizer:
    def test_well_formed_passes(self, workspace: Path):
        sources, _ = _run(workspace, [{
            "tool": "web_fetch",
            "arguments": {"url": "https://realpython.com/page"},
        }])
        assert len(sources) == 1
        assert sources[0]["arguments"]["url"] == "https://realpython.com/page"
        assert sources[0]["label"].startswith("web_fetch:https://realpython.com")

    @pytest.mark.parametrize("url", [
        "https://example.com",
        "https://example.com/page",
        "http://www.example.org/",
        "https://example.net/feed",
        "https://example.edu/x",
        "https://something.invalid/x",
        "https://host.test/x",
        "https://box.example/x",
    ])
    def test_placeholder_hosts_dropped(self, workspace: Path, url: str):
        sources, warnings = _run(workspace, [{
            "tool": "web_fetch", "arguments": {"url": url},
        }])
        assert sources == []
        assert any("placeholder" in w for w in warnings)

    def test_missing_url_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "web_fetch", "arguments": {},
        }])
        assert sources == []
        assert any("without url" in w for w in warnings)

    def test_non_string_url_dropped(self, workspace: Path):
        sources, _ = _run(workspace, [{
            "tool": "web_fetch", "arguments": {"url": 42},
        }])
        assert sources == []

    def test_url_too_long_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "web_fetch",
            "arguments": {"url": "https://x.com/" + "a" * 2050},
        }])
        assert sources == []
        assert any("too long" in w for w in warnings)

    def test_non_ascii_url_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "web_fetch", "arguments": {"url": "https://пример.рф/"},
        }])
        assert sources == []
        assert any("not ASCII" in w for w in warnings)

    @pytest.mark.parametrize("scheme", [
        "file://", "ftp://", "data:text/plain,x", "javascript:alert(1)",
        "ws://example.com/",
    ])
    def test_disallowed_schemes_dropped(self, workspace: Path, scheme: str):
        sources, warnings = _run(workspace, [{
            "tool": "web_fetch", "arguments": {"url": scheme + "x"},
        }])
        assert sources == []
        assert any("http://" in w or "https://" in w for w in warnings)

    @pytest.mark.parametrize("url", [
        "http://localhost/x",
        "http://127.0.0.1/x",
        "http://10.0.0.1/x",
        "http://192.168.1.1/x",
        "http://169.254.169.254/latest",   # AWS metadata
        "http://0.0.0.0/",
        "http://[::1]/x",
    ])
    def test_ssrf_targets_dropped(self, workspace: Path, url: str):
        sources, warnings = _run(workspace, [{
            "tool": "web_fetch", "arguments": {"url": url},
        }])
        assert sources == []
        assert any("local network" in w for w in warnings)

    def test_label_truncated(self, workspace: Path):
        url = "https://realpython.com/" + "a" * 200
        sources, _ = _run(workspace, [{
            "tool": "web_fetch", "arguments": {"url": url},
        }])
        # Label cap at 60 chars after the "web_fetch:" prefix.
        assert len(sources[0]["label"]) <= len("web_fetch:") + 60


class TestRssFetchSanitizer:
    def test_well_formed_passes(self, workspace: Path):
        sources, _ = _run(workspace, [{
            "tool": "rss_fetch",
            "arguments": {"url": "https://realpython.com/feed.xml", "max_entries": 7},
        }])

        assert len(sources) == 1
        assert sources[0]["arguments"] == {
            "url": "https://realpython.com/feed.xml",
            "max_entries": 7,
        }
        assert sources[0]["label"].startswith("rss_fetch:https://realpython.com")

    def test_missing_url_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "rss_fetch",
            "arguments": {},
        }])

        assert sources == []
        assert any("without url" in warning for warning in warnings)

    def test_placeholder_host_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "rss_fetch",
            "arguments": {"url": "https://example.com/feed.xml"},
        }])
        assert sources == []
        assert any("placeholder" in warning for warning in warnings)

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "https://пример.рф/feed.xml",
        "http://localhost/feed.xml",
        "http://169.254.169.254/latest",
    ])
    def test_unsafe_url_dropped(self, workspace: Path, url: str):
        sources, warnings = _run(workspace, [{
            "tool": "rss_fetch",
            "arguments": {"url": url},
        }])

        assert sources == []
        assert warnings

    def test_max_entries_default_and_cap(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "rss_fetch",
            "arguments": {"url": "https://realpython.com/feed.xml", "max_entries": "bad"},
        }])

        assert sources[0]["arguments"]["max_entries"] == 20
        assert any("defaulting to 20" in warning for warning in warnings)

        sources, _ = _run(workspace, [{
            "tool": "rss_fetch",
            "arguments": {"url": "https://realpython.com/feed.xml", "max_entries": 500},
        }])
        assert sources[0]["arguments"]["max_entries"] == 50
