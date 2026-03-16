"""
Knowledge retrieval: search by query. MVP: simple substring match.
"""
from __future__ import annotations

from src.knowledge.store import list_documents


def search(query: str, top_k: int = 5) -> list[str]:
    q = query.lower()
    out: list[tuple[int, str]] = []
    for d in list_documents():
        c = (d.get("content") or "").lower()
        if q in c:
            out.append((c.index(q), d.get("content", "")))
    out.sort(key=lambda x: x[0])
    return [s for _, s in out[:top_k]]
