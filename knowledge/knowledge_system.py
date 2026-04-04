# Knowledge System (система знаний) — Слой 2
# Архитектура автономного AI-агента
# Хранение, обработка и извлечение всех видов знаний агента.
# pylint: disable=broad-except

import hashlib
import logging
import re
import time
from typing import Any

from knowledge.knowledge_graph import KnowledgeGraph

_log = logging.getLogger(__name__)


class KnowledgeSystem:
    """
    Система знаний (Слой 2).

    Хранит и обрабатывает все виды знаний агента:
        - краткосрочная память (short-term) — контекст текущей задачи
        - долгосрочная память (long-term)   — накопленные проверенные знания
        - эпизодическая память (episodic)   — опыт прошлых действий
        - семантическая память (semantic)   — фактические знания о мире

    Компоненты:
        - vector_db         — векторная база для семантического поиска
        - embedder          — создание эмбеддингов из текста
        - knowledge_graph   — граф связей между знаниями
        - document_storage  — хранилище документов
        - code_storage      — репозитории кода
        - dataset_storage   — наборы данных

    Используется:
        - Cognitive Core (Слой 3) — через build_context()
        - Knowledge Acquisition Pipeline (Слой 31)
        - Memory Consolidation (Слой 47)
    """

    def __init__(
        self,
        vector_db=None,
        embedder=None,
        knowledge_graph=None,
        document_storage=None,
        code_storage=None,
        dataset_storage=None,
        governance=None,
        security=None,
    ):
        self.vector_db = vector_db
        self.embedder = embedder
        # Граф знаний — создаём встроенный если внешний не передан
        self.knowledge_graph: KnowledgeGraph = (
            knowledge_graph if knowledge_graph is not None else KnowledgeGraph()
        )
        self.document_storage = document_storage
        self.code_storage = code_storage
        self.dataset_storage = dataset_storage
        self.governance = governance
        self.security = security

        # Памяти хранятся локально если внешние хранилища не подключены
        self._short_term: list = []   # контекст текущей сессии
        self._long_term: dict = {}    # ключ → факт/знание
        self._episodic: list = []     # лог прошлых действий и их результатов
        self._semantic: dict = {}     # фактические знания: сущность → описание
        self._last_errors: list[str] = []
        # Data Lifecycle (Слой 33) — подключается после создания через knowledge.lifecycle = ...
        self.lifecycle: Any | None = None

        # ── Provenance & write-safety ─────────────────────────────────────
        # {key: {source, ts, trust, verified}} — метаданные каждой записи
        self._provenance: dict[str, dict] = {}
        # Append-only журнал последних записей (для откатов и аудита)
        self._write_journal: list[dict] = []
        _MAX_JOURNAL = 10_000
        self._max_journal = _MAX_JOURNAL
        # {key: prev_value} — предыдущее значение для однократного отката
        self._prev_values: dict = {}

        # Восстанавливаем long_term из ChromaDB (если доступна) после инициализации
        self._restore_long_term_from_vector()

    # ── Основной интерфейс (используется Cognitive Core) ──────────────────────

    def get_relevant_knowledge(self, task):
        """
        Возвращает знания, релевантные задаче.
        Ищет в long-term, semantic, knowledge_graph и через vector_db.
        """
        results = {}
        task_str = str(task).lower()
        tokens = re.findall(r'[a-zа-яё0-9_+-]{2,}', task_str)
        short_important = {'ai', 'ml', 'cv', 'rl', 'nlp', 'llm'}
        words = [w for w in tokens if len(w) > 2 or w in short_important]
        if not words and task_str.strip():
            words = [task_str.strip()]

        # Семантический поиск через vector DB (VectorStore обрабатывает эмбеддинги сам)
        if self.vector_db:
            try:
                results['vector_search'] = self.vector_db.search(str(task))
            except Exception as e:
                self._record_error('vector_search', e)

        # Поиск по ключевым словам в long-term
        long_term_hits = {
            k: v for k, v in self._long_term.items()
            if any(word in k.lower() for word in words)
        }
        # Дополнительно: поиск в значениях для acquired-записей (GitHub, HF, книги)
        for k, v in self._long_term.items():
            if k in long_term_hits:
                continue
            val_str = str(v).lower()
            if any(word in val_str for word in words) and k.startswith('acquired:'):
                long_term_hits[k] = v
        # Ограничиваем количество записей чтобы не раздувать контекст
        if long_term_hits:
            limited = dict(list(long_term_hits.items())[:20])
            results['long_term'] = limited

        # Семантическая память
        semantic_hits = {
            k: v for k, v in self._semantic.items()
            if any(word in k.lower() for word in words)
        }
        if semantic_hits:
            results['semantic'] = semantic_hits

        # Граф знаний — ищем концепты совпадающие со словами задачи,
        # возвращаем их окрестность (связанные концепты глубиной 1)
        graph_hits = {}
        for concept in self.knowledge_graph.concepts():
            if any(word in concept.lower() for word in words):
                related = self.knowledge_graph.get_related(concept)
                if related:
                    graph_hits[concept] = related
        if graph_hits:
            results['knowledge_graph'] = graph_hits

        return results or None

    def get_episodic_memory(self, task=None):
        """
        Возвращает эпизодическую память — опыт прошлых действий, связанных с задачей.
        """
        task_str = str(task or '').lower()
        if not task_str:
            return list(self._episodic) if self._episodic else None

        relevant = [
            ep for ep in self._episodic
            if task_str in str(ep.get('task', '')).lower()
        ]
        return relevant if relevant else None

    # ── Short-term memory ─────────────────────────────────────────────────────

    def add_short_term(self, item):
        """Добавляет запись в краткосрочную память (контекст текущей задачи)."""
        self._short_term.append(item)

    def get_short_term(self):
        """Возвращает всю краткосрочную память."""
        return list(self._short_term)

    def clear_short_term(self):
        """Очищает краткосрочную память (конец задачи/сессии)."""
        self._short_term.clear()

    # ── Long-term memory ──────────────────────────────────────────────────────

    # Источники с низким trust, для которых governance gate обязателен
    _UNTRUSTED_SOURCES = frozenset({'web', 'unknown', 'generated', 'external'})

    def store_long_term(self, key, value, *, source: str = 'internal',
                        trust: float = 0.7, verified: bool = False):
        """Сохраняет знание в долгосрочную память с provenance и safety-gate.

        Args:
            key      — ключ записи
            value    — значение
            source   — происхождение: 'internal', 'web', 'user', 'learning',
                       'reflection', 'pipeline', 'unknown' и т.д.
            trust    — уровень доверия 0.0–1.0 (default 0.7 для внутренних)
            verified — прошло ли значение через KnowledgeVerificationSystem
        """
        # ── SECURITY: не допускаем попадание секретов в долгосрочную память ──
        if self.security and hasattr(self.security, 'contains_secret'):
            combined = f"{key} {value}"
            if self.security.contains_secret(combined):
                _log.warning(
                    "store_long_term BLOCKED: значение '%s' содержит секрет (source=%s)",
                    str(key)[:60], source,
                )
                if hasattr(self.security, 'audit'):
                    self.security.audit('secret_in_knowledge_BLOCKED',
                                        resource=str(key)[:60], success=False)
                return

        # ── Governance gate для недоверенных источников ────────────────────
        if self.governance and source in self._UNTRUSTED_SOURCES:
            try:
                result = self.governance.check(
                    action=f'store_long_term:{key}',
                    context={'source': source, 'trust': trust,
                             'value_preview': str(value)[:300]},
                )
                if not result.get('allowed', True):
                    _log.warning(
                        "Governance заблокировал запись '%s' (source=%s): %s",
                        key, source, result.get('reason', ''))
                    return
            except Exception:
                pass  # governance недоступен — не блокируем

        # ── Write journal (append-only, для аудита и rollback) ────────────
        # ── Dedup: если идентичное значение уже хранится под тем же ключом — пропускаем ──
        prev = self._long_term.get(key)
        if prev is not None:
            if prev == value:
                return  # идентичный контент — перезапись не нужна
            self._prev_values[key] = prev
        self._write_journal.append({
            'ts': time.time(), 'key': key,
            'action': 'update' if prev is not None else 'create',
            'source': source, 'trust': trust,
        })
        if len(self._write_journal) > self._max_journal:
            self._write_journal = self._write_journal[-self._max_journal:]

        # ── Provenance ────────────────────────────────────────────────────
        self._provenance[key] = {
            'source': source,
            'ts': time.time(),
            'trust': trust,
            'verified': verified,
        }

        # ── Сохранение ───────────────────────────────────────────────────
        self._long_term[key] = value
        # VectorStore использует TF-IDF/ChromaDB внутри — embedder не нужен
        if self.vector_db:
            try:
                self.vector_db.add(str(key), f"{key}: {value}", metadata={'value': str(value)})
            except Exception as e:
                self._record_error('vector_add', e)
        # Уведомляем Data Lifecycle о новой записи — чтобы archive_stale работал по возрасту
        if self.lifecycle:
            try:
                getattr(self.lifecycle, 'track', lambda *_args, **_kwargs: None)(key)
            except Exception:
                pass

    def get_long_term(self, key):
        """Извлекает знание из долгосрочной памяти по ключу.
        
        При промахе в RAM — ищет в ChromaDB и кеширует результат.
        """
        value = self._long_term.get(key)
        if value is not None:
            return value
        # Промах — пробуем ChromaDB
        if self.vector_db:
            try:
                doc = self.vector_db.get(str(key))
                if doc is not None:
                    # Значение хранится в metadata['value']
                    restored = doc.metadata.get('value')
                    if restored is not None:
                        self._long_term[key] = restored  # кешируем в RAM
                        return restored
            except Exception:
                pass
        return None

    def long_term_items(self):
        """Публичный доступ к копии пар key/value долгосрочной памяти."""
        return list(self._long_term.items())

    def long_term_keys(self):
        """Публичный доступ к списку ключей долгосрочной памяти."""
        return list(self._long_term.keys())

    def _restore_long_term_from_vector(self):
        """При старте восстанавливает все long_term записи из ChromaDB в RAM."""
        if not self.vector_db:
            return
        try:
            docs = self.vector_db.all_docs()
            count = 0
            for doc in docs:
                key = doc.doc_id
                value = doc.metadata.get('value')
                if value is not None and key not in self._long_term:
                    self._long_term[key] = value
                    count += 1
            if count:
                pass  # тихое восстановление, без лишних логов при каждом старте
        except Exception:
            pass

    def delete_long_term(self, key):
        """Удаляет знание из долгосрочной памяти."""
        prev = self._long_term.pop(key, None)
        if prev is not None:
            self._prev_values[key] = prev
            self._write_journal.append({
                'ts': time.time(), 'key': key, 'action': 'delete',
                'source': 'lifecycle', 'trust': 1.0,
            })
        self._provenance.pop(key, None)
        if self.vector_db:
            self.vector_db.delete(key)

    # ── Provenance / Rollback / Verification helpers ─────────────────────────

    def get_provenance(self, key) -> dict | None:
        """Возвращает provenance-метаданные записи (source, ts, trust, verified)."""
        return self._provenance.get(key)

    def rollback_key(self, key) -> bool:
        """Откатывает последнюю запись к предыдущему значению.

        Returns True если откат удался, False если предыдущего значения нет.
        """
        prev = self._prev_values.pop(key, None)
        if prev is None:
            return False
        self._long_term[key] = prev
        self._write_journal.append({
            'ts': time.time(), 'key': key, 'action': 'rollback',
            'source': 'manual', 'trust': 1.0,
        })
        _log.info("Откат ключа '%s' к предыдущему значению", key)
        return True

    def mark_verified(self, key) -> bool:
        """Помечает знание как верифицированное."""
        prov = self._provenance.get(key)
        if prov is None:
            return False
        prov['verified'] = True
        prov['verified_at'] = time.time()
        return True

    def get_unverified_keys(self, source: str | None = None) -> list[str]:
        """Возвращает ключи непроверенных записей (опционально фильтр по source)."""
        keys = []
        for k, prov in self._provenance.items():
            if prov.get('verified'):
                continue
            if source and prov.get('source') != source:
                continue
            keys.append(k)
        return keys

    def get_write_journal(self, last_n: int = 100) -> list[dict]:
        """Возвращает последние N записей журнала (для аудита)."""
        return list(self._write_journal[-max(0, int(last_n)):])

    # ── Episodic memory ───────────────────────────────────────────────────────

    def record_episode(self, task, action, result, success: bool, notes=None):
        """
        Записывает эпизод в эпизодическую память.

        Args:
            task    — описание задачи
            action  — что было сделано
            result  — что получилось
            success — успешно ли выполнено
            notes   — дополнительные наблюдения
        """
        episode = {
            'task': task,
            'action': action,
            'result': result,
            'success': success,
            'notes': notes,
            'ts': time.time(),
        }
        self._episodic.append(episode)
        return episode

    def get_all_episodes(self):
        """Возвращает всю эпизодическую память."""
        return list(self._episodic)

    # ── Semantic memory ───────────────────────────────────────────────────────

    def store_semantic(self, entity: str, description: str,
                       relations: list[tuple[str, str]] | None = None,
                       *, source: str = 'internal'):
        """
        Сохраняет фактическое знание о сущности.
        Автоматически добавляет тройку в граф знаний.

        Args:
            entity      — название сущности (концепт)
            description — текстовое описание
            relations   — дополнительные связи [(predicate, object), ...]
                          Например: [("is_a", "language"), ("used_for", "ML")]
            source      — происхождение знания
        """
        self._semantic[entity] = description
        self._provenance[f'semantic:{entity}'] = {
            'source': source, 'ts': time.time(),
            'trust': 0.7 if source == 'internal' else 0.5,
            'verified': False,
        }
        # Автоматически регистрируем сущность в графе
        self.knowledge_graph.add_triple(entity, 'has_description',
                                        str(description)[:80])
        # Дополнительные явные связи
        if relations:
            for predicate, obj in relations:
                self.knowledge_graph.add_triple(entity, predicate, obj)

    def get_semantic(self, entity):
        """Возвращает фактическое знание о сущности."""
        return self._semantic.get(entity)

    # ── Documents ─────────────────────────────────────────────────────────────

    def store_document(self, doc_id, content, metadata=None):
        """Сохраняет документ в document storage."""
        if self.document_storage:
            return self.document_storage.store(doc_id, content, metadata=metadata)
        # Fallback: сохраняем в long-term
        self.store_long_term(f"doc:{doc_id}", content)

    def get_document(self, doc_id):
        """Извлекает документ из storage."""
        if self.document_storage:
            return self.document_storage.get(doc_id)
        return self.get_long_term(f"doc:{doc_id}")

    # ── Code storage ──────────────────────────────────────────────────────────

    def store_code(self, name, code, language=None, metadata=None):
        """Сохраняет фрагмент кода в code storage."""
        if self.code_storage:
            return self.code_storage.store(name, code, language=language, metadata=metadata)
        self.store_long_term(f"code:{name}", code)

    def get_code(self, name):
        """Извлекает код по имени."""
        if self.code_storage:
            return self.code_storage.get(name)
        return self.get_long_term(f"code:{name}")

    # ── Semantic search ───────────────────────────────────────────────────────

    def semantic_search(self, query, top_k=5):
        """Семантический поиск по всем знаниям через vector DB."""
        if not self.vector_db:
            return []
        try:
            return self.vector_db.search(str(query), n=top_k)
        except Exception as e:
            self._record_error('semantic_search', e)
            return []

    # ── Knowledge Graph ───────────────────────────────────────────────────────

    def add_relation(self, subject: str, predicate: str, obj: str):
        """
        Добавляет связь в граф знаний: subject -[predicate]-> object.
        Граф всегда доступен (создаётся автоматически при инициализации).

        Примеры:
            knowledge.add_relation("Python", "is_a", "programming_language")
            knowledge.add_relation("pandas", "part_of", "Python ecosystem")
            knowledge.add_relation("GPT-4", "used_for", "text generation")
        """
        self.knowledge_graph.add_triple(subject, predicate, obj)

    def query_graph(self, query: str) -> list[dict]:
        """
        Запрос к графу знаний по шаблону с wildcard '?'.

        Форматы:
            "Python is_a ?"             → что такое Python
            "? is_a programming_language" → что является языком программирования
            "Python ? ?"                → все связи Python

        Returns:
            Список {'subject', 'predicate', 'object'}
        """
        return self.knowledge_graph.query(query)

    def get_related_concepts(self, concept: str,
                             predicate: str | None = None) -> list[dict]:
        """
        Возвращает концепты, связанные с данным.

        Args:
            concept   — исходный концепт
            predicate — фильтр по типу связи (None = все)
        """
        return self.knowledge_graph.get_related(concept, predicate=predicate)

    def find_knowledge_path(self, start: str, end: str) -> list[dict] | None:
        """Находит путь между двумя концептами в графе знаний."""
        return self.knowledge_graph.find_path(start, end)

    def graph_summary(self) -> dict:
        """Статистика графа знаний."""
        return self.knowledge_graph.summary()

    # ── Memory Consolidation (Слой 47) ────────────────────────────────────────

    def consolidate(self):
        """
        Переносит ценные краткосрочные знания в долгосрочную память.
        Удаляет устаревшее и дублирующееся. (Memory Consolidation, Слой 47)
        """
        for item in self._short_term:
            if isinstance(item, dict) and item.get('persist'):
                key = item.get('key') or self._stable_item_key(item)
                self.store_long_term(key, item.get('value', item))
        self.clear_short_term()

    @staticmethod
    def _stable_item_key(item) -> str:
        payload = str(item.get('value', item))
        norm = ' '.join(payload.split())
        digest = hashlib.md5(norm.encode('utf-8', errors='replace')).hexdigest()[:12]
        return f'consolidated:{digest}'

    def _record_error(self, scope: str, error: Exception):
        msg = f"{scope}: {type(error).__name__}: {str(error)[:160]}"
        self._last_errors.append(msg)
        if len(self._last_errors) > 20:
            self._last_errors = self._last_errors[-20:]

    def get_recent_errors(self, limit: int = 5) -> list[str]:
        return list(self._last_errors[-max(0, int(limit)):])

    def export_state(self) -> dict:
        """Возвращает полное состояние для персистентности."""
        return {
            "long_term": dict(self._long_term),
            "episodic": list(self._episodic),
            "semantic": dict(self._semantic),
        }

    def import_state(self, data: dict):
        """Восстанавливает состояние из персистентного хранилища."""
        if data.get("long_term"):
            self._long_term.update(data["long_term"])
        if data.get("episodic"):
            self._episodic.extend(data["episodic"])
        if data.get("semantic"):
            self._semantic.update(data["semantic"])

    def knowledge_count(self) -> int:
        return len(self._long_term) + len(self._episodic) + len(self._semantic)
