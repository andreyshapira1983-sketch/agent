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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from core.evidence import ProvenanceChain, make_evidence
from core.knowledge_pipeline import KnowledgePipelineResult
from core.source_ranker import rank_chain
from core.source_registry import SourceRegistry


TEXT_EXTENSIONS: frozenset[str] = frozenset({
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".ps1",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
})

SKIP_DIR_NAMES: frozenset[str] = frozenset({
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "data",
    "dist",
    "logs",
    "node_modules",
    "procedural_chroma",
    "venv",
})

MAX_FILE_BYTES = 1_000_000
DEFAULT_PROJECT_LIMIT = 80
SOURCE_MAX_CHUNKS = 16
PROJECT_MAX_CHUNKS_PER_FILE = 3
CHUNK_CHARS = 800


@dataclass
class IngestReport:
    mode: str
    requested_path: str
    files_seen: int = 0
    files_ingested: int = 0
    files_skipped: int = 0
    bytes_read: int = 0
    chunks: int = 0
    source_count: int = 0
    claim_count: int = 0
    source_store: dict[str, int] = field(default_factory=dict)
    memory_saved: int = 0
    memory_rejected: int = 0
    memory_skipped: int = 0
    conflicts: int = 0
    dry_run: bool = False
    auto_write_memory: bool = False
    ingested_paths: list[str] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "requested_path": self.requested_path,
            "files_seen": self.files_seen,
            "files_ingested": self.files_ingested,
            "files_skipped": self.files_skipped,
            "bytes_read": self.bytes_read,
            "chunks": self.chunks,
            "source_count": self.source_count,
            "claim_count": self.claim_count,
            "source_store": dict(self.source_store),
            "memory_saved": self.memory_saved,
            "memory_rejected": self.memory_rejected,
            "memory_skipped": self.memory_skipped,
            "conflicts": self.conflicts,
            "dry_run": self.dry_run,
            "auto_write_memory": self.auto_write_memory,
            "ingested_paths": list(self.ingested_paths),
            "skipped_paths": list(self.skipped_paths[:20]),
            "error_count": len(self.errors),
            "errors": list(self.errors[:20]),
        }

    def user_summary(self) -> str:
        store = self.source_store or {}
        parts = [
            f"ingest {self.mode}: files={self.files_ingested}/{self.files_seen}",
            f"chunks={self.chunks}",
            f"sources={self.source_count}",
            f"claims={self.claim_count}",
            f"saved_sources={store.get('sources_saved', 0)}",
            f"saved_claims={store.get('claims_saved', 0)}",
            f"memory_saved={self.memory_saved}",
            f"memory_skipped={self.memory_skipped}",
            f"memory_rejected={self.memory_rejected}",
            f"conflicts={self.conflicts}",
        ]
        if self.dry_run:
            parts.append("dry_run=True")
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        return "(" + "; ".join(parts) + ")"


def ingest_source(
    *,
    agent: Any,
    workspace: Path,
    path: str,
    dry_run: bool = False,
    auto_write_memory: bool | None = None,
) -> IngestReport:
    target = _resolve_inside_workspace(workspace, path)
    report = _ingest_paths(
        agent=agent,
        workspace=workspace,
        paths=[target],
        mode="source",
        requested_path=path,
        dry_run=dry_run,
        auto_write_memory=auto_write_memory,
        max_chunks_per_file=SOURCE_MAX_CHUNKS,
    )
    return report


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
    if root.is_file():
        paths = [root]
    else:
        paths = list(_iter_project_files(root, limit=limit))
    report = _ingest_paths(
        agent=agent,
        workspace=workspace,
        paths=paths,
        mode="project",
        requested_path=path,
        dry_run=dry_run,
        auto_write_memory=auto_write_memory,
        max_chunks_per_file=PROJECT_MAX_CHUNKS_PER_FILE,
    )
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

    # Do not feed raw filenames into `is_realtime_question`: names like
    # "knowledge.txt" contain the substring "now" and can accidentally
    # trigger realtime-source downgrades. Ingestion is a local document
    # task, not a live market/weather query.
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


def _resolve_inside_workspace(workspace: Path, raw: str) -> Path:
    if not raw or not raw.strip():
        raise ValueError("path is required")
    workspace = workspace.resolve()
    candidate = Path(raw.strip().strip('"').strip("'"))
    target = candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
    _ensure_inside_workspace(workspace, target)
    return target


def _ensure_inside_workspace(workspace: Path, target: Path) -> None:
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise PermissionError(f"Path escapes workspace: {target}") from exc


def _iter_project_files(root: Path, *, limit: int) -> Iterable[Path]:
    yielded = 0
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix().casefold()):
        if yielded >= limit:
            break
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        yield path
        yielded += 1


def _chunk_text(text: str, *, max_chunks: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for paragraph in text.replace("\r\n", "\n").split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > CHUNK_CHARS:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(paragraph), CHUNK_CHARS):
                piece = paragraph[start:start + CHUNK_CHARS].strip()
                if piece:
                    chunks.append(piece)
                if len(chunks) >= max_chunks:
                    return chunks
            continue
        candidate = (current + "\n\n" + paragraph).strip() if current else paragraph
        if len(candidate) <= CHUNK_CHARS:
            current = candidate
        else:
            if current:
                chunks.append(current)
                if len(chunks) >= max_chunks:
                    return chunks
            current = paragraph
    if current and len(chunks) < max_chunks:
        chunks.append(current)
    return chunks[:max_chunks]


def _skip(report: IngestReport, path: Path, workspace: Path, reason: str) -> None:
    report.files_skipped += 1
    label = _relative_label(workspace, path)
    report.skipped_paths.append(f"{label} ({reason})")
    report.errors.append(f"{label}: {reason}")


def _relative_label(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except Exception:
        return str(path)
