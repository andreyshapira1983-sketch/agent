# Secrets Redaction Middleware — централизованная редакция секретов
# Контракт: formal_contracts_spec §5, §6, §10, §12
#
# Все выходные каналы (audit, Telegram, ответы пользователю, лог-файлы)
# ОБЯЗАНЫ пропускать данные через SecretsRedactor перед записью/отправкой.

from __future__ import annotations

import re
import traceback
from typing import Any

# ── Pattern-based detection ────────────────────────────────────────────────
# Regex-паттерны покрывают типичные форматы API-ключей и токенов,
# которые SecuritySystem.scrub_text() не поймает, если значение
# не загружено в _secrets (например, пришло из LLM-ответа).

_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # OpenAI
    re.compile(r'sk-[A-Za-z0-9_-]{20,}'),
    # Anthropic
    re.compile(r'sk-ant-[A-Za-z0-9_-]{20,}'),
    # GitHub PAT
    re.compile(r'gh[ps]_[A-Za-z0-9]{36,}'),
    # GitHub fine-grained
    re.compile(r'github_pat_[A-Za-z0-9_]{20,}'),
    # HuggingFace
    re.compile(r'hf_[A-Za-z0-9]{20,}'),
    # Telegram Bot token
    re.compile(r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b'),
    # Bearer token in text
    re.compile(r'Bearer\s+[A-Za-z0-9_.+/=-]{20,}'),
    # Generic long hex/base64 that look like keys (≥40 hex chars)
    re.compile(r'\b[0-9a-fA-F]{40,}\b'),
    # AWS style
    re.compile(r'AKIA[0-9A-Z]{16}'),
    # Slack
    re.compile(r'xox[baprs]-[A-Za-z0-9-]{10,}'),
]

_PLACEHOLDER = '***'

# Dict ключи, чьи значения всегда маскируются
_SENSITIVE_KEYS = frozenset({
    'password', 'passwd', 'token', 'secret', 'key', 'api_key',
    'apikey', 'access_token', 'refresh_token', 'private_key',
    'authorization', 'credential', 'credentials', 'auth',
    'client_secret', 'session_id', 'cookie',
})


class SecretsRedactor:
    """Центральный редактор секретов для всех выходных каналов.

    Может использоваться как:
        - ``redactor.scrub_text(text)``
        - ``redactor.scrub_dict(data)``
        - ``redactor(text)`` — callable, совместим с ``Monitoring._scrubber``

    Args:
        security: SecuritySystem (опционально) — для value-based scrubbing.
        extra_values: дополнительные значения-секреты (список строк).
    """

    def __init__(self, security=None, extra_values: list[str] | None = None):
        self._security = security
        self._extra: list[str] = [
            v for v in (extra_values or []) if v and len(v) >= 4
        ]

    # ── Callable interface ────────────────────────────────────────────────

    def __call__(self, text: str) -> str:
        return self.scrub_text(text)

    # ── Public API ────────────────────────────────────────────────────────

    def scrub_text(self, text: str) -> str:
        """Заменяет все обнаруженные секреты на ``***``.

        1. Value-based: SecuritySystem.scrub_text (известные значения).
        2. Extra values: переданные при инициализации.
        3. Pattern-based: regex для типичных форматов ключей/токенов.
        """
        if not text:
            return text

        result = text

        # 1. SecuritySystem value-based
        if self._security and hasattr(self._security, 'scrub_text'):
            result = self._security.scrub_text(result)

        # 2. Extra values
        for val in self._extra:
            if val in result:
                result = result.replace(val, _PLACEHOLDER)

        # 3. Pattern-based
        for pattern in _SECRET_PATTERNS:
            result = pattern.sub(_PLACEHOLDER, result)

        return result

    def scrub_dict(self, data: Any) -> Any:
        """Рекурсивно очищает dict/list/str от секретов.

        - Значения dict-ключей из _SENSITIVE_KEYS маскируются.
        - Все строковые значения проходят через scrub_text.
        """
        if isinstance(data, dict):
            return {
                k: (
                    _PLACEHOLDER
                    if isinstance(v, str) and _is_sensitive_key(k)
                    else self.scrub_dict(v)
                )
                for k, v in data.items()
            }
        if isinstance(data, list):
            return [self.scrub_dict(item) for item in data]
        if isinstance(data, str):
            return self.scrub_text(data)
        return data

    def scrub_exception(self, exc: BaseException) -> str:
        """Безопасно форматирует исключение, убирая секреты из traceback."""
        raw = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        return self.scrub_text(raw)

    def add_values(self, values: list[str]):
        """Добавляет новые значения-секреты (runtime)."""
        for v in values:
            if v and len(v) >= 4 and v not in self._extra:
                self._extra.append(v)


def _is_sensitive_key(key: str) -> bool:
    """Проверяет, является ли ключ dict чувствительным."""
    lower = key.lower()
    return any(s in lower for s in _SENSITIVE_KEYS)
