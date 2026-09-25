"""Self-repair helpers: reading test output, judging a diagnosis or an empty diff, and summarising approval state.

Extracted from `core/self_repair` by autonomous self-build module split.
"""
from __future__ import annotations

from typing import Any

#: Весь набор — не «названный тест»: красное где-то в нём не воспроизводит ЭТОТ диагноз.
_WHOLE_SUITE = frozenset({"", ".", "tests", "tests/"})


#: Коды выхода pytest, которыми прогон НА СТАРОМ КОДЕ свидетельствует о дефекте:
#: 1 — тесты упали; 2 — прерван или ошибка сбора (новый тест импортирует то, чего
#: до правки нет; SWE-bench такой FAIL_TO_PASS тоже считает падением). 3 —
#: внутренняя ошибка pytest, 4 — ошибка вызова, 5 — не собран ни один тест: ни
#: один из них дефекта не показывает (пустой тестовый файл давал 5 и проходил).
_WITNESS_EXIT_CODES = frozenset({1, 2})


def red_before_fix(output: Any, proposal: Any = None) -> bool:
    """ОДНО правило «воспроизведено до правки» — для самопочинки и для свидетеля.

    Было два (сведение слоёв, 25.09): `_diagnosis_verified` (MIR-110) требовал,
    чтобы упавший тест был НАЗВАН диагнозом, а свидетель patch_route/patch_check
    реально гонял новый тест НА СТАРОМ КОДЕ, но верил любому ненулевому коду.
    Плюсы обоих: прогон на старом коде не завис, в нём упал тест (Agentless),
    код выхода — падение, а не «тестов нет», и, если известно предложение,
    упавший тест им назван (`_names_failure`). Свидетель гоняет только новые
    тесты правки, поэтому у него упавшее названо самим прогоном.
    """
    if not isinstance(output, dict) or output.get("timed_out") is not False:
        return False
    code = output.get("exit_code")
    if code is not None and code not in _WITNESS_EXIT_CODES:
        return False
    failed = [str(t) for t in output.get("failed_tests") or []]
    counted = "failed" in output or "errors" in output or failed
    red = (bool(failed) or int(output.get("failed") or 0) + int(output.get("errors") or 0) > 0
           if counted else code in _WITNESS_EXIT_CODES)
    if not red or proposal is None:
        return red
    return any(_names_failure(proposal, test_id) for test_id in failed)


def _diagnosis_verified(output: Any, proposal: Any = None) -> bool:
    """Диагноз проверен, только если прогон до правки воспроизвёл дефект (MIR-110).

    Решение оператора 25.09, вариант «а» (как в Agentless: тест, воспроизводящий
    ошибку, падает до правки). Само правило — `red_before_fix`, одно на двоих.
    """
    return red_before_fix(output, proposal)


def _names_failure(proposal: Any, test_id: str) -> bool:
    """Назван ли упавший тест: узкой областью прогона или в тексте диагноза."""
    if getattr(proposal, "test_pattern", None):
        return True  # прогон шёл с -k: всё упавшее в нём выбрано по имени
    path = test_id.split("::", maxsplit=1)[0]
    for scope in getattr(proposal, "test_paths", ()) or ():
        scope = str(scope).strip().rstrip("/")
        if scope not in _WHOLE_SUITE and (path == scope or path.startswith(scope + "/")):
            return True
    func = test_id.rsplit("::", maxsplit=1)[-1].split("[", maxsplit=1)[0]
    said = " ".join([str(getattr(proposal, "reason", "") or "")]
                    + [str(e) for e in getattr(proposal, "evidence", ()) or ()])
    return test_id in said or (len(func) > 4 and func in said)


def _tests_passed(output: Any) -> bool:
    if not isinstance(output, dict):
        return False
    return (
        output.get("timed_out") is False
        and output.get("exit_code") == 0
        and int(output.get("failed") or 0) == 0
        and int(output.get("errors") or 0) == 0
    )


def _extract_pass_count(output: Any) -> int:
    if not isinstance(output, dict):
        return 0
    try:
        return int(output.get("passed") or 0)
    except (TypeError, ValueError):
        return 0


def _is_empty_diff(output: Any) -> bool:
    if not isinstance(output, dict):
        return False
    return int(output.get("additions") or 0) == 0 and int(output.get("deletions") or 0) == 0


def _new_compensation_plan_id(before_ids: set[str], plans: list[Any]) -> str | None:
    for plan in reversed(plans):
        if plan.id not in before_ids:
            return plan.id
    return None


def _blocked_status(status: str):
    if status == "approval_deny":
        return "approval_denied"
    if status == "approval_abort":
        return "approval_aborted"
    if status == "approval_unavailable":
        return "approval_unavailable"
    if status in {"tool_error", "verify_failed"}:
        return "failed"
    return "blocked"


def _first_output(steps: list[Any], name: str) -> Any:
    for step in steps:
        if step.name == name:
            return step.output
    return None


def _approval_summary(steps: list[Any]) -> str | None:
    for step in steps:
        if step.name != "write":
            continue
        if step.status == "ok":
            return "approved"
        if step.status == "approval_deny":
            return "denied"
        if step.status == "approval_abort":
            return "aborted"
        if step.status == "approval_unavailable":
            return "unavailable"
        return step.status
    return None


def _test_summary(output: Any) -> dict[str, Any] | None:
    if not isinstance(output, dict):
        return None
    return {
        "exit_code": output.get("exit_code"),
        "timed_out": output.get("timed_out"),
        "passed": output.get("passed"),
        "failed": output.get("failed"),
        "errors": output.get("errors"),
        "skipped": output.get("skipped"),
        "total": output.get("total"),
        "failed_tests": output.get("failed_tests") or [],
    }


def _diff_summary(output: Any) -> dict[str, Any] | None:
    if not isinstance(output, dict):
        return None
    return {
        "path": output.get("path"),
        "file_exists": output.get("file_exists"),
        "additions": output.get("additions"),
        "deletions": output.get("deletions"),
        "diff_truncated": output.get("diff_truncated"),
    }
