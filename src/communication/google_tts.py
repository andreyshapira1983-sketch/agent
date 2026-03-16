"""
Google Cloud TTS. Заглушка — задай GOOGLE_APPLICATION_CREDENTIALS и установи google-cloud-texttospeech для работы.
"""
from __future__ import annotations


def synthesize(text: str, language_code: str = "ru-RU") -> str:
    """Синтез через Google Text-to-Speech. Возвращает путь к файлу или ошибку."""
    if not (text or "").strip():
        return "Error: empty text."
    try:
        from google.cloud import texttospeech
    except ImportError:
        return "Install google-cloud-texttospeech and set GOOGLE_APPLICATION_CREDENTIALS for TTS."
    # TODO: создать клиент, вызвать synthesize_speech, сохранить в файл, вернуть путь
    return "Google TTS stub: not implemented. Configure credentials and implement synthesize_speech call."
