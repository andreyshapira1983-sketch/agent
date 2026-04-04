# Command Gateway — единственная точка запуска внешних процессов.
# ВСЕ subprocess-вызовы обязаны проходить через этот модуль.
# Прямой доступ к subprocess запрещён в остальном коде.
#
# Принципы:
#   1. Whitelist исполняемых файлов (CommandValidator.ALLOWED_EXECUTABLES)
#   2. Блокировка shell=True и опасных паттернов
#   3. Аудит каждого вызова (мониторинг + change_tracker)
#   4. Поддержка Docker-изоляции (если настроено)
#   5. Fail-closed: при любом сомнении — отказ
#
# SECURITY: Этот модуль — ЕДИНСТВЕННЫЙ разрешённый способ запуска процессов.

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import time
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class GatewayResult:
    """Результат выполнения команды через шлюз."""
    allowed: bool
    stdout: str = ''
    stderr: str = ''
    returncode: int = -1
    reject_reason: str = ''
    duration: float = 0.0
    command: str = ''
    exe: str = ''


# ── Whitelist / Blocklist (immutable) ────────────────────────────────────────

ALLOWED_EXECUTABLES: frozenset[str] = frozenset({
    # Python
    'python', 'python3', 'pip', 'pip3', 'poetry', 'pipenv',
    # Node
    'node', 'npm', 'npx', 'yarn', 'pnpm',
    # Git
    'git',
    # Системные read-only
    'ls', 'dir', 'cat', 'head', 'tail', 'echo', 'pwd',
    'find', 'grep', 'wc', 'sort', 'uniq', 'diff', 'which', 'where',
    'type', 'file', 'stat', 'whoami', 'hostname', 'date', 'uname',
    # Тестирование
    'pytest',
    # Сборка
    'make', 'cmake', 'cargo', 'go', 'javac', 'java', 'gcc', 'g++',
    # Медиа (только конкретные read/convert утилиты)
    'ffprobe', 'ffmpeg',
    # Архивация (только через gateway)
    'rar', 'unrar',
    # Lint
    'pylint', 'black',
    # Ping (сетевая диагностика)
    'ping',
})

BLOCKED_EXECUTABLES: frozenset[str] = frozenset({
    'rm', 'rmdir', 'del', 'format', 'mkfs', 'dd',
    'shutdown', 'reboot', 'halt', 'poweroff', 'init',
    'sudo', 'su', 'runas', 'doas',
    'chmod', 'chown', 'chgrp',
    'kill', 'killall', 'pkill', 'taskkill',
    'curl', 'wget', 'nc', 'ncat', 'netcat', 'nmap',
    'ssh', 'scp', 'rsync', 'ftp', 'sftp', 'telnet',
    'eval', 'exec', 'source',
    'bash', 'sh', 'cmd', 'powershell', 'pwsh',
    'reg', 'regedit', 'sc', 'net', 'netsh', 'wmic',
    'crontab', 'at', 'schtasks',
})

_ARG_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r';\s*'),         # цепочка ;
    re.compile(r'\|\|'),         # ||
    re.compile(r'&&'),           # &&
    re.compile(r'\|'),           # pipe
    re.compile(r'`'),            # backtick
    re.compile(r'\$\('),         # $(...)
    re.compile(r'>\s*'),         # redirect out
    re.compile(r'<\s*'),         # redirect in
    re.compile(r'\.\./\.\./'),   # deep traversal
]


