# Speech Recognizer — подсистема Perception Layer (Слой 1)
# Архитектура автономного AI-агента
# Распознавание речи через OpenAI Whisper API.
# pylint: disable=broad-except

from __future__ import annotations

import os
import time
from typing import Any, cast


class TranscriptionResult:
    """Результат транскрипции аудио."""

    def __init__(self, path: str, text: str, language: str | None = None,
                 duration: float | None = None, confidence: float = 1.0):
        self.path = path
        self.text = text
        self.language = language or 'unknown'
        self.duration = duration
        self.confidence = confidence
        self.transcribed_at = time.time()

    def to_dict(self) -> dict:
        return {
            'path': self.path,
            'text': self.text[:500],
            'language': self.language,
            'duration': self.duration,
            'confidence': self.confidence,
        }


class SpeechRecognizer:
    """
    Speech Recognizer — подсистема PerceptionLayer.

    Использует OpenAI Whisper API для транскрипции.

    Поддерживаемые форматы: mp3, mp4, mpeg, mpga, m4a, wav, webm, ogg, flac
    Максимальный размер файла: 25 MB (лимит Whisper API)

    Методы:
        transcribe(path)              — транскрибирует аудио/видео файл
        transcribe_and_translate(path) — транскрибирует И переводит на английский
        detect_language(path)          — определяет язык речи

    Fallback: при отсутствии openai_client возвращает пустой результат.
    """

    SUPPORTED_FORMATS = {
        '.mp3', '.mp4', '.mpeg', '.mpga', '.m4a',
        '.wav', '.webm', '.ogg', '.oga', '.flac',
    }
    MAX_FILE_MB = 25

    def __init__(self, openai_client=None, model: str = 'whisper-1',
                 monitoring=None):
        """
        Args:
            openai_client — экземпляр OpenAIClient (llm/openai_client.py)
            model         — 'whisper-1' (единственная модель Whisper в API)
        """
        self.client = openai_client
        self.model = model
        self.monitoring = monitoring

    # ── Основные методы ───────────────────────────────────────────────────────

    def transcribe(self, path: str, language: str | None = None,
                   prompt: str | None = None) -> TranscriptionResult:
        """
        Транскрибирует аудио/видео файл.

        Args:
            path     — путь к аудио файлу
            language — подсказка языка в ISO 639-1 ('ru', 'en', ...)
                       Если None — автоматически определяется Whisper
            prompt   — подсказка о содержании (повышает точность)

        Returns:
            TranscriptionResult с текстом и метаданными
        """
        check = self._preflight(path)
        if check:
            return check

        self._log(f"Транскрипция: {os.path.basename(path)}")
        try:
            # Переиспользуем уже созданный openai.OpenAI из OpenAIClient
            oa = getattr(self.client, '_client', None)
            if oa is None:
                # SECURITY: не читаем os.environ напрямую
                import openai as _openai
                if self.client is not None and hasattr(self.client, 'api_key'):
                    oa = _openai.OpenAI(api_key=self.client.api_key)
                else:
                    return TranscriptionResult(path=path, text='', confidence=0.0)

            kwargs: dict[str, Any] = {'model': self.model, 'response_format': 'verbose_json'}
            if language:
                kwargs['language'] = language
            if prompt:
                kwargs['prompt'] = prompt

            with open(path, 'rb') as f:
                response = cast(Any, oa.audio.transcriptions.create)(file=f, **kwargs)

            text = response.text or ''
            lang = getattr(response, 'language', None) or language or 'unknown'
            duration = getattr(response, 'duration', None)

            result = TranscriptionResult(
                path=path,
                text=text,
                language=lang,
                duration=duration,
                confidence=0.95,
            )
            self._log(f"Транскрипция готова: {len(text)} символов, язык: {lang}")
            return result

        except Exception as e:
            self._log(f'Whisper API error: {e}', level='error')
            return TranscriptionResult(path=path, text='', confidence=0.0)

    def transcribe_and_translate(self, path: str) -> TranscriptionResult:
        """
        Транскрибирует и переводит на английский язык за один запрос.
        Использует Whisper translations endpoint.
        """
        check = self._preflight(path)
        if check:
            return check

        self._log(f"Транскрипция+перевод: {os.path.basename(path)}")
        try:
            oa = getattr(self.client, '_client', None)
            if oa is None:
                # SECURITY: не читаем os.environ напрямую
                import openai as _openai
                if self.client is not None and hasattr(self.client, 'api_key'):
                    oa = _openai.OpenAI(api_key=self.client.api_key)
                else:
                    return TranscriptionResult(path=path, text='', confidence=0.0)

            with open(path, 'rb') as f:
                response = oa.audio.translations.create(
                    file=f,
                    model=self.model,
                    response_format='verbose_json',
                )

            text = response.text or ''
            return TranscriptionResult(
                path=path, text=text, language='en',
                duration=getattr(response, 'duration', None),
                confidence=0.9,
            )
        except Exception as e:
            self._log(f'Whisper translation error: {e}', level='error')
            return TranscriptionResult(path=path, text='', confidence=0.0)

    def detect_language(self, path: str) -> str:
        """
        Определяет язык речи в аудио файле.
        Транскрибирует первые 30 секунд и возвращает код языка.
        """
        result = self.transcribe(path)
        return result.language

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _preflight(self, path: str) -> TranscriptionResult | None:
        """Проверка перед транскрипцией. Возвращает ошибку или None."""
        if not self.client:
            self._log('speech_recognizer: openai_client не подключён', level='warning')
            return TranscriptionResult(
                path=path,
                text='[Speech recognition недоступен: OpenAI client не подключён]',
                confidence=0.0,
            )

        if not os.path.exists(path):
            self._log(f'Файл не найден: {path}', level='error')
            return TranscriptionResult(path=path, text='', confidence=0.0)

        ext = os.path.splitext(path)[1].lower()
        if ext not in self.SUPPORTED_FORMATS:
            self._log(f'Неподдерживаемый формат: {ext}', level='warning')
            return TranscriptionResult(path=path, text='', confidence=0.0)

        size_mb = os.path.getsize(path) / (1024 * 1024)
        if size_mb > self.MAX_FILE_MB:
            self._log(f'Файл слишком большой: {size_mb:.1f}MB > {self.MAX_FILE_MB}MB',
                      level='warning')
            return TranscriptionResult(path=path, text='', confidence=0.0)

        return None

    def _log(self, message: str, level: str = 'info'):
        if self.monitoring:
            getattr(self.monitoring, level, self.monitoring.info)(
                message, source='speech_recognizer'
            )
        else:
            print(f'[SpeechRecognizer] {message}')
