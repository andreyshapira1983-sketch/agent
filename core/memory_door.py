"""Дверь записи в постоянную память — дверь самого агента (его устройство, его отказы).

Вынесено из core/persistent_memory.py: там — хранилище записей (JSONL,
загрузка, удаление, архив), здесь — решение, ЧТО агент вправе положить в
него сам: словарь видов, фильтр «похоже на код», вызов политики записи с
журналом недавних записей и подтверждение записи чтением.

Contract: memory_door_write(store, policy, text, kind, provenance) -> mem_id | None.
Conclusions pass, raw code / duplicates / frozen sources / unmapped kinds do not.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from core.models import MemoryRecord

#: Знаки кода. Двоеточие — только без пробела после (`a:b`, `def f():`), скобка —
#: только вплотную к имени (вызов `foo(`): 24.09 дверь отказывала короткому
#: русскому выводу «…отдаёт две модели: deepseek-flash и deepseek-v4-pro.» —
#: двоеточие после обобщения и скобка-пояснение — норма прозы.
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

#: Словарь двери — одной строкой, чтобы его можно было показать тому, кто в
#: дверь стучится (замер 2026-09-03: шесть ходов, kind='lesson'/'reflection',
#: ноль записей — дверь молчала о своих ключах).
ACCEPTED_KINDS_TEXT = "EXACTLY one of: " + ", ".join(repr(k) for k in (
    "[ВЫВОД, проверен боем]", "[ВЫВОД, замерен N раз]", "[НАБЛЮДЕНИЕ, один день]",
))


def memory_door_verdict(store, policy, text, kind, provenance):
    """(mem_id | None, reason) — отказ называет правило, которое сработало.

    До 2026-09-03 дверь на отказ возвращала None без слова, и агент угадывал
    причину (и угадывал неверно). Причина — для того, кто стучится; решение
    двери не меняется.
    """
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
    # Provenance is free text from the caller, so it is NAMESPACED, never a
    # bare tag: `provenance="sensitive-data-consent"` used to pass the PII gate
    # and `"source-backed"` to dress an agent record as pipeline-verified
    # (проверка 16.11, серьёзная №7).
    tags = [kind, f"provenance:{provenance}", consent_tag]
    if provenance.lower().startswith("memory:"):
        # Authority change 2026-09-04: memory is not a source of memory — a
        # record whose only evidence is another record is the self-echo the
        # antibody exists to stop, one layer earlier.
        return None, "provenance 'memory:…' refused: a memory cannot be the source of a memory"
    # Block 7 (audit W4, 2026-09-03): the door consulted the policy WITHOUT
    # the recent-writes log, so the echo antibody never saw the agent's own
    # door writes and `data/memory_writes.jsonl` was never fed by them.
    registry = _door_write_registry(store)
    recent = []
    echo_off = ""
    if registry is not None:
        try:
            recent = registry.recent()
        except Exception as exc:  # noqa: BLE001 — a registry hiccup must never block the door
            # Дверь работает дальше, но выключенный фильтр эха больше не
            # молчит: журнал, счётчик и текст отказа (проверка 16.11, средняя).
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
    # Authority change 2026-09-04: «stored» is said only after an independent
    # readback finds the id on disk. A write that cannot be read back is not
    # a memory, and the caller must not report DONE on it.
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

#: Сколько раз журнал эха не прочитался/не дописался и последняя причина
#: (проверка 16.11, средняя): раньше это глоталось, и фильтр эха был выключен молча.
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
