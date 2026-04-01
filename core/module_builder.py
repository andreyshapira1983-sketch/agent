# Module Builder — динамическое создание новых модулей — Слой 7/12
# Архитектура автономного AI-агента
# Позволяет агенту строить новые .py файлы (агентов, инструментов, утилит)
# прямо во время работы, без остановки и участия человека.
#
# Цепочка: описание → LLM генерирует класс → ast.parse → py_compile
#          → SandboxLayer тест → запись на диск → importlib → регистрация
#
# SECURITY: файлы создаются ТОЛЬКО внутри working_dir.
# Sandbox-тест обязателен перед записью.
# pylint: disable=broad-except

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import types


# ── Результат сборки модуля ───────────────────────────────────────────────────

class BuildStatus:
    SUCCESS  = 'success'
    FAILED   = 'failed'
    REJECTED = 'rejected'   # sandbox заблокировал


class ModuleBuildResult:
    """Итог одной сборки модуля."""

    def __init__(self, name: str):
        self.name = name
        self.status: str = BuildStatus.FAILED
        self.file_path: str | None = None
        self.class_name: str | None = None
        self.module_object: types.ModuleType | None = None  # загруженный Python-модуль
        self.error: str | None = None
        self.generated_code: str | None = None
        self.duration: float = 0.0
        self.timestamp = time.time()

    @property
    def ok(self) -> bool:
        return self.status == BuildStatus.SUCCESS

    def to_dict(self) -> dict:
        return {
            'name':       self.name,
            'status':     self.status,
            'file_path':  self.file_path,
            'class_name': self.class_name,
            'error':      self.error,
            'duration':   round(self.duration, 2),
            'timestamp':  self.timestamp,
        }


# ── Реестр динамически созданных модулей ──────────────────────────────────────

class DynamicRegistry:
    """
    Персистентный реестр всего что агент создал сам.
    Хранится в <working_dir>/dynamic_registry.json.
    При перезапуске агента все записанные модули подгружаются автоматически.
    """

    def __init__(self, path: str):
        self._path = path
        self._data: dict = {'agents': [], 'tools': [], 'modules': []}
        self._load()

    # ── Чтение / запись ───────────────────────────────────────────────────────

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                # merge — добавляем только известные ключи
                for key in ('agents', 'tools', 'modules'):
                    if key in loaded and isinstance(loaded[key], list):
                        self._data[key] = loaded[key]
            except (OSError, json.JSONDecodeError):
                pass  # испорченный файл — стартуем с чистого реестра

    def save(self):
        try:
            os.makedirs(os.path.dirname(self._path) or '.', exist_ok=True)
            tmp = self._path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except (OSError, IOError):
            pass  # не критично — при следующем старте создастся снова

    # ── Добавление / поиск ────────────────────────────────────────────────────

    def register(self, category: str, entry: dict):
        """
        Добавляет запись. category: 'agents' | 'tools' | 'modules'.
        entry обязан иметь ключ 'name'.
        """
        if category not in self._data:
            self._data[category] = []
        # Обновляем если уже есть (по имени)
        existing = [x for x in self._data[category] if x.get('name') == entry.get('name')]
        for old in existing:
            self._data[category].remove(old)
        self._data[category].append(entry)
        self.save()

    def get_all(self, category: str) -> list[dict]:
        return list(self._data.get(category, []))

    def find(self, category: str, name: str) -> dict | None:
        for entry in self._data.get(category, []):
            if entry.get('name') == name:
                return entry
        return None

    def remove(self, category: str, name: str):
        self._data[category] = [
            x for x in self._data.get(category, []) if x.get('name') != name
        ]
        self.save()


# ── Основной класс ────────────────────────────────────────────────────────────

