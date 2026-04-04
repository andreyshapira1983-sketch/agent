# Knowledge Acquisition Pipeline (конвейер пополнения знаний) — Слой 31
# Архитектура автономного AI-агента
# Поиск новых источников, фильтрация информации,
# извлечение знаний, обновление базы знаний.
# pylint: disable=broad-except


import time
import threading
import hashlib
import re
import traceback
from enum import Enum
from perception.text_classifier import TextClassifier, TextType

try:
    from safety.content_fence import detect_injection
except ImportError:
    def detect_injection(text):  # type: ignore[misc]  # pylint: disable=unused-argument
        return []


class SourceStatus(Enum):
    PENDING    = 'pending'
    FETCHING   = 'fetching'
    PROCESSING = 'processing'
    DONE       = 'done'
    FAILED     = 'failed'
    FILTERED   = 'filtered'   # отфильтрован как нерелевантный


class KnowledgeSource:
    """Один источник знаний в конвейере."""

    def __init__(self, source_id: str, url_or_path: str,
                 source_type: str = 'web', tags: list | None = None):
        self.source_id = source_id
        self.url_or_path = url_or_path
        self.source_type = source_type   # 'web', 'file', 'api', 'github', 'article'
        self.tags = tags or []
        self.status = SourceStatus.PENDING
        self.raw_content: str | None = None
        self.extracted: str | None = None
        self.quality_score: float | None = None
        self.added_at = time.time()

    def to_dict(self):
        return {
            'source_id': self.source_id,
            'url_or_path': self.url_or_path,
            'source_type': self.source_type,
            'tags': self.tags,
            'status': self.status.value,
            'quality_score': self.quality_score,
            'has_content': self.raw_content is not None,
            'has_extracted': self.extracted is not None,
        }


