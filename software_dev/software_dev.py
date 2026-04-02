# Software Development System — Слой 7
# Архитектура автономного AI-агента
# Анализ кода, генерация тестов, статический анализ, build-система, CI-конвейер.

from __future__ import annotations

import ast
import os
import re
import shlex
import subprocess
import time


class CodeAnalysisResult:
    """Результат статического анализа кода."""

    def __init__(self, path: str):
        self.path = path
        self.lines_total = 0
        self.lines_code = 0
        self.functions: list[str] = []
        self.classes: list[str] = []
        self.imports: list[str] = []
        self.issues: list[dict] = []          # {line, severity, message}
        self.complexity: dict[str, int] = {}   # функция → цикломатическая сложность
        self.analyzed_at = time.time()

    def to_dict(self) -> dict:
        return {
            'path': self.path,
            'lines_total': self.lines_total,
            'lines_code': self.lines_code,
            'functions': self.functions,
            'classes': self.classes,
            'imports': self.imports,
            'issues': self.issues,
            'complexity': self.complexity,
        }


class TestSuite:
    """Сгенерированный набор тестов."""

    __test__ = False

    def __init__(self, source_path: str, code: str, framework: str = 'unittest'):
        self.source_path = source_path
        self.code = code
        self.framework = framework
        self.generated_at = time.time()

    def save(self, output_path: str) -> bool:
        try:
            os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(self.code)
            return True
        except (OSError, IOError):
            return False


class BuildResult:
    """Результат сборки/запуска тестов."""

    def __init__(self, command: str, success: bool, output: str,
                 duration: float, returncode: int):
        self.command = command
        self.success = success
        self.output = output
        self.duration = duration
        self.returncode = returncode

    def to_dict(self) -> dict:
        return {
            'command': self.command,
            'success': self.success,
            'output': self.output[:2000],
            'duration': round(self.duration, 2),
            'returncode': self.returncode,
        }