class ModuleBuilder:
    """
    Module Builder — динамическое создание новых .py модулей агентом.

    Использует:
        Cognitive Core (Слой 3)  — генерация кода
        SandboxLayer   (Слой 28) — тест перед записью
        working_dir              — корень проекта (файлы только сюда)

    Методы:
        build_agent(name, description)  — строит нового агента
        build_tool(name, description)   — строит новый инструмент
        build_module(name, description, target_dir)  — строит произвольный модуль
        load_all_from_registry()        — при старте подгружает всё ранее созданное
    """

    # ── Правила качества кода — внедряются в каждый промпт ─────────────────
    _CODE_QUALITY_RULES = (
        "CRITICAL — ПРАВИЛА КАЧЕСТВА (нарушение = отклонение файла):"
        "\n"
        "\n1. ЗАГЛУШКИ ЗАПРЕЩЕНЫ. Не пиши:\n"
        "   - `pass` в теле метода без реального кода после\n"
        "   - `raise NotImplementedError` / `raise NotImplemented`\n"
        "   - `# TODO`, `# FIXME`, `# placeholder`, `# stub`\n"
        "   - `return None  # TODO implement`, `return {}  # placeholder`\n"
        "   - \"... (в теле метода)\" / \"...остальной код\"\n"
        "\n2. ОТЛАДОЧНЫЙ КОД ЗАПРЕЩЁН. Не оставляй в финальном коде:\n"
        "   - `print(`, `print(debug`, `print(f\"DEBUG`, `print(\"ERROR`\n"
        "   - `pdb.set_trace()`, `breakpoint()`, `import pdb`\n"
        "   - `logging.debug(\"test`, `logger.debug(\"check`\n"
        "   - временные `time.sleep(5)` для ожидания / тестового ожидания\n"
        "\n3. ПОЛНЫЕ РЕАЛИЗАЦИИ. Каждый метод должен работать:\n"
        "   - Весь код должен быть исполняемым (не скелетом)\n"
        "   - if/else должны обрабатывать реальные случаи, не просто `return None`\n"
        "   - Исключения except должны что-то делать (логировать/возвращать error-результат), не `pass`\n"
        "\n4. НЕ ПЕРЕЗАПИСЫВАЙ СУЩЕСТВУЮЩИЙ КОД:\n"
        "   - Если файл уже существует, сохрани все уже работающие классы/методы\n"
        "   - Добавляй новые методы рядом со старыми, не вместо них\n"
        "   - Не переименовывай существующие методы и не меняй их сигнатуры\n"
        "\n5. ИМПОРТЫ. Используй только реальные stdlib / установленные пакеты:\n"
        "   - Стандартные: os, sys, re, json, time, pathlib, collections, itertools, typing\n"
        "   - Установленные: requests, anthropic, openai, ddgs, psutil, numpy\n"
        "   - Не выдумывай import для несуществующих пакетов\n"
        "\n6. СТРУКТУРА КЛАССА:\n"
        "   - `__init__` должен инициализировать все атрибуты\n"
        "   - Публичные методы должны возвращать правильный тип (dict, str, list, None)\n"
        "   - Если метод принимает параметр, используй его в теле"
    )

    # Максимальный размер сборанного кода (поднят для поддержки ~5000-строчных файлов)
    _MAX_CODE_LEN     = 500_000
    # Токенов на один LLM-проход при генерации кода
    _CHUNK_TOKENS     = 8_000
    # Максимум итераций продолжения (10 × ~600 строк ≈ 6000 строк)
    _MAX_CONT_PASSES  = 10

    def __init__(
        self,
        cognitive_core=None,
        sandbox=None,
        monitoring=None,
        working_dir: str | None = None,
        registry_path: str | None = None,
        arch_docs: list[str] | None = None,
        human_approval=None,
    ):
        self.cognitive_core = cognitive_core
        self.sandbox = sandbox
        self.monitoring = monitoring
        self.human_approval = human_approval
        self.working_dir = os.path.abspath(working_dir or os.getcwd())
        self.arch_docs: list[str] = arch_docs or []

        _reg_path = registry_path or os.path.join(
            self.working_dir, 'dynamic_registry.json'
        )
        self.registry = DynamicRegistry(_reg_path)

        if self.sandbox is None:
            self._log('[build] WARNING: sandbox не подключён; сборка модулей будет отклоняться политикой безопасности')

        # История сборок этого сеанса
        self._builds: list[ModuleBuildResult] = []

    # ── Публичный API ─────────────────────────────────────────────────────────

    def build_agent(self, name: str, description: str) -> ModuleBuildResult:
        """
        Строит нового специализированного агента на основе описания.

        Args:
            name        — имя в snake_case, напр. 'data_analyst'
            description — что умеет, какие задачи решает
        Returns:
            ModuleBuildResult
        """
        class_name = _to_class_name(name) + 'Agent'
        prompt = self._agent_prompt(name, class_name, description)
        return self._build(
            name=name,
            class_name=class_name,
            prompt=prompt,
            target_dir=os.path.join(self.working_dir, 'agents'),
            category='agents',
        )

    def build_tool(self, name: str, description: str) -> ModuleBuildResult:
        """
        Строит новый инструмент (наследник BaseTool или standalone-функция).

        Args:
            name        — имя в snake_case, напр. 'pdf_summarizer'
            description — что делает инструмент
        """
        class_name = _to_class_name(name) + 'Tool'
        prompt = self._tool_prompt(name, class_name, description)
        return self._build(
            name=name,
            class_name=class_name,
            prompt=prompt,
            target_dir=os.path.join(self.working_dir, 'tools'),
            category='tools',
        )

    def build_module(
        self,
        name: str,
        description: str,
        target_dir: str | None = None,
        class_name: str | None = None,
        extra_prompt: str = '',
    ) -> ModuleBuildResult:
        """
        Строит произвольный Python-модуль.

        Args:
            name        — имя модуля (snake_case)
            description — что должен делать
            target_dir  — куда положить; по умолчанию working_dir
            class_name  — имя главного класса; если None — выводится из name
            extra_prompt — дополнительный контекст для LLM
        """
        cls = class_name or _to_class_name(name)
        tdir = target_dir or self.working_dir
        prompt = self._generic_prompt(name, cls, description, extra_prompt, target_dir=tdir)
        return self._build(
            name=name,
            class_name=cls,
            prompt=prompt,
            target_dir=tdir,
            category='modules',
        )

    def load_all_from_registry(self) -> dict[str, list]:
        """
        Загружает все ранее созданные модули из dynamic_registry.json.
        Вызывается при старте agent.py.

        Returns:
            {'agents': [{'name': ..., 'module': ..., 'class': ...}], ...}
        """
        results: dict[str, list] = {'agents': [], 'tools': [], 'modules': []}
        for category in ('agents', 'tools', 'modules'):
            for entry in self.registry.get_all(category):
                file_path = entry.get('file_path', '')
                cls_name  = entry.get('class_name', '')
                mod_name  = entry.get('name', '')
                if not file_path or not os.path.exists(file_path):
                    self._log(f"[registry] Файл не найден, пропуск: {file_path}")
                    continue
                loaded = self._import_file(file_path, mod_name)
                if loaded:
                    cls = getattr(loaded, cls_name, None)
                    results[category].append({
                        'name': mod_name,
                        'module': loaded,
                        'class': cls,
                        'class_name': cls_name,
                        'file_path': file_path,
                    })
                    self._log(f"[registry] Загружен {category[:-1]}: {mod_name}")
                else:
                    self._log(f"[registry] Не удалось загрузить: {file_path}")
        return results

    def get_summary(self) -> dict:
        """Статистика сессии."""
        return {
            'total_builds': len(self._builds),
            'success': sum(1 for b in self._builds if b.ok),
            'failed':  sum(1 for b in self._builds if not b.ok),
            'registry_agents':  len(self.registry.get_all('agents')),
            'registry_tools':   len(self.registry.get_all('tools')),
            'registry_modules': len(self.registry.get_all('modules')),
        }

    # ── Внутренняя сборка ────────────────────────────────────────────────────

    def _build(
        self,
        name: str,
        class_name: str,
        prompt: str,
        target_dir: str,
        category: str,
    ) -> ModuleBuildResult:
        result = ModuleBuildResult(name)
        t0 = time.time()
        self._log(f"[build] Строю {category[:-1]}: '{name}' (класс {class_name})")

        try:
            # ── 1. Генерируем код ──────────────────────────────────────────
            if not self.cognitive_core:
                result.error = 'cognitive_core не подключён'
                return self._finish(result, t0)

            try:
                code = self._generate_code(prompt, name, class_name)
            except (AttributeError, RuntimeError) as e:
                result.error = f'LLM недоступен: {e}'
                return self._finish(result, t0)

            if not code or len(code.strip()) < 30:
                result.error = 'LLM вернул пустой или слишком короткий код'
                return self._finish(result, t0)

            result.generated_code = code

            # ── 2. Проверяем синтаксис ────────────────────────────────────
            try:
                ast.parse(code)
            except SyntaxError as e:
                result.error = f'SyntaxError в сгенерированном коде: {e}'
                return self._finish(result, t0)

            # ── 3. Проверяем что нужный класс / функция присутствует ──────
            if class_name and class_name not in code:
                result.error = (f'Класс {class_name!r} отсутствует в коде. '
                                f'LLM мог использовать другое имя.')
                return self._finish(result, t0)

            # ── 4. Sandbox-тест ───────────────────────────────────────────
            if not self.sandbox:
                result.status = BuildStatus.REJECTED
                result.error = 'Sandbox обязателен: сборка без sandbox запрещена политикой безопасности'
                self._log(f"[build] Отклонено: sandbox отсутствует ({name})")
                return self._finish(result, t0)

            from environment.sandbox import SandboxResult as SR
            run = self.sandbox.run_code(code)
            if run.verdict == SR.UNSAFE:
                result.status = BuildStatus.REJECTED
                result.error = f'Sandbox UNSAFE: {run.error}'
                self._log(f"[build] Отклонено sandbox: {name}")
                return self._finish(result, t0)
            self._log(f"[build] Sandbox: {run.verdict.value} для {name}")

            # ── 4a. CodeValidator: статическая проверка опасных конструкций ──
            try:
                from tools.tool_layer import CodeValidator
                cv_ok, cv_reason = CodeValidator.validate(code)
                if not cv_ok:
                    result.status = BuildStatus.REJECTED
                    result.error = f'CodeValidator: {cv_reason}'
                    self._log(f"[build] Отклонено CodeValidator: {name} — {cv_reason}")
                    return self._finish(result, t0)
                self._log(f"[build] CodeValidator: OK для {name}")
            except ImportError:
                self._log(f"[build] WARNING: CodeValidator недоступен, пропуск проверки ({name})")

            # ── 4b. Sandbox diff-review сгенерированного кода ──────────
            diff_action = {
                'type': 'module_build',
                'module': name,
                'code_length': len(code),
                'diff': f'+++ NEW FILE {name}.py\n{code[:2000]}',
            }
            sim = self.sandbox.simulate_action(diff_action)
            if hasattr(sim, 'verdict'):
                from environment.sandbox import SandboxResult as _SR
                if sim.verdict == _SR.UNSAFE:
                    result.status = BuildStatus.REJECTED
                    result.error = f'Sandbox diff-review UNSAFE: {getattr(sim, "error", "")}'
                    self._log(f"[build] Отклонено diff-review: {name}")
                    return self._finish(result, t0)
            self._log(f"[build] Sandbox diff-review: OK для {name}")

            # ── 4c. Human Approval — обязательное одобрение человеком ──────
            if self.human_approval:
                _preview = (
                    f"ModuleBuilder создаёт новый файл: {name}.py\n"
                    f"Класс: {class_name}\nРазмер: {len(code)} символов\n"
                    f"Первые 500 символов кода:\n{code[:500]}"
                )
                approved = self.human_approval.request_approval(
                    'module_build', _preview)
                if not approved:
                    result.status = BuildStatus.REJECTED
                    result.error = 'Модуль отклонён человеком'
                    self._log(f"[build] Отклонено человеком: {name}")
                    return self._finish(result, t0)

            # ── 4d. HMAC-подпись сгенерированного кода ────────────────────
            from self_repair.patch_signing import sign_patch, verify_patch
            _original = ''  # новый файл — оригинал пустой
            _module_signature = sign_patch(
                os.path.join(target_dir, f'{name}.py'),
                _original, code,
                patch_source='module_builder',
            )

            # ── 5. Проверка пути (security) ───────────────────────────────
            abs_target = os.path.realpath(os.path.abspath(target_dir))
            wd = os.path.realpath(self.working_dir)
            if not (abs_target.startswith(wd + os.sep) or abs_target == wd):
                result.error = (f'target_dir {abs_target!r} вне working_dir '
                                f'{wd!r} — запрещено')
                return self._finish(result, t0)

            os.makedirs(abs_target, exist_ok=True)
            file_path = os.path.join(abs_target, f'{name}.py')
            abs_file  = os.path.realpath(os.path.abspath(file_path))

            # ── 6. Бэкап если файл уже существует ────────────────────────
            if os.path.exists(abs_file):
                shutil.copy2(abs_file, abs_file + '.bak')

            # ── 7. Записываем файл ────────────────────────────────────────
            with open(abs_file, 'w', encoding='utf-8') as f:
                f.write(code)

            # ── 7b. Верификация подписи записанного файла ─────────────
            try:
                with open(abs_file, 'r', encoding='utf-8') as f:
                    written_code = f.read()
                if not verify_patch(
                    abs_file, _original, written_code, _module_signature
                ):
                    bak = abs_file + '.bak'
                    if os.path.exists(bak):
                        os.replace(bak, abs_file)
                    else:
                        os.remove(abs_file)
                    result.error = 'Подпись не совпала после записи — файл повреждён, откат'
                    self._log(f"[build] Подпись не совпала: {name}")
                    return self._finish(result, t0)
            except (OSError, IOError) as e:
                bak = abs_file + '.bak'
                if os.path.exists(bak):
                    os.replace(bak, abs_file)
                elif os.path.exists(abs_file):
                    os.remove(abs_file)
                result.error = f'Ошибка верификации подписи: {e}'
                return self._finish(result, t0)

            # ── 8. py_compile — финальная валидация ───────────────────────
            from safety.secrets_proxy import safe_env as _safe_env
            check = subprocess.run(
                [sys.executable, '-m', 'py_compile', abs_file],
                capture_output=True, text=True, timeout=15, check=False,
                env=_safe_env(),
            )
            if check.returncode != 0:
                # Откатываемся
                bak = abs_file + '.bak'
                if os.path.exists(bak):
                    os.replace(bak, abs_file)
                else:
                    os.remove(abs_file)
                result.error = f'py_compile провалился: {check.stderr[:300]}'
                return self._finish(result, t0)

            smoke_ok, smoke_error = self._run_core_smoke_if_needed(abs_file)
            if not smoke_ok:
                bak = abs_file + '.bak'
                if os.path.exists(bak):
                    os.replace(bak, abs_file)
                elif os.path.exists(abs_file):
                    os.remove(abs_file)
                result.error = smoke_error
                return self._finish(result, t0)

            # ── 9. Загружаем модуль в память ──────────────────────────────
            mod = self._import_file(abs_file, name)
            if not mod:
                result.error = 'importlib не смог загрузить готовый файл'
                return self._finish(result, t0)

            # ── 10. Регистрируем ──────────────────────────────────────────
            entry = {
                'name':       name,
                'class_name': class_name,
                'file_path':  abs_file,
                'created_at': time.time(),
                'description': prompt[:150],
            }
            self.registry.register(category, entry)

            result.status     = BuildStatus.SUCCESS
            result.file_path  = abs_file
            result.class_name = class_name
            result.module_object = mod
            self._log(f"[build] ✓ {category[:-1]} '{name}' создан: {abs_file}")

        except Exception as e:
            result.error = f'Неожиданная ошибка: {e}'

        return self._finish(result, t0)

    def _run_core_smoke_if_needed(self, file_path: str) -> tuple[bool, str | None]:
        """Автозапуск smoke_runner после генерации файлов в core/."""
        core_dir = os.path.join(self.working_dir, 'core')
        smoke_runner = os.path.join(self.working_dir, 'smoke_runner.py')
        if not file_path.startswith(core_dir + os.sep):
            return True, None
        if not os.path.isfile(smoke_runner):
            return True, None

        self._log(f"[build] Запускаю smoke_runner после правки ядра: {os.path.basename(file_path)}")
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
                file_path=file_path,
                output=(result.stdout or 'OK')[:500],
            )
            self._log('[build] smoke_runner завершился успешно')
            return True, None

        summary = (result.stdout or result.stderr or 'Нет вывода')[:500]
        self._record_core_smoke_event(
            'core_smoke_failed',
            file_path=file_path,
            output=summary,
        )
        self._log(f'[build] smoke_runner провалился: {summary}')
        return False, f'smoke_runner провалился после правки ядра: {summary}'

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
            self.monitoring.info(event_name, source='module_builder', data=data)
        else:
            self.monitoring.warning(event_name, source='module_builder', data=data)

    # ── Промпты для LLM ───────────────────────────────────────────────────────

    def _agent_prompt(self, name: str, class_name: str, description: str) -> str:
        arch     = self._arch_hint()
        existing = self._existing_file_hint(
            os.path.join(self.working_dir, 'agents', f'{name}.py'))
        return (
            f"Ты — Python-разработчик. Тебе нужно создать нового специализированного агента "
            f"для автономного AI-агента.\n\n"
            f"{arch}\n"
            f"{existing}\n"
            f"ТРЕБОВАНИЯ К АГЕНТУ:\n"
            f"- Имя класса: {class_name}\n"
            f"- Назначение: {description}\n"
            f"- Унаследовать от BaseAgent из agents.agent_system (from agents.agent_system import BaseAgent, AgentRole)\n"
            f"- Реализовать метод handle(self, task: dict) -> dict\n"
            f"- handle возвращает {{'result': ..., 'status': 'success'|'failed', 'agent': self.name}}\n"
            f"- В __init__ принять: cognitive_core=None, tools=None\n"
            f"- Логика должна соответствовать описанию\n"
            f"- Не добавлять внешние зависимости кроме стандартных и уже установленных\n\n"
            f"{self._CODE_QUALITY_RULES}\n\n"
            f"ФОРМАТ ОТВЕТА:\n"
            f"- Верни ТОЛЬКО полный Python-файл в блоке ```python ... ```\n"
            f"- Никаких пояснений вне блока кода\n"
            f"- Имя файла будет: agents/{name}.py\n"
        )

    def _tool_prompt(self, name: str, class_name: str, description: str) -> str:
        arch     = self._arch_hint()
        existing = self._existing_file_hint(
            os.path.join(self.working_dir, 'tools', f'{name}.py'))
        return (
            f"Ты — Python-разработчик. Тебе нужно создать новый инструмент "
            f"для автономного AI-агента.\n\n"
            f"{arch}\n"
            f"{existing}\n"
            f"ТРЕБОВАНИЯ К ИНСТРУМЕНТУ:\n"
            f"- Имя класса: {class_name}\n"
            f"- Назначение: {description}\n"
            f"- Опционально унаследоваться от BaseTool (from tools.tool_layer import BaseTool)\n"
            f"- Реализовать метод use(self, **kwargs) -> str | dict\n"
            f"- Все зависимости — только stdlib или уже установленные пакеты\n\n"
            f"{self._CODE_QUALITY_RULES}\n\n"
            f"ФОРМАТ ОТВЕТА:\n"
            f"- Верни ТОЛЬКО полный Python-файл в блоке ```python ... ```\n"
            f"- Никаких пояснений вне блока кода\n"
            f"- Имя файла будет: tools/{name}.py\n"
        )

    def _generic_prompt(
        self, name: str, class_name: str, description: str, extra: str,
        target_dir: str | None = None,
    ) -> str:
        arch     = self._arch_hint()
        tdir     = target_dir or self.working_dir
        existing = self._existing_file_hint(os.path.join(tdir, f'{name}.py'))
        return (
            f"Ты — Python-разработчик. Создай Python-модуль для автономного AI-агента.\n\n"
            f"{arch}\n"
            f"{existing}\n"
            f"ТРЕБОВАНИЯ:\n"
            f"- Имя главного класса: {class_name}\n"
            f"- Назначение модуля: {description}\n"
            f"{extra}\n\n"
            f"{self._CODE_QUALITY_RULES}\n\n"
            f"ФОРМАТ ОТВЕТА:\n"
            f"- Верни ТОЛЬКО полный Python-файл в блоке ```python ... ```\n"
            f"- Никаких пояснений вне блока кода\n"
            f"- Имя файла будет: {name}.py\n"
        )

    def _existing_file_hint(self, file_path: str) -> str:
        """
        Если файл уже существует, возвращает секцию промпта, чтобы LLM зналъ\nкакие классы/методы уже есть и не перезаписывал их.
        Если файл не существует, возвращает пустую строку.
        """
        try:
            if not os.path.isfile(file_path):
                return ''
            with open(file_path, 'r', encoding='utf-8', errors='replace') as fh:
                src = fh.read()
        except (OSError, IOError):
            return ''

        # Собираем список class X / def y(
        try:
            tree = ast.parse(src)
        except SyntaxError:
            # Текущий файл синтаксически невалиден — возвращаем как есть
            return (f"СУЩЕСТВУЮЩИЙ ФАЙЛ (`{os.path.basename(file_path)}`) — "
                    f"сохрани всё существующее, не удаляй и не переименовывай.\n"
                    f"WARN: текущий файл содержит SyntaxError.\n")

        symbols: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = [n.name for n in ast.walk(node)
                           if isinstance(n, ast.FunctionDef) and n.col_offset > 0]
                msig = ', '.join(methods[:12])
                symbols.append(f"  class {node.name}: [{msig}]")
            elif isinstance(node, ast.FunctionDef) and node.col_offset == 0:
                symbols.append(f"  def {node.name}(...)")

        if not symbols:
            return ''

        lines_count = src.count('\n')
        hint = (f"СУЩЕСТВУЮЩИЙ ФАЙЛ `{os.path.basename(file_path)}` "
                f"({lines_count} строк). "
                f"Сохрани (не удаляй, не переименовывай):\n"
                + '\n'.join(symbols[:30]) + '\n')
        return hint

    def _arch_hint(self) -> str:
        """Читает ключевые строки из архитектурных документов."""
        if not self.arch_docs:
            return ''
        lines_out: list[str] = []
        keywords = ('слой', 'layer', 'import', 'class ', 'def ',
                    'связан', 'использует', 'интерфейс', 'автоном')
        for doc_path in self.arch_docs:
            try:
                with open(doc_path, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        stripped = line.strip()
                        if stripped and any(kw in stripped.lower() for kw in keywords):
                            lines_out.append(stripped)
                            if len(lines_out) >= 25:
                                break
            except (OSError, IOError):
                pass
        if not lines_out:
            return ''
        return 'КОНТЕКСТ АРХИТЕКТУРЫ ПРОЕКТА:\n' + '\n'.join(lines_out)

    # ── Вспомогательные ───────────────────────────────────────────────────────

    def _generate_code(self, prompt: str, name: str, class_name: str) -> str:
        """
        Генерирует Python-код в один или несколько проходов.
        Поддерживает файлы до ~5000–6000 строк через итеративное продолжение.

        Сигнал завершения: ast.parse(code) не бросает SyntaxError.
        Каждый проход добавляет ~500–700 строк; до _MAX_CONT_PASSES итераций.
        """
        core = self.cognitive_core
        llm = getattr(core, 'llm', None) if core is not None else None
        if llm is None:
            raise RuntimeError('LLM backend не подключён')

        # ── Первый проход ──────────────────────────────────────────────────
        raw  = llm.infer(prompt, max_tokens=self._CHUNK_TOKENS)
        code = self._extract_python_partial(str(raw))
        if not code:
            return ''

        for _pass in range(1, self._MAX_CONT_PASSES):
            # Если код синтаксически завершён — готово
            try:
                ast.parse(code)
                break
            except SyntaxError:
                pass

            # Последние 40 строк как контекст для продолжения
            tail = '\n'.join(code.splitlines()[-40:])
            cont_prompt = (
                f"Продолжи Python-файл `{name}.py` точно с того места, "
                f"где оборвался предыдущий фрагмент.\n\n"
                f"Конец написанного кода:\n```python\n{tail}\n```\n\n"
                f"Напиши ТОЛЬКО продолжение (без повтора уже написанного). "
                f"Оберни в ```python ... ```. "
                f"Класс: {class_name}."
            )
            raw  = llm.infer(cont_prompt, max_tokens=self._CHUNK_TOKENS)
            cont = self._extract_python_partial(str(raw))
            if not cont:
                break
            code += '\n' + cont
            n_lines = len(code.splitlines())
            self._log(f"[build] Продолжение {_pass}/{self._MAX_CONT_PASSES - 1}: "
                      f"итого {n_lines} строк ({name})")

        return code.strip()

    @staticmethod
    def _extract_python(text: str) -> str:
        """Извлекает первый ```python ... ``` блок (требует закрывающий тег)."""
        m = re.search(r'```python\s*(.*?)```', text, re.DOTALL)
        if m:
            return m.group(1).strip()
        # Fallback: весь текст если нет маркеров
        if text.strip().startswith(('import ', 'from ', 'class ', 'def ')):
            return text.strip()
        return ''

    @staticmethod
    def _extract_python_partial(text: str) -> str:
        """
        Извлекает код из ```python...``` блока.
        В отличие от _extract_python, принимает незакрытый блок —
        нужно для промежуточных продолжений, которые могут обрываться.
        """
        # Полный блок с закрывающим тегом
        m = re.search(r'```python\s*(.*?)```', text, re.DOTALL)
        if m:
            return m.group(1).strip()
        # Незакрытый блок — берём всё после открывающего тега
        m2 = re.search(r'```python\s*(.*)', text, re.DOTALL)
        if m2:
            return m2.group(1).strip()
        # Fallback: текст уже начинается с Python-конструкции
        stripped = text.strip()
        if stripped.startswith(('import ', 'from ', 'class ', 'def ', '#')):
            return stripped
        return ''

    @staticmethod
    def _import_file(file_path: str, module_name: str):
        """Загружает .py файл как модуль через importlib."""
        try:
            spec = importlib.util.spec_from_file_location(
                f'_dynamic.{module_name}', file_path
            )
            if spec is None or spec.loader is None:
                return None
            mod = importlib.util.module_from_spec(spec)
            # Регистрируем чтобы relative imports работали
            sys.modules[f'_dynamic.{module_name}'] = mod
            spec.loader.exec_module(mod)
            return mod
        except Exception:
            return None

    def _finish(self, result: ModuleBuildResult, t0: float) -> ModuleBuildResult:
        result.duration = time.time() - t0
        self._builds.append(result)
        if not result.ok:
            self._log(f"[build] ✗ '{result.name}': {result.error}")

        # ── Персистентная память: сохраняем урок в PersistentBrain ───────
        # Это позволяет агенту не повторять те же ошибки после перезапуска.
        try:
            brain = getattr(self.cognitive_core, 'persistent_brain', None)
            if brain is not None:
                lesson = (
                    f"Сборка модуля '{result.name}': "
                    f"{'успех' if result.ok else 'ошибка — ' + str(result.error)}"
                )
                brain.record_lesson(
                    goal=f"build_module:{result.name}",
                    success=result.ok,
                    lesson=lesson,
                    context=f"class={result.class_name}, duration={result.duration:.1f}s",
                )
        except Exception:
            pass  # не критично — продолжаем без записи в brain

        return result

    def _log(self, msg: str):
        if self.monitoring:
            try:
                self.monitoring.info(msg, source='module_builder')
            except Exception:
                pass
        else:
            print(msg)


# ── Утилиты ───────────────────────────────────────────────────────────────────

def _to_class_name(snake: str) -> str:
    """'data_analyst' → 'DataAnalyst'"""
    return ''.join(part.capitalize() for part in snake.split('_'))
