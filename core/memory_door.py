"""Write door to persistent memory for the agent's own records: kind dictionary, code-likeness filter, write-policy call with the recent-writes log, and read-back confirmation. memory_door_write(store, policy, text, kind, provenance) -> mem_id | None; conclusions pass, raw code / duplicates / frozen sources / unmapped kinds do not."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from core.models import MemoryRecord

#: Code marks: a colon only without a following space (`a:b`, `def f():`), a parenthesis only right after a name (`foo(`); a colon after a generalisation or a parenthetical is normal prose.
_CODE_PUNCT_RE = re.compile(r":(?!\s)|[=+\[\]{}<>]|\w\(|\)\s*:|->|;\s*$")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{2,}")


def _count_code_punct(text):
    return len(_CODE_PUNCT_RE.findall(text))


def _count_words(text):
    return len(_WORD_RE.findall(text))


def _looks_like_code(text):
    words = _count_words(text)
    punct = _count_code_punct(text)
    if punct >= 1 and words <= 8:
        return True
    if words >= 10 and punct == 0:
        return False
    return bool(punct >= 2 and words <= 12)


CONSENT_TAG_MAP = {
    "[ВЫВОД, проверен боем]": "decision",
    "[ВЫВОД, замерен N раз]": "fact",
    "[НАБЛЮДЕНИЕ, один день]": "insight",
}

#: The door dictionary on one line so it can be shown to whoever knocks.
ACCEPTED_KINDS_TEXT = "EXACTLY one of: " + ", ".join(repr(k) for k in (
    "[ВЫВОД, проверен боем]", "[ВЫВОД, замерен N раз]", "[НАБЛЮДЕНИЕ, один день]",
))


def memory_door_verdict(store, policy, text, kind, provenance):
    """(mem_id | None, reason): a refusal names the rule that fired; the decision itself is unchanged."""
    text = (text or '').strip()
    kind = (kind or '').strip()
    provenance = (provenance or '').strip()
    if not text or not kind or not provenance:
        missing = [n for n, v in (("text", text), ("kind", kind), ("provenance", provenance)) if not v]
        return None, f"missing required field(s): {', '.join(missing)}"
    if _looks_like_code(text):
        return None, "text looks like code (code punctuation with few words); the door banks prose conclusions only"
    consent_tag = CONSENT_TAG_MAP.get(kind)
    if consent_tag is None:
        return None, f"unknown kind {kind!r}; the door accepts {ACCEPTED_KINDS_TEXT}"
    # Provenance is free text from the caller, so it is NAMESPACED, never a bare tag, or a caller could pass the PII gate or dress a record as pipeline-verified.
    tags = [kind, f"provenance:{provenance}", consent_tag]
    if provenance.lower().startswith("memory:"):
        # Memory is not a source of memory: a record whose only evidence is another record is the self-echo the antibody stops, one layer earlier.
        return None, "provenance 'memory:…' refused: a memory cannot be the source of a memory"
    # The policy is consulted WITH the recent-writes log, so the echo antibody sees the agent's own door writes.
    registry = _door_write_registry(store)
    recent = []
    echo_off = ""
    if registry is not None:
        try:
            recent = registry.recent()
        except Exception as exc:  # noqa: BLE001 — a registry hiccup must never block the door
            # The door keeps working, but a disabled echo filter is no longer silent: journal, counter and refusal text.
            recent = []
            echo_off = _note_echo_gate_failure("recent", exc)
    decision = policy.decide(
        text, tags, source="agent-auto", existing=store.load(), recent_writes=recent,
    )
    if decision.decision != "save":
        why = "; ".join(str(r) for r in (getattr(decision, "reasons", None) or ())) or "write policy refused"
        if echo_off:
            why = f"{why}; {echo_off}"
        return None, f"write policy refused: {why}"
    record = MemoryRecord(content=text, type="semantic", tags=tags, owner="self", source="agent-auto")
    store.save(record)
    # "stored" is said only after an independent readback finds the id on disk; a write that cannot be read back is not a memory.
    try:
        found = any(getattr(r, "id", None) == record.id for r in store.load())
    except Exception:  # noqa: BLE001 — an unreadable store cannot confirm a write
        found = False
    if not found:
        return None, "not stored: readback after save did not find the record"
    if registry is not None:
        try:
            from core.memory_echo_antibody import make_event

            registry.append(make_event(text, tags=tags, record_type="semantic", source="agent-auto"))
        except Exception as exc:  # noqa: BLE001 — the write stands; the echo log is best-effort
            _note_echo_gate_failure("append", exc)
    return record.id, ""


_LOG = logging.getLogger(__name__)

#: How many times the echo log failed to read or append, and the last reason; previously swallowed, which disabled the echo filter silently.
ECHO_GATE_FAILURES = {"count": 0, "last": ""}


def _note_echo_gate_failure(step, exc):
    """Count, log and describe an echo-log failure; returns the reason text."""
    reason = f"echo gate off ({step} of memory_writes.jsonl failed: {type(exc).__name__}: {str(exc)[:120]})"
    ECHO_GATE_FAILURES["count"] += 1
    ECHO_GATE_FAILURES["last"] = reason
    _LOG.warning("memory door: %s", reason)
    return reason


def _door_write_registry(store):
    """The recent-writes log beside the store (`data/memory_writes.jsonl`),
    or None for a store without a path."""
    path = getattr(store, "path", None)
    if path is None:
        return None
    try:
        from core.memory_echo_antibody import MemoryWriteRegistry

        return MemoryWriteRegistry(Path(path).parent / "memory_writes.jsonl")
    except Exception:  # noqa: BLE001 — no registry is «no recent writes known»
        return None


def memory_door_write(store, policy, text, kind, provenance):
    return memory_door_verdict(store, policy, text, kind, provenance)[0]
