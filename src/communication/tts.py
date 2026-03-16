"""
TTS: синтез речи из текста. Заглушка — реализация через Google TTS или другой бэкенд подключается отдельно.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _synthesize_openai(text: str) -> str:
    """Generate speech with OpenAI TTS and return path to an MP3 file."""
    key = (os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_KEY_API") or "").strip()
    if not key:
        return ""
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
        fd, out = tempfile.mkstemp(prefix="tts_", suffix=".mp3")
        os.close(fd)
        with client.audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=text[:2000],
            format="mp3",
        ) as response:
            response.stream_to_file(out)
        path = Path(out)
        return str(path) if path.exists() and path.stat().st_size > 0 else ""
    except Exception:
        return ""


def _synthesize_gtts(text: str, language_code: str = "ru-RU") -> str:
    """Fallback TTS via gTTS. Requires internet access but no API key."""
    lang = (language_code or "ru-RU").split("-")[0].lower() or "ru"
    try:
        from gtts import gTTS

        fd, out = tempfile.mkstemp(prefix="tts_gtts_", suffix=".mp3")
        os.close(fd)
        gTTS(text=text[:2000], lang=lang).save(out)
        path = Path(out)
        return str(path) if path.exists() and path.stat().st_size > 0 else ""
    except Exception:
        return ""


def synthesize(text: str, language_code: str = "ru-RU") -> str:
    """Синтезировать речь из текста. Возвращает путь к файлу или сообщение об ошибке."""
    if not (text or "").strip():
        return "Error: empty text."
    openai_path = _synthesize_openai(text)
    if openai_path:
        return openai_path
    gtts_path = _synthesize_gtts(text, language_code=language_code)
    if gtts_path:
        return gtts_path
    try:
        from src.communication.google_tts import synthesize as _google_synthesize
        return _google_synthesize(text, language_code=language_code)
    except (ImportError, SyntaxError, AttributeError):
        return "TTS not configured. Add google_tts or another backend to enable synthesis."
