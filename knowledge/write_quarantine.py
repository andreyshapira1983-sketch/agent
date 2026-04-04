# WriteQuarantine — карантин записей в долгосрочную/семantическую память
# formal_contracts_spec §9: unverified record quarantined
# Архитектура: «мягкая карантинизация спорных фактов»

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)

# Источники, которые считаются доверенными и не карантинизируются
TRUSTED_SOURCES = frozenset({'internal', 'user', 'reflection'})


@dataclass(slots=True)
class QuarantineRecord:
    """Одна запись в карантине."""
    key: str
    value: Any
    provenance: dict             # source, trust, ts, verified
    submitted_at: float = field(default_factory=time.time)
    memory_type: str = 'long_term'   # 'long_term' | 'semantic'
    review_count: int = 0        # сколько раз прошла через auto-verify
    rejection_reason: str = ''

    def to_dict(self) -> dict:
        return {
            'key': self.key,
            'value': self.value,
            'provenance': self.provenance,
            'submitted_at': self.submitted_at,
            'memory_type': self.memory_type,
            'review_count': self.review_count,
            'rejection_reason': self.rejection_reason,
        }


class WriteQuarantine:
    """Карантинное хранилище для непроверенных записей в память.

    Записи из недоверенных источников (web, external, generated, unknown)
    помещаются сюда вместо прямой записи в long_term/semantic.

    Lifecycle:
        submit(...)  →  pending (в карантине)
        promote(key) →  запись передаётся в KnowledgeSystem (verified=True)
        reject(key)  →  запись удаляется с причиной

    Thread-safe.
    """

    def __init__(self, knowledge_system=None, verifier=None):
        """
        Args:
            knowledge_system — KnowledgeSystem для promote (delayed injection OK)
            verifier         — KnowledgeVerificationSystem для auto_verify
        """
        self._pending: dict[str, QuarantineRecord] = {}
        self._rejected: list[dict] = []
        self._promoted: list[dict] = []
        self._lock = threading.Lock()
        self._knowledge_system = knowledge_system
        self._verifier = verifier

    # ── Configuration / DI ────────────────────────────────────────────────────

    def set_knowledge_system(self, ks) -> None:
        self._knowledge_system = ks

    def set_verifier(self, verifier) -> None:
        self._verifier = verifier

    # ── Submit ────────────────────────────────────────────────────────────────

    def submit(
        self,
        key: str,
        value: Any,
        provenance: dict,
        memory_type: str = 'long_term',
    ) -> bool:
        """Помещает запись в карантин.

        Returns True если запись принята (в карантин или напрямую в память),
        False если отклонена.
        """
        source = provenance.get('source', 'unknown')

        # Доверенные источники не карантинизируются
        if source in TRUSTED_SOURCES:
            return self._direct_write(key, value, provenance, memory_type)

        record = QuarantineRecord(
            key=key,
            value=value,
            provenance=dict(provenance),
            memory_type=memory_type,
        )

        with self._lock:
            self._pending[key] = record

        _log.debug("Quarantined: key=%s source=%s type=%s", key, source, memory_type)
        return True

    # ── Promote / Reject ──────────────────────────────────────────────────────

    def promote(self, key: str) -> bool:
        """Переносит запись из карантина в основную память (verified=True).

        Returns True если запись найдена и перенесена, False — если нет.
        """
        with self._lock:
            record = self._pending.pop(key, None)
        if record is None:
            return False

        record.provenance['verified'] = True
        record.provenance['verified_at'] = time.time()

        if self._knowledge_system is not None:
            self._write_to_memory(record)

        self._promoted.append({
            'key': key, 'ts': time.time(),
            'source': record.provenance.get('source', ''),
        })
        _log.debug("Promoted from quarantine: key=%s", key)
        return True

    def reject(self, key: str, reason: str = '') -> bool:
        """Отклоняет запись из карантина.

        Returns True если запись найдена и отклонена, False — если нет.
        """
        with self._lock:
            record = self._pending.pop(key, None)
        if record is None:
            return False

        record.rejection_reason = reason
        self._rejected.append({
            'key': key, 'ts': time.time(), 'reason': reason,
            'source': record.provenance.get('source', ''),
        })
        _log.debug("Rejected from quarantine: key=%s reason=%s", key, reason)
        return True

    # ── Query ─────────────────────────────────────────────────────────────────

    def list_pending(self) -> list[QuarantineRecord]:
        """Возвращает список записей в карантине."""
        with self._lock:
            return list(self._pending.values())

    def get_pending(self, key: str) -> QuarantineRecord | None:
        """Возвращает конкретную запись из карантина (или None)."""
        with self._lock:
            return self._pending.get(key)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def get_promoted_log(self, last_n: int = 50) -> list[dict]:
        return list(self._promoted[-last_n:])

    def get_rejected_log(self, last_n: int = 50) -> list[dict]:
        return list(self._rejected[-last_n:])

    # ── Auto-verify batch ─────────────────────────────────────────────────────

    def auto_verify_batch(self, max_items: int = 10) -> dict:
        """Пропускает pending-записи через KnowledgeVerificationSystem.

        Для каждой записи:
            - verified → promote
            - contradicted / failed → reject
            - inconclusive → оставить в карантине (review_count++)

        Returns dict с подсчётами: promoted, rejected, skipped.
        """
        if self._verifier is None:
            return {'promoted': 0, 'rejected': 0, 'skipped': 0, 'error': 'no_verifier'}

        with self._lock:
            candidates = list(self._pending.items())[:max_items]

        stats = {'promoted': 0, 'rejected': 0, 'skipped': 0}

        for key, record in candidates:
            try:
                result = self._verifier.verify(
                    str(record.value),
                    source=record.provenance.get('source', 'unknown'),
                )
                status = getattr(result, 'status', None)
                if status is not None:
                    status = str(status)

                if status in ('verified', 'VERIFIED'):
                    self.promote(key)
                    stats['promoted'] += 1
                elif status in ('contradicted', 'CONTRADICTED', 'false', 'FALSE'):
                    self.reject(key, reason=f'auto_verify: {status}')
                    stats['rejected'] += 1
                else:
                    with self._lock:
                        if key in self._pending:
                            self._pending[key].review_count += 1
                    stats['skipped'] += 1
            except Exception as exc:
                _log.warning("auto_verify failed for key=%s: %s", key, exc)
                stats['skipped'] += 1

        return stats

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _direct_write(self, key: str, value: Any, provenance: dict,
                      memory_type: str) -> bool:
        """Прямая запись в KnowledgeSystem (для доверенных источников)."""
        if self._knowledge_system is None:
            return False
        if memory_type == 'semantic':
            self._knowledge_system.store_semantic(
                entity=key, description=str(value),
                source=provenance.get('source', 'internal'),
            )
        else:
            self._knowledge_system.store_long_term(
                key, value,
                source=provenance.get('source', 'internal'),
                trust=provenance.get('trust', 0.7),
                verified=provenance.get('verified', False),
            )
        return True

    def _write_to_memory(self, record: QuarantineRecord) -> None:
        """Записывает promoted запись в KnowledgeSystem."""
        if self._knowledge_system is None:
            return
        if record.memory_type == 'semantic':
            self._knowledge_system.store_semantic(
                entity=record.key, description=str(record.value),
                source=record.provenance.get('source', 'internal'),
            )
            self._knowledge_system.mark_verified(f'semantic:{record.key}')
        else:
            self._knowledge_system.store_long_term(
                record.key, record.value,
                source=record.provenance.get('source', 'internal'),
                trust=record.provenance.get('trust', 0.7),
                verified=True,
            )
