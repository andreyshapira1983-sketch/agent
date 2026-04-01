# TextClassifier — классификатор типа текста — Слой 1 (расширение Perception)
# Архитектура автономного AI-агента
#
# Определяет что именно читает агент:
#   CODE        — программный код (Python, JS, SQL и др.)
#   SCIENCE     — научная статья, исследование, учебник
#   LITERATURE  — художественный текст, книга (Project Gutenberg и др.)
#   NEWS        — новость, репортаж, публикация СМИ
#   DOCUMENT    — официальный документ, договор, инструкция
#   WEB         — веб-страница смешанного содержания
#   CONVERSATION — диалог, чат, разговорный текст
#
# Работает ДЕТЕРМИНИРОВАННО — без LLM, только регулярные выражения и эвристики.
# Результат прикрепляется к каждому входящему тексту в PerceptionLayer.

import re
from enum import Enum


class TextType(Enum):
    CODE         = 'code'
    SCIENCE      = 'science'
    LITERATURE   = 'literature'
    NEWS         = 'news'
    DOCUMENT     = 'document'
    WEB          = 'web'
    CONVERSATION = 'conversation'


class TextClassification:
    """Результат классификации одного текстового блока."""

    def __init__(self, text_type: TextType, confidence: float,
                 language: str = 'unknown', signals: list | None = None):
        self.text_type   = text_type
        self.confidence  = confidence   # 0.0 — 1.0
        self.language    = language     # 'ru', 'en', 'code', ...
        self.signals     = signals or []  # что именно сработало

    def to_dict(self) -> dict:
        return {
            'text_type':  self.text_type.value,
            'confidence': round(self.confidence, 2),
            'language':   self.language,
            'signals':    self.signals,
        }

    def __repr__(self):
        return (f"TextClassification({self.text_type.value}, "
                f"conf={self.confidence:.0%}, lang={self.language})")


