"""Register what a turn actually read (web pages via web_fetch) in the source registry at turn end, so reading leaves a source, not only a note."""
# Not a topic ingestion (ingest_web_topic stays a command): a record of what was already read, with address, content fingerprint and read time.
# Quiet and optional: a write failure does not fail the turn but is logged; the registry drops repeats by address.
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

#: Evidence kinds that ARE a read source: web pages only. Tool output, test results and search hits are not sources; names are taken
#: verbatim from the registry's SourceType (an invented name would be rejected silently). Workspace files are already registered by ingest_files.
_SOURCE_KINDS: dict[str, str] = {
    "web_page": "web_page",
}
_MAX_PER_TURN = 12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_read_sources(agent: Any, chain: Any) -> int:
    """Record what this turn read in the source registry; returns the count."""
    store = getattr(agent, "source_registry_store", None)
    if store is None or chain is None:
        return 0
    try:
        from core.source_registry import SourceRegistry
    except Exception:  # noqa: BLE001 — without the registry the turn continues as before
        return 0

    # Do not write twice: the store dedups by id, but within one turn a source can arrive via normal ingestion and from here, giving two rows for one file.
    known: set[str] = set()
    try:
        known = {str(getattr(rec, "id", "")) for rec in store.load_sources()}
    except Exception as exc:  # noqa: BLE001 — store unreadable: write as is,
        # The store's dedup still drops id matches; do not stay silent.
        log_early = getattr(agent, "log", None)
        if log_early is not None:
            log_early.log("read_sources_known_unread",
                          {"error": f"{type(exc).__name__}: {exc}"[:200]})

    registry = SourceRegistry()
    seen: set[str] = set()
    for evidence in list(getattr(chain, "evidences", ()) or ())[:60]:
        kind = str(getattr(evidence, "kind", "") or "")
        source_type = _SOURCE_KINDS.get(kind)
        # The evidence field is `source_id`, NOT `locator` (locator is the registry's name); tests must use the real evidence shape.
        locator = str(getattr(evidence, "source_id", "") or "").strip()
        if source_type is None or not locator or locator in seen:
            continue
        if f"{source_type}:{locator}" in known:
            continue
        seen.add(locator)
        registry.register_source(
            type=source_type,
            title=str(getattr(evidence, "title", "") or locator)[:200],
            locator=locator,
            last_read_at=str(getattr(evidence, "fetched_at", "") or _now()),
            metadata={
                # Content fingerprint: shows later whether the same page was read.
                "content_hash": str(getattr(evidence, "content_hash", "") or ""),
                "registered_by": "turn",
            },
        )
        if len(seen) >= _MAX_PER_TURN:
            break
    log = getattr(agent, "log", None)
    if not seen:
        # Loud, not silent: evidence existed but none matched, which means a new evidence kind or a wrong field name.
        looked = len(list(getattr(chain, "evidences", ()) or ()))
        if looked and log is not None:
            log.log("read_sources_none_matched", {
                "evidences_seen": looked,
                "kinds": sorted({str(getattr(e, "kind", "")) for e in
                                 list(getattr(chain, "evidences", ()) or ())[:60]})[:8],
                "expected_kinds": sorted(_SOURCE_KINDS),
            })
        return 0
    try:
        written = store.save_registry(registry)
    except Exception as exc:  # noqa: BLE001 — a knowledge write does not fail the turn,
        # but not silent: a swallowed failure is the exact thing this module exists to prevent.
        if log is not None:
            log.log("read_sources_unregistered",
                    {"error": f"{type(exc).__name__}: {exc}"[:200], "named": len(seen)})
        return 0
    if log is not None:
        log.log("read_sources_registered", {"named": len(seen), "written": written})
    return len(seen)
