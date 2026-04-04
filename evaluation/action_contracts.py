# Action Result Contracts — типизированная проверка успеха действий
# Каждое действие имеет success contract с чёткими критериями.

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class ActionResultContract:
    """Контракт успеха для действия.

    expected_artifacts: что должно появиться (файлы, сообщения, etc.)
    checks: список callable-проверок (возвращают bool)
    on_fail: действие при провале проверки
    max_verify_attempts: сколько раз можно перепроверить
    required_indices: индексы обязательных проверок — если хоть одна из них
        провалилась, контракт считается FAILED независимо от score.
    """
    expected_artifacts: list[str] = field(default_factory=list)
    checks: list[Callable[[], bool]] = field(default_factory=list)
    check_descriptions: list[str] = field(default_factory=list)
    on_fail: str = 'mark_verification_failed'
    max_verify_attempts: int = 2
    partial_success_threshold: float = 0.5  # доля пройденных проверок для partial
    required_indices: list[int] = field(default_factory=list)


@dataclass
class VerificationResult:
    """Результат проверки контракта."""
    passed: bool
    partial: bool = False
    score: float = 0.0
    passed_checks: list[str] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)
    on_fail_action: str = ''


def verify_contract(contract: ActionResultContract) -> VerificationResult:
    """Выполняет все проверки контракта, возвращает типизированный результат."""
    passed_list = []
    failed_list = []

    for i, check_fn in enumerate(contract.checks):
        desc = (
            contract.check_descriptions[i]
            if i < len(contract.check_descriptions)
            else f'check_{i}'
        )
        try:
            if check_fn():
                passed_list.append(desc)
            else:
                failed_list.append(desc)
        except Exception as e:
            failed_list.append(f'{desc}: {e}')

    total = len(passed_list) + len(failed_list)
    score = len(passed_list) / total if total > 0 else 0.0
    full_pass = len(failed_list) == 0 and total > 0

    # Обязательные проверки: если хоть одна failed — контракт FAILED
    required_failed = False
    if contract.required_indices:
        all_descs = list(passed_list) + list(failed_list)
        for ri in contract.required_indices:
            req_desc = (
                contract.check_descriptions[ri]
                if ri < len(contract.check_descriptions)
                else f'check_{ri}'
            )
            # Проверяем по описанию: если оно в failed_list — required провалился
            if any(fd == req_desc or fd.startswith(req_desc + ':') for fd in failed_list):
                required_failed = True
                break

    if required_failed:
        full_pass = False

    partial = (not full_pass and not required_failed
               and score >= contract.partial_success_threshold)

    return VerificationResult(
        passed=full_pass,
        partial=partial,
        score=round(score, 3),
        passed_checks=passed_list,
        failed_checks=failed_list,
        on_fail_action='' if full_pass else contract.on_fail,
    )


# ── Фабрика стандартных контрактов ───────────────────────────────────────────

def contract_file_created(path: str, min_size: int = 1) -> ActionResultContract:
    """Контракт: файл создан и размер > min_size."""
    return ActionResultContract(
        expected_artifacts=[f'file:{path}'],
        checks=[
            lambda: os.path.exists(path),
            lambda: os.path.getsize(path) >= min_size if os.path.exists(path) else False,
        ],
        check_descriptions=[
            f'path_exists({path})',
            f'file_size_gte({path}, {min_size})',
        ],
        on_fail='mark_verification_failed',
    )


def contract_command_success(returncode: int, stderr: str = '') -> ActionResultContract:
    """Контракт: команда вернула 0. stderr без ошибок."""
    error_markers = ('error:', 'fatal:', 'traceback', 'permission denied')

    def check_rc():
        return returncode == 0

    def check_stderr():
        if not stderr:
            return True
        s = stderr.lower()
        return not any(m in s for m in error_markers)

    return ActionResultContract(
        checks=[check_rc, check_stderr],
        check_descriptions=[
            f'returncode_zero(got={returncode})',
            'stderr_clean',
        ],
        on_fail='mark_verification_failed',
    )


def contract_for_action_type(
    action_type: str,
    action_input: str = '',
    action_output: str = '',
    action_success: bool = True,
    action_stderr: str = '',
) -> Optional[ActionResultContract]:
    """Создаёт контракт исходя из типа действия.

    Вызывается action_dispatcher / autonomous_loop после каждого действия.
    """
    atype = action_type.lower()

    if atype == 'write':
        path = action_input.strip()
        if path:
            return contract_file_created(path)

    if atype in ('bash', 'python'):
        rc = 0 if action_success else 1
        return contract_command_success(rc, action_stderr)

    if atype == 'search':
        def check_non_empty():
            return bool(action_output and action_output.strip())
        def check_results_persisted():
            sr_path = os.path.join('outputs', 'search_results.json')
            return os.path.exists(sr_path)
        return ActionResultContract(
            checks=[check_non_empty, check_results_persisted],
            check_descriptions=['search_results_non_empty', 'search_results_file_persisted'],
            on_fail='mark_verification_failed',
            partial_success_threshold=0.5,
        )

    if atype == 'build_module':
        return contract_build_module(
            file_path=action_output,
            module_name=action_input,
            success=action_success,
        )

    return None


def contract_build_module(
    file_path: str,
    module_name: str = '',
    success: bool = True,
) -> ActionResultContract:
    """Контракт: модуль создан, импортируется, содержит entrypoint.

    REQUIRED (провал любого = FAIL):
        - file_exists
        - module_imports
    OPTIONAL:
        - file_non_trivial
        - has_entrypoint
    """
    import importlib.util
    import ast as _ast

    def check_file_exists():
        return bool(file_path) and os.path.isfile(file_path)

    def check_module_imports():
        if not file_path or not os.path.isfile(file_path):
            return False
        try:
            spec = importlib.util.spec_from_file_location(
                f'_contract_check.{module_name or "mod"}', file_path,
            )
            if spec is None or spec.loader is None:
                return False
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return True
        except Exception:
            return False

    def check_file_non_trivial():
        if not file_path or not os.path.isfile(file_path):
            return False
        return os.path.getsize(file_path) >= 50

    def check_has_entrypoint():
        """Проверяет что модуль содержит класс с handle/use/run/process методом."""
        if not file_path or not os.path.isfile(file_path):
            return False
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                tree = _ast.parse(f.read())
        except (OSError, SyntaxError):
            return False
        entrypoints = {'handle', 'use', 'run', 'process', 'execute', 'analyze'}
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ClassDef):
                methods = {
                    n.name for n in _ast.walk(node)
                    if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                }
                if methods & entrypoints:
                    return True
        return False

    return ActionResultContract(
        expected_artifacts=[f'file:{file_path}'] if file_path else [],
        checks=[
            check_file_exists,       # index 0 — REQUIRED
            check_module_imports,    # index 1 — REQUIRED
            check_file_non_trivial,  # index 2
            check_has_entrypoint,    # index 3
        ],
        check_descriptions=[
            f'file_exists({file_path})',
            f'module_imports({module_name})',
            f'file_non_trivial({file_path}, >=50b)',
            'has_entrypoint(handle|use|run|process|execute|analyze)',
        ],
        required_indices=[0, 1],  # file_exists + module_imports обязательны
        on_fail='mark_verification_failed',
        partial_success_threshold=0.5,
    )
