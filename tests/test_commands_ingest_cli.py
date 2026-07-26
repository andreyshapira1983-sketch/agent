"""Ingestion commands — the flags that decide whether we hit the network and write memory.

`:ingest-web` and `:ingest-rss` fetch from the internet; `:ingest-source` and
`:ingest-project` read the disk. All four can write to persistent memory. So the
three flags that matter are `--dry-run`, `--write-memory` and `--no-memory`, and
every one of them must arrive at the ingestion call intact.

This file covers the **command surface** of `cli/commands_ingest.py`: option
parsing, usage refusals, failure reporting and flag forwarding. The pure
planning/formatting helpers below the handlers (source-review, implementation
plan, patch proposal) are a separate pass — they are covered here only where a
handler walks through them.

Nothing here reaches the network: every ingestion entry point is faked, and a
usage error must never reach it at all.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import cli.commands_ingest as mod
from cli.commands_ingest import (
    _handle_ingest_project,
    _handle_ingest_rss,
    _handle_ingest_source,
    _handle_ingest_web,
    _handle_source_library,
    _handle_source_registry,
)


class FakeLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def log(self, event, payload=None):
        self.events.append((event, payload))

    def kinds(self):
        return [e for e, _ in self.events]


@pytest.fixture
def agent():
    return SimpleNamespace(log=FakeLog())


def report(summary="INGEST-SUMMARY"):
    return SimpleNamespace(user_summary=lambda: summary)


def never(*a, **k):
    raise AssertionError("no ingestion may run for a usage error")


# ── :ingest-source / :ingest-project — disk ingestion ────────────────────────

def test_ingest_source_without_a_path_prints_usage(agent, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(mod, "ingest_source", never)
    assert _handle_ingest_source("", agent, tmp_path) is True
    assert "Usage: :ingest-source <path>" in capsys.readouterr().err


def test_ingest_source_forwards_its_flags(agent, tmp_path, monkeypatch):
    seen: dict = {}

    def _ingest(**kwargs):
        seen.update(kwargs)
        return report()

    monkeypatch.setattr(mod, "ingest_source", _ingest)

    assert _handle_ingest_source("docs/notes.txt --dry-run --no-memory", agent, tmp_path) is True

    assert seen["path"] == "docs/notes.txt"
    assert seen["dry_run"] is True
    assert seen["auto_write_memory"] is False
    assert seen["workspace"] == tmp_path


def test_ingest_source_reports_a_failure_instead_of_raising(agent, tmp_path, capsys, monkeypatch):
    def _boom(**kwargs):
        raise OSError("file vanished")

    monkeypatch.setattr(mod, "ingest_source", _boom)

    assert _handle_ingest_source("docs/notes.txt", agent, tmp_path) is True
    assert "(ingest failed: OSError: file vanished)" in capsys.readouterr().err


def test_ingest_project_defaults_to_the_current_directory(agent, tmp_path, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(mod, "ingest_project", lambda **kw: seen.update(kw) or report())

    assert _handle_ingest_project("--write-memory --limit 3", agent, tmp_path) is True

    assert seen["path"] == "."
    assert seen["limit"] == 3
    assert seen["auto_write_memory"] is True
    assert seen["dry_run"] is False


def test_ingest_project_reports_a_failure(agent, tmp_path, capsys, monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("scanner exploded")

    monkeypatch.setattr(mod, "ingest_project", _boom)

    assert _handle_ingest_project(".", agent, tmp_path) is True
    assert "(ingest failed: RuntimeError: scanner exploded)" in capsys.readouterr().err


def test_ingest_project_rejects_a_bad_option(agent, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(mod, "ingest_project", never)
    assert _handle_ingest_project(". --limit", agent, tmp_path) is True
    assert "--limit" in capsys.readouterr().err


# ── :source-library ──────────────────────────────────────────────────────────

def test_source_library_lists_groups_and_entries(capsys):
    assert _handle_source_library("") is True

    err = capsys.readouterr().err
    assert "=== source library ===" in err
    assert "groups: " in err
    assert "trust=" in err


def test_source_library_filters_by_group(capsys):
    assert _handle_source_library("--json") is True
    payload = json.loads(capsys.readouterr().err)
    group = sorted(payload["groups"])[0]

    assert _handle_source_library(group) is True
    err = capsys.readouterr().err
    assert "=== source library ===" in err


def test_source_library_refuses_two_group_arguments(capsys):
    assert _handle_source_library("wikis books") is True
    assert "Usage: :source-library [group|all] [--json]" in capsys.readouterr().err


# ── :source-registry ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "rest,expected",
    [
        ("--limit", "Usage: --limit requires a number"),
        ("--limit many", "Usage: --limit requires a number"),
        ("--unknown", "unknown :source-registry option --unknown"),
        ("--limit 0", "Usage: --limit must be >= 1"),
    ],
)
def test_source_registry_usage_errors(rest, expected, agent, tmp_path, capsys):
    assert _handle_source_registry(rest, agent, tmp_path) is True
    assert expected in capsys.readouterr().err


def test_source_registry_without_a_registry_reports_an_empty_payload(agent, tmp_path, capsys):
    assert _handle_source_registry("--json", agent, tmp_path) is True

    payload = json.loads(capsys.readouterr().err)
    assert payload["sources"] == 0
    assert payload["path"] is None


# ── :ingest-web — the network command ────────────────────────────────────────

@pytest.mark.parametrize(
    "rest,expected",
    [
        ("", "Usage: :ingest-web <topic>"),
        ("--dry-run", "Usage: :ingest-web <topic>"),
        ("agents --sources", "Usage: --sources requires a comma-separated list or group"),
        ("agents --limit", "Usage: --limit requires a number"),
        ("agents --limit lots", "Usage: --limit requires a number"),
        ("agents --per-source", "Usage: --per-source requires a number"),
        ("agents --per-source many", "Usage: --per-source requires a number"),
    ],
)
def test_ingest_web_usage_errors_never_reach_the_network(
    rest, expected, agent, tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(mod, "ingest_web_topic", never)

    assert _handle_ingest_web(rest, agent, tmp_path) is True
    assert expected in capsys.readouterr().err


def test_ingest_web_defaults_are_conservative(agent, tmp_path, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(mod, "ingest_web_topic", lambda **kw: seen.update(kw) or report())

    assert _handle_ingest_web("autonomous agents", agent, tmp_path) is True

    assert seen["topic"] == "autonomous agents"
    assert seen["dry_run"] is False
    assert seen["limit"] == 5
    assert seen["per_source"] == 1
    assert seen["source_selection"] is None
    assert seen["auto_write_memory"] is None, "memory writing is left to the caller's policy"


def test_ingest_web_forwards_every_flag(agent, tmp_path, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(mod, "ingest_web_topic", lambda **kw: seen.update(kw) or report())

    assert _handle_ingest_web(
        "multi agent systems --sources wikis,science --limit 7 --per-source 2 "
        "--dry-run --write-memory",
        agent,
        tmp_path,
    ) is True

    assert seen["topic"] == "multi agent systems"
    assert seen["source_selection"] == "wikis,science"
    assert seen["limit"] == 7
    assert seen["per_source"] == 2
    assert seen["dry_run"] is True
    assert seen["auto_write_memory"] is True


def test_ingest_web_no_memory_flag_wins_over_the_default(agent, tmp_path, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(mod, "ingest_web_topic", lambda **kw: seen.update(kw) or report())

    assert _handle_ingest_web("agents --no-memory", agent, tmp_path) is True
    assert seen["auto_write_memory"] is False


def test_ingest_web_reports_a_fetch_failure(agent, tmp_path, capsys, monkeypatch):
    def _boom(**kwargs):
        raise TimeoutError("provider did not answer")

    monkeypatch.setattr(mod, "ingest_web_topic", _boom)

    assert _handle_ingest_web("agents", agent, tmp_path) is True
    assert "(ingest web failed: TimeoutError: provider did not answer)" in capsys.readouterr().err


def test_ingest_web_prints_the_report(agent, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(mod, "ingest_web_topic", lambda **kw: report("WEB-REPORT"))

    assert _handle_ingest_web("agents", agent, tmp_path) is True
    assert "WEB-REPORT" in capsys.readouterr().err


# ── :ingest-rss — the other network command ──────────────────────────────────

@pytest.mark.parametrize(
    "rest,expected",
    [
        ("", "Usage: :ingest-rss <feed_url>"),
        ("--dry-run", "Usage: :ingest-rss <feed_url>"),
        ("https://example.com/feed --limit", "Usage: --limit requires a number"),
        ("https://example.com/feed --limit soon", "Usage: --limit requires a number"),
    ],
)
def test_ingest_rss_usage_errors_never_reach_the_network(
    rest, expected, agent, tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(mod, "ingest_rss_feed", never)

    assert _handle_ingest_rss(rest, agent, tmp_path) is True
    assert expected in capsys.readouterr().err


def test_ingest_rss_defaults_and_flag_forwarding(agent, tmp_path, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(mod, "ingest_rss_feed", lambda **kw: seen.update(kw) or report())

    assert _handle_ingest_rss("https://example.com/feed", agent, tmp_path) is True
    assert seen == {
        "agent": agent,
        "url": "https://example.com/feed",
        "limit": 10,
        "dry_run": False,
        "auto_write_memory": None,
    }

    seen.clear()
    assert _handle_ingest_rss(
        "https://example.com/feed --limit 3 --dry-run --no-memory", agent, tmp_path
    ) is True
    assert seen["limit"] == 3
    assert seen["dry_run"] is True
    assert seen["auto_write_memory"] is False


def test_ingest_rss_write_memory_flag(agent, tmp_path, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(mod, "ingest_rss_feed", lambda **kw: seen.update(kw) or report())

    assert _handle_ingest_rss("https://example.com/feed --write-memory", agent, tmp_path) is True
    assert seen["auto_write_memory"] is True


def test_ingest_rss_reports_a_fetch_failure(agent, tmp_path, capsys, monkeypatch):
    def _boom(**kwargs):
        raise ConnectionError("feed unreachable")

    monkeypatch.setattr(mod, "ingest_rss_feed", _boom)

    assert _handle_ingest_rss("https://example.com/feed", agent, tmp_path) is True
    assert "(ingest rss failed: ConnectionError: feed unreachable)" in capsys.readouterr().err


def test_ingest_rss_prints_the_report(agent, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(mod, "ingest_rss_feed", lambda **kw: report("RSS-REPORT"))

    assert _handle_ingest_rss("https://example.com/feed", agent, tmp_path) is True
    assert "RSS-REPORT" in capsys.readouterr().err
