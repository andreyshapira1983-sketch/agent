# Secure Secrets Proxy — единый шлюз доступа к секретам
# Все компоненты получают секреты ТОЛЬКО через этот прокси.
# LLM-бэкенды и генерируемый код не имеют прямого доступа к os.environ.

from __future__ import annotations

import os
import re

# Переменные окружения, которые безопасно передавать subprocess-ам.
# Всё остальное — секреты, конфигурация LLM и т.д. — отфильтровывается.
_SAFE_ENV_KEYS = frozenset({
    'PATH', 'PATHEXT', 'SYSTEMROOT', 'SYSTEMDRIVE', 'COMSPEC',
    'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'LANG', 'LC_ALL',
    'PYTHONPATH', 'PYTHONDONTWRITEBYTECODE', 'PYTHONUNBUFFERED',
    'VIRTUAL_ENV', 'CONDA_DEFAULT_ENV', 'CONDA_PREFIX',
    'TERM', 'COLORTERM', 'HOMEDRIVE', 'HOMEPATH',
    'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS',
    'OS', 'WINDIR', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMFILES',
    'COMMONPROGRAMFILES',
})

# Паттерн для «очевидно секретных» имён переменных
_SECRET_NAME_RE = re.compile(
    r'(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|SID)',
    re.IGNORECASE,
)


def safe_env() -> dict[str, str]:
    """Возвращает копию os.environ, очищенную от всех секретов.

    Используется как ``env=`` аргумент для subprocess.run / Popen
    чтобы порождённые процессы не наследовали API-ключи, токены и пароли.
    """
    result: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in _SAFE_ENV_KEYS:
            result[key] = value
        elif not _SECRET_NAME_RE.search(upper):
            # Переменная не в whitelist, но и не выглядит как секрет —
            # пропускаем для совместимости (BLENDER_PATH, OPENSCAD_PATH…).
            # ОДНАКО если имя содержит KEY/TOKEN/SECRET/PASSWORD — блокируем.
            result[key] = value
    return result


class SecretsProxy:
    """Единый шлюз для получения секретов.

    Секреты загружаются из os.environ один раз при инициализации
    и хранятся в памяти. Компоненты запрашивают их по имени через
    ``get(name, requester)``.

    LLM-компоненты блокируются по ``requester``.
    Все обращения логируются.
    """

    _BLOCKED_REQUESTERS = frozenset({
        'llm', 'cognitive_core', 'openai_client', 'claude_backend',
        'github_backend', 'huggingface_backend', 'local_backend',
        'search_backend', 'sandbox', 'generated_code', 'module_builder_code',
    })

    def __init__(self, security_system=None):
        self._security = security_system
        self._store: dict[str, str] = {}
        self._access_log: list[dict] = []

    def load_from_env(self, keys: list[str]):
        """Загружает указанные переменные из os.environ в внутреннее хранилище."""
        for key in keys:
            val = os.environ.get(key)
            if val:
                self._store[key] = val

    def set(self, name: str, value: str):
        """Устанавливает секрет программно."""
        self._store[name] = value

    def get(self, name: str, requester: str = 'system') -> str | None:
        """Возвращает секрет если requester имеет доступ; иначе None."""
        import time
        entry = {
            'ts': time.time(),
            'name': name,
            'requester': requester,
            'granted': False,
        }

        if requester.lower() in self._BLOCKED_REQUESTERS:
            entry['reason'] = 'blocked_requester'
            self._access_log.append(entry)
            if self._security:
                self._security.audit(
                    'secrets_proxy_BLOCKED', resource=name, success=False,
                    data={'requester': requester},
                )
            return None

        value = self._store.get(name)
        entry['granted'] = value is not None
        self._access_log.append(entry)
        return value

    def has(self, name: str) -> bool:
        return name in self._store

    def names(self) -> list[str]:
        """Возвращает имена секретов (без значений)."""
        return list(self._store.keys())

    def all_values(self) -> list[str]:
        """Возвращает все значения секретов (для scrub_text)."""
        return [v for v in self._store.values() if v and len(v) >= 4]

    def get_access_log(self) -> list[dict]:
        return list(self._access_log)
