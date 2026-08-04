"""Операторские команды памяти: запомнить, забыть, показать.

`core/loop_methods.py`, откуда это приехало, не было модулем: его сделал
`core/incremental_splitter.py`, резавший `core/loop.py` по бюджету строк, а не
по смыслу. Имя `methods` — это «остальное», и по нему нельзя было узнать, что
внутри лежат пять несвязанных ответственностей.

Это НЕ код цикла. Ни `_run_inner`, ни один из его этапов сюда не заходит —
зовут отсюда CLI (`cli/command_dispatch.py`, `cli/commands_memory.py`) и
самопочинка. Оно жило рядом с циклом лишь потому, что оттуда до него
дотягивался CLI.

`remember` — самый крупный метод: явное распоряжение оператора всё равно
проходит политику записи, антидот эха и редакцию. Согласие оператора снимает
вопрос «нужно ли это помнить», но не вопрос «можно ли это хранить».
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.memory_echo_antibody import MemoryWriteEvent, make_event
from core.memory_policy import MemoryWriteDecision
from core.models import MemoryRecord
from core.redaction import redact_dlp_text


class AgentLoopMemoryCommands:
    """Подмешивается в ``AgentLoop``; состояние живёт на композированном цикле.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется.
    """

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
        source: str = "user-explicit",
        record_type: str = "semantic",
        owner: str = "user",
        existing: list[MemoryRecord] | None = None,
    ) -> tuple[MemoryWriteDecision, MemoryRecord | None]:
        """Run a `:remember`-style write through the Write Policy.

        Returns the decision plus the saved record (or None on reject).
        The store must be wired; otherwise a reject decision is returned.

        `owner` flows into the Write Policy. Anything outside the first-
        party whitelist needs a `cross-owner-consent` tag.

        ``existing`` is an optional pre-loaded snapshot of the persistent
        records used by the dedup gate. When a caller writes many records in
        one pass (e.g. the knowledge pipeline attempting dozens of agent-auto
        claims per cycle) it can load the store ONCE and pass the same list in,
        avoiding a full store reload per write. When provided, any record saved
        here is appended to it so later writes in the same pass still dedup
        against it — matching the reload-every-time semantics exactly. When
        ``None`` (the default) the store is loaded fresh, as before.
        """
        if self.persistent_store is None:
            decision = MemoryWriteDecision(
                "reject", ["persistent store not configured"]
            )
            self.log.log("persistent_memory_write", {"decision": decision.decision, "reasons": decision.reasons})
            return decision, None

        tags = tags or []
        # Dedup gate (MVP-10) needs the existing records. Load them once and
        # pass into the policy so the policy stays a pure function. A caller
        # may supply the snapshot (TD-019) to avoid reloading per write.
        existing = self.persistent_store.load() if existing is None else existing
        # Echo gate (A1) needs the recent agent-auto write-log, time-windowed.
        # Only consulted when a registry is wired; stays a pure input to the
        # policy. `user-explicit` writes are never echo-guarded.
        recent_writes: list[MemoryWriteEvent] = []
        if self.memory_write_registry is not None:
            try:
                recent_writes = self.memory_write_registry.recent()
            except Exception:
                recent_writes = []  # Registry hiccup must never block a write.
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
            # MIR-074 root fix: persist the origin. Without it every stored
            # record read back as origin-less, and the MIR-046 independence
            # rule demoted every memory citation to topic-only — measured as
            # the all-history ZERO of verified memory citations.
            source=source,
        )
        self.persistent_store.save(record)
        # Keep a caller-supplied snapshot (TD-019) current so subsequent writes
        # in the same pass dedup against this record, exactly as a fresh reload
        # would have. Harmless when ``existing`` is a private freshly-loaded list.
        existing.append(record)
        # Record this agent-auto write in the rolling echo-log so the next
        # cycle's echo gate can see it. Only agent-auto writes are logged —
        # the registry exists to catch the agent echoing *itself*.
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
            except Exception:
                pass  # Echo-log write must never abort the memory write.
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
