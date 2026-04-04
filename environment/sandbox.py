# Simulation / Sandbox Testing Layer (песочница) — Слой 28
# Архитектура автономного AI-агента
# Тестирование действий до реального выполнения: код, стратегии, сценарии.
# Контейнерная изоляция (Docker) + fallback на subprocess.


import ast
import json as _json
import logging
import re
import shutil
import subprocess as _subprocess
import time
import sys
import uuid as _uuid
from enum import Enum


class SandboxResult(Enum):
    SAFE     = 'safe'       # безопасно, можно выполнять
    RISKY    = 'risky'      # выполнимо, но есть риски
    UNSAFE   = 'unsafe'     # не выполнять — опасно
    ERROR    = 'error'      # ошибка при симуляции


class SimulationRun:
    """Результат одного прогона симуляции."""

    def __init__(self, run_id: str, action: str):
        self.run_id = run_id
        self.action = action
        self.verdict = SandboxResult.SAFE
        self.stdout = ''
        self.stderr = ''
        self.error: str | None = None
        self.side_effects: list[str] = []
        self.duration: float = 0.0
        self.timestamp = time.time()

    def to_dict(self):
        return {
            'run_id': self.run_id,
            'action': self.action,
            'verdict': self.verdict.value,
            'stdout': self.stdout,
            'stderr': self.stderr,
            'error': self.error,
            'side_effects': self.side_effects,
            'duration': self.duration,
        }


# ── Container Sandbox (runbook §10, security_behavior_hardening §2.5) ────────

_SANDBOX_IMAGE = 'agent-sandbox'

# Ограничения по умолчанию (из Dockerfile.sandbox comments + runbook §10)
_DEFAULT_LIMITS = {
    'memory':    '256m',
    'cpus':      '1',
    'pids_limit': '64',
    'timeout':    30,      # секунды
}


