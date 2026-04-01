# Speech Synthesizer — подсистема Perception Layer (Слой 1)
# Архитектура автономного AI-агента
# Синтез речи через OpenAI TTS API (tts-1 / tts-1-hd).
# pylint: disable=broad-except,unused-import

from __future__ import annotations

import os
import tempfile


class SpeechSynthesizer:
    """
    Speech Synthesizer — подсистема PerceptionLayer.

    Использует OpenAI TTS API для генерации аудио из текста.

    Выходной формат: opus (.ogg) — нативно поддерживается Telegram sendVoice.

    Голоса: alloy, echo, fable, onyx, nova, shimmer
    Модели: tts-1 (быстрая), tts-1-hd (высокое качество)

    Методы:
        synthesize(text)  — возвращает путь к временному .ogg файлу
        cleanup(path)     — удаляет временный файл
    """

    VOICES = ('alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer')

    def __init__(self, openai_client=None, model: str = 'tts-1',
                 voice: str = 'nova', monitoring=None):
        """
        Args:
            openai_client — экземпляр OpenAIClient (llm/openai_client.py)
            model         — 'tts-1' (быстрая) или 'tts-1-hd' (качество)
            voice         — голос: alloy | echo | fable | onyx | nova | shimmer
        """
        self.client = openai_client
        self.model = model
        self.voice = voice if voice in self.VOICES else 'nova'
        self.monitoring = monitoring

    # ── Основные методы ───────────────────────────────────────────────────────

    def synthesize(self, text: str, voice: str | None = None) -> str | None:
        """
        Синтезирует речь из текста.

        Returns:
            Путь к временному .ogg файлу или None при ошибке.
            Вызывающая сторона обязана удалить файл через cleanup().
        """
        if not self.client:
            self._log('speech_synthesizer: openai_client не подключён',
                      level='warning')
            return None

        if not text or not text.strip():
            return None

        text = text.strip()
        # Telegram ограничивает голосовые — обрезаем слишком длинные тексты
        if len(text) > 4096:
            text = text[:4093] + '...'

        use_voice = voice if (voice and voice in self.VOICES) else self.voice

        self._log(f'TTS: {len(text)} символов, голос={use_voice}')
        try:
            oa = getattr(self.client, '_client', None)
            if oa is None:
                self._log('openai._client недоступен', level='error')
                return None

            # Запрашиваем opus — нативный формат для Telegram voice
            response = oa.audio.speech.create(
                model=self.model,
                voice=use_voice,
                input=text,
                response_format='opus',
            )

            # Сохраняем во временный файл .opus (соответствует response_format)
            fd, tmp_path = tempfile.mkstemp(suffix='.opus')
            with os.fdopen(fd, 'wb') as f:
                f.write(response.content)

            self._log(f'TTS готов: {len(response.content)} bytes -> {tmp_path}')
            return tmp_path

        except Exception as e:
            self._log(f'TTS API error: {e}', level='error')
            return None

    def cleanup(self, path: str):
        """Удаляет временный аудио файл."""
        try:
            if path and os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log(self, message: str, level: str = 'info'):
        if self.monitoring:
            getattr(self.monitoring, level, self.monitoring.info)(
                message, source='speech_synthesizer'
            )
        else:
            print(f'[SpeechSynthesizer] {message}')
