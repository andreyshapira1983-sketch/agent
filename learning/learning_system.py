# Learning System (система самообучения) — Слой 9
# Архитектура автономного AI-агента
# Чтение статей, анализ кода, изучение документации, обновление знаний.

from typing import Callable


class LearningSystem:
    """
    Learning System — Слой 9.

    Функции:
        - чтение и анализ внешних источников (статьи, книги, GitHub, документация)
        - извлечение знаний и сохранение в Knowledge System (Слой 2)
        - фильтрация: что стоит включать в рабочую картину мира
        - обновление существующих знаний при появлении новой информации

    Используется:
        - Knowledge Acquisition Pipeline (Слой 31) — автоматическое пополнение
        - Autonomous Loop (Слой 20)               — фаза learn
        - Reflection System (Слой 10)             — обучение на ошибках
    """

    SOURCES = ('article', 'book', 'github', 'documentation', 'dataset', 'code', 'web')

    def __init__(self, knowledge_system=None, cognitive_core=None, monitoring=None):
        self.knowledge = knowledge_system
        self.cognitive_core = cognitive_core
        self.monitoring = monitoring

        self._learned: list[dict] = []       # история всего изученного
        self._queue: list[dict] = []         # очередь источников для изучения

    # ── Основной интерфейс ────────────────────────────────────────────────────

    def learn_from(self, content: str, source_type: str = 'article',
                   source_name: str | None = None, tags: list | None = None) -> dict:
        """
        Изучает контент и сохраняет извлечённые знания.

        Args:
            content     — текст для изучения
            source_type — тип источника (article, book, github, documentation...)
            source_name — название/URL источника
            tags        — теги для классификации знаний

        Returns:
            dict с извлечёнными знаниями и статусом.
        """
        self._log(f"Изучение источника: {source_type} '{source_name or '—'}'")

        # Извлечение знаний через Cognitive Core
        extracted = self._extract_knowledge(content, source_type)

        entry = {
            'source_type': source_type,
            'source_name': source_name,
            'tags': tags or [],
            'extracted': extracted,
            'content_length': len(content),
        }
        self._learned.append(entry)

        # Сохраняем в Knowledge System
        if self.knowledge and extracted:
            key = f"{source_type}:{source_name or 'unknown'}"
            self.knowledge.store_long_term(key, extracted, source='learning')
            self._log(f"Знание сохранено в KnowledgeSystem: '{key}'")

        return entry

    def enqueue(self, source_type: str, source_name: str, fetch_fn: Callable | None = None,
                tags: list | None = None):
        """
        Добавляет источник в очередь на изучение.

        Args:
            fetch_fn — функция без аргументов, возвращающая контент (str)
        """
        self._queue.append({
            'source_type': source_type,
            'source_name': source_name,
            'fetch_fn': fetch_fn,
            'tags': tags or [],
        })

    def process_queue(self) -> list[dict]:
        """Обрабатывает всю очередь источников."""
        results = []
        while self._queue:
            item = self._queue.pop(0)
            try:
                content = item['fetch_fn']() if item.get('fetch_fn') else ''
                if not content:
                    self._log(f"Пустой контент для '{item['source_name']}', пропуск.")
                    continue
                result = self.learn_from(
                    content,
                    source_type=item['source_type'],
                    source_name=item['source_name'],
                    tags=item['tags'],
                )
                results.append(result)
            except (TypeError, ValueError, AttributeError) as e:
                self._log(f"Ошибка при изучении '{item['source_name']}': {e}")
        return results

    def update_knowledge(self, key: str, new_content: str):
        """Обновляет существующее знание новой информацией."""
        if not self.knowledge:
            return
        existing = self.knowledge.get_long_term(key)
        if existing and self.cognitive_core:
            merged = self.cognitive_core.reasoning(
                f"Объедини старое знание:\n{existing}\n\nС новым:\n{new_content}\n"
                f"Верни обновлённую версию без дублирования."
            )
            self.knowledge.store_long_term(key, merged, source='learning')
            self._log(f"Знание обновлено: '{key}'")
        else:
            self.knowledge.store_long_term(key, new_content, source='learning')

    # ── История ───────────────────────────────────────────────────────────────

    def get_learned(self, source_type: str | None = None) -> list[dict]:
        if source_type:
            return [e for e in self._learned if e['source_type'] == source_type]
        return list(self._learned)

    def get_stats(self) -> dict:
        from collections import Counter
        types = Counter(e['source_type'] for e in self._learned)
        return {
            'total_learned': len(self._learned),
            'by_source_type': dict(types),
            'queue_size': len(self._queue),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_knowledge(self, content: str, source_type: str = 'article') -> dict:
        """
        Extracts structured knowledge from content deterministically (no LLM required).

        Steps:
          1. Key sentences: split by '.', keep sentences with 10+ words, top 5.
          2. Key terms: words len>4, count frequency, top-10 (excluding stopwords).
          3. Topic: most frequent significant word.
          4. Summary: join top-3 key sentences (max 300 chars).
          5. Returns dict with topic, key_terms, key_sentences, summary, word_count.

        If cognitive_core is available AND content is complex (>500 words), also
        calls LLM and merges extra key_terms from it.
        """
        import re
        from collections import Counter

        STOPWORDS = {
            'этот', 'that', 'this', 'from', 'with', 'have', 'been',
            'were', 'they', 'their', 'будет', 'также', 'такой', 'which', 'about',
        }

        # ── Step 1: key sentences ─────────────────────────────────────────────
        raw_sentences = [s.strip() for s in content.split('.') if s.strip()]
        key_sentences = [
            s for s in raw_sentences if len(s.split()) >= 10
        ][:5]

        # ── Step 2: key terms ─────────────────────────────────────────────────
        words_raw = re.findall(r"[a-zA-Zа-яА-ЯёЁ']+", content.lower())
        significant = [
            w for w in words_raw if len(w) > 4 and w not in STOPWORDS
        ]
        term_counts = Counter(significant)
        key_terms = [term for term, _ in term_counts.most_common(10)]

        # ── Step 3: topic ─────────────────────────────────────────────────────
        topic = key_terms[0] if key_terms else ''

        # ── Step 4: summary ───────────────────────────────────────────────────
        summary = '. '.join(key_sentences[:3])
        if len(summary) > 300:
            summary = summary[:297] + '...'

        # ── Step 5: word count ────────────────────────────────────────────────
        word_count = len(words_raw)

        result: dict = {
            'topic': topic,
            'key_terms': key_terms,
            'key_sentences': key_sentences,
            'summary': summary,
            'word_count': word_count,
        }

        # ── Optional LLM enhancement ──────────────────────────────────────────
        if self.cognitive_core and word_count > 500:
            try:
                llm_raw = self.cognitive_core.reasoning(
                    f"Извлеки ключевые термины из следующего {source_type}.\n"
                    f"Верни список через запятую.\n\n{content[:4000]}"
                )
                if llm_raw:
                    llm_terms = [
                        t.strip().lower()
                        for t in str(llm_raw).split(',')
                        if t.strip()
                    ]
                    # Merge: add LLM terms not already present
                    existing = set(result['key_terms'])
                    for t in llm_terms:
                        if t not in existing:
                            result['key_terms'].append(t)
                            existing.add(t)
            except (AttributeError, TypeError, ValueError):
                pass  # LLM failure is non-fatal

        return result

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='learning')
        else:
            print(f"[Learning] {message}")