class ContainerSandbox:
    """Контейнерная песочница — каждый job в отдельном Docker-контейнере.

    Enforcement (runbook §10):
        - отдельный контейнер на каждый job
        - CPU / RAM / PID limits
        - read-only root FS
        - /tmp → tmpfs (единственная writable area)
        - сеть выключена по умолчанию (--network none)
        - непривилегированный пользователь
        - --no-new-privileges

    Fallback: если Docker недоступен, возвращает (False, ...) — caller
    использует subprocess-based sandbox.
    """

    def __init__(
        self,
        image: str = _SANDBOX_IMAGE,
        memory: str = _DEFAULT_LIMITS['memory'],
        cpus: str = _DEFAULT_LIMITS['cpus'],
        pids_limit: str = _DEFAULT_LIMITS['pids_limit'],
        default_timeout: int = _DEFAULT_LIMITS['timeout'],
        network: bool = False,
    ):
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.default_timeout = default_timeout
        self.network = network
        self._available: bool | None = None  # кэш проверки Docker

    def is_available(self) -> bool:
        """Проверяет, доступен ли Docker daemon и образ."""
        if self._available is not None:
            return self._available
        try:
            docker_bin = shutil.which('docker')
            if not docker_bin:
                self._available = False
                return False
            result = _subprocess.run(
                ['docker', 'image', 'inspect', self.image],
                capture_output=True, text=True, timeout=10, check=False,
            )
            self._available = result.returncode == 0
        except (OSError, _subprocess.TimeoutExpired):
            self._available = False
        return self._available

    def run(
        self,
        code: str,
        context: dict | None = None,
        timeout: int | None = None,
    ) -> tuple[bool, str, str, float]:
        """Выполняет Python-код в изолированном контейнере.

        Args:
            code    — Python-код
            context — JSON-сериализуемый контекст (dict)
            timeout — таймаут в секундах

        Returns:
            (success, stdout, stderr, duration_seconds)
            success=False если timeout / nonzero exit / Docker error.
        """
        timeout = timeout or self.default_timeout

        # Формируем runner-скрипт (передаём код через stdin)
        safe_ctx = {}
        if isinstance(context, dict):
            for k, v in context.items():
                if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                    safe_ctx[str(k)] = v

        runner_code = (
            "import sys, json\n"
            "ctx = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}\n"
            "safe_names = ['print','len','range','enumerate','zip','map','filter',"
            "'sorted','reversed','list','dict','set','tuple','str','int','float',"
            "'bool','isinstance','min','max','sum','abs','round','repr']\n"
            "import builtins\n"
            "safe = {n: getattr(builtins, n) for n in safe_names if hasattr(builtins, n)}\n"
            "ns = {'__builtins__': safe, **ctx}\n"
            "code = sys.stdin.read()\n"
            "exec(compile(code, '<sandbox>', 'exec'), ns)\n"
        )

        ctx_json = _json.dumps(safe_ctx, ensure_ascii=True)

        cmd = [
            'docker', 'run', '--rm',
            '--read-only',
            '--network', 'none' if not self.network else 'bridge',
            '--memory', self.memory,
            '--cpus', self.cpus,
            '--pids-limit', self.pids_limit,
            '--no-new-privileges',
            '--user', 'nobody',
            '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
            '-i',  # stdin
            self.image,
            'python', '-I', '-c', runner_code, ctx_json,
        ]

        t_start = time.time()
        try:
            proc = _subprocess.run(
                cmd,
                input=code,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            duration = time.time() - t_start
            return (
                proc.returncode == 0,
                proc.stdout or '',
                proc.stderr or '',
                duration,
            )
        except _subprocess.TimeoutExpired:
            duration = time.time() - t_start
            return (False, '', f'Container timeout ({timeout}s)', duration)
        except OSError as e:
            duration = time.time() - t_start
            return (False, '', f'Docker error: {e}', duration)

    def reset_cache(self):
        """Сбрасывает кэш проверки Docker (для тестов)."""
        self._available = None


class SandboxLayer:
    """
    Simulation / Sandbox Testing Layer — Слой 28.

    Функции:
        - запуск Python-кода в изолированном namespace (без побочных эффектов)
        - симуляция сценариев через Cognitive Core (what-if)
        - проверка стратегий до их применения
        - dry-run команд: предсказание результата без выполнения
        - оценка безопасности действия до реального запуска

    Используется:
        - Cognitive Core (Слой 3)      — генерирует код/стратегии для проверки
        - Execution System (Слой 8)    — проверка команды перед запуском
        - Self-Improvement (Слой 12)   — тест новых стратегий
        - Governance (Слой 21)         — проверка политик на симуляции
    """

    def __init__(self, environment_model=None, cognitive_core=None,
                 governance=None, monitoring=None,
                 container_sandbox: ContainerSandbox | None = None):
        self.environment_model = environment_model
        self.cognitive_core = cognitive_core
        self.governance = governance
        self.monitoring = monitoring

        # Контейнерная песочница (runbook §10): если передана и доступна,
        # run_code() будет выполнять код в Docker-контейнере.
        # Fallback: subprocess на хосте (текущее поведение).
        self.container_sandbox = container_sandbox or ContainerSandbox()

        self._runs: list[SimulationRun] = []
        self._allowed_modules = {
            'math', 'json', 're', 'datetime', 'collections',
            'itertools', 'functools', 'string', 'random',
        }

    # ── Запуск кода в изоляции ────────────────────────────────────────────────

    def run_code(self, code: str, context: dict | None = None, timeout: int = 10) -> SimulationRun:
        """
        Выполняет Python-код в изолированном namespace.
        Перехватывает stdout/stderr. Не имеет доступа к файловой системе и сети.
        SECURITY (VULN-11): Предварительная проверка кода + ограниченные builtins.

        Args:
            code    -- Python-код для тестирования
            context -- начальные переменные в namespace
            timeout -- максимальное время выполнения (сек)

        Returns:
            SimulationRun с результатами.
        """
        import uuid
        import json
        import base64
        import subprocess
        run = SimulationRun(str(uuid.uuid4())[:8], action=code[:100])

        # SECURITY (VULN-11): Предварительная проверка кода на опасные конструкции
        side_effects = self._detect_side_effects(code)
        if side_effects:
            # Если код содержит опасные вызовы — НЕ запускаем
            blocked_effects = [e for e in side_effects
                               if any(w in e for w in ('ОС', 'процесс', 'сетевые', 'файлов'))]
            if blocked_effects:
                run.verdict = SandboxResult.UNSAFE
                run.side_effects = side_effects
                run.error = f"Код заблокирован до выполнения: {', '.join(blocked_effects)}"
                self._runs.append(run)
                self._log(f"Sandbox BLOCKED [{run.run_id}]: {run.error}")
                return run

        # SECURITY: Проверяем импорты — только реально опасные (сеть, процессы, память)
        blocked_imports = {'subprocess', 'sys',
                           'socket', 'http', 'urllib', 'requests', 'httpx', 'aiohttp',
                           'ctypes', 'multiprocessing', 'threading',
                           'signal', 'resource', 'pty',
                           'pickle', 'shelve', 'marshal',
                           'importlib', 'pkgutil',
                           'builtins', 'code', 'webbrowser',
                           'ftplib', 'telnetlib', 'paramiko', 'smtplib',
                           'imaplib', 'poplib'}
        # 1) Regex: ловит прямые import/from
        import_matches = re.findall(r'(?:^|\n)\s*(?:import|from)\s+([\w.]+)', code)
        for mod in import_matches:
            root = mod.split('.')[0]
            if root in blocked_imports:
                run.verdict = SandboxResult.UNSAFE
                run.error = f"Запрещённый импорт в sandbox: '{mod}'"
                self._runs.append(run)
                self._log(f"Sandbox BLOCKED [{run.run_id}]: {run.error}")
                return run
        # 2) Ловим динамические обходы: __import__('x'), importlib.import_module('x')
        _DYNAMIC_IMPORT_RE = re.compile(
            r'__import__\s*\(|importlib\s*\.\s*import_module\s*\(',
        )
        if _DYNAMIC_IMPORT_RE.search(code):
            run.verdict = SandboxResult.UNSAFE
            run.error = "Запрещённый динамический импорт (sandbox escape prevention)"
            self._runs.append(run)
            self._log(f"Sandbox BLOCKED [{run.run_id}]: {run.error}")
            return run

        # Приводим контекст к JSON-совместимому виду, чтобы безопасно передать в subprocess.
        safe_context = {}
        if isinstance(context, dict):
            for k, v in context.items():
                if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                    safe_context[str(k)] = v

        # SECURITY: AST-валидация — блокируем dunder-атрибуты для предотвращения
        # побега из песочницы через ().__class__.__bases__[0].__subclasses__()
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            run.verdict = SandboxResult.ERROR
            run.error = f"SyntaxError: {e}"
            self._runs.append(run)
            return run

        _BLOCKED_DUNDERS = frozenset({
            '__class__', '__bases__', '__subclasses__', '__mro__',
            '__globals__', '__code__', '__closure__', '__func__',
            '__self__', '__module__', '__dict__', '__weakref__',
            '__init_subclass__', '__set_name__', '__del__',
            '__builtins__', '__import__', '__loader__', '__spec__',
        })
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in _BLOCKED_DUNDERS:
                run.verdict = SandboxResult.UNSAFE
                run.error = f"Запрещённый доступ к '{node.attr}' (sandbox escape prevention)"
                self._runs.append(run)
                self._log(f"Sandbox BLOCKED [{run.run_id}]: dunder access '{node.attr}'")
                return run
            # Блокируем getattr/setattr/delattr — обход через строковые имена
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in ('getattr', 'setattr', 'delattr', 'vars', 'dir',
                                     'exec', 'eval', 'compile', '__import__', 'breakpoint'):
                    run.verdict = SandboxResult.UNSAFE
                    run.error = f"Запрещённый вызов '{node.func.id}()' в sandbox"
                    self._runs.append(run)
                    self._log(f"Sandbox BLOCKED [{run.run_id}]: call '{node.func.id}'")
                    return run

        # SECURITY: Ограничиваем размер кода
        if len(code) > 100_000:
            run.verdict = SandboxResult.UNSAFE
            run.error = "Код слишком большой (>100KB)"
            self._runs.append(run)
            return run

        # ── Container path (runbook §10): предпочтительный метод ──────────
        if self.container_sandbox and self.container_sandbox.is_available():
            success, stdout, stderr, duration = self.container_sandbox.run(
                code, context=safe_context, timeout=timeout,
            )
            run.stdout = stdout
            run.stderr = stderr
            run.duration = duration
            if not success:
                run.verdict = SandboxResult.ERROR
                run.error = stderr.strip() or 'Container execution failed'
            else:
                run.verdict = SandboxResult.SAFE
                run.side_effects = side_effects
                if run.side_effects:
                    run.verdict = SandboxResult.RISKY
            self._runs.append(run)
            self._log(f"Sandbox container [{run.run_id}]: {run.verdict.value}, {run.duration:.2f}s")
            return run

        # ── Subprocess fallback (если Docker недоступен) ──────────────────
        encoded_code = base64.b64encode(code.encode('utf-8')).decode('ascii')
        encoded_ctx = base64.b64encode(
            json.dumps(safe_context, ensure_ascii=False).encode('utf-8')
        ).decode('ascii')

        runner = (
            "import sys, json, base64, builtins;"
            "safe_names=['print','len','range','enumerate','zip','map','filter','sorted','reversed',"
            "'list','dict','set','tuple','str','int','float','bool','isinstance','min','max','sum','abs','round','repr'];"
            "safe={n:getattr(builtins,n) for n in safe_names if hasattr(builtins,n)};"
            "code=base64.b64decode(sys.argv[1]).decode('utf-8');"
            "ctx=json.loads(base64.b64decode(sys.argv[2]).decode('utf-8'));"
            "ns={'__builtins__':safe, **ctx};"
            "compiled=compile(code,'<sandbox>','exec');"
            "exec(compiled, ns)"
        )

        t_start = time.time()
        try:
            # SECURITY: фильтруем env — subprocess не наследует секреты
            from safety.secrets_proxy import safe_env
            proc = subprocess.run(
                [sys.executable, '-I', '-c', runner, encoded_code, encoded_ctx],
                capture_output=True,
                text=True,
                timeout=min(max(1, int(timeout)), 300),
                check=False,
                env=safe_env(),
            )
            run.duration = time.time() - t_start
            run.stdout = (proc.stdout or '')
            run.stderr = (proc.stderr or '')

            if proc.returncode != 0:
                run.verdict = SandboxResult.ERROR
                run.error = run.stderr.strip() or f"Sandbox process failed: rc={proc.returncode}"
            else:
                run.verdict = SandboxResult.SAFE
                run.side_effects = side_effects
                if run.side_effects:
                    run.verdict = SandboxResult.RISKY
        except subprocess.TimeoutExpired:
            run.duration = time.time() - t_start
            run.verdict = SandboxResult.ERROR
            run.error = f"Timeout {timeout}s превышен"

        self._runs.append(run)
        self._log(f"Sandbox run [{run.run_id}]: {run.verdict.value}, {run.duration:.2f}s")
        return run

    # ── Симуляция через Cognitive Core ────────────────────────────────────────

    def simulate_action(self, action: str, scenario: dict | None = None) -> SimulationRun:
        """
        Симулирует действие.
        SECURITY: Сначала детерминированные проверки (Governance + keyword),
        только потом LLM как дополнение. LLM не может переопределить UNSAFE.

        Args:
            action   -- описание действия
            scenario -- гипотетическое состояние среды для симуляции
        """
        import uuid
        run = SimulationRun(str(uuid.uuid4())[:8], action=action)
        t_start = time.time()

        # ШАГ 1: Детерминированная проверка Governance (всегда, без LLM)
        if self.governance:
            try:
                check = self.governance.check(action)
                if not check['allowed']:
                    run.verdict = SandboxResult.UNSAFE
                    run.error = check['reason']
                    run.side_effects.append(f"Запрещено политикой: {check['reason']}")
                    self._runs.append(run)
                    self._log(f"Симуляция BLOCKED (Governance): '{action[:50]}'")
                    return run
            except (TypeError, KeyError, AttributeError):
                # PolicyViolation — тоже UNSAFE
                run.verdict = SandboxResult.UNSAFE
                run.error = "Нарушение политики Governance"
                self._runs.append(run)
                return run

        # ШАГ 2: Детерминированный keyword-анализ действия
        deterministic_verdict = self._keyword_verdict(action)
        if deterministic_verdict == SandboxResult.UNSAFE:
            run.verdict = SandboxResult.UNSAFE
            run.error = "Обнаружены опасные ключевые слова в действии"
            run.side_effects = self._detect_side_effects(action)
            self._runs.append(run)
            self._log(f"Симуляция BLOCKED (keywords): '{action[:50]}'")
            return run

        # ШАГ 3: LLM-анализ (только если детерминированные проверки прошли)
        # LLM может добавить RISKY, но НЕ может снять UNSAFE
        if self.cognitive_core:
            env_context = ''
            if self.environment_model:
                try:
                    env_state = self.environment_model.get_full_state()
                    env_context = f"Состояние среды:\n{env_state}\n\n"
                except (AttributeError, TypeError):
                    pass
            if scenario:
                env_context += f"Сценарий:\n{scenario}\n\n"

            try:
                analysis = self.cognitive_core.reasoning(
                    f"{env_context}"
                    f"Симулируй выполнение действия: {action}\n\n"
                    f"Ответь:\n"
                    f"1. Что произойдёт (пошагово)\n"
                    f"2. Побочные эффекты\n"
                    f"3. Риски\n"
                    f"4. ВЕРДИКТ: SAFE / RISKY / UNSAFE"
                )
                run.stdout = str(analysis)
                run.duration = time.time() - t_start
                llm_verdict = self._parse_verdict(str(analysis))
                run.side_effects = self._extract_side_effects(str(analysis))

                # LLM может добавить RISKY, но НЕ может дать UNSAFE —
                # право на UNSAFE принадлежит только детерминированным проверкам
                # (governance + keywords). Нестабильный LLM не должен блокировать
                # целые циклы ложными UNSAFE-вердиктами.
                llm_capped = (SandboxResult.RISKY
                              if llm_verdict == SandboxResult.UNSAFE
                              else llm_verdict)
                run.verdict = self._worst_verdict(deterministic_verdict, llm_capped)
            except (AttributeError, TypeError, ValueError) as e:
                # LLM упал — используем детерминированный результат
                run.verdict = deterministic_verdict
                run.error = f"LLM ошибка: {e}"
        else:
            # Без LLM — только детерминированный результат
            run.verdict = deterministic_verdict

        run.duration = time.time() - t_start
        self._runs.append(run)
        self._log(f"Симуляция '{action[:50]}': {run.verdict.value}")
        return run

    def dry_run(self, command: str) -> SimulationRun:
        """
        Dry-run системной команды: предсказывает результат без выполнения.
        Использует Cognitive Core для анализа команды.
        """
        return self.simulate_action(f"Системная команда: {command}")

    # ── Проверка стратегии ────────────────────────────────────────────────────

    def test_strategy(self, strategy: str, test_cases: list[dict]) -> dict:
        """
        Тестирует стратегию на наборе сценариев.

        Args:
            strategy   — описание стратегии
            test_cases — список {'scenario': ..., 'expected_outcome': ...}

        Returns:
            {'passed': int, 'failed': int, 'runs': list}
        """
        results = []
        for case in test_cases:
            run = self.simulate_action(
                action=strategy,
                scenario=case.get('scenario', {}),
            )
            expected = case.get('expected_outcome', '')
            passed = expected.lower() in run.stdout.lower() if expected else True
            results.append({
                'run': run.to_dict(),
                'expected': expected,
                'passed': passed,
            })

        passed = sum(1 for r in results if r['passed'])
        self._log(f"Тест стратегии: {passed}/{len(results)} пройдено")
        return {
            'strategy': strategy[:100],
            'total': len(results),
            'passed': passed,
            'failed': len(results) - passed,
            'runs': results,
        }

    # ── История ───────────────────────────────────────────────────────────────

    def get_runs(self, verdict: SandboxResult | None = None, last_n: int | None = None) -> list[dict]:
        runs = self._runs
        if verdict:
            runs = [r for r in runs if r.verdict == verdict]
        if last_n is not None:
            runs = runs[-last_n:]
        return [r.to_dict() for r in runs]

    def summary(self) -> dict:
        from collections import Counter
        verdicts = Counter(r.verdict.value for r in self._runs)
        return {
            'total_runs': len(self._runs),
            'safe': verdicts.get(SandboxResult.SAFE.value, 0),
            'risky': verdicts.get(SandboxResult.RISKY.value, 0),
            'unsafe': verdicts.get(SandboxResult.UNSAFE.value, 0),
            'error': verdicts.get(SandboxResult.ERROR.value, 0),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _safe_builtins(self) -> dict:
        """Минимальный набор builtins для безопасного выполнения."""
        safe = ['print', 'len', 'range', 'enumerate', 'zip', 'map', 'filter',
                'sorted', 'reversed', 'list', 'dict', 'set', 'tuple',
            'str', 'int', 'float', 'bool', 'isinstance',
                'min', 'max', 'sum', 'abs', 'round', 'repr']
        import builtins
        return {name: getattr(builtins, name) for name in safe
                if hasattr(builtins, name)}

    def _detect_side_effects(self, code: str) -> list[str]:
        """AST-based detection of potentially dangerous operations."""
        effects: list[str] = []
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return effects
        _DANGEROUS_MODULES = {
            'os': 'операции ОС', 'subprocess': 'запуск процессов',
            'requests': 'сетевые запросы', 'urllib': 'сетевые запросы',
            'socket': 'сетевые соединения', 'shutil': 'операции с файловой системой',
            'http': 'сетевые запросы',
        }
        seen: set[str] = set()
        for node in ast.walk(tree):
            # Import checks
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = ''
                if isinstance(node, ast.ImportFrom) and node.module:
                    mod = node.module.split('.')[0]
                for alias in getattr(node, 'names', []):
                    name = (alias.name or '').split('.')[0]
                    for m in (mod, name):
                        if m in _DANGEROUS_MODULES and m not in seen:
                            seen.add(m)
                            effects.append(_DANGEROUS_MODULES[m])
            # open() call
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == 'open':
                    if 'файлов' not in seen:
                        seen.add('файлов')
                        effects.append('запись/чтение файлов')
            # os.* attribute access
            if isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == 'os':
                    if 'os' not in seen:
                        seen.add('os')
                        effects.append('операции ОС')
        return effects

    def _parse_verdict(self, text: str) -> SandboxResult:
        """Парсинг вердикта из LLM-ответа.
        Ищет явный ВЕРДИКТ: маркер, иначе — проверяет весь текст.
        Приоритет: UNSAFE > RISKY > SAFE. По умолчанию RISKY (осторожно)."""
        # Ищем явную строку вердикта (последнее вхождение — финальный ответ LLM)
        verdict_pattern = re.compile(
            r'(?:вердикт|verdict)\s*[:\-–]\s*(unsafe|risky|safe|'
            r'небезопасно|рискованно|безопасно)',
            re.IGNORECASE
        )
        matches = verdict_pattern.findall(text)
        if matches:
            last = matches[-1].lower()
            if last in ('unsafe', 'небезопасно'):
                return SandboxResult.UNSAFE
            if last in ('risky', 'рискованно'):
                return SandboxResult.RISKY
            if last in ('safe', 'безопасно'):
                return SandboxResult.SAFE

        # Fallback: анализируем весь текст с приоритетом UNSAFE > RISKY > SAFE
        text_lower = text.lower()
        if 'unsafe' in text_lower or 'небезопасно' in text_lower:
            return SandboxResult.UNSAFE
        if 'risky' in text_lower or 'высокий риск' in text_lower:
            return SandboxResult.RISKY
        if 'safe' in text_lower or 'безопасно' in text_lower:
            return SandboxResult.SAFE
        return SandboxResult.RISKY  # по умолчанию — осторожно

    # Ключевые слова для детерминированной классификации
    _UNSAFE_KEYWORDS = frozenset({
        'rm -rf', 'rm -r', 'del /f', 'mkfs', 'dd if=',
        'format c:', 'format d:', 'format /fs',        # точечные format-команды
        'drop table', 'drop database', 'truncate',
        'shutdown', 'reboot', 'halt', 'poweroff',
        'sudo', 'runas', 'su ',
        'curl', 'wget', 'nc ', 'ncat',
        'bash -c', 'sh -c', 'cmd /c',
        # Опасные варианты PowerShell (обфускация / загрузка кода)
        'powershell -enc', 'powershell -e ',
        'powershell -nop', 'powershell -w hidden',
        'powershell iex', 'powershell invoke-expression',
        'powershell downloadstring', 'powershell downloadfile',
        'exec(', 'eval(', '__import__',
        'kill all', 'killall',
        'bypass', 'override safety', 'disable monitoring',
        'ssh ', 'scp ', 'exfiltrate', 'credentials',
    })

    _RISKY_KEYWORDS = frozenset({
        'delete', 'remove', 'uninstall', 'drop',
        'install', 'deploy', 'push', 'publish',
        'modify', 'update', 'change', 'alter',
        'write to', 'overwrite', 'git push',
        'send', 'post', 'upload',
        'restart', 'stop service',
    })

    def _keyword_verdict(self, action: str) -> SandboxResult:
        """
        Детерминированная проверка по ключевым словам — без LLM.

        Для ключевых слов без пробелов используем границы слов (\b) чтобы
        избежать ложных совпадений (например 'format' внутри 'information').
        Для многословных команд ('rm -rf', 'bash -c') — подстрочный поиск.
        """
        a = action.lower()
        for kw in self._UNSAFE_KEYWORDS:
            if ' ' in kw or '(' in kw:
                # многословные / команды с аргументами — подстрочный поиск
                if kw in a:
                    return SandboxResult.UNSAFE
            else:
                # одиночные слова — граница слова, чтобы не матчить подстроки
                if re.search(r'\b' + re.escape(kw.strip()) + r'\b', a):
                    return SandboxResult.UNSAFE
        for kw in self._RISKY_KEYWORDS:
            if ' ' in kw or '(' in kw:
                if kw in a:
                    return SandboxResult.RISKY
            else:
                if re.search(r'\b' + re.escape(kw.strip()) + r'\b', a):
                    return SandboxResult.RISKY
        return SandboxResult.SAFE

    @staticmethod
    def _worst_verdict(v1: SandboxResult, v2: SandboxResult) -> SandboxResult:
        """Возвращает более строгий из двух вердиктов."""
        order = {SandboxResult.SAFE: 0, SandboxResult.RISKY: 1, SandboxResult.UNSAFE: 2}
        return v1 if order.get(v1, 1) >= order.get(v2, 1) else v2

    def _extract_side_effects(self, text: str) -> list[str]:
        lines = text.splitlines()
        effects = []
        capture = False
        for line in lines:
            if 'побочн' in line.lower() or 'side effect' in line.lower():
                capture = True
                continue
            if capture and line.strip().startswith(('-', '•', '*', '2.', '3.')):
                effects.append(line.strip('- •*').strip())
            elif capture and line.strip() == '':
                capture = False
        return effects[:5]

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='sandbox')
        else:
            print(f"[Sandbox] {message}")

    def export_state(self) -> dict:
        """Возвращает полное состояние для персистентности."""
        safe_runs = []
        for r in self._runs:
            try:
                d = r.to_dict()
                safe_runs.append({
                    'run_id':    str(d.get('run_id', ''))[:50],
                    'action':    str(d.get('action', ''))[:500],
                    'verdict':   str(d.get('verdict', 'safe')),
                    'error':     str(d.get('error') or '')[:300],
                    'duration':  float(d.get('duration', 0.0)),
                    'timestamp': float(d.get('timestamp', 0.0)),
                })
            except (OSError, IOError, TypeError, AttributeError, ValueError):
                pass
        return {'runs': safe_runs}

    def import_state(self, data: dict):
        """Восстанавливает состояние из персистентного хранилища."""
        for d in data.get('runs', []):
            try:
                run = SimulationRun(
                    run_id=d.get('run_id', ''),
                    action=d.get('action', ''),
                )
                verdict_str = d.get('verdict', 'safe')
                run.verdict = SandboxResult(verdict_str) if verdict_str in [
                    e.value for e in SandboxResult
                ] else SandboxResult.SAFE
                run.error = d.get('error') or None
                run.duration = float(d.get('duration', 0.0))
                run.timestamp = float(d.get('timestamp', 0.0))
                self._runs.append(run)
            except (KeyError, TypeError, AttributeError, ValueError):
                pass