class SoftwareDevelopmentSystem:
    """
    Software Development System — Слой 7.

    Функции:
        - статический анализ Python-кода через AST
        - измерение цикломатической сложности
        - обнаружение типичных проблем (длинные функции, дублирование, TODO)
        - генерация unit-тестов через Cognitive Core (LLM)
        - запуск тестов (pytest / unittest)
        - сборка проектов (pip install, setup.py, pyproject.toml)
        - CI-конвейер: анализ → тесты → сборка

    Используется:
        - Self-Improvement (Слой 12) — улучшение кода агента
        - Execution System (Слой 8)  — запуск скриптов
        - Tool Layer (Слой 5)        — доступ к терминалу
    """

    MAX_FUNCTION_LINES = 50     # предупреждение при превышении
    MAX_COMPLEXITY = 10         # предупреждение при высокой сложности

    def __init__(
        self,
        cognitive_core=None,
        terminal=None,
        monitoring=None,
        working_dir: str | None = None,
    ):
        self.cognitive_core = cognitive_core
        self.terminal = terminal
        self.monitoring = monitoring
        self.working_dir = working_dir or os.getcwd()

        self._analyses: dict[str, CodeAnalysisResult] = {}
        self._build_history: list[BuildResult] = []

    # ── Статический анализ ────────────────────────────────────────────────────

    def analyze(self, path: str) -> CodeAnalysisResult:
        """
        Статически анализирует Python-файл или директорию.

        Returns:
            CodeAnalysisResult с функциями, классами, проблемами и сложностью.
        """
        full_path = path if os.path.isabs(path) else os.path.join(self.working_dir, path)
        result = CodeAnalysisResult(full_path)

        if os.path.isdir(full_path):
            # Анализируем все .py файлы в директории
            for root, _, files in os.walk(full_path):
                for fname in files:
                    if fname.endswith('.py'):
                        sub = self.analyze(os.path.join(root, fname))
                        result.functions.extend(sub.functions)
                        result.classes.extend(sub.classes)
                        result.imports.extend(sub.imports)
                        result.issues.extend(sub.issues)
                        result.lines_total += sub.lines_total
                        result.lines_code += sub.lines_code
            self._analyses[full_path] = result
            return result

        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                source = f.read()
        except (OSError, IOError) as e:
            result.issues.append({'line': 0, 'severity': 'error',
                                   'message': f'Не удалось прочитать файл: {e}'})
            return result

        lines = source.splitlines()
        result.lines_total = len(lines)
        result.lines_code = sum(
                1 for line in lines
                if line.strip() and not line.strip().startswith('#')
        )

        # Сканируем код на TODO/FIXME/HACK маркеры
        for i, line in enumerate(lines, 1):
            stripped = line.strip().upper()
            for marker in ('TODO', 'FIXME', 'HACK', 'XXX'):
                if marker in stripped:
                    result.issues.append({
                        'line': i,
                        'severity': 'info',
                        'message': f'{marker}: {line.strip()[:80]}',
                    })

        # AST-анализ
        try:
            tree = ast.parse(source, filename=full_path)
        except SyntaxError as e:
            result.issues.append({'line': e.lineno or 0, 'severity': 'error',
                                   'message': f'SyntaxError: {e.msg}'})
            self._analyses[full_path] = result
            return result

        for node in ast.walk(tree):
            # Импорты
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                result.imports.extend(names)

            # Классы
            elif isinstance(node, ast.ClassDef):
                result.classes.append(node.name)

            # Функции
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result.functions.append(node.name)
                fn_lines = (node.end_lineno or node.lineno) - node.lineno + 1
                complexity = self._cyclomatic_complexity(node)
                result.complexity[node.name] = complexity

                if fn_lines > self.MAX_FUNCTION_LINES:
                    result.issues.append({
                        'line': node.lineno,
                        'severity': 'warning',
                        'message': (f"Функция '{node.name}' слишком длинная "
                                    f"({fn_lines} строк > {self.MAX_FUNCTION_LINES})"),
                    })
                if complexity > self.MAX_COMPLEXITY:
                    result.issues.append({
                        'line': node.lineno,
                        'severity': 'warning',
                        'message': (f"Функция '{node.name}' высокая сложность "
                                    f"(cyclomatic={complexity})"),
                    })

        # Дублирующиеся имена функций
        seen_funcs: dict[str, int] = {}
        for name in result.functions:
            seen_funcs[name] = seen_funcs.get(name, 0) + 1
        for name, cnt in seen_funcs.items():
            if cnt > 1:
                result.issues.append({
                    'line': 0,
                    'severity': 'warning',
                    'message': f"Дублирующееся имя функции: '{name}' ({cnt} раз)",
                })

        self._analyses[full_path] = result
        self._log(f"Анализ '{os.path.basename(full_path)}': "
                  f"{len(result.functions)} функций, "
                  f"{len(result.issues)} проблем")
        return result

    def _cyclomatic_complexity(self, node: ast.AST) -> int:
        """Цикломатическая сложность функции (ветвления, включая elif-цепочки)."""
        complexity = 1

        def _walk(n: ast.AST):
            nonlocal complexity
            if isinstance(n, (ast.If, ast.While, ast.For, ast.ExceptHandler,
                              ast.With, ast.Assert, ast.comprehension, ast.IfExp)):
                complexity += 1
            elif isinstance(n, ast.BoolOp):
                complexity += len(n.values) - 1

            for child in ast.iter_child_nodes(n):
                _walk(child)

        _walk(node)
        return complexity

    # ── Генерация тестов ──────────────────────────────────────────────────────

    def generate_tests(
        self,
        path: str,
        framework: str = 'pytest',
        save_to: str | None = None,
    ) -> TestSuite | None:
        """
        Генерирует unit-тесты для Python-файла через LLM.

        Args:
            path      — путь к файлу с кодом
            framework — 'pytest' или 'unittest'
            save_to   — путь для сохранения (опционально)
        """
        if not self.cognitive_core:
            self._log('generate_tests: cognitive_core не подключён', level='warning')
            return None

        full_path = path if os.path.isabs(path) else os.path.join(self.working_dir, path)
        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                source = f.read()
        except (OSError, IOError) as e:
            self._log(f'generate_tests: не удалось прочитать {path}: {e}', level='error')
            return None

        analysis = self.analyze(full_path)
        fn_list = ', '.join(analysis.functions[:20]) or 'не найдено'

        prompt = (
            f"Напиши unit-тесты для следующего Python-кода, используя {framework}.\n"
            f"Функции: {fn_list}\n\n"
            f"Код:\n```python\n{source[:3000]}\n```\n\n"
            f"Требования:\n"
            f"- Полный рабочий файл с тестами\n"
            f"- Тестировать граничные случаи\n"
            f"- Использовать только стандартные библиотеки и {framework}\n"
            f"- Добавить фикстуры если нужно\n"
            f"Верни только Python-код, без пояснений."
        )

        raw = self.cognitive_core.reasoning(prompt)
        code = self._extract_code(str(raw))

        suite = TestSuite(source_path=full_path, code=code, framework=framework)

        if save_to:
            suite.save(save_to)
            self._log(f"Тесты сохранены: {save_to}")
        else:
            self._log(f"Тесты сгенерированы для '{os.path.basename(path)}'")

        return suite

    # ── Запуск тестов ─────────────────────────────────────────────────────────

    def run_tests(self, path: str = '.', framework: str = 'pytest') -> BuildResult:
        """Запускает тесты через pytest или unittest."""
        if framework == 'pytest':
            cmd = f'python -m pytest {path} -v --tb=short'
        else:
            cmd = f'python -m unittest discover -s {path}'
        return self._run(cmd)

    # ── Сборка / установка ────────────────────────────────────────────────────

    def install_deps(self, requirements_file: str = 'requirements.txt') -> BuildResult:
        """Устанавливает зависимости из requirements.txt."""
        return self._run(f'pip install -r {requirements_file}')

    def run_linter(self, path: str = '.') -> BuildResult:
        """Запускает flake8 (если установлен)."""
        return self._run(f'python -m flake8 {path} --max-line-length=120')

    def run_formatter(self, path: str = '.', check_only: bool = False) -> BuildResult:
        """Запускает black (если установлен)."""
        flag = '--check' if check_only else ''
        return self._run(f'python -m black {path} {flag}')

    # ── CI-конвейер ───────────────────────────────────────────────────────────

    def ci_pipeline(self, path: str = '.') -> dict:
        """
        Полный CI-конвейер: анализ → lint → тесты.
        Возвращает суммарный отчёт.
        """
        self._log(f"CI pipeline: {path}")
        report = {'path': path, 'steps': [], 'passed': True}

        # 1. Статический анализ
        analysis = self.analyze(path)
        step_analysis = {
            'step': 'static_analysis',
            'issues': len(analysis.issues),
            'functions': len(analysis.functions),
            'passed': len([i for i in analysis.issues
                           if i['severity'] == 'error']) == 0,
        }
        report['steps'].append(step_analysis)
        if not step_analysis['passed']:
            report['passed'] = False

        # 2. Линтер
        lint = self.run_linter(path)
        report['steps'].append({
            'step': 'linter',
            'output': lint.output[:500],
            'passed': lint.success,
        })

        # 3. Тесты
        tests = self.run_tests(path)
        report['steps'].append({
            'step': 'tests',
            'output': tests.output[:500],
            'passed': tests.success,
            'duration': tests.duration,
        })
        if not tests.success:
            report['passed'] = False

        status = 'PASS' if report['passed'] else 'FAIL'
        self._log(f"CI pipeline завершён: {status}")
        return report

    # ── История сборок ────────────────────────────────────────────────────────

    def build_history(self, n: int = 10) -> list[dict]:
        return [b.to_dict() for b in self._build_history[-n:]]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _run(self, cmd: str) -> BuildResult:
        if self.terminal:
            t0 = time.time()
            res = self.terminal.run(cmd)
            duration = time.time() - t0
            result = BuildResult(
                command=cmd,
                success=res.get('success', False),
                output=res.get('stdout', '') + res.get('stderr', ''),
                duration=duration,
                returncode=res.get('returncode', -1),
            )
        else:
            t0 = time.time()
            try:
                from tools.tool_layer import CommandValidator
                allowed, reason = CommandValidator.validate(cmd)
                if not allowed:
                    return BuildResult(
                        command=cmd, success=False,
                        output=f"CommandValidator заблокировал: {reason}",
                        duration=0.0, returncode=-1,
                    )
                from execution.command_gateway import CommandGateway
                gw = CommandGateway.get_instance()
                r = gw.execute(
                    shlex.split(cmd),
                    timeout=120, cwd=self.working_dir,
                    caller='SoftwareDev.build',
                )
                if not r.allowed:
                    result = BuildResult(
                        command=cmd, success=False,
                        output=f"CommandGateway: {r.reject_reason}",
                        duration=0.0, returncode=-1,
                    )
                else:
                    result = BuildResult(
                        command=cmd,
                        success=r.returncode == 0,
                        output=r.stdout + r.stderr,
                        duration=r.duration,
                        returncode=r.returncode,
                    )
            except (OSError, subprocess.TimeoutExpired, ValueError) as e:
                result = BuildResult(
                    command=cmd, success=False,
                    output=str(e), duration=time.time() - t0, returncode=-1,
                )
        self._build_history.append(result)
        self._log(
            f"CMD: {cmd[:60]} -> {'OK' if result.success else 'FAIL'} "
            f"({result.duration:.1f}s)"
        )
        return result

    def _extract_code(self, raw: str) -> str:
        """Извлекает Python-код из ответа LLM."""
        m = re.search(r'```python\s*(.*?)```', raw, re.DOTALL)
        if m:
            return m.group(1).strip()
        m = re.search(r'```\s*(.*?)```', raw, re.DOTALL)
        if m:
            return m.group(1).strip()
        return raw.strip()

    def _log(self, message: str, level: str = 'info'):
        if self.monitoring:
            getattr(self.monitoring, level, self.monitoring.info)(
                message, source='software_dev'
            )
        else:
            print(f'[SoftwareDev] {message}')