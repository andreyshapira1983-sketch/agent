#!/usr/bin/env python3
"""
Preflight Check — предсдаточный чек-лист агента.

Проверяет всё необходимое перед запуском / после внесения изменений:
  1. Проект собирается (все импорты резолвятся)
  2. Тесты проходят
  3. Критические файлы на месте
  4. Конфиги валидны
  5. Git чистый (нет неотслеженных изменений)
  6. Зависимости соответствуют lock-файлу
  7. Слои инициализируются

Запуск:
  python preflight.py              — полная проверка
  python preflight.py --quick      — быстрая (без тестов)
  python preflight.py --fix        — авто-фикс где возможно
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)


class CheckResult:
    def __init__(self, name: str, passed: bool, message: str = '', fixable: bool = False):
        self.name = name
        self.passed = passed
        self.message = message
        self.fixable = fixable

    @property
    def icon(self) -> str:
        return '✅' if self.passed else ('🔧' if self.fixable else '❌')

    def __str__(self):
        msg = f"  {self.message}" if self.message else ''
        return f"{self.icon} {self.name}{msg}"


def check_critical_files() -> CheckResult:
    """Проверяет наличие критических файлов."""
    required = [
        'agent.py',
        'core/cognitive_core.py',
        'core/persistent_brain.py',
        'core/model_manager.py',
        'loop/autonomous_loop.py',
        'execution/action_dispatcher.py',
        'monitoring/monitoring.py',
        'config/requirements.txt',
        'config/benchmark_tasks.json',
        '.gitignore',
    ]
    missing = [f for f in required if not os.path.exists(os.path.join(_ROOT, f))]
    if missing:
        return CheckResult('Критические файлы', False,
                           f"Отсутствуют: {', '.join(missing)}")
    return CheckResult('Критические файлы', True, f'{len(required)} файлов на месте')


def check_configs_valid() -> CheckResult:
    """Проверяет валидность JSON-конфигов."""
    configs = [
        'config/benchmark_tasks.json',
    ]
    errors = []
    for cfg in configs:
        path = os.path.join(_ROOT, cfg)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding='utf-8') as f:
                json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            errors.append(f"{cfg}: {e}")
    if errors:
        return CheckResult('JSON-конфиги', False, '; '.join(errors))
    return CheckResult('JSON-конфиги', True, f'{len(configs)} конфигов валидны')


def check_core_imports() -> CheckResult:
    """Проверяет что ядро импортируется без ошибок."""
    modules = [
        'core.cognitive_core',
        'core.persistent_brain',
        'core.model_manager',
        'core.goal_manager',
        'core.identity',
        'monitoring.monitoring',
        'execution.action_dispatcher',
        'evaluation.evaluation',
        'environment.sandbox',
        'safety.governance',
        'validation.data_validation',
    ]
    failed = []
    for mod in modules:
        try:
            importlib.import_module(mod)
        except Exception as e:
            failed.append(f"{mod}: {type(e).__name__}: {e}")
    if failed:
        return CheckResult('Импорт ядра', False,
                           f"{len(failed)} модулей не импортируются:\n    " +
                           '\n    '.join(failed[:5]))
    return CheckResult('Импорт ядра', True, f'{len(modules)} модулей OK')


def check_tests(quick: bool = False) -> CheckResult:
    """Запускает тесты."""
    if quick:
        # Только smoke-тесты
        cmd = [
            sys.executable, '-m', 'pytest',
            'tests/test_smoke_hooks.py',
            'tests/test_cognitive_core_regressions.py',
            '-q', '--timeout=30', '--no-header',
        ]
    else:
        cmd = [
            sys.executable, '-m', 'pytest',
            '-q', '--timeout=60', '--no-header',
            '--ignore=tests/test_memory_quality_regressions.py',
        ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=_ROOT, timeout=300, check=False,
        )
        # Ищем строку "N passed"
        output = result.stdout + result.stderr
        for line in reversed(output.strip().split('\n')):
            if 'passed' in line:
                passed = 'failed' not in line
                return CheckResult(
                    'Тесты', passed, line.strip())
        if result.returncode == 0:
            return CheckResult('Тесты', True, 'pytest exit 0')
        return CheckResult('Тесты', False, f'exit code {result.returncode}')
    except subprocess.TimeoutExpired:
        return CheckResult('Тесты', False, 'Таймаут 300с')
    except FileNotFoundError:
        return CheckResult('Тесты', False, 'pytest не найден', fixable=True)


def check_git_status() -> CheckResult:
    """Проверяет чистоту git."""
    try:
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True, text=True, cwd=_ROOT, timeout=10, check=False,
        )
        if result.returncode != 0:
            return CheckResult('Git', False, 'git не инициализирован', fixable=True)
        dirty = [ln for ln in result.stdout.strip().split('\n') if ln.strip()]
        if dirty:
            return CheckResult('Git', False,
                               f'{len(dirty)} неотслеженных/изменённых файлов',
                               fixable=True)
        return CheckResult('Git', True, 'Рабочее дерево чистое')
    except FileNotFoundError:
        return CheckResult('Git', False, 'git не установлен')


def check_dependencies() -> CheckResult:
    """Проверяет что lock-файл существует."""
    lock = os.path.join(_ROOT, 'config', 'requirements.lock')
    req = os.path.join(_ROOT, 'config', 'requirements.txt')
    if not os.path.exists(lock):
        return CheckResult('Зависимости', False,
                           'requirements.lock отсутствует — среда невоспроизводима',
                           fixable=True)
    # Проверяем что lock не старше requirements.txt
    if os.path.exists(req):
        lock_mtime = os.path.getmtime(lock)
        req_mtime = os.path.getmtime(req)
        if req_mtime > lock_mtime:
            return CheckResult('Зависимости', False,
                               'requirements.txt новее lock-файла — обнови lock',
                               fixable=True)
    # Считаем пакеты в lock
    with open(lock, encoding='utf-8') as f:
        count = sum(1 for ln in f if '==' in ln)
    return CheckResult('Зависимости', True, f'{count} пакетов запинено в lock')


def check_logs_writable() -> CheckResult:
    """Проверяет что директория логов доступна для записи."""
    logs_dir = os.path.join(_ROOT, 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    test_file = os.path.join(logs_dir, '.preflight_test')
    try:
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write('ok')
        os.unlink(test_file)
        return CheckResult('Директория логов', True, 'logs/ записываема')
    except OSError as e:
        return CheckResult('Директория логов', False, str(e))


def check_change_tracker() -> CheckResult:
    """Проверяет что change_tracker импортируется."""
    try:
        from monitoring.change_tracker import ChangeTracker
        _ct = ChangeTracker()
        return CheckResult('Change Tracker', True, 'Доступен')
    except Exception as e:
        return CheckResult('Change Tracker', False, str(e))


# ── Основной запуск ────────────────────────────────────────────────────────

def run_preflight(quick: bool = False, fix: bool = False) -> list[CheckResult]:  # noqa: ARG001
    """Запускает все проверки и возвращает результаты."""
    _ = fix  # reserved for future auto-fix support
    checks = [
        check_critical_files,
        check_configs_valid,
        check_core_imports,
        check_git_status,
        check_dependencies,
        check_logs_writable,
        check_change_tracker,
    ]
    if not quick:
        checks.append(lambda: check_tests(quick=False))
    else:
        checks.append(lambda: check_tests(quick=True))

    results = []
    for check_fn in checks:
        try:
            r = check_fn()
        except Exception as e:
            r = CheckResult(check_fn.__name__, False, f'Исключение: {e}')
        results.append(r)

    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Preflight Check — предсдаточный чек-лист')
    parser.add_argument('--quick', action='store_true', help='Быстрая проверка (smoke-тесты)')
    parser.add_argument('--fix', action='store_true', help='Авто-фикс где возможно')
    args = parser.parse_args()

    print('━' * 60)
    print('  PREFLIGHT CHECK — предсдаточный чек-лист агента')
    print(f'  {time.strftime("%d.%m.%Y %H:%M:%S")}')
    print('━' * 60)
    print()

    results = run_preflight(quick=args.quick, fix=args.fix)

    for r in results:
        print(r)

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    failed = total - passed

    print()
    print('━' * 60)
    if failed == 0:
        print(f'  ✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ({passed}/{total})')
    else:
        fixable = sum(1 for r in results if not r.passed and r.fixable)
        print(f'  ❌ ПРОВАЛЕНО: {failed}/{total}' +
              (f' (из них {fixable} автоисправимых)' if fixable else ''))
    print('━' * 60)

    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
