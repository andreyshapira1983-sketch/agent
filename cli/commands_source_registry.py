"""REPL commands over the source library and the Source Registry.

Split out of ``cli/commands_ingest.py`` on 2026-08-20: reading what is already
stored is a different subject from putting things in, and the two shared no
reference.
"""
from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from cli.parsers import _split_meta_args
from core.source_library import list_source_library, source_library_payload

if TYPE_CHECKING:
    from pathlib import Path

    from core.loop import AgentLoop

def _handle_source_library(rest: str) -> bool:
    tokens = _split_meta_args(rest)
    as_json = False
    group: str | None = None
    for token in tokens:
        if token == "--json":
            as_json = True
            continue
        if group is not None:
            print("Usage: :source-library [group|all] [--json]", file=sys.stderr)
            return True
        group = token

    payload = source_library_payload()
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return True

    entries = list_source_library()
    if group and group != "all":
        wanted = set(payload["groups"].get(group, [group]))
        entries = tuple(entry for entry in entries if entry.id in wanted)
    print("=== source library ===", file=sys.stderr)
    print("groups: " + ", ".join(sorted(payload["groups"])), file=sys.stderr)
    for entry in entries:
        print(
            f"  {entry.id} [{entry.category}] trust={entry.trust_level:.2f} "
            f"domains={','.join(entry.allowed_domains)}",
            file=sys.stderr,
        )
        print(f"    {entry.description}", file=sys.stderr)
    return True


def _handle_source_registry(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    tokens = _split_meta_args(rest)
    as_json = False
    limit = 20
    show_claims = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--json":
            as_json = True
            i += 1
            continue
        if token == "--claims":
            show_claims = True
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
        print(f"Usage: unknown :source-registry option {token}", file=sys.stderr)
        return True
    if limit < 1:
        print("Usage: --limit must be >= 1", file=sys.stderr)
        return True

    store = getattr(agent, "source_registry_store", None)
    registry = store.load_registry() if store is not None else getattr(agent, "last_source_registry", None)
    if registry is None:
        payload = {
            "path": None,
            "sources": 0,
            "claims": 0,
            "items": [],
        }
    else:
        claims_by_source: dict[str, list] = {}
        for claim in registry.claims:
            claims_by_source.setdefault(claim.source_id, []).append(claim)
        sources = list(registry.sources)
        items = []
        for source in sources[:limit]:
            source_claims = claims_by_source.get(source.id, [])
            item = {
                "id": source.id,
                "type": source.type,
                "title": source.title,
                "locator": source.locator,
                "trust_level": source.trust_level,
                "claim_count": len(source_claims),
            }
            if show_claims:
                item["claims"] = [
                    {
                        "id": claim.id,
                        "status": claim.status,
                        "confidence": claim.confidence,
                        "locator": claim.locator,
                        "text": claim.text,
                    }
                    for claim in source_claims[:limit]
                ]
            items.append(item)
        payload = {
            "path": str(store.path) if store is not None else None,
            "sources": len(registry.sources),
            "claims": len(registry.claims),
            "shown": len(items),
            "limit": limit,
            "items": items,
        }

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return True

    print("=== source registry ===", file=sys.stderr)
    if payload["path"]:
        print(f"path: {payload['path']}", file=sys.stderr)
    print(
        f"sources={payload['sources']} claims={payload['claims']} "
        f"shown={payload.get('shown', 0)} limit={payload.get('limit', limit)}",
        file=sys.stderr,
    )
    if not payload["items"]:
        print("(no ingested sources)", file=sys.stderr)
        return True
    for item in payload["items"]:
        print(
            f"  {item['id']} [{item['type']}] claims={item['claim_count']} "
            f"trust={float(item['trust_level']):.2f}",
            file=sys.stderr,
        )
        title = item.get("title") or item.get("locator") or ""
        if title:
            print(f"    {title}", file=sys.stderr)
        if show_claims:
            for claim in item.get("claims", []):
                print(
                    f"    - [{claim['status']} {float(claim['confidence']):.2f}] "
                    f"{claim['text']}",
                    file=sys.stderr,
                )
    return True
