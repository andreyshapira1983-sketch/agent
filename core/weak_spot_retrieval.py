"""Reflection weak spot -> evidence -> code files.

Two techniques from the literature: evidence grounding (a reflection conclusion must cite concrete cases; for a lesson the
evidence is the error pattern from the logs, `Lesson.pattern`; no evidence means the weak spot is not studied) and BM25 file
retrieval by description (query = task description, documents = files; the same BM25 as core/memory_policy.py). BM25 finds no
relevant file in about half of the cases, so this is a baseline, not exact.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.bm25 import bm25_scores, term_counts, tokens
from core.ingestion import SKIP_DIR_NAMES

#: Сколько файлов поиск отдаёт в план: столько же, сколько план берёт обычно.
TOP_K = 5


def evidence_query(lesson: Any) -> str:
    """Query built from the lesson's evidence; empty means no evidence and nothing to study."""
    pattern = getattr(lesson, "pattern", None)
    if pattern is None or int(getattr(pattern, "count", 0) or 0) < 1:
        return ""
    parts = (getattr(lesson, "focus_area", ""), getattr(pattern, "tool_name", ""),
             getattr(pattern, "event_type", ""), getattr(pattern, "sample_message", ""))
    return " ".join(str(p) for p in parts if p).strip()


def retrieve_files(workspace: Path, query: str, *, top_k: int = TOP_K) -> list[str]:
    """`.py` files with the best BM25 score against the query; only non-zero scores."""
    root = Path(workspace).resolve()
    q = tokens(query)
    if not q:
        return []
    rels: list[str] = []
    docs = []
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIR_NAMES for part in path.parts) or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith("tests/"):
            # Tests quote error texts verbatim and took all five places; studying a weak spot means reading the code that errs,
            # and the relevant files are sources, not tests.
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # The path is part of the document: a module name says as much as its text.
        rels.append(rel)
        docs.append(term_counts(rel.replace("/", " ").replace("_", " ") + " " + text, []))
    scores = bm25_scores(q, docs)
    ranked = sorted((s, r) for s, r in zip(scores, rels, strict=True) if s > 0)
    return [r for _s, r in sorted(ranked, key=lambda x: (-x[0], x[1]))[:top_k]]
