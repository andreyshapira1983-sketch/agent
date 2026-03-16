"""
TTS tool: синтез речи из текста. Регистрирует инструмент для агента.
"""
from __future__ import annotations

from src.tools.base import tool_schema
from src.tools.registry import register


def _synthesize_speech(text: str, language_code: str = "ru-RU") -> str:
    """Синтез речи: текст → аудио. При отсутствии бэкенда возвращает сообщение."""
    if not (text or "").strip():
        return "Error: empty text."
    try:
        from src.communication.tts import synthesize
        return synthesize((text or "").strip(), language_code=language_code)
    except ImportError:
        return "TTS not configured (module tts not implemented)."
    except Exception as e:
        return f"TTS error: {e!s}"[:300]


def register_tts_tool() -> None:
    register(
        "synthesize_speech",
        tool_schema(
            "synthesize_speech",
            "Synthesize speech from text (TTS). Returns path to audio file or error.",
            {
                "text": {"type": "string", "description": "Text to speak"},
                "language_code": {"type": "string", "description": "e.g. ru-RU, en-US (default ru-RU)"},
            },
            required=["text"],
        ),
        lambda text, language_code="ru-RU": _synthesize_speech(text, language_code=language_code),
    )
