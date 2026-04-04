# Security Hardening — централизованные примитивы безопасности
# Содержит:
#   1. ContentSanitizer   — строгая санитизация LLM-вывода перед исполнением
#   2. NetworkGuard       — единая SSRF/allowlist-проверка URL и IP
#   3. InstructionClassifier — классификация + deny опасных инструкций
#   4. RateLimiter        — hard limits: actions/cycle, tools/min, kill switch, immutable audit

from __future__ import annotations

import ast
import hashlib
import ipaddress
import json
import os
import re
import socket
import time
from urllib.parse import urlparse


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ContentSanitizer — strict content sanitization + sandboxed parsing
# ═══════════════════════════════════════════════════════════════════════════════

class ContentSanitizer:
    """Строгая санитизация LLM-вывода ПЕРЕД исполнением.

    В отличие от LLMResponseSanitizer (который sanitizes текст ответа),
    ContentSanitizer проверяет *извлечённые блоки кода/команд* непосредственно
    перед их передачей в инструменты.

    Принцип: trust score ≠ execution permission.
    Даже «доверенный» LLM-вывод проходит все статические проверки.
    """

    # Опасные конструкции в Python-коде — AST-based (не обходятся обфускацией)
    _DANGEROUS_AST_CALLS = frozenset({
        'eval', 'exec', 'compile', 'execfile',
        '__import__',
        'globals', 'locals', 'vars',
        # open — разрешён: PythonRuntimeTool подменяет open() на _safe_open (jail)
        # getattr/setattr/delattr — проверяются отдельно: блокируются только
        # при обращении к опасным атрибутам из _DANGEROUS_AST_ATTRS
    })

    # Функции доступа к атрибутам — блокируем только если 2-й аргумент опасный
    _ATTR_ACCESS_CALLS = frozenset({'getattr', 'setattr', 'delattr'})

    _DANGEROUS_AST_ATTRS = frozenset({
        '__class__', '__bases__', '__subclasses__', '__mro__',
        '__builtins__', '__globals__', '__code__', '__closure__',
    })

    # Опасные паттерны в bash/shell — regex (ловит обфускацию)
    _DANGEROUS_BASH_PATTERNS = [
        re.compile(r'\brm\s+(-[rfRF]+\s+)?/', re.IGNORECASE),
        re.compile(r'\b(sudo|su\s+|runas)\b', re.IGNORECASE),
        re.compile(r'\b(curl|wget|nc|ncat|netcat)\s', re.IGNORECASE),
        re.compile(r'\b(ssh|scp|rsync|ftp|sftp|telnet)\s', re.IGNORECASE),
        re.compile(r'>\s*/dev/sd|>\s*/dev/null.*2>&1.*\|', re.IGNORECASE),
        re.compile(r':\(\)\s*\{.*\|.*&\s*\}', re.IGNORECASE),  # fork bomb
        re.compile(r'\bdd\s+if=', re.IGNORECASE),
        re.compile(r'\b(mkfs|format)\b', re.IGNORECASE),
        re.compile(r'\b(shutdown|reboot|halt|poweroff|init\s+0)\b', re.IGNORECASE),
        re.compile(r'\bchmod\s+(777|\+s)\b', re.IGNORECASE),
        re.compile(r'(?:;|\||&&|`|\$\()', re.IGNORECASE),  # command chaining
        re.compile(r'\bbase64\s+-d\b|\bpython[23]?\s+-c\b', re.IGNORECASE),  # encoded exec
        re.compile(r"\$'[^']*\\x[0-9a-fA-F]{2}", re.IGNORECASE),  # $'\xHH' hex-encoded args
        re.compile(r'\\x[0-9a-fA-F]{2}', re.IGNORECASE),  # raw hex escape sequences
    ]

    @classmethod
    def validate_python(cls, code: str) -> tuple[bool, str]:
        """AST-based валидация Python-кода. Не regex — не обходится обфускацией.

        Returns: (ok, reason)
        """
        if not code or not code.strip():
            return False, 'пустой код'

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f'SyntaxError: {e}'

        for node in ast.walk(tree):
            # Проверяем вызовы опасных функций
            if isinstance(node, ast.Call):
                func_name = cls._get_call_name(node)
                if func_name in cls._DANGEROUS_AST_CALLS:
                    return False, f'запрещённый вызов: {func_name}()'

                # getattr/setattr/delattr — блокируем только если 2-й аргумент
                # строковый литерал из _DANGEROUS_AST_ATTRS
                if func_name in cls._ATTR_ACCESS_CALLS:
                    attr_arg = cls._get_attr_arg(node)
                    if attr_arg is not None and attr_arg in cls._DANGEROUS_AST_ATTRS:
                        return False, f'запрещённый вызов: {func_name}(…, "{attr_arg}")' 

            # Проверяем доступ к опасным атрибутам
            if isinstance(node, ast.Attribute):
                if node.attr in cls._DANGEROUS_AST_ATTRS:
                    return False, f'запрещённый атрибут: .{node.attr}'

            # Проверяем импорт опасных модулей
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, 'names', []):
                    mod = alias.name.split('.')[0] if alias.name else ''
                    if mod in {'subprocess', 'shutil', 'ctypes', 'signal', 'multiprocessing'}:
                        return False, f'запрещённый import: {mod}'
                if isinstance(node, ast.ImportFrom) and node.module:
                    root_mod = node.module.split('.')[0]
                    if root_mod in {'subprocess', 'shutil', 'ctypes', 'signal', 'multiprocessing'}:
                        return False, f'запрещённый import from: {root_mod}'

        return True, 'OK'

    @classmethod
    def validate_bash(cls, command: str) -> tuple[bool, str]:
        """Regex-based валидация bash/shell команды.

        Returns: (ok, reason)
        """
        if not command or not command.strip():
            return False, 'пустая команда'

        if len(command) > 500:
            return False, f'команда слишком длинная: {len(command)} символов (макс 500)'

        for pattern in cls._DANGEROUS_BASH_PATTERNS:
            m = pattern.search(command)
            if m:
                return False, f'опасный паттерн: {m.group()}'

        return True, 'OK'

    @staticmethod
    def _get_call_name(node: ast.Call) -> str:
        """Извлекает имя вызываемой функции из AST-узла."""
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return ''

    @staticmethod
    def _get_attr_arg(node: ast.Call) -> str | None:
        """Извлекает 2-й аргумент (имя атрибута) из getattr/setattr/delattr.

        Возвращает строку если аргумент — строковый литерал, иначе None.
        """
        # getattr(obj, 'name') / setattr(obj, 'name', val) — 2-й позиционный
        if len(node.args) >= 2:
            arg = node.args[1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 2. NetworkGuard — единая SSRF-защита + allowlist доменов
# ═══════════════════════════════════════════════════════════════════════════════

class NetworkGuard:
    """Единая точка валидации всех исходящих HTTP/сетевых запросов.

    Блокирует:
        - localhost, 127.0.0.0/8, ::1
        - link-local 169.254.0.0/16, fe80::/10
        - все приватные диапазоны (10.x, 172.16-31.x, 192.168.x)
        - multicast, reserved
        - non-HTTP(S) schemes
        - DNS rebinding (resolve → check → pin)

    Опциональный allowlist доменов: если задан, разрешены ТОЛЬКО они.
    """

    # Дефолтный allowlist — только публичные API и сервисы
    DEFAULT_DOMAIN_ALLOWLIST = frozenset({
        # Поисковые / AI API
        'api.openai.com', 'api.anthropic.com', 'api-inference.huggingface.co',
        'huggingface.co', 'api.github.com', 'raw.githubusercontent.com',
        # Поисковые движки (DuckDuckGo)
        'html.duckduckgo.com', 'duckduckgo.com', 'lite.duckduckgo.com',
        'api.duckduckgo.com',
        # Знания
        'en.wikipedia.org', 'ru.wikipedia.org',
        'arxiv.org', 'export.arxiv.org',
        'pypi.org', 'files.pythonhosted.org',
        'www.gutenberg.org', 'gutenberg.org',
        # Telegram API
        'api.telegram.org',
        # Google APIs (Calendar)
        'oauth2.googleapis.com', 'www.googleapis.com',
        # Upwork
        'www.upwork.com',
        # Figma
        'api.figma.com',
        # Reddit
        'www.reddit.com', 'oauth.reddit.com',
        # Twilio / Vonage
        'api.twilio.com', 'rest.nexmo.com',
    })

    def __init__(
        self,
        domain_allowlist: frozenset[str] | None = None,
        strict_allowlist: bool = False,
        dns_timeout: float = 5.0,
    ):
        """
        Args:
            domain_allowlist: если задан, заменяет DEFAULT_DOMAIN_ALLOWLIST
            strict_allowlist: если True, разрешены ТОЛЬКО домены из allowlist
                              если False, домены вне allowlist проходят IP-проверку
            dns_timeout: таймаут DNS-резолва
        """
        self._allowlist = domain_allowlist or self.DEFAULT_DOMAIN_ALLOWLIST
        self._strict = strict_allowlist
        self._dns_timeout = dns_timeout
        self._dns_cache: dict[str, list[str]] = {}

    def is_allowed(self, url: str) -> tuple[bool, str]:
        """Проверяет URL на безопасность.

        Returns: (allowed, reason)
        """
        try:
            parsed = urlparse(url)
        except Exception:
            return False, 'невалидный URL'

        # Только HTTP(S)
        if parsed.scheme not in ('http', 'https'):
            return False, f'запрещённая схема: {parsed.scheme}'

        host = (parsed.hostname or '').strip().lower()
        if not host:
            return False, 'пустой hostname'

        # Блокируем localhost-варианты
        if host in {'localhost', '127.0.0.1', '::1', '0.0.0.0'} or host.endswith('.local'):
            return False, f'заблокирован localhost/local: {host}'

        # Проверяем прямой IP
        try:
            ip = ipaddress.ip_address(host)
            ok, reason = self._check_ip(ip)
            if not ok:
                return False, reason
            # IP-адрес прошёл проверку, но if strict — блокировать IP-based URL
            if self._strict:
                return False, f'strict mode: прямой IP {host} не в allowlist'
            return True, 'IP OK'
        except ValueError:
            pass  # не IP, а hostname — продолжаем

        # Проверяем allowlist
        if host in self._allowlist:
            # Доверенный домен, но всё равно проверяем DNS (anti-rebinding)
            ips = self._resolve(host, parsed.port)
            for ip_str in ips:
                ok, reason = self._check_ip(ipaddress.ip_address(ip_str))
                if not ok:
                    return False, f'DNS rebinding: {host} → {ip_str} ({reason})'
            return True, f'allowlist: {host}'

        # Домен не в allowlist
        if self._strict:
            return False, f'strict allowlist: {host} не разрешён'

        # non-strict: проверяем IP всех DNS-записей
        ips = self._resolve(host, parsed.port)
        if not ips:
            return False, f'DNS не резолвится: {host}'

        for ip_str in ips:
            ok, reason = self._check_ip(ipaddress.ip_address(ip_str))
            if not ok:
                return False, f'{host} → {ip_str}: {reason}'

        return True, f'DNS OK: {host}'

    def _check_ip(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> tuple[bool, str]:
        """Проверяет IP на опасные диапазоны."""
        if ip.is_loopback:
            return False, f'loopback: {ip}'
        if ip.is_private:
            return False, f'private: {ip}'
        if ip.is_link_local:
            return False, f'link-local (169.254.x.x): {ip}'
        if ip.is_multicast:
            return False, f'multicast: {ip}'
        if ip.is_reserved:
            return False, f'reserved: {ip}'

        # Дополнительно: metadata endpoint AWS/GCP/Azure
        metadata_ranges = [
            ipaddress.ip_network('169.254.169.254/32'),
            ipaddress.ip_network('100.100.100.200/32'),  # Alibaba
        ]
        for net in metadata_ranges:
            if ip in net:
                return False, f'cloud metadata endpoint: {ip}'

        return True, 'OK'

    def _resolve(self, host: str, port: int | None = None) -> list[str]:
        """DNS resolve с кэшем и таймаутом (thread-safe, без глобального setdefaulttimeout)."""
        cache_key = f'{host}:{port or 80}'
        if cache_key in self._dns_cache:
            return self._dns_cache[cache_key]

        try:
            infos = socket.getaddrinfo(
                host, port or 80, proto=socket.IPPROTO_TCP,
            )
            ips: list[str] = [str(info[4][0]) for info in infos]
            ips = list(set(ips))
            self._dns_cache[cache_key] = ips
            return ips
        except (socket.gaierror, OSError):
            return []

    def clear_dns_cache(self):
        self._dns_cache.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. InstructionClassifier — classify + deny dangerous instructions
# ═══════════════════════════════════════════════════════════════════════════════

class InstructionClassifier:
    """Классификация инструкций по типу и опасности.

    Работает БЕЗ LLM — чисто детерминированная keyword/regex классификация.
    """

    class Intent:
        SAFE       = 'safe'
        READ_ONLY  = 'read_only'
        WRITE      = 'write'
        EXECUTE    = 'execute'
        NETWORK    = 'network'
        DANGEROUS  = 'dangerous'
        BLOCKED    = 'blocked'

    # Паттерны классификации (порядок важен — первый match побеждает)
    _BLOCKED_PATTERNS = [
        (re.compile(r'(?:удали|уничтожь|сотри|стереть)\s+(?:все|всё|систем)', re.I), 'destructive'),
        (re.compile(r'(?:отключи|выключи|disable)\s+(?:безопасность|safe|security|governance|monitoring)', re.I), 'bypass_safety'),
        (re.compile(r'(?:обойди|bypass|skip)\s+(?:проверк|approval|human|валидац)', re.I), 'bypass_approval'),
        (re.compile(r'(?:игнорируй|ignore)\s+(?:предыдущ|previous|все правила|all rules)', re.I), 'injection'),
        (re.compile(r'(?:ты теперь|you are now|новая роль|new role)', re.I), 'role_hijack'),
        (re.compile(r'(?:самоуничтож|self.destruct|format\s+c:|rm\s+-rf\s+/)', re.I), 'destructive'),
        (re.compile(r'(?:отправь|send|forward)\s+(?:все|all)\s+(?:данные|data|секрет|secret|ключ|key)', re.I), 'data_exfil'),
        (re.compile(r'(?:скачай|download|fetch)\s+(?:и\s+)?(?:запусти|execute|run)', re.I), 'remote_exec'),
    ]

    _DANGEROUS_PATTERNS = [
        (re.compile(r'\b(?:exec|eval|compile|__import__|subprocess)\b', re.I), 'code_injection'),
        (re.compile(r'\b(?:rm|del|rmdir|format|mkfs|dd)\b', re.I), 'destructive_cmd'),
        (re.compile(r'\b(?:sudo|su\s|runas|elevat)', re.I), 'privilege_escalation'),
        (re.compile(r'\b(?:passwd|password|credentials?)\b.*(?:change|set|reset|modify)', re.I), 'credential_modify'),
    ]

    _NETWORK_PATTERNS = [
        re.compile(r'\b(?:curl|wget|fetch|request|http|api|url|download|upload)\b', re.I),
        re.compile(r'https?://', re.I),
    ]

    _WRITE_PATTERNS = [
        re.compile(r'\b(?:запиши|write|create|создай|сохрани|save|modify|измени)\b', re.I),
    ]

    _EXECUTE_PATTERNS = [
        re.compile(r'\b(?:запусти|run|execute|выполни|install|pip|npm)\b', re.I),
    ]

    @classmethod
    def classify(cls, instruction: str) -> tuple[str, str]:
        """Классифицирует инструкцию.

        Returns: (intent, detail)
            intent — один из Intent.*
            detail — пояснение
        """
        if not instruction or not instruction.strip():
            return cls.Intent.SAFE, 'пустая инструкция'

        text = instruction.strip()

        # 1. Проверяем blocked (максимальный приоритет)
        for pattern, detail in cls._BLOCKED_PATTERNS:
            if pattern.search(text):
                return cls.Intent.BLOCKED, detail

        # 2. Проверяем dangerous
        for pattern, detail in cls._DANGEROUS_PATTERNS:
            if pattern.search(text):
                return cls.Intent.DANGEROUS, detail

        # 3. Network
        for pattern in cls._NETWORK_PATTERNS:
            if pattern.search(text):
                return cls.Intent.NETWORK, 'network_access'

        # 4. Execute
        for pattern in cls._EXECUTE_PATTERNS:
            if pattern.search(text):
                return cls.Intent.EXECUTE, 'execution'

        # 5. Write
        for pattern in cls._WRITE_PATTERNS:
            if pattern.search(text):
                return cls.Intent.WRITE, 'write_operation'

        return cls.Intent.READ_ONLY, 'no_dangerous_patterns'

    @classmethod
    def is_safe(cls, instruction: str) -> bool:
        intent, _ = cls.classify(instruction)
        return intent not in (cls.Intent.BLOCKED, cls.Intent.DANGEROUS)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. RateLimiter + Kill Switch + Immutable Audit Log
# ═══════════════════════════════════════════════════════════════════════════════

class RateLimiter:
    """Hard limits на действия агента.

    Лимиты:
        - MAX_ACTIONS_PER_CYCLE: макс действий за один цикл (default: 15)
        - MAX_TOOL_CALLS_PER_MINUTE: макс вызовов инструментов в минуту (default: 30)
        - MAX_FILE_WRITES_PER_CYCLE: макс записей файлов за цикл (default: 10)
        - Kill switch: файл .agent_kill → немедленная остановка
    """

    MAX_ACTIONS_PER_CYCLE     = 15
    MAX_TOOL_CALLS_PER_MINUTE = 30
    MAX_FILE_WRITES_PER_CYCLE = 10

    def __init__(self, working_dir: str | None = None):
        self._working_dir = working_dir or os.getcwd()
        self._cycle_actions: int = 0
        self._cycle_file_writes: int = 0
        self._tool_calls: list[float] = []  # timestamps

    def reset_cycle(self):
        """Вызывается в начале каждого нового цикла."""
        self._cycle_actions = 0
        self._cycle_file_writes = 0

    def check_action(self) -> tuple[bool, str]:
        """Проверяет можно ли выполнить ещё одно действие в текущем цикле."""
        # Kill switch
        kill_ok, kill_reason = self.check_kill_switch()
        if not kill_ok:
            return False, kill_reason

        self._cycle_actions += 1
        if self._cycle_actions > self.MAX_ACTIONS_PER_CYCLE:
            return False, (
                f'RATE_LIMIT: достигнут лимит {self.MAX_ACTIONS_PER_CYCLE} '
                f'действий за цикл (выполнено: {self._cycle_actions})'
            )
        return True, 'OK'

    def check_tool_call(self) -> tuple[bool, str]:
        """Проверяет rate limit для вызова инструмента (sliding window 60s)."""
        now = time.time()
        # Очищаем старые записи (> 60s)
        self._tool_calls = [t for t in self._tool_calls if now - t < 60]
        self._tool_calls.append(now)
        if len(self._tool_calls) > self.MAX_TOOL_CALLS_PER_MINUTE:
            return False, (
                f'RATE_LIMIT: {self.MAX_TOOL_CALLS_PER_MINUTE} tool calls/min '
                f'(текущих: {len(self._tool_calls)})'
            )
        return True, 'OK'

    def check_file_write(self) -> tuple[bool, str]:
        """Проверяет лимит записи файлов за цикл."""
        self._cycle_file_writes += 1
        if self._cycle_file_writes > self.MAX_FILE_WRITES_PER_CYCLE:
            return False, (
                f'RATE_LIMIT: {self.MAX_FILE_WRITES_PER_CYCLE} file writes/cycle '
                f'(текущих: {self._cycle_file_writes})'
            )
        return True, 'OK'

    def check_kill_switch(self) -> tuple[bool, str]:
        """Проверяет наличие kill switch файла."""
        kill_path = os.path.join(self._working_dir, '.agent_kill')
        if os.path.exists(kill_path):
            return False, 'KILL_SWITCH: обнаружен файл .agent_kill — немедленная остановка'
        return True, 'OK'


class ImmutableAuditLog:
    """Append-only аудит лог с integrity проверкой.

    Каждая запись содержит SHA-256 хеш предыдущей записи (hash chain).
    Файл — append-only, перезапись невозможна.
    """

    def __init__(self, log_path: str, scrubber=None):
        self._path = log_path
        self._scrubber = scrubber  # callable(str)->str — SecretsRedactor
        self._prev_hash: str = '0' * 64  # genesis
        os.makedirs(os.path.dirname(log_path) or '.', exist_ok=True)

        # Восстанавливаем prev_hash из последней записи
        if os.path.exists(log_path):
            try:
                with open(log_path, 'r', encoding='utf-8') as f:
                    last_line = ''
                    for line in f:
                        line = line.strip()
                        if line:
                            last_line = line
                    if last_line:
                        entry = json.loads(last_line)
                        self._prev_hash = entry.get('hash', self._prev_hash)
            except (OSError, json.JSONDecodeError):
                pass

    def record(self, event_type: str, data: dict):
        """Записывает событие в immutable log."""
        safe_data = self._scrub_data(data) if self._scrubber else data
        entry = {
            'ts': time.time(),
            'ts_iso': time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()),
            'type': event_type,
            'data': safe_data,
            'prev_hash': self._prev_hash,
        }
        # Вычисляем hash этой записи
        payload = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        entry['hash'] = hashlib.sha256(payload.encode('utf-8')).hexdigest()
        self._prev_hash = entry['hash']

        try:
            with open(self._path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def _scrub_data(self, data: dict) -> dict:
        """Рекурсивно очищает data от секретов через scrubber."""
        result = {}
        for k, v in data.items():
            if isinstance(v, str):
                result[k] = self._scrubber(v)  # type: ignore[misc]
            elif isinstance(v, dict):
                result[k] = self._scrub_data(v)
            elif isinstance(v, list):
                result[k] = [
                    self._scrubber(i) if isinstance(i, str) else i  # type: ignore[misc]
                    for i in v
                ]
            else:
                result[k] = v
        return result

    def verify_integrity(self) -> tuple[bool, int, str]:
        """Проверяет целостность цепочки хешей.

        Returns: (ok, entries_checked, error_detail)
        """
        if not os.path.exists(self._path):
            return True, 0, 'файл не существует'

        prev = '0' * 64
        count = 0
        try:
            with open(self._path, 'r', encoding='utf-8') as f:
                for line_no, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    if entry.get('prev_hash') != prev:
                        return False, count, f'строка {line_no}: prev_hash mismatch'

                    stored_hash = entry.pop('hash')
                    payload = json.dumps(entry, sort_keys=True, ensure_ascii=False)
                    computed = hashlib.sha256(payload.encode('utf-8')).hexdigest()
                    entry['hash'] = stored_hash

                    if computed != stored_hash:
                        return False, count, f'строка {line_no}: hash mismatch'

                    prev = stored_hash
                    count += 1
        except (OSError, json.JSONDecodeError) as e:
            return False, count, f'ошибка чтения: {e}'

        return True, count, 'OK'
