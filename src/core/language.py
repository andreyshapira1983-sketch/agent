"""
Multilingual: detect language of input, respond in same language (hint in system prompt).
"""
# For MVP we rely on LLM to follow "answer in user's language" in system prompt.
# Optional: add langdetect or similar for explicit tag.


def detect_language(text: str) -> str:
    """Placeholder: return 'ru' or 'en' based on simple heuristic."""
    if not text or not text.strip():
        return "en"
    # Simple: if Cyrillic present, assume Russian
    for c in text:
        if "\u0400" <= c <= "\u04FF":
            return "ru"
    return "en"