class TextClassifier:
    """
    Детерминированный классификатор типа текста.

    Используется:
        - PerceptionLayer  (Слой 1)  — при любом входящем тексте
        - KnowledgeAcquisitionPipeline (Слой 31) — для роутинга в storage
        - LearningSystem   (Слой 9)  — правильные теги при обучении

    Не требует LLM — работает мгновенно на любом тексте.
    """

    # ── Сигнатуры кода ────────────────────────────────────────────────────────
    _CODE_PATTERNS = [
        re.compile(r'\bdef\s+\w+\s*\('),           # Python function
        re.compile(r'\bclass\s+\w+[\s:(]'),         # Python/JS class
        re.compile(r'\bimport\s+\w+'),              # import statement
        re.compile(r'\bfrom\s+\w[\w.]+\s+import'),  # from X import Y
        re.compile(r'\bfunction\s+\w+\s*\('),       # JS function
        re.compile(r'\bconst\s+\w+\s*='),           # JS const
        re.compile(r'\bvar\s+\w+\s*='),             # JS var
        re.compile(r'\bpublic\s+(static\s+)?\w+\s+\w+\s*\('),  # Java/C#
        re.compile(r'SELECT\s+.+\s+FROM\s+\w+', re.IGNORECASE),  # SQL
        re.compile(r'#include\s*<\w+'),             # C/C++
        re.compile(r'\breturn\s+.+;'),              # return statement
        re.compile(r'if\s*\(.+\)\s*\{'),            # if block
        re.compile(r'for\s*\(.+\)\s*\{'),           # for loop
        re.compile(r'^\s{4,}\w', re.MULTILINE),     # indented code
        re.compile(r'```\w*\n'),                    # code fence markdown
    ]

    _CODE_EXTENSIONS = frozenset({
        '.py', '.js', '.ts', '.java', '.cpp', '.c', '.cs', '.go',
        '.rb', '.php', '.swift', '.kt', '.rs', '.sql', '.sh', '.bash',
        '.r', '.m', '.scala', '.html', '.css', '.json', '.xml', '.yaml',
    })

    # ── Сигнатуры науки ───────────────────────────────────────────────────────
    _SCIENCE_KEYWORDS = frozenset({
        'abstract', 'introduction', 'methodology', 'conclusion', 'references',
        'hypothesis', 'experiment', 'results', 'discussion', 'arxiv',
        'doi:', 'journal', 'proceedings', 'theorem', 'proof', 'lemma',
        'figure', 'table', 'citation', 'bibliography', 'peer-review',
        # Russian
        'аннотация', 'введение', 'методология', 'заключение', 'список литературы',
        'гипотеза', 'эксперимент', 'результаты', 'обсуждение', 'теорема',
        'доказательство', 'рисунок', 'таблица', 'ссылки', 'библиография',
    })

    # ── Сигнатуры литературы ──────────────────────────────────────────────────
    _LITERATURE_KEYWORDS = frozenset({
        'chapter', 'said', 'replied', 'whispered', 'shouted', 'narrator',
        'protagonist', 'novel', 'story', 'once upon', 'gutenberg',
        'project gutenberg', 'pg-13', 'ebook',
        # Russian
        'глава', 'сказал', 'ответил', 'прошептал', 'воскликнул', 'рассказчик',
        'роман', 'повесть', 'рассказ', 'жил-был', 'главный герой',
    })

    _LITERATURE_SIGNALS = [
        re.compile(r'"[^"]{5,}[.,!?]"\s*[—-]?\s*\w+\s+said', re.IGNORECASE),
        re.compile(r'«[^»]{5,}[.,!?]»'),          # Russian quotes
        re.compile(r'Chapter\s+[IVXLC\d]+', re.IGNORECASE),
        re.compile(r'Глава\s+\d+', re.IGNORECASE),
        re.compile(r'\*\s*\*\s*\*'),               # scene break
    ]

    # ── Сигнатуры новостей ────────────────────────────────────────────────────
    _NEWS_KEYWORDS = frozenset({
        'reported', 'according to', 'sources say', 'breaking', 'update',
        'press release', 'reuters', 'ap news', 'bbc', 'cnn',
        # Russian
        'сообщает', 'по данным', 'источники сообщают', 'срочно',
        'пресс-релиз', 'агентство', 'корреспондент',
    })

    _NEWS_SIGNALS = [
        re.compile(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}'),
        re.compile(r'\d{1,2}\s+(января|февраля|марта|апреля|мая|июня|июля|'
                   r'августа|сентября|октября|ноября|декабря)\s+\d{4}'),
    ]

    # ── Сигнатуры документов ──────────────────────────────────────────────────
    _DOCUMENT_KEYWORDS = frozenset({
        'hereby', 'whereas', 'pursuant', 'notwithstanding', 'hereinafter',
        'terms and conditions', 'privacy policy', 'agreement', 'clause',
        'article', 'section', 'paragraph', 'exhibit', 'appendix',
        # Russian
        'настоящим', 'в соответствии', 'согласно', 'условия договора',
        'политика конфиденциальности', 'соглашение', 'пункт', 'статья',
        'раздел', 'приложение', 'приказ', 'постановление',
    })

    # ── Определение языка ─────────────────────────────────────────────────────
    _RU_CHARS = re.compile(r'[а-яёА-ЯЁ]')
    _EN_CHARS = re.compile(r'[a-zA-Z]')

    # ─────────────────────────────────────────────────────────────────────────

    def classify(self, text: str, source_hint: str = '') -> TextClassification:
        """
        Классифицирует текст. Работает без LLM.

        Args:
            text        — текст для классификации
            source_hint — URL или путь к файлу (помогает уточнить тип)

        Returns:
            TextClassification с типом, уверенностью и сигналами
        """
        if not text or not text.strip():
            return TextClassification(TextType.WEB, 0.0, 'unknown', ['empty_text'])

        text_sample = text[:5000]  # анализируем первые 5000 символов
        signals: list[str] = []
        scores: dict[TextType, float] = {t: 0.0 for t in TextType}

        # ── 1. Подсказка по расширению файла ──────────────────────────────────
        if source_hint:
            hint_lower = source_hint.lower()
            ext = '.' + hint_lower.rsplit('.', 1)[-1] if '.' in hint_lower else ''
            if ext in self._CODE_EXTENSIONS:
                scores[TextType.CODE] += 0.6
                signals.append(f'file_ext:{ext}')
            if 'gutenberg' in hint_lower or 'gutenberg.org' in hint_lower:
                scores[TextType.LITERATURE] += 0.5
                signals.append('source:gutenberg')
            if 'arxiv' in hint_lower:
                scores[TextType.SCIENCE] += 0.5
                signals.append('source:arxiv')
            if 'wikipedia' in hint_lower:
                scores[TextType.WEB] += 0.3
                signals.append('source:wikipedia')
            if 'github' in hint_lower or 'gitlab' in hint_lower:
                scores[TextType.CODE] += 0.3
                signals.append('source:github')

        # ── 2. Код — паттерны синтаксиса ──────────────────────────────────────
        code_hits = sum(1 for p in self._CODE_PATTERNS if p.search(text_sample))
        if code_hits >= 3:
            scores[TextType.CODE] += 0.4 + code_hits * 0.05
            signals.append(f'code_patterns:{code_hits}')
        elif code_hits >= 1:
            scores[TextType.CODE] += 0.2
            signals.append(f'code_patterns:{code_hits}')

        # ── 3. Наука — ключевые слова ─────────────────────────────────────────
        text_lower = text_sample.lower()
        sci_hits = sum(1 for kw in self._SCIENCE_KEYWORDS if kw in text_lower)
        if sci_hits >= 4:
            scores[TextType.SCIENCE] += 0.5
            signals.append(f'science_keywords:{sci_hits}')
        elif sci_hits >= 2:
            scores[TextType.SCIENCE] += 0.25
            signals.append(f'science_keywords:{sci_hits}')

        # ── 4. Литература — ключевые слова + паттерны ─────────────────────────
        lit_kw = sum(1 for kw in self._LITERATURE_KEYWORDS if kw in text_lower)
        lit_pat = sum(1 for p in self._LITERATURE_SIGNALS if p.search(text_sample))
        if lit_kw + lit_pat >= 3:
            scores[TextType.LITERATURE] += 0.5
            signals.append(f'literature:{lit_kw}kw+{lit_pat}pat')
        elif lit_kw + lit_pat >= 1:
            scores[TextType.LITERATURE] += 0.2

        # ── 5. Новости ────────────────────────────────────────────────────────
        news_kw = sum(1 for kw in self._NEWS_KEYWORDS if kw in text_lower)
        news_pat = sum(1 for p in self._NEWS_SIGNALS if p.search(text_sample))
        if news_kw + news_pat >= 3:
            scores[TextType.NEWS] += 0.5
            signals.append(f'news:{news_kw}kw+{news_pat}pat')
        elif news_kw + news_pat >= 1:
            scores[TextType.NEWS] += 0.2

        # ── 6. Документ — официальный язык ────────────────────────────────────
        doc_hits = sum(1 for kw in self._DOCUMENT_KEYWORDS if kw in text_lower)
        if doc_hits >= 3:
            scores[TextType.DOCUMENT] += 0.5
            signals.append(f'document_terms:{doc_hits}')
        elif doc_hits >= 1:
            scores[TextType.DOCUMENT] += 0.2

        # ── 7. Разговорный — короткие предложения, вопросы, личные местоимения
        sentences = [s.strip() for s in re.split(r'[.!?]', text_sample) if s.strip()]
        if sentences:
            avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
            question_ratio = sum(1 for s in sentences if '?' in s) / max(len(sentences), 1)
            personal = len(re.findall(r'\b(i|you|we|me|my|your|я|ты|вы|мы|мне|тебе)\b',
                                      text_lower))
            if avg_len < 12 and (question_ratio > 0.2 or personal > 3):
                scores[TextType.CONVERSATION] += 0.35
                signals.append(f'conversation:avg_len={avg_len:.1f},questions={question_ratio:.0%}')

        # ── 8. Определяем язык ────────────────────────────────────────────────
        ru_count = len(self._RU_CHARS.findall(text_sample))
        en_count = len(self._EN_CHARS.findall(text_sample))
        if ru_count > en_count * 2:
            language = 'ru'
        elif en_count > ru_count * 2:
            language = 'en'
        elif scores[TextType.CODE] > 0.3:
            language = 'code'
        else:
            language = 'mixed'

        # ── 9. Нормализуем и выбираем победителя ─────────────────────────────
        # Если ни один тип не набрал очков — по умолчанию WEB
        if max(scores.values()) == 0.0:
            scores[TextType.WEB] = 0.3
            signals.append('default:web')

        best_type = max(scores, key=lambda t: scores[t])
        confidence = min(1.0, scores[best_type])

        return TextClassification(best_type, confidence, language, signals)

    def classify_batch(self, texts: list[str],
                       source_hints: list[str] | None = None) -> list[TextClassification]:
        """Классифицирует список текстов."""
        hints = source_hints or [''] * len(texts)
        return [self.classify(t, h) for t, h in zip(texts, hints)]
