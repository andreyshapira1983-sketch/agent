# Self-Repair System (система самовосстановления) — Слой 11
# Архитектура автономного AI-агента
# Обнаружение ошибок, исправление кода, восстановление сервисов, перезапуск процессов.


import ast
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from enum import Enum


class FailureType(Enum):
    CODE_ERROR    = 'code_error'
    SERVICE_DOWN  = 'service_down'
    PROCESS_CRASH = 'process_crash'
    DATA_CORRUPT  = 'data_corrupt'
    TIMEOUT       = 'timeout'
    UNKNOWN       = 'unknown'


class RepairResult(Enum):
    FIXED       = 'fixed'
    ESCALATED   = 'escalated'   # передано человеку
    FAILED      = 'failed'      # не смогли починить


class SelfRepairSystem:
    """
    Self-Repair System — Слой 11.

    Функции:
        - обнаружение сбоев: коды ошибок, упавшие сервисы, битые данные
        - автоматическое исправление: перезапуск, откат, патч кода
        - решение что чинить автоматически, а что — эскалировать человеку
        - журнал всех инцидентов и восстановлений

    Используется:
        - Autonomous Loop (Слой 20)    — при ошибках в фазах цикла
        - Execution System (Слой 8)    — при падении команд
        - Human Approval (Слой 22)     — эскалация критичных сбоев
        - Monitoring (Слой 17)         — обнаружение по метрикам/логам
    """

    # Сбои которые можно чинить автоматически
    AUTO_REPAIR_TYPES = {
        FailureType.CODE_ERROR,    # ошибки кода — патчим через patch_code()
        FailureType.PROCESS_CRASH, FailureType.TIMEOUT, FailureType.SERVICE_DOWN,
        FailureType.UNKNOWN,   # sandbox-блокировки и прочие штатные сбои — не эскалируем
    }

    def __init__(
        self,
        execution_system=None,
        cognitive_core=None,
        human_approval=None,
        monitoring=None,
        sandbox=None,
        governance=None,
        auto_repair: bool = False,  # SECURITY: патчи требуют явного одобрения
        working_dir: str | None = None,
        arch_docs: list | None = None,
    ):
        self.execution = execution_system
        self.cognitive_core = cognitive_core
        self.human_approval = human_approval
        self.monitoring = monitoring
        self.sandbox = sandbox
        self.governance = governance
        self.auto_repair = auto_repair
        # Корень проекта — патчить разрешено только файлы внутри него
        self.working_dir = os.path.abspath(working_dir or os.getcwd())
        # Пути к архитектурным документам для инжекта в LLM-промпт
        self.arch_docs: list[str] = arch_docs or []

        self._incidents: list[dict] = []
        self._repair_handlers: dict[FailureType, Callable] = {}
        self._semantic_gate_last_seen: dict[str, float] = {}
        self._semantic_gate_cooldown_sec: int = 180

        # Обработчики по умолчанию
        self._register_defaults()

    # ── Основной интерфейс ────────────────────────────────────────────────────

    def report_failure(self, failure_type: FailureType, description: str,
                       component: str | None = None, context: dict | None = None) -> dict:
        """
        Принимает сообщение о сбое и запускает процесс восстановления.

        Returns:
            {'result': RepairResult, 'action_taken': str, 'incident_id': str}
        """
        import uuid
        incident_id = str(uuid.uuid4())[:8]
        self._log(f"Инцидент [{incident_id}] {failure_type.value}: {description} "
                  f"(компонент: {component or '—'})")

        incident = {
            'incident_id': incident_id,
            'failure_type': failure_type.value,
            'description': description,
            'component': component,
            'context': context or {},
            'timestamp': time.time(),
            'result': None,
            'action_taken': None,
        }

        # Решаем: автоматически или эскалируем
        if self.auto_repair and failure_type in self.AUTO_REPAIR_TYPES:
            result, action = self._attempt_auto_repair(failure_type, description, component, context)
        else:
            result, action = self._escalate(failure_type, description, incident_id)

        incident['result'] = result.value
        incident['action_taken'] = action
        self._incidents.append(incident)

        self._log(f"Инцидент [{incident_id}] → {result.value}: {action}")
        return incident

    def detect_from_logs(self, logs: list[dict]) -> list[dict]:
        """
        Сканирует логи на признаки сбоев и автоматически репортит их.

        Args:
            logs — список LogEntry.to_dict() из Monitoring (Слой 17)
        """
        triggered = []
        for entry in logs:
            level = entry.get('level', '')
            message = entry.get('message', '').lower()
            source = entry.get('source', '')

            if level in ('ERROR', 'CRITICAL'):
                ftype = self.classify_from_message(message)
                incident = self.report_failure(
                    ftype,
                    description=entry.get('message', ''),
                    component=source,
                    context={'log_entry': entry},
                )
                triggered.append(incident)

        return triggered

    def register_handler(self, failure_type: FailureType, handler: Callable):
        """Регистрирует пользовательский обработчик для типа сбоя."""
        self._repair_handlers[failure_type] = handler

    # ── Журнал инцидентов ─────────────────────────────────────────────────────

    def get_incidents(self, failure_type: FailureType | None = None) -> list[dict]:
        if failure_type:
            return [i for i in self._incidents if i['failure_type'] == failure_type.value]
        return list(self._incidents)

    def get_unresolved(self) -> list[dict]:
        return [i for i in self._incidents if i['result'] == RepairResult.FAILED.value]

    def summary(self) -> dict:
        from collections import Counter
        results = Counter(i['result'] for i in self._incidents)
        return {
            'total_incidents': len(self._incidents),
            'fixed': results.get(RepairResult.FIXED.value, 0),
            'escalated': results.get(RepairResult.ESCALATED.value, 0),
            'failed': results.get(RepairResult.FAILED.value, 0),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _attempt_auto_repair(self, failure_type, description, component, context):
        # SECURITY (VULN-08): Валидация component name — ВСЕГДА, до любого использования
        if component and not re.match(r'^[a-zA-Z0-9_.-]+$', component):
            return RepairResult.FAILED, f"Невалидное имя компонента: '{component}' (injection attempt?)"

        # Семантический gate — это управляемый отказ безопасности.
        # Не эскалируем в LLM бесконечно, а возвращаем короткое локальное действие.
        if self._is_semantic_gate_issue(description):
            return self._handle_semantic_gate_issue(description, component, context)

        handler = self._repair_handlers.get(failure_type)
        if handler:
            try:
                action = handler(description=description, component=component, context=context)
                return RepairResult.FIXED, action or 'Автообработчик выполнен'
            except (TypeError, ValueError, AttributeError, RuntimeError) as e:
                return RepairResult.FAILED, f"Автообработчик упал: {e}"

        # Дефолтные стратегии
        if failure_type == FailureType.PROCESS_CRASH and self.execution and component:
            cmd = f"sc start {component}" if self._is_windows() else f"systemctl restart {component}"

            # Проверяем команду в sandbox перед выполнением
            if self.sandbox:
                from environment.sandbox import SandboxResult
                dry = self.sandbox.dry_run(cmd)
                if dry.verdict == SandboxResult.UNSAFE:
                    return RepairResult.FAILED, (
                        f"Ремонт '{component}' заблокирован sandbox: {dry.error}"
                    )
                self._log(f"[SelfRepair] Sandbox dry_run: {dry.verdict.value} для '{cmd}'")

            result = self.execution.run_command(cmd)
            ok = result.status.value == 'success'
            return (RepairResult.FIXED if ok else RepairResult.FAILED,
                    f"Перезапуск '{component}': {'успешно' if ok else 'не удалось'}")

        if failure_type == FailureType.TIMEOUT:
            return RepairResult.FIXED, 'Таймаут зафиксирован, следующий цикл продолжит работу'

        # CODE_ERROR — сначала пробуем автопатч кода, не задействуя человека
        if failure_type == FailureType.CODE_ERROR and component:
            _desc_lower = description.lower()
            # NameError с кириллическим именем — LLM написал псевдокод на русском
            import re as _rim2
            _ne = _rim2.search(r"name '([^']+)' is not defined", description)
            if _ne:
                _varname = _ne.group(1)
                if any('\u0400' <= c <= '\u04ff' for c in _varname):
                    return RepairResult.ESCALATED, (
                        f"Сгенерированный код использует кириллическую переменную "
                        f"'{_varname}' как Python-идентификатор — это псевдокод, "
                        f"не настоящий Python. В следующий раз используй только "
                        f"латинские имена переменных (например: code, result, data)."
                    )
            # AttributeError: 'float' object has no attribute '...'
            # Типичная ошибка: cpu = psutil.cpu_percent() возвращает float,
            # а код делает cpu.percent — .percent уже является float.
            if "'float' object has no attribute" in description:
                import re as _rim3
                _attr_m = _rim3.search(r"has no attribute '([^']+)'", description)
                _attr = _attr_m.group(1) if _attr_m else 'percent'
                return RepairResult.ESCALATED, (
                    f"'float' object has no attribute '{_attr}': "
                    f"psutil.cpu_percent() и psutil.virtual_memory().percent уже "
                    f"возвращают float (число). Не вызывай .{_attr} второй раз. "
                    f"Пример: mem_pct = psutil.virtual_memory().percent  # уже float"
                )
            # Заблокированный import в sandbox — прямая подсказка, CognCore тут ошибётся
            if 'запрещён в sandbox' in _desc_lower and 'импорт' in _desc_lower:
                import re as _rim
                _m = _rim.search(r"[Ии]мпорт '([^']+)'", description)
                _lib = _m.group(1) if _m else '?'
                return RepairResult.ESCALATED, (
                    f"Библиотека '{_lib}' заблокирована в sandbox. "
                    f"Разрешённые: os, pathlib, json, csv, sqlite3, psutil, re, "
                    f"datetime, collections, itertools. "
                    f"Вместо графиков используй текстовые таблицы или запись CSV в файл."
                )
            ok, action = self.patch_code(component, description)
            if ok:
                return RepairResult.FIXED, action
            # patch_code не смог (нет .py файла / вне working_dir) — Cognitive Core как fallback
            if self.cognitive_core:
                suggestion = self.cognitive_core.problem_solving(
                    f"Ошибка кода в компоненте '{component}':\n{description}\n"
                    f"( {action} )\nКак исправить?"
                )
                return RepairResult.ESCALATED, f"Cognitive Core предложил: {suggestion}"
            return RepairResult.FAILED, action

        # Cognitive Core предлагает шаги восстановления (для прочих типов)
        if self.cognitive_core:
            suggestion = self.cognitive_core.problem_solving(
                f"Как восстановить компонент '{component}' после ошибки: {description}?"
            )
            return RepairResult.ESCALATED, f"Cognitive Core предложил: {suggestion}"

        return RepairResult.FAILED, 'Нет подходящего обработчика'

    @staticmethod
    def _is_semantic_gate_issue(description: str) -> bool:
        msg = str(description or '').lower()
        return (
            'semantic_gate_aborted_by_fail_closed' in msg
            or 'semantic_gate_rejected' in msg
            or 'domain mismatch goal=' in msg
        )

    def _handle_semantic_gate_issue(self, description: str, component: str | None,
                                    context: dict | None) -> tuple[RepairResult, str]:
        msg = str(description or '')
        msg_lower = msg.lower()

        goal_domain = '?'
        action_domain = '?'
        m = re.search(r'domain mismatch\s+goal=([^,\s]+),\s*action=([^\s\]\)]+)', msg_lower)
        if m:
            goal_domain = m.group(1).strip()
            action_domain = m.group(2).strip()

        cycle_id = ''
        if isinstance(context, dict):
            cycle_id = str(context.get('cycle_id', ''))
        dedup_key = f"{component or '?'}|{goal_domain}|{action_domain}|{'aborted' if 'aborted_by_fail_closed' in msg_lower else 'rejected'}"
        now = time.time()
        last = float(self._semantic_gate_last_seen.get(dedup_key, 0.0))
        self._semantic_gate_last_seen[dedup_key] = now

        # Защита от спама одинаковой эскалацией внутри короткого окна.
        if now - last < self._semantic_gate_cooldown_sec:
            left = int(self._semantic_gate_cooldown_sec - (now - last))
            return RepairResult.FIXED, (
                f"SEMANTIC_GATE уже обработан; повтор подавлен на {left}с "
                f"(cycle={cycle_id or '?'}, component={component or '?'})"
            )

        if 'aborted_by_fail_closed' in msg_lower:
            return RepairResult.FIXED, (
                "SEMANTIC_GATE fail-closed: переключаюсь в мягкий ремонт без tool-вызовов "
                f"(cycle={cycle_id or '?'}, component={component or '?'})"
            )

        if goal_domain != '?' and action_domain != '?':
            return RepairResult.FIXED, (
                "SEMANTIC_GATE domain mismatch: исключаю нерелевантный домен "
                f"action={action_domain} для цели goal={goal_domain} в текущем ремонте"
            )

        return RepairResult.FIXED, "SEMANTIC_GATE отклонение обработано локально: реплан без эскалации"

    # ── Безопасный патч кода ──────────────────────────────────────────────────

    def patch_code(self, file_path: str, error_description: str) -> tuple[bool, str]:
        """
        Безопасно патчит Python-файл при ошибке:
          1. Проверяет что файл внутри working_dir (защита от path traversal)
          2. Читает оригинальный код
          3. Просит LLM исправить ошибку
          4. Извлекает только Python-код из ответа
          5. Проверяет синтаксис через ast.parse
          6. Создаёт резервную копию (.bak)
          7. Записывает исправленный файл
          8. Финальная проверка py_compile — если фейл, откат из .bak

        Returns:
            (success: bool, message: str)
        """
        if not self.cognitive_core:
            return False, 'patch_code: cognitive_core не подключён'

        # ── 1. Безопасность: файл должен быть внутри working_dir ──
        try:
            abs_path = os.path.realpath(os.path.abspath(file_path))
        except (OSError, ValueError) as e:
            return False, f'patch_code: плохой путь: {e}'

        if not abs_path.startswith(self.working_dir + os.sep) and abs_path != self.working_dir:
            return False, (f'patch_code: файл {abs_path!r} вне разрешённой зоны '
                           f'{self.working_dir!r} — патч запрещён')

        if not abs_path.endswith('.py'):
            return False, 'patch_code: патчинг разрешён только для .py файлов'

        # ── 1b. Защищённые директории: self-repair НЕ может менять safety/ ──
        _PROTECTED_PREFIXES = (
            os.path.join(self.working_dir, 'safety') + os.sep,
        )
        for _pfx in _PROTECTED_PREFIXES:
            if abs_path.startswith(_pfx):
                return False, (f'patch_code: файл {abs_path!r} находится в защищённой '
                               f'директории — self-repair отклонён')

        # ── 2. Читаем оригинальный код ──
        try:
            with open(abs_path, 'r', encoding='utf-8') as f:
                original_code = f.read()
        except (OSError, IOError) as e:
            return False, f'patch_code: не удалось прочитать {abs_path}: {e}'

        # ── 3. Собираем контекст архитектуры и интерфейсов ──
        arch_hint = self._load_arch_digest()
        own_iface = self._extract_interfaces(abs_path)
        import_ifaces = self._collect_import_interfaces(abs_path, original_code)

        ctx_parts = []
        if arch_hint:
            ctx_parts.append(f"АРХИТЕКТУРА ПРОЕКТА (ключевые правила):\n{arch_hint}")
        if own_iface:
            ctx_parts.append(f"ИНТЕРФЕЙСЫ ТЕКУЩЕГО ФАЙЛА (не менять сигнатуры):\n{own_iface}")
        if import_ifaces:
            ctx_parts.append(f"ИНТЕРФЕЙСЫ ЗАВИСИМОСТЕЙ (модули от которых зависит файл):\n{import_ifaces}")
        context_block = "\n\n".join(ctx_parts)

        # ── 4. Просим LLM исправить с полным контекстом ──
        prompt = (
            f"Ты — Python-разработчик. Ты чинишь файл в многослойном автономном AI-агенте.\n"
            f"Код должен соответствовать архитектуре проекта — не изобретай новых паттернов.\n\n"
            f"{context_block}\n\n"
            f"ОШИБКА В ФАЙЛЕ: {error_description}\n\n"
            f"КОД ДЛЯ ИСПРАВЛЕНИЯ ({os.path.basename(abs_path)}):\n"
            f"```python\n{original_code[:6000]}\n```\n\n"
            f"ПРАВИЛА:\n"
            f"- Исправь только причину ошибки, ничего лишнего\n"
            f"- Сохрани все имена классов, методов, их сигнатуры в точности\n"
            f"- Сохрани все импорты зависимостей которые есть сейчас\n"
            f"- Верни ПОЛНЫЙ исправленный файл в блоке ```python ... ```\n"
            f"- Никаких пояснений вне блока кода"
        )

        try:
            raw = self.cognitive_core.llm.infer(prompt)
        except (AttributeError, RuntimeError) as e:
            return False, f'patch_code: LLM недоступен: {e}'

        # ── 5. Извлекаем Python-код из ответа ──
        patched_code = self._extract_python_block(str(raw))
        if not patched_code or len(patched_code.strip()) < 20:
            return False, 'patch_code: LLM не вернул корректный код'

        # Защита: патч не должен быть короче 30% оригинала (LLM обрезал?)
        if len(patched_code) < len(original_code) * 0.3:
            return False, 'patch_code: ответ LLM подозрительно короткий — отклонено'

        # ── 5. Проверяем синтаксис через AST ──
        try:
            ast.parse(patched_code)
        except SyntaxError as e:
            return False, f'patch_code: SyntaxError в патче — откат: {e}'

        # ── 5a. Governance gate: проверяем патч через политику ──
        if self.governance:
            try:
                gov_result = self.governance.check(
                    f"patch_code: запись патча в {abs_path}",
                    context={'patched_file': abs_path, 'code_len': len(patched_code)},
                )
                if not gov_result.get('allowed', True):
                    return False, f"patch_code: заблокировано Governance — {gov_result.get('reason', '?')}"
            except Exception as gov_err:
                return False, f'patch_code: Governance отклонил патч: {gov_err}'

        # ── 5b. CodeValidator: статическая проверка опасных конструкций ──
        try:
            from tools.tool_layer import CodeValidator
            cv_ok, cv_reason = CodeValidator.validate(patched_code)
            if not cv_ok:
                return False, f'patch_code: CodeValidator отклонил патч — {cv_reason}'
        except ImportError:
            pass  # CodeValidator недоступен — пропускаем

        # ── 5c. MANDATORY sandbox diff review ──
        # Патч ОБЯЗАН пройти sandbox-проверку до записи на диск
        if not self.sandbox:
            return False, 'patch_code: sandbox обязателен — патч без sandbox запрещён'

        from environment.sandbox import SandboxResult as SR
        sandbox_run = self.sandbox.run_code(patched_code)
        if sandbox_run.verdict == SR.UNSAFE:
            return False, (f'patch_code: sandbox UNSAFE — патч отклонён: '
                           f'{sandbox_run.error}')
        diff_action = (f"patch_code: применение патча к {os.path.basename(abs_path)}, "
                       f"delta {len(patched_code) - len(original_code):+d} символов, "
                       f"sandbox={sandbox_run.verdict.value}")
        sim_run = self.sandbox.simulate_action(diff_action)
        if sim_run.verdict == SR.UNSAFE:
            return False, (f'patch_code: sandbox diff review UNSAFE — '
                           f'{sim_run.error}')
        self._log(f'patch_code: sandbox review passed '
                  f'(code={sandbox_run.verdict.value}, sim={sim_run.verdict.value})')

        # ── 5d. Human Approval gate — патч ВСЕГДА требует одобрения ──
        if self.human_approval:
            _diff_preview = (
                f"Файл: {os.path.basename(abs_path)}\n"
                f"Ошибка: {error_description[:200]}\n"
                f"Оригинал: {len(original_code)} символов\n"
                f"Патч: {len(patched_code)} символов "
                f"(delta: {len(patched_code) - len(original_code):+d})\n"
                f"Sandbox: code={sandbox_run.verdict.value}, "
                f"sim={sim_run.verdict.value}"
            )
            approved = self.human_approval.request_approval(
                'patch_code', _diff_preview)
            if not approved:
                return False, 'patch_code: патч отклонён человеком'

        # ── 5e. Подпись патча HMAC-SHA256 ──
        from self_repair.patch_signing import sign_patch, verify_patch
        patch_signature = sign_patch(
            abs_path, original_code, patched_code,
            patch_source='self_repair',
        )

        # ── 6. Бэкап оригинала ──
        bak_path = abs_path + '.bak'
        try:
            shutil.copy2(abs_path, bak_path)
        except (OSError, IOError) as e:
            return False, f'patch_code: не удалось создать бэкап: {e}'

        # ── 7. Записываем исправленный файл ──
        try:
            with open(abs_path, 'w', encoding='utf-8') as f:
                f.write(patched_code)
        except (OSError, IOError) as e:
            shutil.copy2(bak_path, abs_path)  # откат
            return False, f'patch_code: ошибка записи, откат: {e}'

        # ── 7b. Верификация подписи записанного файла ──
        try:
            with open(abs_path, 'r', encoding='utf-8') as f:
                written_code = f.read()
            if not verify_patch(abs_path, original_code, written_code, patch_signature):
                shutil.copy2(bak_path, abs_path)
                return False, 'patch_code: подпись не совпала после записи — файл повреждён, откат'
        except (OSError, IOError) as e:
            shutil.copy2(bak_path, abs_path)
            return False, f'patch_code: ошибка верификации подписи, откат: {e}'

        # ── 8. Финальная валидация через py_compile ──
        from safety.secrets_proxy import safe_env as _safe_env
        check = subprocess.run(
            [sys.executable, '-m', 'py_compile', abs_path],
            capture_output=True, text=True, timeout=15, check=False,
            env=_safe_env(),
        )
        if check.returncode != 0:
            shutil.copy2(bak_path, abs_path)  # откат
            return False, (f'patch_code: py_compile провалился — оригинал восстановлен.\n'
                           f'{check.stderr[:300]}')

        smoke_ok, smoke_msg = self._run_core_smoke_if_needed(abs_path, bak_path)
        if not smoke_ok:
            if os.path.exists(bak_path):
                shutil.copy2(bak_path, abs_path)
            return False, smoke_msg

        # ── 9. Pytest-валидация: прогоняем тесты для пропатченного модуля ──
        test_ok, test_msg = self._run_tests_for_file(abs_path, bak_path)
        if not test_ok:
            if os.path.exists(bak_path):
                shutil.copy2(bak_path, abs_path)
            return False, test_msg

        fname = os.path.basename(abs_path)
        self._log(f'patch_code: {fname} успешно исправлен (бэкап: {fname}.bak)')
        return True, f'Файл {fname} исправлен LLM, синтаксис проверен, тесты пройдены, бэкап сохранён'

    def _run_core_smoke_if_needed(self, abs_path: str, _bak_path: str) -> tuple[bool, str]:
        """Автозапуск smoke_runner после правок файлов ядра."""
        core_dir = os.path.join(self.working_dir, 'core')
        smoke_runner = os.path.join(self.working_dir, 'smoke_runner.py')
        if not abs_path.startswith(core_dir + os.sep):
            return True, 'smoke: не требуется вне core/'
        if not os.path.isfile(smoke_runner):
            return True, 'smoke_runner.py отсутствует — smoke пропущен'

        self._log(f"[SelfRepair] Запускаю smoke_runner после правки ядра: {os.path.basename(abs_path)}")
        from safety.secrets_proxy import safe_env as _safe_env
        result = subprocess.run(
            [sys.executable, smoke_runner],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            cwd=self.working_dir,
            env=_safe_env(),
        )
        if result.returncode == 0:
            self._record_core_smoke_event(
                'core_smoke_passed',
                file_path=abs_path,
                output=(result.stdout or 'OK')[:500],
            )
            self._log('[SelfRepair] smoke_runner завершился успешно')
            return True, 'smoke_runner ok'

        summary = (result.stdout or result.stderr or 'Нет вывода')[:500]
        self._record_core_smoke_event(
            'core_smoke_failed',
            file_path=abs_path,
            output=summary,
        )
        self._log(f'[SelfRepair] smoke_runner провалился, откат: {summary}')
        return False, f'smoke_runner провалился после правки ядра: {summary}'

    def _run_tests_for_file(self, abs_path: str, bak_path: str) -> tuple[bool, str]:
        """
        Запускает pytest для тестов, относящихся к пропатченному файлу.
        Ищет tests/test_<module>.py. Если тестов нет — пропускает (не блокирует).
        При провале тестов — откат не выполняется здесь, он на вызывающей стороне.
        """
        basename = os.path.basename(abs_path)  # e.g. "sandbox.py"
        module_name = os.path.splitext(basename)[0]
        tests_dir = os.path.join(self.working_dir, 'tests')

        # Ищем файл вида tests/test_<module>.py
        test_file = os.path.join(tests_dir, f'test_{module_name}.py')
        if not os.path.isfile(test_file):
            return True, f'tests: тест-файл test_{module_name}.py не найден — пропускаем'

        self._log(f'[SelfRepair] Запускаю pytest для {os.path.basename(test_file)} '
                  f'после патча {basename}')
        try:
            from safety.secrets_proxy import safe_env as _safe_env
            result = subprocess.run(
                [sys.executable, '-m', 'pytest', test_file, '-x', '--tb=short', '-q'],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                cwd=self.working_dir,
                env=_safe_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            self._log(f'[SelfRepair] pytest таймаут/ошибка: {e}')
            return True, f'tests: pytest не удалось запустить ({e}) — пропускаем'

        if result.returncode == 0:
            self._log(f'[SelfRepair] pytest прошёл для {basename}')
            return True, f'tests: pytest passed для test_{module_name}.py'

        summary = (result.stdout or result.stderr or 'Нет вывода')[:500]
        self._log(f'[SelfRepair] pytest провалился для {basename}: {summary}')
        return False, (f'patch_code: pytest провалился для test_{module_name}.py — '
                       f'оригинал восстановлен.\n{summary}')

    def _record_core_smoke_event(self, event_name: str, file_path: str, output: str = '') -> None:
        """Пишет отдельное событие smoke-проверки в логи и метрики."""
        if not self.monitoring:
            return
        data = {
            'event': event_name,
            'file_path': file_path,
            'output': output[:500],
        }
        self.monitoring.increment(event_name)
        if event_name == 'core_smoke_passed':
            self.monitoring.info(event_name, source='self_repair', data=data)
        else:
            self.monitoring.warning(event_name, source='self_repair', data=data)

    # ── Контекст для LLM ──────────────────────────────────────────────────────

    def _load_arch_digest(self) -> str:
        """Читает архитектурные документы и возвращает ключевые строки (не более 40)."""
        lines_out: list[str] = []
        keywords = ('слой', 'layer', 'import', 'class ', 'def ', '──', 'связан',
                    'использует', 'цель', 'функци', 'интерфейс', 'автоном')
        for doc_path in self.arch_docs:
            try:
                with open(doc_path, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        stripped = line.strip()
                        if stripped and any(kw in stripped.lower() for kw in keywords):
                            lines_out.append(stripped)
                            if len(lines_out) >= 40:
                                break
            except (OSError, IOError):
                pass
        return '\n'.join(lines_out)

    @staticmethod
    def _extract_interfaces(file_path: str) -> str:
        """AST: извлекает имена классов и сигнатуры методов из файла."""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                source = f.read()
            tree = ast.parse(source, filename=file_path)
        except (OSError, SyntaxError):
            return ''

        lines: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [getattr(b, 'id', '?') for b in node.bases]
                lines.append(f"class {node.name}({', '.join(bases)}):")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in node.args.args]
                lines.append(f"  def {node.name}({', '.join(args)})")
        return '\n'.join(lines[:60])

    def _collect_import_interfaces(self, file_path: str, source: str) -> str:
        """AST: находит локальные импорты файла и извлекает их интерфейсы."""
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            return ''

        result_parts: list[str] = []
        file_dir = os.path.dirname(file_path)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                # Только относительные или проектные импорты (не stdlib)
                mod_parts = node.module.split('.')
                candidate = os.path.join(self.working_dir, *mod_parts) + '.py'
                if not os.path.isfile(candidate):
                    # Попробуем относительно текущей директории
                    candidate = os.path.join(file_dir, *mod_parts) + '.py'
                if os.path.isfile(candidate):
                    iface = self._extract_interfaces(candidate)
                    if iface:
                        result_parts.append(
                            f"# {node.module}\n{iface}"
                        )
                if len(result_parts) >= 5:  # не более 5 зависимостей
                    break

        return '\n\n'.join(result_parts)

    @staticmethod
    def _extract_python_block(text: str) -> str:
        """Извлекает Python-код из блока ```python ... ``` или первого ```...```."""
        # Сначала ищем ```python
        m = re.search(r'```python\s*\n(.*?)\n```', text, re.DOTALL)
        if m:
            return m.group(1).strip()
        # Запасной: любой ```...
        m = re.search(r'```\s*\n(.*?)\n```', text, re.DOTALL)
        if m:
            return m.group(1).strip()
        # Если нет блока — весь текст как есть (на случай прямого кода)
        if text.strip().startswith(('import ', 'from ', '#', 'class ', 'def ')):
            return text.strip()
        return ''

    def _escalate(self, failure_type, description, incident_id) -> tuple:
        msg = f"Инцидент [{incident_id}]: {failure_type.value}\n{description}"
        if self.human_approval:
            self.human_approval.request_approval('incident_escalation', msg)
        self._log(f"Эскалация инцидента [{incident_id}] человеку")
        return RepairResult.ESCALATED, 'Передано на ручное восстановление'

    def classify_from_message(self, message: str) -> FailureType:
        msg = message.lower()
        if any(w in msg for w in ('timeout', 'timed out', 'истёк')):
            return FailureType.TIMEOUT
        if any(w in msg for w in ('crash', 'segfault', 'killed', 'упал')):
            return FailureType.PROCESS_CRASH
        if any(w in msg for w in ('service', 'connection refused', 'unavailable')):
            return FailureType.SERVICE_DOWN
        if any(w in msg for w in ('corrupt', 'invalid data', 'parse error')):
            return FailureType.DATA_CORRUPT
        # [FILE:...] — аннотация traceback из PythonRuntimeTool → всегда CODE_ERROR
        if '[file:' in msg:
            return FailureType.CODE_ERROR
        # Ошибки кода (английские и русские формы)
        if any(w in msg for w in ('syntaxerror', 'nameerror', 'typeerror', 'attributeerror',
                                  'unexpected keyword', 'exception', 'got an unexpected',
                                  'is not defined', 'синтаксическая ошибка',
                                  'индентационная ошибка', 'indentationerror')):
            return FailureType.CODE_ERROR
        # «Код заблокирован: Синтаксическая ошибка» — тоже CODE_ERROR, не sandbox-блокировка
        if 'заблокирован' in msg and 'ошибка' in msg:
            return FailureType.CODE_ERROR
        # «Импорт '...' запрещён в sandbox» — неверный выбор библиотеки → CODE_ERROR
        if 'запрещён в sandbox' in msg and 'импорт' in msg:
            return FailureType.CODE_ERROR
        if any(w in msg for w in ('sandbox', 'simulate', 'заблокирован', 'unsafe', 'policyviolation', 'blocked')):
            return FailureType.UNKNOWN  # sandbox-блокировка — штатная ситуация
        return FailureType.UNKNOWN

    def _register_defaults(self):
        """Регистрирует дефолтные обработчики для типичных сбоев."""
        def _handle_timeout(_description: str, component: str | None, _context: dict) -> str:
            return f"Таймаут в '{component or '?'}' зафиксирован, next цикл продолжит"

        def _handle_service_down(_description: str, component: str | None, _context: dict) -> str:
            return f"SERVICE_DOWN '{component or '?'}': ожидание восстановления (retry next cycle)"

        def _handle_data_corrupt(_description: str, component: str | None, _context: dict) -> str:
            return f"DATA_CORRUPT в '{component or '?'}': помечено для проверки целостности"

        self._repair_handlers[FailureType.TIMEOUT]       = _handle_timeout
        self._repair_handlers[FailureType.SERVICE_DOWN]  = _handle_service_down
        self._repair_handlers[FailureType.DATA_CORRUPT]  = _handle_data_corrupt

    def _is_windows(self) -> bool:
        return os.name == 'nt'

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.warning(message, source='self_repair')
        else:
            print(f"[SelfRepair] {message}")