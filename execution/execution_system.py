# Execution System (система исполнения) — Слой 8
# Архитектура автономного AI-агента
# Исполнение действий: скрипты, команды, программы, сервисы, деплой.
#
# SECURITY: shell=False, команды валидируются через CommandValidator.
# Локальный мозг контролирует исполнение — LLM не может обойти.
# pylint: disable=broad-except


import os
import subprocess
import shlex
import threading
import uuid
from enum import Enum


class TaskStatus(Enum):
    PENDING  = 'pending'
    RUNNING  = 'running'
    SUCCESS  = 'success'
    FAILED   = 'failed'
    REJECTED = 'rejected'
    TIMEOUT  = 'timeout'


class ExecutionTask:
    """Представляет одну единицу исполнения."""

    def __init__(self, task_id, command, task_type='command', metadata=None):
        self.task_id = task_id
        self.command = command
        self.task_type = task_type          # 'command', 'script', 'service', 'deploy'
        self.metadata = metadata or {}
        self.status = TaskStatus.PENDING
        self.stdout: str | None = None
        self.stderr: str | None = None
        self.returncode: int | None = None
        self.error: str | None = None

    def to_dict(self):
        return {
            'task_id': self.task_id,
            'command': self.command,
            'task_type': self.task_type,
            'status': self.status.value,
            'stdout': self.stdout,
            'stderr': self.stderr,
            'returncode': self.returncode,
            'error': self.error,
            'metadata': self.metadata,
        }


