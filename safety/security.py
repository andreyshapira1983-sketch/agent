# Security System (система безопасности) — Слой 16
# Архитектура автономного AI-агента
# Управление секретами, контроль доступа, защита данных, аудит действий.


import os
import hashlib
import hmac
import base64
from enum import Enum


class AccessLevel(Enum):
    READ    = 'read'
    WRITE   = 'write'
    EXECUTE = 'execute'
    ADMIN   = 'admin'


class SecuritySystem:
    """
    Security System — Слой 16.

    Функции:
        - управление секретами (токены, ключи, пароли)
        - контроль доступа (кто что может делать)
        - защита данных (маскировка, хэширование)
        - аудит действий

    Используется:
        - Tool Layer (Слой 5)           — безопасная передача токенов
        - Execution System (Слой 8)     — контроль перед исполнением
        - Governance Layer (Слой 21)    — совместная проверка политик
        - Все слои                      — через audit()
    """

    def __init__(self, load_env: bool = True, audit_log=None):
        """
        Args:
            load_env  — загружать секреты из переменных окружения автоматически
            audit_log — список для журнала безопасности
        """
        self._secrets: dict[str, str] = {}
        self._access_rules: dict[str, set[AccessLevel]] = {}  # субъект → набор прав
        self._audit_log = audit_log if audit_log is not None else []

        if load_env:
            self._load_from_env()

    # ── Секреты ───────────────────────────────────────────────────────────────

    def set_secret(self, name: str, value: str):
        """Сохраняет секрет в памяти (не на диск)."""
        self._secrets[name] = value

    def get_secret(self, name: str, requester: str = 'system') -> str | None:
        """
        Возвращает секрет по имени. Логирует обращение.
        SECURITY (VULN-13): Проверяет доступ запрашивающего.
        LLM-компоненты не получают секреты напрямую.
        """
        # SECURITY: Блокируем доступ для LLM-компонентов
        blocked_requesters = {'llm', 'cognitive_core', 'openai', 'llm_client'}
        if requester.lower() in blocked_requesters:
            self._audit('get_secret_BLOCKED', resource=name, success=False,
                        data={'requester': requester, 'reason': 'LLM access denied'})
            return None

        value = self._secrets.get(name)
        self._audit('get_secret', resource=name, success=value is not None,
                    data={'requester': requester})
        return value

    def delete_secret(self, name: str):
        """Удаляет секрет из памяти."""
        self._secrets.pop(name, None)
        self._audit('delete_secret', resource=name, success=True)

    def has_secret(self, name: str) -> bool:
        return name in self._secrets

    def list_secret_names(self) -> list[str]:
        """Возвращает имена секретов — без значений."""
        return list(self._secrets.keys())

    # Полный список переменных-секретов, которые агент использует (agent.py).
    _KNOWN_SECRET_KEYS = [
        'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
        'TELEGRAM', 'TELEGRAM_ALERTS_CHAT_ID',
        'GITHUB', 'GITHUB_TOKEN', 'HF_TOKEN',
        'EMAIL_USERNAME', 'EMAIL_PASSWORD',
        'GOOGLE_CREDENTIALS_PATH',
        'UPWORK_CLIENT_ID', 'UPWORK_CLIENT_SECRET',
        'UPWORK_ACCESS_TOKEN', 'UPWORK_REFRESH_TOKEN',
        'FIGMA_TOKEN', 'FIGMA_ACCESS_TOKEN',
        'TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN',
        'VONAGE_API_KEY', 'VONAGE_API_SECRET',
        'REDDIT_CLIENT_ID', 'REDDIT_CLIENT_SECRET',
        'REDDIT_USERNAME', 'REDDIT_PASSWORD',
        'WEB_TOKEN',
    ]

    def _load_from_env(self):
        """Загружает известные секреты из переменных окружения (.env / os.environ)."""
        for key in self._KNOWN_SECRET_KEYS:
            value = os.environ.get(key)
            if value:
                self._secrets[key] = value

    # ── Контроль доступа ──────────────────────────────────────────────────────

    def grant(self, subject: str, *levels: AccessLevel):
        """Выдаёт субъекту (агент, пользователь, роль) указанные права."""
        if subject not in self._access_rules:
            self._access_rules[subject] = set()
        for level in levels:
            self._access_rules[subject].add(level)
        self._audit('grant', resource=subject, success=True,
                    data={'levels': [lvl.value for lvl in levels]})

    def revoke(self, subject: str, *levels: AccessLevel):
        """Отзывает права у субъекта."""
        if subject in self._access_rules:
            for level in levels:
                self._access_rules[subject].discard(level)
        self._audit('revoke', resource=subject, success=True,
                    data={'levels': [lvl.value for lvl in levels]})

    def can(self, subject: str, level: AccessLevel) -> bool:
        """Проверяет, есть ли у субъекта данный уровень доступа."""
        rules = self._access_rules.get(subject, set())
        allowed = level in rules or AccessLevel.ADMIN in rules
        self._audit('access_check', resource=subject, success=allowed,
                    data={'level': level.value})
        return allowed

    def require(self, subject: str, level: AccessLevel):
        """Бросает PermissionError если субъект не имеет нужного доступа."""
        if not self.can(subject, level):
            raise PermissionError(
                f"'{subject}' не имеет прав '{level.value}'. "
                f"Текущие права: {[lvl.value for lvl in self._access_rules.get(subject, [])]}"
            )

    # ── Защита данных ─────────────────────────────────────────────────────────

    @staticmethod
    def mask(value: str, show_chars: int = 4) -> str:
        """Маскирует строку, оставляя только последние N символов."""
        if len(value) <= show_chars:
            return '*' * len(value)
        return '*' * (len(value) - show_chars) + value[-show_chars:]

    @staticmethod
    def hash_value(value: str, salt: str = '') -> str:
        """Хэширует строку через SHA-256 с солью."""
        salted = (salt + value).encode('utf-8')
        return hashlib.sha256(salted).hexdigest()

    @staticmethod
    def verify_hash(value: str, hashed: str, salt: str = '') -> bool:
        """Проверяет соответствие строки хэшу."""
        return hmac.compare_digest(
            SecuritySystem.hash_value(value, salt), hashed
        )

    @staticmethod
    def encode_b64(data: bytes) -> str:
        return base64.b64encode(data).decode('utf-8')

    @staticmethod
    def decode_b64(data: str) -> bytes:
        return base64.b64decode(data.encode('utf-8'))

    def sanitize_for_log(self, data: dict) -> dict:
        """Заменяет значения известных секретных полей маской перед логированием."""
        sensitive_keys = {
            'password', 'token', 'secret', 'key', 'api_key',
            'access_token', 'refresh_token', 'private_key',
        }
        result = {}
        for k, v in data.items():
            if any(s in k.lower() for s in sensitive_keys):
                result[k] = self.mask(str(v)) if v else v
            else:
                result[k] = v
        return result

    # ── Scrub: удаление секретов из произвольного текста ──────────────────────

    def scrub_text(self, text: str) -> str:
        """Заменяет все известные значения секретов в тексте на '***'.

        Используется перед записью в логи, long-term memory, reject-memory,
        state-файлы и любые другие персистентные хранилища.
        """
        if not text or not self._secrets:
            return text
        result = text
        for _key, value in self._secrets.items():
            if value and len(value) >= 4 and value in result:
                result = result.replace(value, '***')
        return result

    def contains_secret(self, text: str) -> bool:
        """Возвращает True если текст содержит хотя бы одно значение секрета."""
        if not text or not self._secrets:
            return False
        for _key, value in self._secrets.items():
            if value and len(value) >= 4 and value in text:
                return True
        return False

    # ── Аудит ─────────────────────────────────────────────────────────────────

    def audit(self, action: str, resource: str | None = None, success: bool = True, data: dict | None = None):
        """Публичный метод аудита — вызывается другими слоями."""
        self._audit(action, resource=resource, success=success, data=data)

    def _audit(self, action: str, resource: str | None = None, success: bool = True, data: dict | None = None):
        import time
        entry = {
            'timestamp': time.time(),
            'action': action,
            'resource': resource,
            'success': success,
            'data': data or {},
        }
        if hasattr(self._audit_log, 'append'):
            self._audit_log.append(entry)

    def get_audit_log(self) -> list:
        return list(self._audit_log) if isinstance(self._audit_log, list) else []

    def get_failed_access(self) -> list:
        """Возвращает все неудачные попытки доступа."""
        return [e for e in self.get_audit_log() if not e['success']]
