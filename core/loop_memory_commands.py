"""Operator memory commands: remember, forget, show.

    Not loop code: called from the CLI and self-repair. `remember` still runs the
    write policy, echo antidote and redaction: operator consent settles whether to
    remember, not whether the text may be stored."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.memory_echo_antibody import MemoryWriteEvent, make_event
from core.memory_policy import MemoryWriteDecision
from core.models import MemoryRecord
from core.redaction import redact_dlp_text


class AgentLoopMemoryCommands:
    """Mixin for ``AgentLoop``; state lives on the composed loop.

        Members below are host-contract declarations (no assignments)."""

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        persistent_store: Any
        write_policy: Any
        memory_write_registry: Any

        def _durable_learning_suppressed(self, sink: str) -> bool: ...

    def remember(
        self,
        content: str,
        tags: list[str] | None = None,
        *,
        source: str,
        record_type: str = "semantic",
        owner: str = "user",
        existing: list[MemoryRecord] | None = None,
        supersedes: bool = False,
    ) -> tuple[MemoryWriteDecision, MemoryRecord | None]:
        """Run a `:remember`-style write through the Write Policy.

            `source` is required on purpose: it is the record's ORIGIN, and only
            `user-explicit` later counts as independent evidence.
            Returns the decision plus the saved record (None on reject); the store
            must be wired, else a reject decision is returned. `owner` outside the
            first-party whitelist needs a `cross-owner-consent` tag.
            ``existing`` is an optional pre-loaded snapshot for the dedup gate; saved
            records are appended to it so later writes in the same pass still dedup."""
        if self.persistent_store is None:
            decision = MemoryWriteDecision(
                "reject", ["persistent store not configured"]
            )
            self.log.log("persistent_memory_write", {"decision": decision.decision, "reasons": decision.reasons})
            return decision, None

        tags = tags or []
        # Dedup gate needs the existing records; loaded once and passed in so the policy stays pure.
        existing = self.persistent_store.load() if existing is None else existing
        # Echo gate needs the recent agent-auto write-log (time-windowed); `user-explicit` writes are never echo-guarded.
        recent_writes: list[MemoryWriteEvent] = []
        # `supersedes`: the record REPLACES an earlier one on the same question, which is archived. The echo gate would block a correction that nearly repeats the error, so it is off here.
        if self.memory_write_registry is not None and not supersedes:
            try:
                recent_writes = self.memory_write_registry.recent()
            except Exception as exc:  # noqa: BLE001 — a registry hiccup must never block a write
                # The echo gate is OFF for this write, and that is said.
                recent_writes = []
                self.log.log("memory_echo_registry_read_failed",
                             {"error": f"{type(exc).__name__}: {exc}"})
        decision = self.write_policy.decide(
            content=content,
            tags=tags,
            source=source,
            owner=owner,
            existing=existing,
            recent_writes=recent_writes,
        )
        if decision.decision == "reject":
            self.log.log(
                "persistent_memory_write",
                {
                    "decision": "reject",
                    "reasons": decision.reasons,
                    "policy_id": decision.policy_id,
                    "source": source,
                    "tag_count": len(tags),
                    "content_chars": len(content or ""),
                },
            )
            return decision, None

        safe_content, _secret_findings, pii_findings = redact_dlp_text(content.strip())
        # The redacted text is what gets stored, so compare that form: a record with PII was never recognised as a duplicate.
        if safe_content.strip() != content.strip() and existing:
            from core.memory_hygiene import DEFAULT_DEDUP_THRESHOLD, find_duplicate

            match = find_duplicate(safe_content.strip(), existing, threshold=DEFAULT_DEDUP_THRESHOLD)
            if match is not None:
                rec, score = match
                decision = MemoryWriteDecision(
                    "reject",
                    [(f"duplicate of {rec.id} (similarity={score:.2f} >= "
                      f"{DEFAULT_DEDUP_THRESHOLD}, redacted form)")],
                    policy_id=decision.policy_id,
                )
                self.log.log(
                    "persistent_memory_write",
                    {
                        "decision": "reject",
                        "reasons": decision.reasons,
                        "policy_id": decision.policy_id,
                        "source": source,
                        "tag_count": len(tags),
                        "content_chars": len(content or ""),
                    },
                )
                return decision, None
        if pii_findings:
            self.log.log(
                "sensitive_detected",
                {
                    "label": "persistent_memory_candidate",
                    "kinds": sorted({f"pii-{f.kind}" for f in pii_findings}),
                    "count": len(pii_findings),
                    "surface": "persistent_memory",
                },
            )

        record = MemoryRecord(
            type=record_type,  # type: ignore[arg-type]
            content=safe_content.strip(),
            tags=tags,
            owner=owner,
            # Persist the origin: without it every record reads back origin-less and memory citations are demoted to topic-only.
            source=source,
        )
        self.persistent_store.save(record)
        # Keep a caller-supplied snapshot current so later writes in the same pass dedup against this record.
        existing.append(record)
        # Log this agent-auto write for the next cycle's echo gate; only agent-auto writes are logged.
        if (
            self.memory_write_registry is not None
            and (source or "").strip().lower() == "agent-auto"
        ):
            try:
                self.memory_write_registry.append(
                    make_event(
                        record.content if isinstance(record.content, str) else str(record.content),
                        tags=tags,
                        record_type=record_type,
                        source=source,
                        cycle_id=getattr(self.log, "trace_id", "") or "",
                    )
                )
            except Exception as exc:  # noqa: BLE001 — echo-log write must never abort the memory write
                # A failure is journaled, not swallowed.
                self.log.log(
                    "memory_echo_registry_append_failed",
                    {"record_id": record.id, "error": f"{type(exc).__name__}: {exc}"},
                )
        self.log.log(
            "persistent_memory_write",
            {
                "decision": "save",
                "reasons": decision.reasons,
                "policy_id": decision.policy_id,
                "record_id": record.id,
                "tags": record.tags,
                "type": record.type,
                "chars": len(record.content) if isinstance(record.content, str) else 0,
            },
        )
        return decision, record

    def forget(self, record_id: str | None = None) -> int:
        """Delete one record (by id) or all records. Returns deletion count."""
        if self.persistent_store is None:
            return 0
        if record_id is None:
            n = self.persistent_store.delete_all()
            self.log.log("persistent_memory_delete", {"scope": "all", "deleted": n})
            return n
        ok = self.persistent_store.delete(record_id)
        self.log.log(
            "persistent_memory_delete",
            {"scope": "one", "record_id": record_id, "deleted": int(ok)},
        )
        return 1 if ok else 0

    def list_persistent(self) -> list[MemoryRecord]:
        if self.persistent_store is None:
            return []
        return self.persistent_store.load()