class ExecutionSystem:
    """
    Execution System — система исполнения действий (Слой 8).

    Отвечает за запуск скриптов, команд, программ, управление сервисами
    и деплой приложений.

    Компоненты:
        - task_executor  — исполнение задач
        - command_runner — запуск системных команд
        - job_scheduler  — планировщик задач

    Связан с:
        - Cognitive Core (Слой 3)   — получает команды для исполнения
        - Human Approval (Слой 22)  — подтверждение перед исполнением
        - Monitoring (Слой 17)      — логирование результатов
        - Reliability System (Слой 19) — retry / timeout / fallback

    ВАЖНО: опасные или необратимые действия требуют human_approval.
    """

    def __init__(
        self,
        human_approval=None,
        monitoring=None,
        working_dir=None,
        timeout_default=60,
        safe_mode=True,
    ):
        """
        Args:
            human_approval  -- экземпляр HumanApprovalLayer (Слой 22)
            monitoring      -- экземпляр Monitoring (Слой 17)
            working_dir     -- рабочая директория по умолчанию
            timeout_default -- таймаут команд в секундах
            safe_mode       -- если True, опасные команды требуют human_approval
        """
        self.human_approval = human_approval
        self.monitoring = monitoring
        self.working_dir = working_dir
        self.timeout_default = timeout_default
        # SECURITY: safe_mode хранится в защищённом атрибуте (VULN-10)
        self.__safe_mode = safe_mode

        self._tasks: dict[str, ExecutionTask] = {}  # история задач
        self._running: dict[str, threading.Thread] = {}

    @property
    def safe_mode(self):
        """SECURITY: safe_mode только для чтения — LLM не может отключить."""
        return self.__safe_mode

    # ── Task Executor ─────────────────────────────────────────────────────────

    def submit(self, command, task_type='command', metadata=None, timeout=None, require_approval=False) -> ExecutionTask:
        """
        Регистрирует и запускает задачу.

        Args:
            command          — строка команды или путь к скрипту
            task_type        — 'command' | 'script' | 'service' | 'deploy'
            metadata         — произвольные метаданные задачи
            timeout          — таймаут в секундах (None = default)
            require_approval — форсировать запрос Human Approval

        Returns:
            ExecutionTask с результатом выполнения.
        """
        task_id = str(uuid.uuid4())[:8]
        task = ExecutionTask(task_id, command, task_type, metadata)
        self._tasks[task_id] = task

        # Human Approval: если запрошено явно или safe_mode + опасная команда
        needs_approval = require_approval or (self.safe_mode and self._is_dangerous(command))
        if needs_approval and self.human_approval:
            approved = self.human_approval.approve_execution(command)
            if not approved:
                task.status = TaskStatus.REJECTED
                self._log(task)
                return task

        task.status = TaskStatus.RUNNING
        self._run(task, timeout or self.timeout_default)
        return task

    def _run(self, task: ExecutionTask, timeout: int):
        """Исполняет команду через subprocess.
        SECURITY: shell=False, команда валидируется через CommandValidator."""
        # SECURITY: Импортируем валидатор из tool_layer
        from tools.tool_layer import CommandValidator

        allowed, reason = CommandValidator.validate(task.command)
        if not allowed:
            task.status = TaskStatus.REJECTED
            task.error = f"Команда заблокирована: {reason}"
            self._log(task)
            return

        try:
            # SECURITY: все вызовы идут через центральный CommandGateway
            from execution.command_gateway import CommandGateway
            gw = CommandGateway.get_instance()
            args = shlex.split(task.command)
            r = gw.execute(
                args,
                timeout=timeout,
                cwd=self.working_dir,
                caller='ExecutionSystem._run',
            )
            if not r.allowed:
                task.status = TaskStatus.REJECTED
                task.error = f"CommandGateway: {r.reject_reason}"
                return
            task.stdout = r.stdout
            task.stderr = r.stderr
            task.returncode = r.returncode
            task.status = TaskStatus.SUCCESS if r.returncode == 0 else TaskStatus.FAILED
            if r.reject_reason and 'Таймаут' in r.reject_reason:
                task.status = TaskStatus.TIMEOUT
                task.error = r.reject_reason
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error = str(exc)
        finally:
            self._log(task)

    # ── Command Runner ────────────────────────────────────────────────────────

    def run_command(self, command, timeout=None) -> ExecutionTask:
        """Запускает системную команду. Короткий синтаксис."""
        return self.submit(command, task_type='command', timeout=timeout)

    def run_script(self, script_path, args=None, timeout=None) -> ExecutionTask:
        """Запускает скрипт (Python, bash и др.)."""
        cmd = f"{script_path} {' '.join(args or [])}"
        return self.submit(cmd, task_type='script', timeout=timeout)

    # ── Service Management ────────────────────────────────────────────────────

    def start_service(self, service_name) -> ExecutionTask:
        """Запускает системный сервис (требует Human Approval в safe_mode)."""
        return self.submit(
            f"sc start {service_name}" if self._is_windows() else f"systemctl start {service_name}",
            task_type='service',
            require_approval=self.safe_mode,
        )

    def stop_service(self, service_name) -> ExecutionTask:
        """Останавливает системный сервис (требует Human Approval в safe_mode)."""
        return self.submit(
            f"sc stop {service_name}" if self._is_windows() else f"systemctl stop {service_name}",
            task_type='service',
            require_approval=self.safe_mode,
        )

    # ── Deploy ────────────────────────────────────────────────────────────────

    def deploy(self, deploy_command, metadata=None) -> ExecutionTask:
        """Деплой приложения — всегда требует Human Approval."""
        return self.submit(
            deploy_command,
            task_type='deploy',
            metadata=metadata,
            require_approval=True,
        )

    # ── Job Scheduler ─────────────────────────────────────────────────────────

    def schedule(self, command, delay_seconds: int, metadata=None):
        """
        Планирует выполнение команды через delay_seconds секунд.
        Возвращает task_id для последующей отмены.
        """
        task_id = str(uuid.uuid4())[:8]
        task = ExecutionTask(task_id, command, task_type='scheduled', metadata=metadata)
        self._tasks[task_id] = task

        def _delayed():
            import time
            time.sleep(delay_seconds)
            self._run(task, self.timeout_default)

        thread = threading.Thread(target=_delayed, daemon=True)
        self._running[task_id] = thread
        thread.start()
        return task_id

    # ── Task history ──────────────────────────────────────────────────────────

    def get_task(self, task_id) -> ExecutionTask | None:
        """Возвращает задачу по ID."""
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list:
        """Возвращает историю всех задач."""
        return [t.to_dict() for t in self._tasks.values()]

    def get_failed_tasks(self) -> list:
        """Возвращает только упавшие задачи."""
        return [t.to_dict() for t in self._tasks.values() if t.status == TaskStatus.FAILED]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _is_dangerous(self, command: str) -> bool:
        """Эвристика: команда потенциально опасна.
        SECURITY: Расширенный список + структурный анализ."""
        from tools.tool_layer import CommandValidator
        # Если команда не проходит whitelist — она автоматически опасна
        allowed, _ = CommandValidator.validate(command)
        if not allowed:
            return True

        dangerous_patterns = [
            'rm ', 'rmdir', 'del ', 'format', 'mkfs', 'dd ',
            'DROP ', 'DELETE ', 'TRUNCATE', 'ALTER ', 'UPDATE ',
            'shutdown', 'reboot', 'halt', 'poweroff', 'init ',
            'chmod', 'chown', 'chgrp',
            'curl', 'wget', 'nc ', 'ncat', 'netcat',
            'sudo', 'su ', 'runas', 'doas',
            'deploy', 'kubectl', 'helm',
            'kill', 'killall', 'pkill', 'taskkill',
            'ssh ', 'scp ', 'rsync ', 'ftp ',
            'eval ', 'exec ', 'bash ', 'sh ', 'cmd ', 'powershell',
            'reg ', 'regedit', 'sc ', 'net ', 'netsh', 'wmic',
            'crontab', 'schtasks',
            '>', '>>', '|', '&&', '||', ';', '`', '$(',
            'docker run', 'docker exec',
        ]
        cmd_lower = command.lower()
        return any(p.lower() in cmd_lower for p in dangerous_patterns)

    def _is_windows(self) -> bool:
        return os.name == 'nt'

    def _log(self, task: ExecutionTask):
        """Передаёт результат в Monitoring (Слой 17) если подключён."""
        if self.monitoring:
            self.monitoring.log_execution(task.to_dict())