class CommandGateway:
    """
    Центральный шлюз запуска внешних процессов.

    Все модули агента обязаны вызывать команды ТОЛЬКО через этот класс.
    Прямое использование subprocess запрещено.
    """

    _instance: CommandGateway | None = None
    _lock = threading.Lock()

    def __init__(self, *,
                 monitoring=None,
                 docker_image: str | None = None,
                 working_dir: str | None = None,
                 audit_log: str = 'logs/commands.jsonl'):
        self._monitoring = monitoring
        self._docker_image = docker_image
        self._working_dir = working_dir
        self._audit_path = audit_log
        self._call_count = 0
        self._blocked_count = 0
        self._counters_lock = threading.Lock()
        self._scrubber: Callable[[str], str] | None = None

    @classmethod
    def get_instance(cls, **kwargs) -> CommandGateway:
        """Singleton: один шлюз на весь процесс."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(**kwargs)
        return cls._instance

    # ── PUBLIC API ───────────────────────────────────────────────────────────

    def execute(self, args: list[str], *,
                timeout: int = 60,
                cwd: str | None = None,
                env: dict | None = None,
                allow_network: bool = False,
                caller: str = '') -> GatewayResult:
        """
        Запускает процесс через whitelist-проверку.

        Args:
            args:      список аргументов (НЕ строка!), e.g. ['git', 'status']
            timeout:   макс. время в секундах
            cwd:       рабочая директория
            env:       переменные окружения (если None — наследуется)
            allow_network: разрешить сетевые утилиты (ping и т.п.)
            caller:    имя вызывающего модуля (для аудита)

        Returns:
            GatewayResult
        """
        with self._counters_lock:
            self._call_count += 1
        cmd_str = ' '.join(args) if args else ''

        # 1) Проверяем что args — список, не строка
        if isinstance(args, str):
            with self._counters_lock:
                self._blocked_count += 1
            return self._reject(cmd_str, 'args должен быть списком, не строкой',
                                caller=caller)

        if not args:
            return self._reject('', 'Пустая команда', caller=caller)

        # 2) Извлекаем исполняемый файл
        exe = os.path.basename(args[0]).lower()
        if exe.endswith('.exe'):
            exe = exe[:-4]

        # 3) Blocklist
        if exe in BLOCKED_EXECUTABLES:
            with self._counters_lock:
                self._blocked_count += 1
            return self._reject(cmd_str, f"'{exe}' в чёрном списке",
                                exe=exe, caller=caller)

        # 4) Whitelist
        if exe not in ALLOWED_EXECUTABLES:
            with self._counters_lock:
                self._blocked_count += 1
            return self._reject(cmd_str, f"'{exe}' не в белом списке",
                                exe=exe, caller=caller)

        # 5) Injection-паттерны в аргументах
        args_joined = ' '.join(args[1:])
        for pat in _ARG_INJECTION_PATTERNS:
            if pat.search(args_joined):
                with self._counters_lock:
                    self._blocked_count += 1
                return self._reject(cmd_str,
                                    f"Инъекция в аргументах: {pat.pattern}",
                                    exe=exe, caller=caller)

        # 6) python -c с сетевыми модулями
        if exe in ('python', 'python3') and '-c' in args:
            try:
                idx = args.index('-c')
                inline = ' '.join(args[idx + 1:])
                _net = ('socket', 'http.', 'urllib', 'requests',
                        'httpx', 'aiohttp', 'urlopen')
                for nk in _net:
                    if nk in inline:
                        with self._counters_lock:
                            self._blocked_count += 1
                        return self._reject(cmd_str,
                                            f"Сетевой код в python -c: '{nk}'",
                                            exe=exe, caller=caller)
            except (ValueError, IndexError):
                pass

        # 7) Docker-изоляция (если настроена)
        if self._docker_image:
            args = self._wrap_docker(args, allow_network=allow_network)

        # 8) Выполнение
        t0 = time.time()
        try:
            proc = subprocess.run(
                args,
                shell=False,
                capture_output=True,
                text=True,
                timeout=max(1, timeout),
                cwd=cwd or self._working_dir,
                env=env,
                check=False,
            )
            dur = time.time() - t0
            result = GatewayResult(
                allowed=True,
                stdout=proc.stdout or '',
                stderr=proc.stderr or '',
                returncode=proc.returncode,
                duration=dur,
                command=cmd_str,
                exe=exe,
            )
            self._audit(result, caller)
            return result

        except subprocess.TimeoutExpired:
            dur = time.time() - t0
            result = GatewayResult(
                allowed=True,
                returncode=-1,
                reject_reason=f'Таймаут {timeout}с',
                duration=dur,
                command=cmd_str,
                exe=exe,
            )
            self._audit(result, caller)
            return result

        except OSError as e:
            dur = time.time() - t0
            return GatewayResult(
                allowed=True,
                returncode=-1,
                reject_reason=str(e),
                duration=dur,
                command=cmd_str,
                exe=exe,
            )

    def execute_str(self, command: str, *,
                    timeout: int = 60,
                    cwd: str | None = None,
                    caller: str = '') -> GatewayResult:
        """
        Удобный метод: принимает строку, парсит через shlex.split.
        """
        if not command or not command.strip():
            return self._reject('', 'Пустая команда', caller=caller)
        try:
            args = shlex.split(command.strip())
        except ValueError as e:
            return self._reject(command, f'Невалидный синтаксис: {e}', caller=caller)
        return self.execute(args, timeout=timeout, cwd=cwd, caller=caller)

    # ── Docker isolation ─────────────────────────────────────────────────────

    def _wrap_docker(self, args: list[str], *,
                     allow_network: bool = False) -> list[str]:
        """
        Оборачивает команду в docker run с изоляцией.

        Параметры изоляции:
          --rm                   удалить контейнер после выполнения
          --read-only            файловая система только для чтения
          --no-new-privileges    запрет escalation
          --network none         без сети (по умолчанию)
          --memory 256m          лимит памяти
          --cpus 1               лимит CPU
          --user nobody          непривилегированный пользователь
        """
        docker_args = [
            'docker', 'run', '--rm',
            '--read-only',
            '--no-new-privileges',
            '--memory', '256m',
            '--cpus', '1',
            '--user', 'nobody',
            '--pids-limit', '64',
            '--security-opt', 'no-new-privileges:true',
        ]
        if not allow_network:
            docker_args += ['--network', 'none']

        # Монтируем рабочую директорию как read-only том
        if self._working_dir:
            docker_args += ['-v', f'{self._working_dir}:/workspace:ro',
                            '-w', '/workspace']

        docker_args.append(self._docker_image)  # type: ignore[arg-type]  # guarded by caller check
        docker_args.extend(args)
        return docker_args

    # ── Stats ────────────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        return {
            'total_calls': self._call_count,
            'blocked': self._blocked_count,
            'allowed': self._call_count - self._blocked_count,
        }

    # ── Internal ─────────────────────────────────────────────────────────────

    def _reject(self, cmd: str, reason: str, *,
                exe: str = '', caller: str = '') -> GatewayResult:
        r = GatewayResult(
            allowed=False,
            reject_reason=reason,
            command=cmd,
            exe=exe,
        )
        self._audit(r, caller)
        return r

    def _audit(self, result: GatewayResult, caller: str):
        """Записывает аудит-запись в JSONL."""
        import json
        _scrub = self._scrubber
        _cmd = result.command[:500]
        if _scrub is not None:
            _cmd = _scrub(_cmd)
        entry = {
            'ts': time.time(),
            'caller': caller,
            'exe': result.exe,
            'command': _cmd,
            'allowed': result.allowed,
            'returncode': result.returncode,
            'duration': round(result.duration, 3),
        }
        if not result.allowed:
            entry['reject_reason'] = result.reject_reason

        try:
            os.makedirs(os.path.dirname(self._audit_path), exist_ok=True)
            with self._lock:
                with open(self._audit_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError as _audit_err:
            if self._monitoring:
                self._monitoring.warning(
                    f"Ошибка записи аудит-лога: {_audit_err}", source="command_gateway"
                )

        if self._monitoring:
            try:
                level = 'warning' if not result.allowed else 'debug'
                msg = (f"[CommandGateway] {'BLOCKED' if not result.allowed else 'OK'}: "
                       f"{result.exe} (caller={caller})")
                if not result.allowed:
                    msg += f" reason={result.reject_reason}"
                self._monitoring.log(msg, level=level, source='command_gateway')
            except Exception:
                pass
