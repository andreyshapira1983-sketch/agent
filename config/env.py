"""
Load .env and expose OPENAI_API_KEY, TELEGRAM token.
Plan: map OPEN_KEY_API -> OPENAI_API_KEY for LLM, TTS, Whisper.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load from agent root
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

# OPEN_KEY_API in .env is mapped to OPENAI_API_KEY for use in code
_open_key = os.getenv("OPEN_KEY_API") or os.getenv("OPENAI_API_KEY")
OPENAI_API_KEY: str = _open_key or ""
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM", "")

# Optional: parse TELEGRAM as "id:token" if needed
def get_telegram_id_and_token() -> tuple[str, str]:
    raw = TELEGRAM_TOKEN.strip()
    if ":" in raw:
        part_id, part_token = raw.split(":", 1)
        return part_id.strip(), part_token.strip()
    return "", raw
