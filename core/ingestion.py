"""Controlled document/code ingestion.

This is the bridge between "answer from one file hint" and "learn from a
curated source set":

    local file(s) -> Evidence chain -> Source Registry -> claims
    -> conflict check -> optional persistent memory write

The layer is intentionally local and deterministic. It never calls an LLM,
never writes source text verbatim into memory, and keeps memory writes gated by
the existing KnowledgePipeline + MemoryWritePolicy.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from core.evidence import ProvenanceChain, evidence_from_tool_result, make_evidence
from core.ingestion_reports import IngestReport, RssIngestReport, WebIngestReport
from core.ingestion_utils import (
    SKIP_DIR_NAMES,
    TEXT_EXTENSIONS,
    _candidate_urls,
    _chunk_text,
    _ensure_inside_workspace,
    _is_http_url,
    _iter_project_files,
    _relative_label,
    _resolve_inside_workspace,
    _skip,
)
from core.knowledge_pipeline import KnowledgePipelineResult
from core.source_library import resolve_source_library
from core.source_ranker import rank_chain
from core.source_registry import SourceRegistry


MAX_FILE_BYTES = 1_000_000
DEFAULT_PROJECT_LIMIT = 80
SOURCE_MAX_CHUNKS = 16
PROJECT_MAX_CHUNKS_PER_FILE = 3
CHUNK_CHARS = 800


def ingest_source(
    *,
    agent: Any,
    workspace: Path,
    path: str,
    dry_run: bool = False,
    auto_write_memory: bool | None = None,
) -> IngestReport:
    target = _resolve_inside_workspace(workspace, path)
    return _ingest_paths(
        agent=agent,
        workspace=workspace,
        paths=[target],
        mode="source",
        requested_path=path,
        dry_run=dry_run,
        auto_write_memory=auto_write_memory,
        max_chunks_per_file=SOURCE_MAX_CHUNKS,
    )


def ingest_project(
    *,
    agent: Any,
    workspace: Path,
    path: str = ".",
    limit: int = DEFAULT_PROJECT_LIMIT,
    dry_run: bool = False,
    auto_write_memory: bool | None = None,
) -> IngestReport:
    root = _resolve_inside_workspace(workspace, path)
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")
    paths = [root] if root.is_file() else list(_iter_project_files(root, limit=limit))
    return _ingest_paths(
        agent=agent,
        workspace=workspace,
        paths=paths,
        mode="project",
        requested_path=path,
        dry_run=dry_run,
        auto_write_memory=auto_write_memory,
        max_chunks_per_file=PROJECT_MAX_CHUNKS_PER_FILE,
    )


def ingest_web_topic(
    *,
    agent: Any,
    topic: str,
    source_selection: str | Iterable[str] | None = None,
    limit: int = 5,
    per_source: int = 1,
    dry_run: bool = False,
    auto_write_memory: bool | None = None,
) -> WebIngestReport:
    from core.source_library import SourceLibraryEntry

    topic = (topic or "").strip()
    if not topic:
        raise ValueError("topic is required")
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if per_source < 1:
        raise ValueError("per_source must be >= 1")

    entries: list[SourceLibraryEntry] = resolve_source_library(source_selection)
    write_memory = bool(getattr(agent, "knowledge_auto_write", False))
    if auto_write_memory is not None:
        write_memory = bool(auto_write_memory)
    if dry_run:
        write_memory = False

    report = WebIngestReport(
        mode="web",
        topic=topic,
        source_ids=[entry.id for entry in entries],
        dry_run=dry_run,
        auto_write_memory=write_memory,
    )
    chain = ProvenanceChain()
    seen_urls: set[str] = set()

    registry = getattr(agent, "registry", None)
    if registry is None:
        raise ValueError("agent has no tool registry")
    web_search = registry.get("web_search")
    web_fetch = registry.get("web_fetch")

    for entry in entries:
        if report.pages_fetched >= limit:
            break
        query = entry.query(topic)
        search_limit = max(3, per_source * 3)
        report.searches += 1
        try:
            search_output = web_search.run(query=query, max_results=search_limit)
            ok, issues = web_search.validate_output(search_output)
            if not ok:
                raise ValueError("; ".join(issues) or "web_search validation failed")
            if issues:
                report.errors.extend(f"{entry.id} search warning: {issue}" for issue in issues)
            search_ev = evidence_from_tool_result(
                tool_name="web_search",
                arguments={"query": query, "max_results": search_limit},
                output=search_output,
                status="success",
            )
            if search_ev is not None:
                chain.add(search_ev)
            report.search_results += len(search_output) if isinstance(search_output, list) else 0
        except Exception as exc:
            report.errors.append(f"{entry.id} search failed: {type(exc).__name__}: {exc}")
            continue

        fetched_for_source = 0
        for url in _candidate_urls(search_output, entry=entry):
            if report.pages_fetched >= limit or fetched_for_source >= per_source:
                break
            if url in seen_urls:
                continue
            seen_urls.add(url)
            try:
                fetched = web_fetch.run(url=url)
                ok, issues = web_fetch.validate_output(fetched)
                if not ok:
                    raise ValueError("; ".join(issues) or "web_fetch validation failed")
                if issues:
                    report.errors.extend(f"{url} fetch warning: {issue}" for issue in issues)
                fetch_ev = evidence_from_tool_result(
                    tool_name="web_fetch",
                    arguments={"url": url},
                    output=fetched,
                    status="success",
                )
                if fetch_ev is None:
                    report.skipped_urls.append(f"{url}: no evidence")
                    continue
                chain.add(fetch_ev)
                fetched_for_source += 1
                report.pages_fetched += 1
                report.fetched_urls.append(url)
                report.bytes_read += int(fetched.get("bytes", 0)) if isinstance(fetched, dict) else 0
                text = fetched.get("text", "") if isinstance(fetched, dict) else ""
                report.chunks += len(_chunk_text(str(text), max_chunks=SOURCE_MAX_CHUNKS))
            except Exception as exc:
                report.errors.append(f"{url} fetch failed: {type(exc).__name__}: {exc}")
                report.skipped_urls.append(url)

    ranking = rank_chain(chain, question="controlled online source library ingestion")
    knowledge_result: KnowledgePipelineResult = agent.knowledge_pipeline.run(
        chain,
        ranking=ranking,
        source_store=None if dry_run else getattr(agent, "source_registry_store", None),
        remember=getattr(agent, "_remember_from_knowledge", None),
        auto_write_memory=write_memory,
    )

    registry_out: SourceRegistry = knowledge_result.registry
    report.source_count = len(registry_out.sources)
    report.claim_count = len(registry_out.claims)
    report.source_store = dict(knowledge_result.source_store)
    report.memory_saved = knowledge_result.memory_saved
    report.memory_rejected = knowledge_result.memory_rejected
    report.memory_skipped = knowledge_result.memory_skipped
    report.conflicts = knowledge_result.conflicts.count

    agent.last_provenance = chain
    agent.last_source_ranking = ranking
    agent.last_source_registry = registry_out
    agent.last_knowledge_pipeline = knowledge_result

    log = getattr(agent, "log", None)
    if log is not None:
        log.log("ingest", report.to_log_payload())
        log.log("evidence_collected", {
            "count": len(chain),
            "kinds": sorted({ev.kind for ev in chain.evidences}),
            "chain": chain.to_log_payload(),
            "mode": "ingest_web",
        })
        log.log("source_ranking", {
            **ranking.to_log_payload(),
            "mode": "ingest_web",
        })
        log.log("source_registry", {
            **registry_out.to_log_payload(),
            "mode": "ingest_web",
        })
        log.log("knowledge_pipeline", {
            **knowledge_result.to_log_payload(),
            "mode": "ingest_web",
        })

    return report


def ingest_rss_feed(
    *,
    agent: Any,
    url: str,
    limit: int = 10,
    dry_run: bool = False,
    auto_write_memory: bool | None = None,
) -> RssIngestReport:
    url = (url or "").strip()
    if not url:
        raise ValueError("url is required")
    if limit < 1:
        raise ValueError("limit must be >= 1")

    write_memory = bool(getattr(agent, "knowledge_auto_write", False))
    if auto_write_memory is not None:
        write_memory = bool(auto_write_memory)
    if dry_run:
        write_memory = False

    report = RssIngestReport(
        mode="rss",
        feed_url=url,
        dry_run=dry_run,
        auto_write_memory=write_memory,
    )
    chain = ProvenanceChain()

    registry = getattr(agent, "registry", None)
    if registry is None:
        raise ValueError("agent has no tool registry")
    rss_fetch = registry.get("rss_fetch")
    output = rss_fetch.run(url=url, max_entries=limit)
    ok, issues = rss_fetch.validate_output(output)
    if not ok:
        raise ValueError("; ".join(issues) or "rss_fetch validation failed")
    if issues:
        report.errors.extend(f"rss warning: {issue}" for issue in issues)

    entries = output.get("entries", []) if isinstance(output, dict) else []
    report.feed_title = str(output.get("title") or "") if isinstance(output, dict) else ""
    report.entries_seen = len(entries) if isinstance(entries, list) else 0
    report.bytes_read = int(output.get("bytes", 0)) if isinstance(output, dict) else 0
    fetched_at = str(output.get("fetched_at") or "") if isinstance(output, dict) else ""

    if isinstance(entries, list):
        for idx, entry in enumerate(entries[:limit], start=1):
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()
            entry_url = str(entry.get("url") or "").strip() or f"{url}#entry-{idx}"
            summary = str(entry.get("summary") or "").strip()
            published_at = str(entry.get("published_at") or "").strip()
            text = "\n".join(
                part
                for part in (
                    title,
                    f"URL: {entry_url}",
                    f"Published: {published_at}" if published_at else "",
                    summary,
                )
                if part
            )
            if not text.strip():
                continue
            chain.add(make_evidence(
                kind="web_page",
                source_id=f"web_page:{entry_url}",
                obtained_via="rss_fetch",
                claim=f"RSS entry from {url}: {title or entry_url}",
                excerpt=text,
                fetched_at=fetched_at or None,
                confidence=0.68,
            ))
            report.entries_ingested += 1
            report.entry_urls.append(entry_url)

    ranking = rank_chain(chain, question="controlled rss feed ingestion")
    knowledge_result: KnowledgePipelineResult = agent.knowledge_pipeline.run(
        chain,
        ranking=ranking,
        source_store=None if dry_run else getattr(agent, "source_registry_store", None),
        remember=getattr(agent, "_remember_from_knowledge", None),
        auto_write_memory=write_memory,
    )

    registry_out: SourceRegistry = knowledge_result.registry
    report.source_count = len(registry_out.sources)
    report.claim_count = len(registry_out.claims)
    report.source_store = dict(knowledge_result.source_store)
    report.memory_saved = knowledge_result.memory_saved
    report.memory_rejected = knowledge_result.memory_rejected
    report.memory_skipped = knowledge_result.memory_skipped
    report.conflicts = knowledge_result.conflicts.count

    agent.last_provenance = chain
    agent.last_source_ranking = ranking
    agent.last_source_registry = registry_out
    agent.last_knowledge_pipeline = knowledge_result

    log = getattr(agent, "log", None)
    if log is not None:
        log.log("ingest", report.to_log_payload())
        log.log("evidence_collected", {
            "count": len(chain),
            "kinds": sorted({ev.kind for ev in chain.evidences}),
            "chain": chain.to_log_payload(),
            "mode": "ingest_rss",
        })
        log.log("source_ranking", {
            **ranking.to_log_payload(),
            "mode": "ingest_rss",
        })
        log.log("source_registry", {
            **registry_out.to_log_payload(),
            "mode": "ingest_rss",
        })
        log.log("knowledge_pipeline", {
            **knowledge_result.to_log_payload(),
            "mode": "ingest_rss",
        })

    return report


def ingest_files(
    *,
    agent: Any,
    workspace: Path,
    paths: Iterable[str],
    dry_run: bool = False,
    auto_write_memory: bool | None = None,
) -> IngestReport:
    resolved = [_resolve_inside_workspace(workspace, path) for path in paths]
    return _ingest_paths(
        agent=agent,
        workspace=workspace,
        paths=resolved,
        mode="learning",
        requested_path="; ".join(paths),
        dry_run=dry_run,
        auto_write_memory=auto_write_memory,
        max_chunks_per_file=PROJECT_MAX_CHUNKS_PER_FILE,
    )


def _ingest_paths(
    *,
    agent: Any,
    workspace: Path,
    paths: Iterable[Path],
    mode: str,
    requested_path: str,
    dry_run: bool,
    auto_write_memory: bool | None,
    max_chunks_per_file: int,
) -> IngestReport:
    workspace = workspace.resolve()
    write_memory = bool(getattr(agent, "knowledge_auto_write", False))
    if auto_write_memory is not None:
        write_memory = bool(auto_write_memory)
    if dry_run:
        write_memory = False
    # Self-ingest of the workspace's own tree (``:ingest-project``) is
    # evidence-only: it builds the source registry to answer the CURRENT
    # question. It must NEVER mass-persist code/docstring fragments as long-term
    # "facts" — that floods memory with thousands of mid-sentence source chunks
    # (retrieval distractors) that are re-derivable from the code itself. A
    # caller wanting to learn one specific document uses ``:ingest-source``.
    if mode == "project":
        write_memory = False

    report = IngestReport(
        mode=mode,
        requested_path=requested_path,
        dry_run=dry_run,
        auto_write_memory=write_memory,
    )
    chain = ProvenanceChain()

    for path in paths:
        report.files_seen += 1
        try:
            path = path.resolve()
            _ensure_inside_workspace(workspace, path)
            if not path.is_file():
                _skip(report, path, workspace, "not a file")
                continue
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                _skip(report, path, workspace, "unsupported extension")
                continue
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                _skip(report, path, workspace, f"too large ({size} bytes)")
                continue
            text = path.read_text(encoding="utf-8", errors="strict")
            if not text.strip():
                _skip(report, path, workspace, "empty file")
                continue
        except UnicodeDecodeError:
            _skip(report, path, workspace, "not valid UTF-8")
            continue
        except Exception as exc:
            _skip(report, path, workspace, f"{type(exc).__name__}: {exc}")
            continue

        rel = _relative_label(workspace, path)
        chunks = _chunk_text(text, max_chunks=max_chunks_per_file)
        if not chunks:
            _skip(report, path, workspace, "no text chunks")
            continue
        report.files_ingested += 1
        report.bytes_read += path.stat().st_size
        report.chunks += len(chunks)
        report.ingested_paths.append(rel)
        for idx, chunk in enumerate(chunks, start=1):
            chain.add(make_evidence(
                kind="file",
                source_id=f"file:{rel}",
                obtained_via="ingest_source",
                claim=f"read {rel} chunk {idx}",
                excerpt=chunk,
            ))

    ranking = rank_chain(chain, question="controlled document ingestion")
    knowledge_result: KnowledgePipelineResult = agent.knowledge_pipeline.run(
        chain,
        ranking=ranking,
        source_store=None if dry_run else getattr(agent, "source_registry_store", None),
        remember=getattr(agent, "_remember_from_knowledge", None),
        auto_write_memory=write_memory,
    )

    registry: SourceRegistry = knowledge_result.registry
    report.source_count = len(registry.sources)
    report.claim_count = len(registry.claims)
    report.source_store = dict(knowledge_result.source_store)
    report.memory_saved = knowledge_result.memory_saved
    report.memory_rejected = knowledge_result.memory_rejected
    report.memory_skipped = knowledge_result.memory_skipped
    report.conflicts = knowledge_result.conflicts.count

    agent.last_provenance = chain
    agent.last_source_ranking = ranking
    agent.last_source_registry = registry
    agent.last_knowledge_pipeline = knowledge_result

    log = getattr(agent, "log", None)
    if log is not None:
        log.log("ingest", report.to_log_payload())
        log.log("evidence_collected", {
            "count": len(chain),
            "kinds": sorted({ev.kind for ev in chain.evidences}),
            "chain": chain.to_log_payload(),
            "mode": f"ingest_{mode}",
        })
        log.log("source_ranking", {
            **ranking.to_log_payload(),
            "mode": f"ingest_{mode}",
        })
        log.log("source_registry", {
            **registry.to_log_payload(),
            "mode": f"ingest_{mode}",
        })
        log.log("knowledge_pipeline", {
            **knowledge_result.to_log_payload(),
            "mode": f"ingest_{mode}",
        })

    return report
