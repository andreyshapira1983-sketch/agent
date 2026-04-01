# Data Lifecycle Management (управление жизненным циклом данных) — Слой 33
# Архитектура автономного AI-агента
# Очистка данных, архивирование, дедупликация, контроль качества знаний.
# pylint: disable=broad-except,protected-access


import time
import hashlib
from enum import Enum


class DataAge(Enum):
    FRESH   = 'fresh'    # < 7 дней
    RECENT  = 'recent'   # 7–30 дней
    OLD     = 'old'      # 30–90 дней
    STALE   = 'stale'    # > 90 дней


class DataLifecycleManager:
    """
    Data Lifecycle Management — Слой 33.

    Функции:
        - дедупликация: удаление дублирующихся знаний
        - архивирование: перенос старых данных из активной памяти
        - очистка: удаление устаревшего, неактуального, некачественного
        - контроль качества знаний: оценка и пометка ненадёжных записей
        - сжатие: объединение похожих записей в одну

    Используется:
        - Knowledge System (Слой 2)    — основное хранилище
        - Autonomous Loop (Слой 20)    — периодическая чистка
        - Memory Consolidation (Слой 47 в доке = consolidate в KnowledgeSystem)
    """

    def __init__(self, knowledge_system=None, cognitive_core=None,
                 monitoring=None, archive_path: str | None = None):
        self.knowledge = knowledge_system
        self.cognitive_core = cognitive_core
        self.monitoring = monitoring
        self.archive_path = archive_path
        self.verifier = None

        self._archive: dict[str, dict] = {}      # ключ → архивная запись
        self._quality_scores: dict[str, float] = {}
        self._timestamps: dict[str, float] = {}  # ключ → время добавления
        self._hashes: dict[str, str] = {}        # хэш контента → ключ

    # ── Дедупликация ──────────────────────────────────────────────────────────

    def deduplicate(self) -> dict:
        """
        Находит и удаляет дублирующиеся записи в Knowledge System.

        Returns:
            {'removed': int, 'duplicates': list[str]}
        """
        if not self.knowledge:
            return {'removed': 0, 'duplicates': []}

        long_term = dict(self.knowledge.long_term_items())
        seen_hashes: dict[str, str] = {}   # хэш → первый ключ
        duplicates: list[str] = []

        for key, value in list(long_term.items()):
            h = self._hash(str(value))
            if h in seen_hashes:
                duplicates.append(key)
                self._log(f"Дубликат найден: '{key}' == '{seen_hashes[h]}'")
            else:
                seen_hashes[h] = key

        for key in duplicates:
            self.knowledge.delete_long_term(key)

        self._log(f"Дедупликация: удалено {len(duplicates)} дублей")
        return {'removed': len(duplicates), 'duplicates': duplicates}

    def find_similar(self, threshold: float = 0.85) -> list[tuple[str, str]]:
        """
        Находит семантически похожие записи через Cognitive Core.
        Возвращает пары ключей которые можно объединить.
        """
        if not self.knowledge or not self.cognitive_core:
            return []

        keys = list(self.knowledge.long_term_keys())
        similar_pairs = []

        # Проверяем подстроковое совпадение ключей (дёшево)
        for i, k1 in enumerate(keys):
            for k2 in keys[i+1:]:
                if self._key_similarity(k1, k2) >= threshold:
                    similar_pairs.append((k1, k2))

        return similar_pairs

    def merge_similar(self, key1: str, key2: str, merged_key: str | None = None) -> str:
        """Объединяет две похожие записи в одну через Cognitive Core."""
        if not self.knowledge:
            return key1

        val1 = self.knowledge.get_long_term(key1) or ''
        val2 = self.knowledge.get_long_term(key2) or ''

        if self.cognitive_core:
            merged = self.cognitive_core.reasoning(
                f"Объедини два похожих знания в одно без потери информации:\n\n"
                f"1. {val1}\n\n2. {val2}"
            )
        else:
            merged = f"{val1}\n---\n{val2}"

        target_key = merged_key or key1
        self.knowledge.store_long_term(target_key, merged, source='lifecycle', trust=0.7)
        if target_key != key1:
            self.knowledge.delete_long_term(key1)
        self.knowledge.delete_long_term(key2)
        self._log(f"Слияние: '{key1}' + '{key2}' → '{target_key}'")
        return target_key

    # ── Архивирование ─────────────────────────────────────────────────────────

    def archive(self, key: str, reason: str = 'manual'):
        """Переносит запись из активной памяти в архив."""
        if not self.knowledge:
            return
        value = self.knowledge.get_long_term(key)
        if value is None:
            return

        self._archive[key] = {
            'value': value,
            'archived_at': time.time(),
            'reason': reason,
        }
        self.knowledge.delete_long_term(key)
        self._log(f"Архивировано: '{key}' (причина: {reason})")
        if self.archive_path:
            self._persist_archive()

    def archive_stale(self, max_age_days: int = 90,
                     require_mastery: bool = True,
                     min_mastery_score: float = 0.65) -> dict:
        """Архивирует старые записи; при require_mastery оставляет непонятые знания в активной базе."""
        cutoff = time.time() - max_age_days * 86400
        archived = []
        skipped_not_mastered = []
        for key, ts in list(self._timestamps.items()):
            if ts < cutoff:
                if require_mastery and not self._is_mastered(key, min_mastery_score):
                    skipped_not_mastered.append(key)
                    continue
                self.archive(key, reason=f'stale_{max_age_days}d')
                archived.append(key)
        self._log(
            f"Архивировано устаревших: {len(archived)} записей, "
            f"пропущено (не подтверждённое mastery): {len(skipped_not_mastered)}"
        )
        return {
            'archived': len(archived),
            'keys': archived,
            'skipped_not_mastered': skipped_not_mastered,
        }

    def restore(self, key: str) -> bool:
        """Восстанавливает запись из архива в активную память."""
        if key not in self._archive or not self.knowledge:
            return False
        entry = self._archive.pop(key)
        self.knowledge.store_long_term(key, entry['value'], source='lifecycle', trust=0.7)
        self._log(f"Восстановлено из архива: '{key}'")
        return True

    def get_archive(self) -> dict:
        return dict(self._archive)

    # ── Контроль качества ─────────────────────────────────────────────────────

    def assess_quality(self, key: str, value: str) -> float:
        """Оценивает качество записи знания (0–1)."""
        score = 0.5
        if len(str(value)) > 100:
            score += 0.2
        if len(str(value)) > 500:
            score += 0.1
        if not value or str(value).strip() in ('None', '', 'null'):
            score = 0.0
        self._quality_scores[key] = score
        return score

    def purge_low_quality(self, min_score: float = 0.3) -> dict:
        """Удаляет записи с низким качеством."""
        if not self.knowledge:
            return {'removed': 0}

        removed = []
        for key in list(self.knowledge.long_term_keys()):
            value = self.knowledge.get_long_term(key)
            score = self._quality_scores.get(key) or self.assess_quality(key, str(value))
            if score < min_score:
                self.knowledge.delete_long_term(key)
                removed.append(key)
                self._log(f"Удалено низкокачественное: '{key}' (score={score:.2f})")

        return {'removed': len(removed), 'keys': removed}

    # ── Полная чистка ─────────────────────────────────────────────────────────

    def run_maintenance(self, archive_older_than_days: int = 60,
                        min_quality: float = 0.3) -> dict:
        """
        Полное техническое обслуживание Knowledge System:
            1. Дедупликация
            2. Архивирование устаревших
            3. Удаление низкокачественных

        Returns:
            Сводная статистика.
        """
        self._log("Начало обслуживания Knowledge System...")
        dedup = self.deduplicate()
        stale = self.archive_stale(archive_older_than_days)
        low_q = self.purge_low_quality(min_quality)

        summary = {
            'duplicates_removed': dedup['removed'],
            'stale_archived': stale['archived'],
            'stale_skipped_not_mastered': len(stale.get('skipped_not_mastered', [])),
            'low_quality_removed': low_q['removed'],
            'archive_size': len(self._archive),
        }
        self._log(f"Обслуживание завершено: {summary}")
        return summary

    # ── Helpers ───────────────────────────────────────────────────────────────

    def track(self, key: str):
        """Отмечает временную метку для ключа (вызывается при добавлении)."""
        self._timestamps[key] = time.time()

    def _hash(self, text: str) -> str:
        return hashlib.md5(text.strip().lower().encode()).hexdigest()

    def _key_similarity(self, k1: str, k2: str) -> float:
        s1, s2 = set(k1.lower().split(':')), set(k2.lower().split(':'))
        if not s1 or not s2:
            return 0.0
        return len(s1 & s2) / len(s1 | s2)

    def _persist_archive(self):
        if not self.archive_path:
            return
        import json
        try:
            with open(self.archive_path, 'w', encoding='utf-8') as f:
                json.dump(self._archive, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"Ошибка сохранения архива: {e}")

    def _is_mastered(self, key: str, min_mastery_score: float) -> bool:
        """Проверка досконального усвоения: качество + (опционально) верификация."""
        if not self.knowledge:
            return False

        value = self.knowledge.get_long_term(key)
        if value is None:
            return False

        quality = self._quality_scores.get(key)
        if quality is None:
            quality = self.assess_quality(key, str(value))

        verification_score = None
        if self.verifier:
            try:
                vr = self.verifier.verify(str(value)[:500], source='knowledge_base')
                verification_score = float(getattr(vr, 'confidence', 0.0))
            except (TypeError, ValueError, AttributeError, RuntimeError, KeyError):
                verification_score = None

        if verification_score is None:
            mastery_score = quality
        else:
            mastery_score = quality * 0.5 + verification_score * 0.5

        return mastery_score >= min_mastery_score

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='data_lifecycle')
        else:
            print(f"[DataLifecycle] {message}")
