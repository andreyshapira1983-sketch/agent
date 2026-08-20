"""Source ingestion + source-registry + planning REPL commands.

Split out of ``main.py``. The whole cluster is self-contained: it uses only
``cli.parsers``, ``core.ingestion`` / ``core.source_library``, the agent's
public surface, and its own internal helpers — never back into ``main`` — so
there is no import cycle. ``main.py`` re-exports the nine command handlers used
by the REPL dispatch and the conversational router; the payload / format /
extract helpers stay internal to this module.
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from cli.parsers import _parse_ingest_options, _split_meta_args
from core.ingestion import (
    ingest_project,
    ingest_rss_feed,
    ingest_source,
    ingest_web_topic,
)

if TYPE_CHECKING:
    from pathlib import Path

    from core.loop import AgentLoop


def _report_ingest_failure(
    agent: AgentLoop, kind: str, exc: BaseException, target: str
) -> None:
    """A failed ingest must survive in the journal, not only on the screen.

    All four ingest handlers printed the exception to stderr and returned
    `True` — the same value a success returns. So the failure existed for
    exactly as long as the operator was watching the console: an hour later,
    reading `logs/*.jsonl`, there was nothing to find. Three other handlers in
    this same file already journal through `agent.log`; these four did not.

    Wrapped, because the report is best effort and the REPL is not: a logger
    that raises must not turn a failed ingest into a crashed session. The
    print below still happens either way.
    """
    try:
        agent.log.log(
            "ingest_failed",
            {
                "kind": kind,
                "target": target,
                "exception_type": type(exc).__name__,
                "error": str(exc)[:300],
            },
        )
    except Exception:  # noqa: BLE001, S110 - last resort around the write itself
        pass  # nosec B110 - the command's own report below is what the user sees


def _handle_ingest_source(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    path, dry_run, auto_write, _limit, error = _parse_ingest_options(
        rest,
        default_path=None,
    )
    if error or path is None:
        print(error or "Usage: :ingest-source <path>", file=sys.stderr)
        return True
    try:
        report = ingest_source(
            agent=agent,
            workspace=workspace,
            path=path,
            dry_run=dry_run,
            auto_write_memory=auto_write,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary: any ingest
        # failure is reported and journalled, never allowed to kill the REPL
        _report_ingest_failure(agent, "source", exc, str(path))
        print(f"(ingest failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True
    print(report.user_summary(), file=sys.stderr)
    return True


def _handle_ingest_project(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    path, dry_run, auto_write, limit, error = _parse_ingest_options(
        rest,
        default_path=".",
    )
    if error or path is None:
        print(error or "Usage: :ingest-project [path]", file=sys.stderr)
        return True
    try:
        report = ingest_project(
            agent=agent,
            workspace=workspace,
            path=path,
            limit=limit,
            dry_run=dry_run,
            auto_write_memory=auto_write,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary: any ingest
        # failure is reported and journalled, never allowed to kill the REPL
        _report_ingest_failure(agent, "project", exc, str(path))
        print(f"(ingest failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True
    print(report.user_summary(), file=sys.stderr)
    return True


def _handle_ingest_web(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    tokens = _split_meta_args(rest)
    dry_run = False
    auto_write: bool | None = None
    limit = 5
    per_source = 1
    source_selection: str | None = None
    topic_parts: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--write-memory":
            auto_write = True
            i += 1
            continue
        if token == "--no-memory":
            auto_write = False
            i += 1
            continue
        if token == "--sources":
            if i + 1 >= len(tokens):
                print("Usage: --sources requires a comma-separated list or group", file=sys.stderr)
                return True
            source_selection = tokens[i + 1]
            i += 2
            continue
        if token == "--limit":
            if i + 1 >= len(tokens):
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        if token == "--per-source":
            if i + 1 >= len(tokens):
                print("Usage: --per-source requires a number", file=sys.stderr)
                return True
            try:
                per_source = int(tokens[i + 1])
            except ValueError:
                print("Usage: --per-source requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        topic_parts.append(token)
        i += 1

    topic = " ".join(topic_parts).strip()
    if not topic:
        print(
            "Usage: :ingest-web <topic> [--sources wikis|books|science|docs|all|id,id] "
            "[--limit N] [--per-source N] [--dry-run] [--write-memory|--no-memory]",
            file=sys.stderr,
        )
        return True

    try:
        report = ingest_web_topic(
            agent=agent,
            topic=topic,
            source_selection=source_selection,
            limit=limit,
            per_source=per_source,
            dry_run=dry_run,
            auto_write_memory=auto_write,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary: any ingest
        # failure is reported and journalled, never allowed to kill the REPL
        _report_ingest_failure(agent, "web", exc, topic)
        print(f"(ingest web failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True
    print(report.user_summary(), file=sys.stderr)
    return True


def _handle_ingest_rss(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    tokens = _split_meta_args(rest)
    dry_run = False
    auto_write: bool | None = None
    limit = 10
    url_parts: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--write-memory":
            auto_write = True
            i += 1
            continue
        if token == "--no-memory":
            auto_write = False
            i += 1
            continue
        if token == "--limit":
            if i + 1 >= len(tokens):
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        url_parts.append(token)
        i += 1

    url = " ".join(url_parts).strip()
    if not url:
        print(
            "Usage: :ingest-rss <feed_url> [--limit N] [--dry-run] "
            "[--write-memory|--no-memory]",
            file=sys.stderr,
        )
        return True

    try:
        report = ingest_rss_feed(
            agent=agent,
            url=url,
            limit=limit,
            dry_run=dry_run,
            auto_write_memory=auto_write,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary: any ingest
        # failure is reported and journalled, never allowed to kill the REPL
        _report_ingest_failure(agent, "rss", exc, url)
        print(f"(ingest rss failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True
    print(report.user_summary(), file=sys.stderr)
    return True