class KnowledgeAcquisitionPipeline:
    """
    Knowledge Acquisition Pipeline — Слой 31.

    Конвейер автоматического пополнения Knowledge System (Слой 2):
        1. Discover  — поиск новых источников знаний
        2. Fetch     — получение контента через Perception Layer
        3. Filter    — оценка релевантности и качества
        4. Extract   — извлечение структурированных знаний
        5. Store     — сохранение в Knowledge System

    Используется:
        - Autonomous Loop (Слой 20)  — фаза learn
        - Learning System (Слой 9)   — совместное обучение
        - Knowledge System (Слой 2)  — целевое хранилище
    """

    def __init__(
        self,
        knowledge_system=None,
        perception_layer=None,
        cognitive_core=None,
        monitoring=None,
        quality_threshold: float = 0.5,
        text_classifier=None,
        governance=None,
        ethics=None,
        gutenberg_backend=None,
        arxiv_backend=None,
        wikipedia_backend=None,
        rss_backend=None,
        weather_backend=None,
        hackernews_backend=None,
        pypi_backend=None,
        huggingface_backend=None,
        github_backend=None,
    ):
        self.knowledge = knowledge_system
        self.perception = perception_layer
        self.cognitive_core = cognitive_core
        self.monitoring = monitoring
        self.quality_threshold = quality_threshold
        self.governance = governance
        self.ethics = ethics
        # TextClassifier — используем из PerceptionLayer или создаём свой
        self._classifier: TextClassifier = (
            text_classifier
            or (perception_layer.text_classifier
                if perception_layer and hasattr(perception_layer, 'text_classifier')
                else TextClassifier())
        )
        # Бэкенды бесплатных источников
        self._gutenberg   = gutenberg_backend
        self._arxiv       = arxiv_backend
        self._wikipedia   = wikipedia_backend
        self._rss         = rss_backend
        self._weather     = weather_backend
        self._hackernews  = hackernews_backend
        self._pypi        = pypi_backend
        self._huggingface = huggingface_backend
        self._github      = github_backend

        self._queue: list[KnowledgeSource] = []
        self._processed: list[KnowledgeSource] = []
        self._seen_keys: set[str] = set()
        self._seen_content_hashes: set[str] = set()
        self._lock = threading.RLock()

        self._restore_seen_sets()

        # Реестр авто-источников для периодического обновления
        # Формат: {'type': 'rss'|'weather'|'hackernews'|..., **kwargs}
        self._auto_sources: list[dict] = []
        self._auto_thread: threading.Thread | None = None
        self._auto_running = False
        self._auto_source_last_refresh: dict[str, float] = {}
        # Реестр авто-источников: 'weather'|'hackernews'|'rss'|'wikipedia'|'arxiv'|'gutenberg'|'pypi'

    def _restore_seen_sets(self):
        """Восстанавливает dedup-наборы из уже сохранённых знаний."""
        if not self.knowledge or not hasattr(self.knowledge, 'long_term_items'):
            return

        try:
            for key, value in self.knowledge.long_term_items():
                if isinstance(key, str) and key.startswith('acquired_source:'):
                    # Формат: acquired_source:<type>:<normalized>
                    parts = key.split(':', 2)
                    if len(parts) == 3:
                        self._seen_keys.add(f"{parts[1]}:{parts[2]}")

                if isinstance(key, str) and key.startswith('acquired_hash:'):
                    content_hash = key.split(':', 1)[1]
                    if content_hash:
                        self._seen_content_hashes.add(content_hash)

                # Fallback для старых записей индекса
                if isinstance(value, dict):
                    source_type = value.get('source_type')
                    url_or_path = value.get('url_or_path')
                    if source_type and url_or_path:
                        normalized = self._normalize_source(str(url_or_path))
                        if normalized:
                            self._seen_keys.add(f"{source_type}:{normalized}")
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass

    # ── Добавление источников ─────────────────────────────────────────────────

    def add_source(self, url_or_path: str, source_type: str = 'web',
                   tags: list | None = None) -> KnowledgeSource | None:
        """Добавляет источник в очередь конвейера."""
        normalized = self._normalize_source(url_or_path)
        if not normalized:
            return None

        # SECURITY: для web-источников — валидация URL (anti-SSRF)
        if source_type == 'web' and normalized.startswith(('http://', 'https://')):
            try:
                from knowledge.source_backends import _is_safe_http_url
                if not _is_safe_http_url(normalized):
                    self._log(f"Источник отклонён (SSRF-защита): {url_or_path[:60]}")
                    return None
            except ImportError:
                pass

        dedup_key = f"{source_type}:{normalized}"
        with self._lock:
            if dedup_key in self._seen_keys:
                self._log(f"Источник пропущен (dedup): {url_or_path[:60]}")
                return None

            if self.knowledge:
                source_index_key = f"acquired_source:{source_type}:{normalized}"
                if self.knowledge.get_long_term(source_index_key) is not None:
                    self._log(f"Источник уже есть в знаниях (skip): {url_or_path[:60]}")
                    self._seen_keys.add(dedup_key)
                    return None

        import uuid
        source = KnowledgeSource(str(uuid.uuid4())[:8], url_or_path,
                                 source_type=source_type, tags=tags or [])
        with self._lock:
            self._queue.append(source)
            self._seen_keys.add(dedup_key)
        self._log(f"Источник добавлен: [{source.source_id}] {url_or_path[:60]}")
        return source

    def add_batch(self, sources: list[dict]) -> list[KnowledgeSource]:
        """Добавляет несколько источников сразу."""
        added = []
        for s in sources:
            source = self.add_source(**s)
            if source:
                added.append(source)
        return added

    # ── Специализированные источники ──────────────────────────────────────────

    def add_gutenberg_book(self, book_id: int | None = None, url: str | None = None,
                           tags: list | None = None,
                           expected_title: str | None = None,
                           expected_author: str | None = None) -> KnowledgeSource | None:
        """
        Добавляет книгу Project Gutenberg в очередь конвейера.

        Args:
            book_id — числовой ID книги на gutenberg.org (например, 1342 — Pride and Prejudice)
            url     — прямой URL .txt файла (альтернатива book_id)
            tags    — метки для хранилища знаний

        Returns:
            KnowledgeSource или None если бэкенд не подключён.
        """
        if not self._gutenberg:
            self._log("GutenbergBackend не подключён — пропуск")
            return None
        if book_id is None and url is None:
            raise ValueError("Требуется book_id или url")

        meta = self._gutenberg.get_metadata(book_id) if book_id else {}
        title  = meta.get('title', f'gutenberg_{book_id or "book"}')
        author = meta.get('author', '')

        # Формируем url_or_path для трекинга
        hint = url or f'https://www.gutenberg.org/ebooks/{book_id}'

        auto_tags = ['literature', 'gutenberg']
        if author:
            auto_tags.append(author.split(',')[0].strip().lower())
        all_tags = list(dict.fromkeys(auto_tags + (tags or [])))

        source = self.add_source(hint, source_type='gutenberg', tags=all_tags)
        if source is None:
            return None
        # Кэшируем данные прямо сейчас, чтобы не делать повторный HTTP в _fetch
        if book_id:
            text = self._gutenberg.fetch_by_id(book_id)
        else:
            text = self._gutenberg.fetch_by_url(url)
        if text:
            if not self._matches_expected_book(
                text=text,
                metadata=meta,
                expected_title=expected_title,
                expected_author=expected_author,
            ):
                source.status = SourceStatus.FAILED
                self._log(
                    f"Gutenberg [{book_id}]: загруженный текст не совпал с ожиданием "
                    f"(title={expected_title or meta.get('title', '?')}, "
                    f"author={expected_author or meta.get('author', '?')})"
                )
                return None
            source.raw_content = text
            source.status = SourceStatus.PROCESSING
            self._log(f"Gutenberg [{book_id}] '{title}': {len(text)} символов")
        else:
            self._log(f"Gutenberg [{book_id}]: не удалось загрузить текст")

        return source

    def add_arxiv_paper(self, arxiv_id: str | None = None, query: str | None = None,
                        category: str | None = None, tags: list | None = None,
                        max_results: int = 5) -> list[KnowledgeSource]:
        """
        Добавляет научную статью (или результаты поиска) с arXiv в очередь.

        Args:
            arxiv_id — конкретный ID статьи (например, '2303.08774')
            query    — поисковый запрос (вместо конкретного ID)
            category — категория arXiv (cs.AI, cs.LG, physics и др.)
            tags     — дополнительные метки

        Returns:
            Список KnowledgeSource (может быть несколько при поиске).
        """
        if not self._arxiv:
            self._log("ArXivBackend не подключён — пропуск")
            return []

        auto_tags = ['science', 'arxiv']
        if category:
            auto_tags.append(category)
        all_tags = list(dict.fromkeys(auto_tags + (tags or [])))

        added = []

        if arxiv_id:
            paper = self._arxiv.fetch_abstract(arxiv_id)
            if paper:
                hint = paper['url']
                source = self.add_source(hint, source_type='arxiv', tags=all_tags)
                if source is None:
                    return []
                text = (
                    f"Title: {paper.get('title', '')}\n"
                    f"Authors: {', '.join(paper.get('authors', []))}\n"
                    f"Published: {paper.get('published', '')}\n\n"
                    f"Abstract:\n{paper.get('summary', '')}"
                )
                source.raw_content = text
                added.append(source)
                self._log(f"arXiv [{arxiv_id}]: '{paper.get('title', '')}' добавлен")

        elif query:
            results = self._arxiv.search(query, max_results=max_results, category=category)
            for paper in results:
                source = self.add_source(
                    paper['url'], source_type='arxiv', tags=all_tags
                )
                if source is None:
                    continue
                text = (
                    f"Title: {paper.get('title', '')}\n"
                    f"Authors: {', '.join(paper.get('authors', []))}\n"
                    f"Published: {paper.get('published', '')}\n\n"
                    f"Abstract:\n{paper.get('summary', '')}"
                )
                source.raw_content = text
                source.status = SourceStatus.PROCESSING
                added.append(source)
            self._log(f"arXiv поиск '{query}': добавлено {len(added)} статей")

        return added

    def add_wikipedia_article(self, title: str, lang: str | None = None,
                               tags: list | None = None) -> KnowledgeSource | None:
        """
        Добавляет статью Wikipedia в очередь конвейера.

        Args:
            title — название статьи (как в URL Wikipedia)
            lang  — язык ('en', 'ru', 'de' и т.д.)
            tags  — дополнительные метки

        Returns:
            KnowledgeSource или None если бэкенд не подключён.
        """
        if not self._wikipedia:
            self._log("WikipediaBackend не подключён — пропуск")
            return None

        # Если передан другой язык — создаём временный бэкенд
        wiki = self._wikipedia
        if lang and lang != wiki.lang:
            from knowledge.source_backends import WikipediaBackend
            wiki = WikipediaBackend(lang=lang)

        auto_tags = ['web', 'wikipedia', lang or wiki.lang]
        all_tags = list(dict.fromkeys(auto_tags + (tags or [])))

        hint = f'https://{wiki.lang}.wikipedia.org/wiki/{title.replace(" ", "_")}'
        source = self.add_source(hint, source_type='wikipedia', tags=all_tags)
        if source is None:
            return None

        text = wiki.fetch_article(title)
        if text:
            source.raw_content = text
            source.status = SourceStatus.PROCESSING
            self._log(f"Wikipedia '{title}' ({wiki.lang}): {len(text)} символов")
        else:
            # Fallback — только summary
            summary = wiki.fetch_summary(title)
            if summary:
                extract = summary.get('extract', '')
                if extract:
                    source.raw_content = extract
                    source.status = SourceStatus.PROCESSING
                    self._log(
                        f"Wikipedia '{title}' ({wiki.lang}): summary {len(extract)} символов"
                    )
                else:
                    self._log(f"Wikipedia '{title}': summary пустой")
            else:
                self._log(f"Wikipedia '{title}': не удалось загрузить статью")

        return source

    def discover(self, topic: str, n: int = 5) -> list[KnowledgeSource]:
        """
        Автоматически находит источники по теме через Cognitive Core.
        Cognitive Core предлагает список URL/источников для изучения.
        """
        if not self.cognitive_core:
            return []

        raw = self.cognitive_core.reasoning(
            f"Предложи {n} надёжных источников знаний по теме: {topic}\n"
            f"Для каждого укажи:\n"
            f"- URL или название\n"
            f"- Тип: web / github / article / documentation\n"
            f"- Теги (ключевые слова)\n"
            f"Формат: нумерованный список"
        )
        sources = self._parse_sources(str(raw))
        added = []
        for s in sources[:n]:
            source = self.add_source(
                s.get('url', topic),
                source_type=s.get('type', 'web'),
                tags=s.get('tags', [topic]),
            )
            if source:
                added.append(source)
        self._log(f"Discover: найдено {len(added)} источников по теме '{topic}'")
        return added

    # ── Конвейер ──────────────────────────────────────────────────────────────

    def run(self, max_sources: int | None = None) -> dict:
        """
        Запускает полный конвейер: fetch → filter → extract → store.

        Returns:
            Статистика обработки.
        """
        batch_limit = max_sources if max_sources is not None else 20
        with self._lock:
            queue = self._queue[:batch_limit]
            self._queue = self._queue[len(queue):]

        stats = {'total': len(queue), 'stored': 0, 'filtered': 0, 'failed': 0}
        self._log(f"Конвейер запущен: {len(queue)} источников")

        for source in queue:
            if source is None:
                stats['failed'] += 1
                self._log("Конвейер: пропущен пустой источник (None после dedup)")
                continue
            # 1. Fetch
            source.status = SourceStatus.FETCHING
            raw = self._fetch(source)
            if raw is None:
                source.status = SourceStatus.FAILED
                stats['failed'] += 1
                with self._lock:
                    # Убираем из seen_keys — чтобы при следующем авто-цикле источник
                    # мог быть загружен повторно (иначе неудачная загрузка блокирует навсегда)
                    _norm = self._normalize_source(source.url_or_path)
                    _dk = f"{source.source_type}:{_norm}"
                    self._seen_keys.discard(_dk)
                    self._processed.append(source)
                continue
            source.raw_content = raw

            # 2. Filter
            source.status = SourceStatus.PROCESSING
            score = self._assess_quality(source)
            source.quality_score = score
            if score < self.quality_threshold:
                source.status = SourceStatus.FILTERED
                stats['filtered'] += 1
                self._log(f"Отфильтрован [{source.source_id}]: score={score:.2f}")
                with self._lock:
                    self._processed.append(source)
                continue

            # 3. Extract
            extracted = self._extract(source)
            source.extracted = extracted

            # Отфильтровываем провальные извлечения — не сохраняем мусор в knowledge
            if extracted:
                _low = extracted.lower()
                _fail_markers = (
                    'не могу извлечь', 'невозможно извлечь', 'не могу получить',
                    'доступ отклонён', 'ssrf', 'политикой безопасности',
                    'к сожалению', 'ошибка доступа', 'не удалось извлечь',
                    'localbrainответ llm отклонён', 'llm отказался',
                )
                if any(m in _low for m in _fail_markers):
                    source.status = SourceStatus.FILTERED
                    stats['filtered'] += 1
                    self._log(
                        f"Извлечение провалено (LLM/доступ) [{source.source_id}] — запись пропущена."
                    )
                    with self._lock:
                        self._processed.append(source)
                    continue

            # 4. Store
            if extracted and self.knowledge:
                normalized = self._normalize_source(source.url_or_path)
                key = f"acquired:{source.source_type}:{normalized}"
                source_index_key = f"acquired_source:{source.source_type}:{normalized}"
                content_hash = self._content_hash(str(extracted))
                hash_key = f"acquired_hash:{content_hash}"

                is_duplicate_content = (
                    content_hash in self._seen_content_hashes
                    or self.knowledge.get_long_term(hash_key) is not None
                )
                if is_duplicate_content:
                    source.status = SourceStatus.FILTERED
                    stats['filtered'] += 1
                    self._log(f"Дубликат знания пропущен: [{source.source_id}] hash={content_hash[:10]}")
                    with self._lock:
                        self._processed.append(source)
                    continue

                # ── Governance gate: проверяем контент перед сохранением ──
                if self.governance:
                    try:
                        gov_result = self.governance.check(
                            f"knowledge_store: {source.source_type}:{source.url_or_path[:100]}",
                            context={'content_preview': str(extracted)[:200]},
                        )
                        if not gov_result.get('allowed', True):
                            source.status = SourceStatus.FILTERED
                            stats['filtered'] += 1
                            self._log(
                                f"Governance заблокировал [{source.source_id}]: "
                                f"{gov_result.get('reason', '?')}"
                            )
                            with self._lock:
                                self._processed.append(source)
                            continue
                    except Exception as _gov_e:
                        source.status = SourceStatus.FILTERED
                        stats['filtered'] += 1
                        self._log(f"Governance exception [{source.source_id}]: {_gov_e}")
                        with self._lock:
                            self._processed.append(source)
                        continue

                # ── Ethics gate: проверяем этичность контента ──
                if self.ethics:
                    try:
                        eth_eval = self.ethics.evaluate(
                            f"store_knowledge: {str(extracted)[:300]}",
                            context={'source': source.url_or_path},
                        )
                        if eth_eval.verdict.value == 'rejected':
                            source.status = SourceStatus.FILTERED
                            stats['filtered'] += 1
                            self._log(
                                f"Ethics отклонил [{source.source_id}]: "
                                f"{'; '.join(eth_eval.reasons)}"
                            )
                            with self._lock:
                                self._processed.append(source)
                            continue
                    except Exception:
                        pass  # ethics недоступен — не блокируем

                _src = source.source_type or 'web'
                self.knowledge.store_long_term(
                    key, extracted,
                    source=_src, trust=0.5, verified=False,
                )
                self.knowledge.store_long_term(
                    source_index_key,
                    {
                        'source_type': source.source_type,
                        'url_or_path': source.url_or_path,
                        'content_hash': content_hash,
                        'updated_at': time.time(),
                    },
                    source=_src, trust=0.5,
                )
                self.knowledge.store_long_term(
                    hash_key,
                    {
                        'source_key': key,
                        'source_type': source.source_type,
                        'url_or_path': source.url_or_path,
                        'updated_at': time.time(),
                    },
                    source=_src, trust=0.5,
                )
                with self._lock:
                    self._seen_content_hashes.add(content_hash)
                # Эпизодическая память
                self.knowledge.record_episode(
                    task=f"acquire:{source.url_or_path[:50]}",
                    action='knowledge_acquisition',
                    result=extracted[:200],
                    success=True,
                )
                stats['stored'] += 1

            source.status = SourceStatus.DONE
            with self._lock:
                self._processed.append(source)

        self._log(f"Конвейер завершён: сохранено={stats['stored']}, "
                  f"отфильтровано={stats['filtered']}, ошибок={stats['failed']}")
        self._log(f"saved_to_long_term={stats['stored']} (disk persistence через PersistentBrain autosave)")
        return stats

    def run_one(self, source: KnowledgeSource | None) -> bool:
        """Обрабатывает один источник вне очереди."""
        if source is None:
            self._log("run_one: источник не поставлен в очередь (dedup/None)")
            return False
        with self._lock:
            self._queue.insert(0, source)
        result = self.run(max_sources=1)
        return result['stored'] > 0

    # ── Статистика ────────────────────────────────────────────────────────────

    def get_processed(self, status: SourceStatus | None = None) -> list[dict]:
        with self._lock:
            sources = list(self._processed)
        if status:
            sources = [s for s in sources if s.status == status]
        return [s.to_dict() for s in sources]

    def queue_size(self) -> int:
        with self._lock:
            return len(self._queue)

    def summary(self) -> dict:
        from collections import Counter
        with self._lock:
            statuses = Counter(s.status.value for s in self._processed)
            queue_len = len(self._queue)
            processed_len = len(self._processed)
        return {
            'queue_size': queue_len,
            'processed': processed_len,
            **dict(statuses),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _fetch(self, source: KnowledgeSource) -> str | None:
        if source is None:
            return None
        # Если контент уже загружен специализированным бэкендом — не запрашиваем повторно
        if source.raw_content:
            return source.raw_content
        try:
            if self.perception:
                if source.source_type == 'web':
                    result = self.perception.fetch_web(source.url_or_path)
                elif source.source_type == 'file':
                    result = self.perception.read_file(source.url_or_path)
                elif source.source_type == 'api':
                    result = self.perception.call_api(source.url_or_path)
                else:
                    result = self.perception.parse_document(source.url_or_path)
                return str(result) if result else None
            return None
        except Exception as e:
            self._log(f"Fetch error [{source.source_id}]: {e}")
            return None

    def _assess_quality(self, source: KnowledgeSource) -> float:
        if not source.raw_content:
            return 0.0
        content = str(source.raw_content)
        score = 0.5  # базовая оценка

        # Защита от indirect prompt injection: отклоняем контент
        # содержащий адверсариальные паттерны
        injection_hits = detect_injection(content)
        if injection_hits:
            self._log(
                f"[quality] INJECTION DETECTED in [{source.source_id}]: "
                f"{injection_hits[:3]}  — отфильтрован"
            )
            return 0.0

        # Эвристики качества
        if len(content) > 500:
            score += 0.2
        if len(content) > 2000:
            score += 0.1
        # Признаки мусора
        if content.count('404') > 3 or 'error' in content.lower()[:100]:
            score -= 0.3

        # Быстрый пре-фильтр по тегам и URL — если очевидно нерелевантно,
        # не тратим LLM-токены на оценку
        _RELEVANT_TAG_KEYWORDS = (
            'ai', 'ml', 'llm', 'agent', 'python', 'tech', 'science',
            'coding', 'automation', 'programming', 'research',
        )
        _LOW_VALUE_TAG_KEYWORDS = (
            'world_news', 'ru_news', 'news_world', 'sports', 'crime',
            'politics', 'entertainment', 'celebrity',
        )
        tags_lower = ' '.join(str(t) for t in (source.tags or [])).lower()
        url_lower = (source.url_or_path or '').lower()
        _LOW_VALUE_URL_PATTERNS = (
            'bbc.com/news/articles', 'skynews.com', 'news.google',
            'theguardian.com/world', 'ria.ru', 'tass.ru',
        )
        is_relevant_tag = any(kw in tags_lower for kw in _RELEVANT_TAG_KEYWORDS)
        is_low_value_tag = any(kw in tags_lower for kw in _LOW_VALUE_TAG_KEYWORDS)
        is_low_value_url = any(p in url_lower for p in _LOW_VALUE_URL_PATTERNS)

        if not is_relevant_tag and (is_low_value_tag or is_low_value_url):
            # Пропускаем LLM-оценку для заведомо нерелевантного контента
            self._log(
                f"[quality] Быстрый пропуск LLM-оценки [{source.source_id}] "
                f"(теги: {source.tags}, url: {url_lower[:60]})"
            )
            return score - 0.2  # чуть снижаем базовую оценку, но без LLM-звонка

        # LLM-оценка если доступна
        if self.cognitive_core and len(content) > 200:
            raw = self.cognitive_core.reasoning(
                f"Оцени качество и релевантность этого контента от 0 до 1.\n"
                f"Теги: {source.tags}\n"
                f"Контент (первые 500 символов):\n{content[:500]}\n\n"
                f"Ответь только числом от 0 до 1."
            )
            try:
                m = re.search(r'([01]?\.\d+)', str(raw))
                if m:
                    score = float(m.group(1))
            except Exception:
                pass

        return max(0.0, min(1.0, score))

    def _extract(self, source: KnowledgeSource) -> str | None:
        content = str(source.raw_content or '')
        if not content:
            return None

        # Определяем тип текста для type-aware промпта
        classification = self._classifier.classify(
            content, source_hint=source.url_or_path
        )
        text_type = classification.text_type

        if self.cognitive_core:
            # Формируем подсказку в зависимости от типа
            if text_type == TextType.SCIENCE:
                instruction = (
                    "Это научный текст. Извлеки: гипотезу, методологию, "
                    "ключевые результаты, выводы и применимые паттерны."
                )
            elif text_type == TextType.LITERATURE:
                instruction = (
                    "Это художественный текст. Извлеки: автора, эпоху, "
                    "ключевых персонажей, основные темы и краткое содержание."
                )
            elif text_type == TextType.CODE:
                instruction = (
                    "Это программный код. Извлеки: язык, цель, ключевые функции/классы, "
                    "архитектурные паттерны и применимые техники."
                )
            elif text_type == TextType.NEWS:
                instruction = (
                    "Это новостной текст. Извлеки: событие, дату, участников, "
                    "ключевые факты и возможные последствия."
                )
            elif text_type == TextType.DOCUMENT:
                instruction = (
                    "Это официальный документ. Извлеки: тип документа, стороны, "
                    "ключевые пункты, обязательства и важные условия."
                )
            else:
                instruction = (
                    "Извлеки ключевые знания: факты, выводы, применимые паттерны."
                )

            # Для длинных текстов — чанкинг: извлекаем из каждого куска и объединяем
            CHUNK = 6000
            if len(content) <= CHUNK:
                chunks = [content]
            else:
                chunks = [content[i:i + CHUNK] for i in range(0, min(len(content), CHUNK * 5), CHUNK)]

            parts = []
            for idx, chunk in enumerate(chunks):
                chunk_label = f" (часть {idx + 1}/{len(chunks)})" if len(chunks) > 1 else ""
                part = self.cognitive_core.reasoning(
                    f"Извлеки ключевые знания из следующего источника{chunk_label}.\n"
                    f"Тип текста: {text_type.value} "
                    f"(уверенность {classification.confidence:.0%}, язык: {classification.language})\n"
                    f"Источник: {source.source_type}, Теги: {source.tags}\n"
                    f"{instruction}\n\n"
                    f"Содержимое:\n{chunk}\n\n"
                    f"Сформулируй ответ кратко и структурированно."
                )
                if part:
                    parts.append(str(part))

            if not parts:
                return None
            if len(parts) == 1:
                return parts[0]
            # Несколько чанков — финальная свёртка
            combined = "\n\n---\n\n".join(parts)
            return self.cognitive_core.reasoning(
                f"Объедини и сверни следующие извлечённые знания из одного источника "
                f"(тип: {text_type.value}, теги: {source.tags}) в единый структурированный конспект.\n\n"
                f"{combined}\n\n"
                f"Убери повторы, сохрани все уникальные факты и выводы."
            )

        # Без LLM — возвращаем контент с заголовком о типе
        header = (f"[{text_type.value.upper()} | "
                  f"conf={classification.confidence:.0%} | "
                  f"lang={classification.language}]\n")
        return header + content[:1000]

    def _parse_sources(self, raw: str) -> list[dict]:
        sources = []
        for line in raw.splitlines():
            if re.match(r'^\d+[.)]\s+', line):
                url = re.sub(r'^\d+[.)]\s+', '', line).strip()
                source_type = 'web'
                for t in ('github', 'article', 'documentation', 'file', 'api'):
                    if t in line.lower():
                        source_type = t
                        break
                tags = [w for w in re.findall(r'\b\w+\b', line.lower())
                        if len(w) > 4][:3]
                sources.append({'url': url, 'type': source_type, 'tags': tags})
        return sources

    @staticmethod
    def _normalize_source(url_or_path: str) -> str:
        """Нормализует адрес источника для устойчивого dedup-ключа."""
        value = str(url_or_path or '').strip().lower()
        value = value.replace('\\\\', '/').replace('\\', '/')
        return value.rstrip('/')

    @staticmethod
    def _content_hash(text: str) -> str:
        """Считает хеш контента для антидубля знаний."""
        normalized = ' '.join(str(text or '').lower().split())
        return hashlib.md5(normalized.encode('utf-8', errors='replace')).hexdigest()

    @staticmethod
    def _normalize_book_marker(text: str) -> str:
        marker = str(text or '').strip().lower()
        marker = marker.replace('_', ' ')
        marker = re.sub(r'[^\w\sа-яё-]+', ' ', marker, flags=re.IGNORECASE)
        return ' '.join(marker.split())

    @classmethod
    def _matches_expected_book(cls, text: str, metadata: dict,
                               expected_title: str | None,
                               expected_author: str | None) -> bool:
        if not expected_title and not expected_author:
            return True

        haystack = cls._normalize_book_marker(
            ' '.join([
                str(metadata.get('title', '')),
                str(metadata.get('author', '')),
                str(text or '')[:3000],
            ])
        )
        if expected_title:
            title_marker = cls._normalize_book_marker(expected_title)
            if title_marker and title_marker not in haystack:
                return False
        if expected_author:
            author_marker = cls._normalize_book_marker(expected_author)
            if author_marker and author_marker not in haystack:
                return False
        return True

    # ── RSS-ленты ─────────────────────────────────────────────────────────────

    def add_rss_feed(self, url: str, tags: list | None = None,
                     limit: int = 20) -> list[KnowledgeSource]:
        """
        Добавляет RSS/Atom ленту в очередь.

        Args:
            url   — URL ленты
            tags  — метки (например ['tech', 'ai'])
            limit — максимум статей из ленты
        """
        if not self._rss:
            self._log("RSSBackend не подключён — пропуск")
            return []

        items = self._rss.fetch(url, limit=limit)
        if not items:
            self._log(f"RSS [{url[:50]}]: нет данных")
            return []

        added = []
        for item in items:
            text = (
                f"Заголовок: {item.get('title', '')}\n"
                f"Ссылка: {item.get('link', '')}\n"
                f"Дата: {item.get('published', '')}\n\n"
                f"{item.get('summary', '')}"
            ).strip()
            source = self.add_source(
                item.get('link', url),
                source_type='rss',
                tags=(tags or []) + ['news', 'rss'],
            )
            if source is None:
                continue
            source.raw_content = text
            source.status = SourceStatus.PROCESSING
            added.append(source)

        self._log(f"RSS [{url[:50]}]: добавлено {len(added)} статей")
        return added

    def add_rss_preset(self, *categories: str,
                       limit_per_feed: int = 5) -> list[KnowledgeSource]:
        """
        Добавляет готовые пресеты RSS-лент.

        Категории: tech, science, world_news, ru_news, ai, crypto.
        Если не указаны — добавляются все.

        Args:
            *categories    — категории лент
            limit_per_feed — статей с каждой ленты
        """
        if not self._rss:
            self._log("RSSBackend не подключён — пропуск")
            return []

        urls = self._rss.get_presets(*categories)
        all_added = []
        for url in urls:
            cat_tags = list(categories) if categories else ['news']
            added = self.add_rss_feed(url, tags=cat_tags,
                                      limit=limit_per_feed)
            all_added.extend(added)
        self._log(f"RSS пресеты {categories or 'all'}: "
                  f"добавлено {len(all_added)} статей")
        return all_added

    # ── Погода ────────────────────────────────────────────────────────────────

    def add_weather(self, city: str,
                    tags: list | None = None) -> KnowledgeSource | None:
        """
        Добавляет текущую погоду и прогноз на 3 дня в память агента.

        Args:
            city — название города (на любом языке, wttr.in сам определит)
            tags — дополнительные метки
        """
        if not self._weather:
            self._log("WeatherBackend не подключён — пропуск")
            return None

        text = self._weather.fetch_text(city)
        if not text or 'недоступны' in text:
            self._log(f"Weather [{city}]: нет данных")
            return None

        source = self.add_source(
            f'weather:{city}',
            source_type='weather',
            tags=(tags or []) + ['weather', city.lower()],
        )
        if source is None:
            return None
        source.raw_content = text
        source.status = SourceStatus.PROCESSING
        self._log(f"Weather [{city}]: добавлен прогноз")
        return source

    # ── Hacker News ───────────────────────────────────────────────────────────

    def add_hackernews(self, section: str = 'top',
                       limit: int = 20,
                    tags: list | None = None) -> KnowledgeSource | None:
        """
        Добавляет топ-истории Hacker News в очередь.

        Args:
            section — 'top' | 'new' | 'best' | 'ask' | 'show'
            limit   — количество историй
            tags    — дополнительные метки
        """
        if not self._hackernews:
            self._log("HackerNewsBackend не подключён — пропуск")
            return None

        text = self._hackernews.fetch_text(limit=limit, section=section)
        if not text or 'нет данных' in text:
            self._log(f"HackerNews [{section}]: нет данных")
            return None

        source = self.add_source(
            f'hackernews:{section}',
            source_type='hackernews',
            tags=(tags or []) + ['tech', 'news', 'hackernews'],
        )
        if source is None:
            return None
        source.raw_content = text
        source.status = SourceStatus.PROCESSING
        self._log(f"HackerNews [{section}]: добавлено {limit} историй")
        return source

    def add_pypi_package(self, name: str,
                          tags: list | None = None) -> 'KnowledgeSource | None':
        """
        Загружает метаданные и описание Python-пакета с PyPI
        и добавляет в очередь конвейера.

        Args:
            name — имя пакета PyPI (например 'requests', 'numpy')
            tags — дополнительные метки
        """
        if not self._pypi:
            self._log("PyPIBackend не подключён — пропуск")
            return None

        text = self._pypi.fetch_text(name)
        if not text or 'не найден' in text:
            self._log(f"PyPI [{name}]: пакет не найден")
            return None

        source = self.add_source(
            f'pypi:{name}',
            source_type='pypi',
            tags=(tags or []) + ['python', 'library', 'pypi', name],
        )
        if source is None:
            return None
        source.raw_content = text
        source.status = SourceStatus.PROCESSING
        self._log(f"PyPI [{name}]: описание добавлено в конвейер")
        return source

    def add_huggingface_models(self, query: str = 'large language models',
                               limit: int = 5, hf_source_type: str = 'models',
                               tags: list | None = None) -> list:
        """
        Поиск моделей и датасетов на HuggingFace Hub.

        Args:
            query           — поисковый запрос (например, 'bert', 'text-generation')
            limit           — максимальное число результатов
            hf_source_type  — тип поиска: 'models' или 'datasets'
            tags            — дополнительные метки

        Returns:
            Список добавленных KnowledgeSource объектов.
        """
        if not self._huggingface:
            self._log("HuggingFaceBackend не подключён — пропуск")
            return []

        added = []
        try:
            if hf_source_type == 'models':
                results = self._huggingface.search_models(query, limit=limit)
            elif hf_source_type == 'datasets':
                results = self._huggingface.search_datasets(query, limit=limit)
            else:
                results = []

            for item in (results or []):
                if 'error' in item:
                    continue

                url = f"https://huggingface.co/{item.get('modelId', item.get('id', ''))}"
                item_id = item.get('modelId', item.get('id', ''))
                all_tags = (tags or []) + ['huggingface', hf_source_type, 'ai-models']
                if item.get('tasks'):
                    all_tags.extend(item.get('tasks', []))

                source = self.add_source(
                    url,
                    source_type=f'huggingface_{hf_source_type}',
                    tags=all_tags,
                )
                if source is None:
                    continue
                # Формируем краткое описание из метаданных
                description = (
                    f"Model: {item_id}\n"
                    f"Downloads: {item.get('downloads', 0)}\n"
                    f"Likes: {item.get('likes', 0)}\n"
                    f"Library: {item.get('library_name', 'unknown')}\n"
                    f"Tasks: {', '.join(item.get('tasks', []))}"
                )
                source.raw_content = description
                source.status = SourceStatus.PROCESSING
                added.append(source)

            self._log(f"HuggingFace [{hf_source_type}] '{query}': добавлено {len(added)} элементов")
        except Exception as e:
            self._log(f"HuggingFace ошибка: {e}")

        return added

    # ── Авто-обучение в фоне ──────────────────────────────────────────────────

    def register_auto_source(self, source_type: str, ttl_hours: float | None = None, **kwargs):
        """
        Регистрирует источник для периодического автообновления.

        Примеры:
            pipeline.register_auto_source('weather', city='Moscow')
            pipeline.register_auto_source('hackernews', section='top', limit=20)
            pipeline.register_auto_source('rss_preset', categories=['tech','ai'])
            pipeline.register_auto_source('wikipedia', title='Artificial_intelligence')
            pipeline.register_auto_source('arxiv', query='large language models', max_results=3)
        """
        ttl = ttl_hours if ttl_hours is not None else self._default_ttl_hours_for_source(source_type)
        with self._lock:
            self._auto_sources.append({'type': source_type, 'ttl_hours': float(ttl), **kwargs})
        self._log(
            f"Авто-источник зарегистрирован: {source_type} {kwargs} "
            f"(ttl={float(ttl):.2f}ч)"
        )

    def auto_refresh(self) -> dict:
        """
        Обновляет все зарегистрированные авто-источники и запускает конвейер.
        Вызывается ProactiveMind периодически.

        Returns:
            Статистика: {'added': N, 'processed': stats_dict}
        """
        added = 0
        skipped_by_ttl = 0
        with self._lock:
            auto_specs = list(self._auto_sources)
        for spec in auto_specs:
            try:
                stype = spec['type']
                kw = {k: v for k, v in spec.items() if k != 'type'}
                ttl_hours = float(kw.pop('ttl_hours', self._default_ttl_hours_for_source(stype)))

                if not self._should_refresh_auto_source(stype, kw, ttl_hours):
                    skipped_by_ttl += 1
                    continue

                if stype == 'weather':
                    r = self.add_weather(**kw)
                    if r:
                        added += 1
                        self._mark_auto_source_refreshed(stype, kw)

                elif stype == 'hackernews':
                    r = self.add_hackernews(**kw)
                    if r:
                        added += 1
                        self._mark_auto_source_refreshed(stype, kw)

                elif stype == 'rss_feed':
                    items = self.add_rss_feed(**kw)
                    added += len(items)
                    if items:
                        self._mark_auto_source_refreshed(stype, kw)

                elif stype == 'rss_preset':
                    cats = kw.pop('categories', [])
                    items = self.add_rss_preset(*cats, **kw)
                    added += len(items)
                    if items:
                        self._mark_auto_source_refreshed(stype, {'categories': cats, **kw})

                elif stype == 'wikipedia':
                    r = self.add_wikipedia_article(**kw)
                    if r:
                        added += 1
                        self._mark_auto_source_refreshed(stype, kw)

                elif stype == 'arxiv':
                    items = self.add_arxiv_paper(**kw)
                    added += len(items)
                    if items:
                        self._mark_auto_source_refreshed(stype, kw)

                elif stype == 'gutenberg':
                    r = self.add_gutenberg_book(**kw)
                    if r:
                        added += 1
                        self._mark_auto_source_refreshed(stype, kw)

                elif stype == 'pypi':
                    r = self.add_pypi_package(**kw)
                    if r:
                        added += 1
                        self._mark_auto_source_refreshed(stype, kw)

                elif stype == 'huggingface':
                    items = self.add_huggingface_models(**kw)
                    added += len(items)
                    if items:
                        self._mark_auto_source_refreshed(stype, kw)

            except Exception as e:
                self._log(
                    f"auto_refresh ошибка [{spec}]: {e}\n"
                    f"{traceback.format_exc()}"
                )

        stats = {}
        if added > 0:
            self._log(f"auto_refresh: добавлено {added} источников → запуск конвейера")
            stats = self.run()

        return {'added': added, 'skipped_by_ttl': skipped_by_ttl, 'processed': stats}

    def start_auto_refresh(self, interval_seconds: int = 1800):
        """
        Запускает фоновый поток автообновления.

        Args:
            interval_seconds — интервал между обновлениями (по умолчанию 30 минут)
        """
        with self._lock:
            if self._auto_running:
                return
            self._auto_running = True

        def _loop():
            # Первый запуск через задержку, чтобы не перегружать старт агента
            time.sleep(interval_seconds)
            while True:
                with self._lock:
                    running = self._auto_running
                if not running:
                    break
                self.auto_refresh()
                time.sleep(interval_seconds)

        self._auto_thread = threading.Thread(target=_loop, daemon=True)
        self._auto_thread.start()
        self._log(f"Фоновое авто-обучение запущено (каждые {interval_seconds//60} мин)")

    def stop_auto_refresh(self):
        """Останавливает фоновый поток автообновления."""
        with self._lock:
            self._auto_running = False
        self._log("Фоновое авто-обучение остановлено")

    def mastery_report(self, sample_size: int = 30) -> dict:
        """
        Возвращает метрику усвоения новых знаний (0..1) и сводку по базе acquired:.

        Логика:
        - берём записи acquired:* (контент знаний)
        - считаем долю записей с признаками структурированного извлечения
        - учитываем полноту покрытия источников (source-index + hash-index)
        """
        if not self.knowledge:
            return {'mastery_score': 0.0, 'checked': 0, 'structured': 0}

        keys = [k for k in getattr(self.knowledge, '_long_term', {}).keys() if str(k).startswith('acquired:')]
        source_keys = [k for k in getattr(self.knowledge, '_long_term', {}).keys() if str(k).startswith('acquired_source:')]
        hash_keys = [k for k in getattr(self.knowledge, '_long_term', {}).keys() if str(k).startswith('acquired_hash:')]

        if not keys:
            return {
                'mastery_score': 0.0,
                'checked': 0,
                'structured': 0,
                'acquired_records': 0,
                'source_indexed': len(source_keys),
                'content_indexed': len(hash_keys),
            }

        checked_keys = keys[-sample_size:]
        structured = 0
        for k in checked_keys:
            value = str(self.knowledge.get_long_term(k) or '')
            markers = ('ключевые', 'факты', 'вывод', 'title:', 'abstract:', 'authors:', 'source:')
            if any(m in value.lower() for m in markers) and len(value) >= 120:
                structured += 1

        structured_ratio = structured / max(1, len(checked_keys))
        index_ratio = min(1.0, len(source_keys) / max(1, len(keys)))
        hash_ratio = min(1.0, len(hash_keys) / max(1, len(keys)))

        mastery_score = round(structured_ratio * 0.6 + index_ratio * 0.2 + hash_ratio * 0.2, 3)
        return {
            'mastery_score': mastery_score,
            'checked': len(checked_keys),
            'structured': structured,
            'acquired_records': len(keys),
            'source_indexed': len(source_keys),
            'content_indexed': len(hash_keys),
        }

    def _default_ttl_hours_for_source(self, source_type: str) -> float:
        """TTL по умолчанию: новости часто, энциклопедии/книги реже."""
        st = str(source_type or '').lower()
        if st in ('hackernews', 'rss', 'rss_feed', 'rss_preset', 'weather'):
            return 1.0
        if st in ('arxiv', 'pypi', 'huggingface', 'wikipedia'):
            return 6.0
        if st in ('gutenberg',):
            return 24.0
        return 4.0

    def _auto_source_key(self, source_type: str, kwargs: dict) -> str:
        """Стабильный ключ авто-источника для контроля TTL."""
        payload = '|'.join(f"{k}={kwargs[k]}" for k in sorted(kwargs.keys()))
        return f"{source_type}:{payload}"

    def _should_refresh_auto_source(self, source_type: str, kwargs: dict,
                                    ttl_hours: float) -> bool:
        """Проверяет, истёк ли TTL для авто-источника."""
        key = self._auto_source_key(source_type, kwargs)
        with self._lock:
            last = self._auto_source_last_refresh.get(key)
        if not last:
            return True
        return (time.time() - last) >= max(1.0, ttl_hours * 3600.0)

    def _mark_auto_source_refreshed(self, source_type: str, kwargs: dict):
        key = self._auto_source_key(source_type, kwargs)
        with self._lock:
            self._auto_source_last_refresh[key] = time.time()

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='acquisition_pipeline')
        else:
            print(f"[AcquisitionPipeline] {message}")
