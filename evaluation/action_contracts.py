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
    """
    expected_artifacts: list[str] = field(default_factory=list)
    checks: list[Callable[[], bool]] = field(default_factory=list)
    check_descriptions: list[str] = field(default_factory=list)
    on_fail: str = 'mark_verification_failed'
    max_verify_attempts: int = 2
    partial_success_threshold: float = 0.5  # доля пройденных проверок для partial


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
    partial = (not full_pass and score >= contract.partial_success_threshold)

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
        return ActionResultContract(
            checks=[check_non_empty],
            check_descriptions=['search_results_non_empty'],
            on_fail='mark_verification_failed',
        )

    return None
