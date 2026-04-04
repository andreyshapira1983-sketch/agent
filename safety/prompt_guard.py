# Prompt Guard — защита от prompt drift (подмены идентичности / системного промпта)
# Встраивается в Слой 45 (Identity) — проверяет целостность при каждом цикле.

import hashlib


class PromptGuard:
    """
    Хранит эталонный хеш системного промпта / идентичности агента.
    При вызове verify() сравнивает текущее состояние с эталоном.
    Если обнаружено расхождение — отклоняет и восстанавливает.
    """

    def __init__(self, identity, monitoring=None):
        """
        Args:
            identity  — IdentityCore (слой 45): имя, роль, миссия, ценности.
            monitoring — Monitoring (слой 17): логирование.
        """
        self._identity = identity
        self._monitoring = monitoring
        # Эталон фиксируется при создании (после финальной инициализации в build_agent)
        self._reference_hash: str | None = None
        self._reference_snapshot: dict | None = None
        self._drift_count = 0

    # ── Публичный интерфейс ───────────────────────────────────────────────────

    def seal(self):
        """Фиксирует текущее состояние identity как эталон.
        Вызывать ОДИН раз в конце build_agent, после всех настроек."""
        snap = self._take_snapshot()
        self._reference_snapshot = snap
        self._reference_hash = self._hash_snapshot(snap)
        if self._monitoring:
            self._monitoring.info(
                f"PromptGuard: эталон зафиксирован (hash={self._reference_hash[:12]}…)",
                source="prompt_guard",
            )

    def verify(self) -> bool:
        """Проверяет, что identity не изменилась с момента seal().
        Returns True если всё в порядке, False если обнаружен drift."""
        if self._reference_hash is None:
            return True  # seal() ещё не вызван — пропускаем

        current = self._take_snapshot()
        current_hash = self._hash_snapshot(current)

        if current_hash == self._reference_hash:
            return True

        # Drift обнаружен
        self._drift_count += 1
        diff = self._diff(self._reference_snapshot, current)
        if self._monitoring:
            self._monitoring.warning(
                f"PromptGuard: DRIFT обнаружен (#{self._drift_count})! "
                f"Изменено: {diff}. Восстанавливаю эталон.",
                source="prompt_guard",
            )
        self._restore()
        return False

    @property
    def drift_count(self) -> int:
        """Сколько раз был обнаружен drift с момента seal()."""
        return self._drift_count

    def status(self) -> dict:
        """Текущий статус guard'а."""
        matches = self.verify() if self._reference_hash else True
        return {
            'sealed': self._reference_hash is not None,
            'reference_hash': self._reference_hash,
            'drift_count': self._drift_count,
            'current_matches': matches,
        }

    # ── Внутренние методы ─────────────────────────────────────────────────────

    def _take_snapshot(self) -> dict:
        """Снимает текущее состояние identity (только критичные поля)."""
        return {
            'name': self._identity.name,
            'role': self._identity.role,
            'mission': self._identity.mission,
            'values': list(self._identity.values),
            'limitations': list(self._identity.limitations),
        }

    def _hash_snapshot(self, snap: dict) -> str:
        """SHA-256 хеш снимка."""
        raw = repr(sorted(snap.items()))
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def _diff(self, reference: dict | None, current: dict) -> list[str]:
        """Возвращает список изменённых полей."""
        if reference is None:
            return ['(нет эталона)']
        changed = []
        for key in reference:
            if reference.get(key) != current.get(key):
                changed.append(key)
        return changed or ['(неизвестное изменение)']

    def _restore(self):
        """Восстанавливает identity из эталонного снимка."""
        if self._reference_snapshot is None:
            return
        snap = self._reference_snapshot
        self._identity.name = snap['name']
        self._identity.role = snap['role']
        self._identity.mission = snap['mission']
        self._identity.values = list(snap['values'])
        self._identity.limitations = list(snap['limitations'])
