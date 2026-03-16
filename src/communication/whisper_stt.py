"""
Speech-to-text через OpenAI Whisper. Используется для голосовых сообщений в Telegram.
"""
from __future__ import annotations

import os
from pathlib import Path


def transcribe_audio(file_path: str | Path) -> str:
    """
    Распознать речь в аудиофайле (OGG, MP3, WAV и др.). Возвращает текст или сообщение об ошибке.
    Требуется OPENAI_API_KEY (или OPEN_KEY_API) в окружении.
    """
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return ""
    try:
        from openai import OpenAI
        key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_KEY_API", "")
        if not key:
            return "(Whisper: OPENAI_API_KEY не задан)"
        client = OpenAI(api_key=key)
        with path.open("rb") as f:
            r = client.audio.transcriptions.create(model="whisper-1", file=f)
        return (r.text or "").strip()
    except Exception as e:
        return f"(Ошибка распознавания: {e!s})"[:200]
