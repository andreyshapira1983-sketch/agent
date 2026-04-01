# Vector Store — подсистема Knowledge System (Слой 2)
# Архитектура автономного AI-агента
# Векторный поиск: TF-IDF (встроено) + опционально ChromaDB / sentence-transformers.

from __future__ import annotations

import importlib
import math
import re
import time
from collections import Counter


class VectorDocument:
    """Документ в векторном хранилище."""

    def __init__(self, doc_id: str, text: str, metadata: dict | None = None):
        self.doc_id = doc_id
        self.text = text
        self.metadata = metadata or {}
        self.added_at = time.time()

    def to_dict(self) -> dict:
        return {
            'doc_id': self.doc_id,
            'text_preview': self.text[:200],
            'metadata': self.metadata,
        }

class SearchResult:
    """Результат векторного поиска."""

    def __init__(self, doc: VectorDocument, score: float):
        self.doc = doc
        self.score = score

    def to_dict(self) -> dict:
        return {
            'doc_id': self.doc.doc_id,
            'score': round(self.score, 4),
            'text_preview': self.doc.text[:300],
            'metadata': self.doc.metadata,
        }

class VectorStore:
    """
    Vector Store — подсистема KnowledgeSystem.

    Два режима работы:
    1. TF-IDF (встроено, без зависимостей) — всегда доступен
    2. ChromaDB + sentence-transformers (если установлены) — семантический поиск

    API одинаковый в обоих режимах.

    Методы:
        add(doc_id, text, metadata)  — добавить документ
        search(query, n)             — найти топ-N похожих
        delete(doc_id)               — удалить документ
        clear()                      — очистить хранилище
        count()                      — количество документов

    Автоматически выбирает ChromaDB если доступен (CHROMA_ENABLED=True),
    иначе падает на TF-IDF.
    """

    def __init__(
        self,
        collection_name: str = 'agent_knowledge',
        persist_dir: str | None = None,
        use_chroma: bool = True,
        embedding_model: str = 'all-MiniLM-L6-v2',
        monitoring=None,
    ):
        self.collection_name = collection_name
        self.persist_dir = persist_dir
        self.monitoring = monitoring

        self._backend = None
        self._backend_type = 'tfidf'

        # Попытка подключить ChromaDB
        if use_chroma:
            self._backend_type = self._try_init_chroma(
                collection_name, persist_dir, embedding_model
            )

        self._docs: dict[str, VectorDocument] = {}
        self._tfidf_index: dict[str, dict[str, float]] = {}   # doc_id -> {term: tfidf}
        self._idf: dict[str, float] = {}
        self._dirty = False

        self._log(f'VectorStore инициализирован ({self._backend_type})')

    # ── ChromaDB init ─────────────────────────────────────────────────────────

    def _try_init_chroma(self, name: str, persist_dir: str | None,
                          embed_model: str) -> str:
        try:
            chromadb = importlib.import_module('chromadb')

            # Пробуем SentenceTransformer — если установлен, даёт лучшее качество
            ef = None
            try:
                embedding_functions = importlib.import_module(
                    'chromadb.utils.embedding_functions'
                )
                SentenceTransformerEF = getattr(
                    embedding_functions,
                    'SentenceTransformerEmbeddingFunction',
                )
                ef = SentenceTransformerEF(model_name=embed_model)
            except (ImportError, ValueError, AttributeError):
                # sentence_transformers не установлен — используем встроенный EF
                try:
                    embedding_functions = importlib.import_module(
                        'chromadb.utils.embedding_functions'
                    )
                    DefaultEF = getattr(embedding_functions, 'DefaultEmbeddingFunction')
                    ef = DefaultEF()
                    self._log('sentence_transformers не найден — используется DefaultEmbeddingFunction', level='warning')
                except (ImportError, AttributeError):
                    ef = None

            if persist_dir:
                client = chromadb.PersistentClient(path=persist_dir)
            else:
                client = chromadb.Client()

            self._backend = client.get_or_create_collection(
                name=name, embedding_function=ef
            )
            return 'chromadb'
        except ImportError as e:
            self._log(f'ChromaDB недоступен, используется TF-IDF: {e}', level='info')
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError) as e:
            self._log(f'ChromaDB init failed: {e}', level='warning')
        return 'tfidf'

    # ── Основной API ──────────────────────────────────────────────────────────

    def add(self, doc_id: str, text: str, metadata: dict | None = None) -> bool:
        """Добавляет или обновляет документ в хранилище."""
        if not text or not text.strip():
            return False

        doc = VectorDocument(doc_id, text, metadata or {})

        if self._backend_type == 'chromadb':
            try:
                if self._backend is None:
                    raise RuntimeError('ChromaDB backend not initialized')
                self._backend.upsert(
                    ids=[doc_id],
                    documents=[text],
                    metadatas=[metadata or {}],
                )
                # Также добавляем в локальный dict для быстрого доступа
                self._docs[doc_id] = doc
                return True
            except (RuntimeError, ValueError, TypeError, OSError, AttributeError) as e:
                self._log(f'ChromaDB upsert error: {e}', level='error')

        # TF-IDF fallback
        self._docs[doc_id] = doc
        self._dirty = True
        return True

    def add_batch(self, documents: list[dict]) -> int:
        """
        Добавляет список документов.

        Args:
            documents — list of {'id': str, 'text': str, 'metadata': dict}
        """
        count = 0
        for d in documents:
            if self.add(d.get('id', str(time.time())),
                        d.get('text', ''),
                        d.get('metadata', {})):
                count += 1
        return count

    def search(self, query: str, n: int = 5,
               where: dict | None = None) -> list[SearchResult]:
        """
        Ищет топ-N документов по запросу.

        Args:
            query — поисковый запрос
            n     — количество результатов
            where — фильтр по metadata (только ChromaDB)

        Returns:
            Список SearchResult, отсортированный по убыванию score.
        """
        if not query.strip():
            return []

        if self._backend_type == 'chromadb':
            try:
                if self._backend is None:
                    raise RuntimeError('ChromaDB backend not initialized')
                requested_n = max(1, int(n or 1))
                kwargs = {'query_texts': [query], 'n_results': requested_n}
                if where:
                    kwargs['where'] = where
                results = self._backend.query(**kwargs)
                ids = results.get('ids', [[]])[0]
                distances = results.get('distances', [[]])[0]
                docs_meta = results.get('metadatas', [[]])[0]
                texts = results.get('documents', [[]])[0]

                out = []
                for doc_id, dist, meta, text in zip(ids, distances, docs_meta, texts):
                    score = 1.0 - min(dist, 1.0)  # cosine distance -> similarity
                    doc = self._docs.get(doc_id) or VectorDocument(doc_id, text, meta)
                    # Кешируем документы, пришедшие только из ChromaDB (persisted state).
                    if doc_id not in self._docs:
                        self._docs[doc_id] = doc
                    out.append(SearchResult(doc, score))
                return out
            except (RuntimeError, ValueError, TypeError, OSError, AttributeError) as e:
                self._log(f'ChromaDB search error: {e}', level='error')

        # TF-IDF fallback
        if not self._docs:
            return []
        return self._tfidf_search(query, n)

    def delete(self, doc_id: str) -> bool:
        """Удаляет документ из хранилища."""
        if doc_id not in self._docs:
            return False

        if self._backend_type == 'chromadb':
            try:
                if self._backend is None:
                    raise RuntimeError('ChromaDB backend not initialized')
                self._backend.delete(ids=[doc_id])
            except (RuntimeError, ValueError, TypeError, OSError, AttributeError):
                pass

        del self._docs[doc_id]
        self._tfidf_index.pop(doc_id, None)
        self._dirty = True
        return True

    def clear(self):
        """Очищает всё хранилище."""
        chroma_ids = list(self._docs.keys())
        self._docs.clear()
        self._tfidf_index.clear()
        self._idf.clear()
        self._dirty = False

        if self._backend_type == 'chromadb':
            try:
                if chroma_ids and self._backend is not None:
                    self._backend.delete(ids=chroma_ids)
            except (RuntimeError, ValueError, TypeError, OSError, AttributeError):
                pass

    def count(self) -> int:
        return len(self._docs)

    def get(self, doc_id: str) -> VectorDocument | None:
        return self._docs.get(doc_id)

    def all_docs(self) -> list[VectorDocument]:
        return list(self._docs.values())

    # ── TF-IDF ────────────────────────────────────────────────────────────────

    def _tokenize(self, text: str) -> list[str]:
        """Простая токенизация: lowercase + split."""
        text = text.lower()
        tokens = re.findall(r'\b[а-яёa-z][а-яёa-z0-9]{1,}\b', text)
        return tokens

    def _compute_tf(self, tokens: list[str]) -> dict[str, float]:
        """Term Frequency."""
        if not tokens:
            return {}
        counts = Counter(tokens)
        total = len(tokens)
        return {term: count / total for term, count in counts.items()}

    def _rebuild_idf(self):
        """Пересчитывает IDF по всем документам."""
        if not self._dirty:
            return
        N = len(self._docs)
        if N == 0:
            self._idf = {}
            self._dirty = False
            return

        df: dict[str, int] = {}
        self._tfidf_index = {}

        for doc_id, doc in self._docs.items():
            tokens = self._tokenize(doc.text)
            tf = self._compute_tf(tokens)
            self._tfidf_index[doc_id] = tf
            for term in tf:
                df[term] = df.get(term, 0) + 1

        self._idf = {
            term: math.log((N + 1) / (count + 1)) + 1
            for term, count in df.items()
        }
        self._dirty = False

    def _tfidf_vector(self, doc_id: str) -> dict[str, float]:
        """Возвращает TF-IDF вектор документа."""
        tf = self._tfidf_index.get(doc_id, {})
        return {term: tf[term] * self._idf.get(term, 1.0) for term in tf}

    def _query_vector(self, query: str) -> dict[str, float]:
        """TF-IDF вектор запроса."""
        tokens = self._tokenize(query)
        tf = self._compute_tf(tokens)
        return {term: tf[term] * self._idf.get(term, 1.0) for term in tf}

    @staticmethod
    def _cosine(v1: dict[str, float], v2: dict[str, float]) -> float:
        """Косинусная схожесть двух sparse-векторов."""
        if not v1 or not v2:
            return 0.0
        common = set(v1) & set(v2)
        if not common:
            return 0.0
        dot = sum(v1[t] * v2[t] for t in common)
        norm1 = math.sqrt(sum(x * x for x in v1.values()))
        norm2 = math.sqrt(sum(x * x for x in v2.values()))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    def _tfidf_search(self, query: str, n: int) -> list[SearchResult]:
        """Поиск через TF-IDF + косинусная схожесть."""
        self._rebuild_idf()
        q_vec = self._query_vector(query)
        scores = []
        for doc_id in self._docs:
            d_vec = self._tfidf_vector(doc_id)
            score = self._cosine(q_vec, d_vec)
            if score > 0:
                scores.append((doc_id, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return [
            SearchResult(self._docs[doc_id], score)
            for doc_id, score in scores[:n]
        ]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log(self, message: str, level: str = 'info'):
        if self.monitoring:
            getattr(self.monitoring, level, self.monitoring.info)(
                message, source='vector_store'
            )
        else:
            print(f'[VectorStore] {message}')
