# Multilingual Understanding — Слой 14
# Архитектура автономного AI-агента
# Перевод, определение языка, адаптация контента.

from __future__ import annotations

import re
import time


# Стандартные языки (ISO 639-1 → название)
LANGUAGES = {
    'ru': 'русский',
    'en': 'английский',
    'zh': 'китайский',
    'de': 'немецкий',
    'fr': 'французский',
    'es': 'испанский',
    'it': 'итальянский',
    'pt': 'португальский',
    'ja': 'японский',
    'ko': 'корейский',
    'ar': 'арабский',
    'nl': 'нидерландский',
    'pl': 'польский',
    'tr': 'турецкий',
    'uk': 'украинский',
}


class TranslationResult:
    """Результат перевода."""

    def __init__(self, original: str, translated: str,
                 source_lang: str, target_lang: str, confidence: float = 1.0):
        self.original = original
        self.translated = translated
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.confidence = confidence
        self.translated_at = time.time()

    def to_dict(self) -> dict:
        return {
            'original': self.original[:200],
            'translated': self.translated[:200],
            'source_lang': self.source_lang,
            'target_lang': self.target_lang,
            'confidence': self.confidence,
        }


class MultilingualSystem:
    """
    Multilingual Understanding — Слой 14.

    Функции:
        - определение языка текста (auto-detect)
        - перевод между языками через LLM (без внешних API)
        - нормализация терминологии
        - адаптация промптов и ответов под язык пользователя
        - определение языка входящих сообщений для SocialModel (Слой 43)

    Поддерживаемые языки: ru, en, zh, de, fr, es, it, pt, ja, ko, ar, ...

    Используется:
        - Social Interaction Model (Слой 43) — адаптация под язык пользователя
        - Perception Layer (Слой 1)          — обработка мультиязычных документов
        - Cognitive Core (Слой 3)            — формирование промптов
    """

    def __init__(self, cognitive_core=None, monitoring=None):
        self.cognitive_core = cognitive_core
        self.monitoring = monitoring

        # Кэш переводов: (text_hash, target_lang) -> TranslationResult
        self._cache: dict[str, TranslationResult] = {}
        self._default_lang = 'ru'

    # ── Определение языка ─────────────────────────────────────────────────────

    def detect_language(self, text: str) -> str:
        """
        Определяет язык текста.
        Сначала эвристически (кириллица, CJK, диакритика),
        затем через LLM если неоднозначно.

        Returns:
            ISO 639-1 код языка ('ru', 'en', 'zh', ...)
        """
        if not text or not text.strip():
            return self._default_lang

        # Быстрые эвристики
        lang = self._heuristic_detect(text)
        if lang:
            return lang

        # LLM для неоднозначных случаев
        if self.cognitive_core:
            try:
                raw = self.cognitive_core.reasoning(
                    f"Определи язык следующего текста. "
                    f"Ответь только двухбуквенным кодом ISO 639-1 (например: en, ru, zh).\n\n"
                    f"Текст: {text[:300]}"
                )
                code = str(raw).strip().lower()[:5]
                m = re.search(r'\b([a-z]{2})\b', code)
                if m and m.group(1) in LANGUAGES:
                    return m.group(1)
            except Exception:  # pylint: disable=broad-except
                pass

        return 'en'  # fallback

    def _heuristic_detect(self, text: str) -> str | None:
        """Быстрое определение по символам юникода."""
        sample = text[:200]
        counts: dict[str, int] = {}

        for ch in sample:
            cp = ord(ch)
            if 0x0400 <= cp <= 0x04FF:   # кириллица
                counts['cyrillic'] = counts.get('cyrillic', 0) + 1
            elif 0x4E00 <= cp <= 0x9FFF:  # CJK (китайский)
                counts['cjk'] = counts.get('cjk', 0) + 1
            elif 0x3040 <= cp <= 0x30FF:  # хирагана/катакана (японский)
                counts['kana'] = counts.get('kana', 0) + 1
            elif 0xAC00 <= cp <= 0xD7AF:  # хангыль (корейский)
                counts['hangul'] = counts.get('hangul', 0) + 1
            elif 0x0600 <= cp <= 0x06FF:  # арабский
                counts['arabic'] = counts.get('arabic', 0) + 1

        total = len([c for c in sample if not c.isspace()])
        if total == 0:
            return None

        def ratio(key):
            return counts.get(key, 0) / total

        if ratio('cyrillic') > 0.3:
            # Различаем русский и украинский по характерным буквам
            uk_chars = set('іїєґ')
            if any(c in uk_chars for c in sample.lower()):
                return 'uk'
            return 'ru'
        if ratio('cjk') > 0.2:
            return 'zh'
        if ratio('kana') > 0.1:
            return 'ja'
        if ratio('hangul') > 0.2:
            return 'ko'
        if ratio('arabic') > 0.2:
            return 'ar'

        return None   # не определено эвристикой

    # ── Перевод ───────────────────────────────────────────────────────────────

    def translate(
        self,
        text: str,
        target_lang: str = 'en',
        source_lang: str | None = None,
    ) -> TranslationResult:
        """
        Переводит текст на целевой язык через LLM.

        Args:
            text        — исходный текст
            target_lang — ISO код языка назначения ('en', 'ru', ...)
            source_lang — ISO код исходного языка (авто-определение если None)
        """
        if not source_lang:
            source_lang = self.detect_language(text)

        # Если язык совпадает — вернуть как есть
        if source_lang == target_lang:
            return TranslationResult(text, text, source_lang, target_lang, 1.0)

        # Кэш
        cache_key = f"{hash(text[:500])}__{target_lang}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        if not self.cognitive_core:
            result = TranslationResult(text, text, source_lang, target_lang, 0.0)
            return result

        target_name = LANGUAGES.get(target_lang, target_lang)
        source_name = LANGUAGES.get(source_lang, source_lang)

        try:
            raw = self.cognitive_core.reasoning(
                f"Переведи следующий текст с {source_name} на {target_name}.\n"
                f"Верни ТОЛЬКО перевод, без пояснений и заголовков.\n\n"
                f"Текст:\n{text[:2000]}"
            )
            translated = str(raw).strip()
            result = TranslationResult(
                original=text,
                translated=translated,
                source_lang=source_lang,
                target_lang=target_lang,
                confidence=0.9,
            )
        except Exception as e:  # pylint: disable=broad-except
            self._log(f"Ошибка перевода: {e}", level='error')
            result = TranslationResult(text, text, source_lang, target_lang, 0.0)

        self._cache[cache_key] = result
        self._log(
            f"Перевод '{source_lang}' -> '{target_lang}': "
            f"{len(text)} символов"
        )
        return result

    def translate_batch(
        self,
        texts: list[str],
        target_lang: str = 'en',
        source_lang: str | None = None,
    ) -> list[TranslationResult]:
        """Переводит список текстов."""
        return [self.translate(t, target_lang, source_lang) for t in texts]

    # ── Адаптация контента ────────────────────────────────────────────────────

    def adapt_for_user(self, text: str, user_lang: str) -> str:
        """
        Адаптирует ответ агента под язык пользователя.
        Если текст уже на нужном языке — возвращает без изменений.
        """
        current_lang = self.detect_language(text)
        if current_lang == user_lang:
            return text
        result = self.translate(text, target_lang=user_lang, source_lang=current_lang)
        return result.translated

    def normalize_terminology(self, text: str, domain: str, lang: str = 'en') -> str:
        """
        Нормализует терминологию текста в заданной предметной области.
        Например: приводит синонимы к единому термину.
        """
        if not self.cognitive_core:
            return text

        try:
            raw = self.cognitive_core.reasoning(
                f"Нормализуй терминологию в следующем тексте для области '{domain}' "
                f"на языке '{LANGUAGES.get(lang, lang)}'.\n"
                f"Замени устаревшие или неточные термины на стандартные.\n"
                f"Верни только исправленный текст.\n\n"
                f"Текст: {text[:1000]}"
            )
            return str(raw).strip()
        except Exception:  # pylint: disable=broad-except
            return text

    def summarize_multilingual(self, text: str, target_lang: str = 'ru',
                                max_sentences: int = 3) -> str:
        """Создаёт краткое резюме текста на целевом языке."""
        if not self.cognitive_core:
            return text[:300]

        target_name = LANGUAGES.get(target_lang, target_lang)

        try:
            raw = self.cognitive_core.reasoning(
                f"Создай краткое резюме следующего текста на {target_name} "
                f"в {max_sentences} предложениях.\n\n"
                f"Текст: {text[:3000]}"
            )
            return str(raw).strip()
        except Exception:  # pylint: disable=broad-except
            return text[:300]

    # ── Утилиты ───────────────────────────────────────────────────────────────

    def supported_languages(self) -> dict[str, str]:
        """Возвращает словарь поддерживаемых языков."""
        return dict(LANGUAGES)

    def cache_size(self) -> int:
        return len(self._cache)

    def clear_cache(self):
        self._cache.clear()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log(self, message: str, level: str = 'info'):
        if self.monitoring:
            getattr(self.monitoring, level, self.monitoring.info)(
                message, source='multilingual'
            )
        else:
            print(f'[Multilingual] {message}')
