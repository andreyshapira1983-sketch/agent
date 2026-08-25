"""Новый инструмент не попадает к безнадзорному агенту молча.

СВЕРКА С ПОЛЕМ, форма F-3 «список-разрешение / запрета»
(docs/audit/FIELD_CHECK_QUEUE.md). Провал, который поле приписывает этой форме:
список — это СНИМОК; множество, которое он описывает, растёт, а список молча
перестаёт его покрывать.

ЗАМЕР 2026-08-25, оба направления.

  по умолчанию ЗАКРЫТО   подкоманды `git` в `shell_exec`: разрешены только
                         названные, и `push`/`fetch`/`clone` отклоняются —
                         проверено поведением, а не комментарием
  по умолчанию ОТКРЫТО   `_AUTONOMOUS_GOAL_BLOCKED_TOOLS`: путь БЛОКИРУЕТ
                         названные, поэтому новый инструмент доступен
                         безнадзорному агенту с первой же минуты и без решения

Второе направление и есть форма провала. Инвертировать список в разрешительный
здесь нельзя без риска сломать сам безнадзорный путь, поэтому вводится не
запрет, а СВИДЕТЕЛЬ: опись всех зарегистрированных инструментов с явной
пометкой у каждого. Появился новый — тест краснеет и требует решения вслух,
вместо того чтобы дать доступ молчанием.

Опись сверяется с РЕАЛЬНЫМ набором инструментов, вычитанным из исходников, а не
переписанным по памяти: иначе она сама стала бы устаревающим снимком, то есть
той же болезнью.
"""
from __future__ import annotations

import ast
import pathlib

from core.autonomous_runtime import _AUTONOMOUS_GOAL_BLOCKED_TOOLS as BLOCKED

_REPO = pathlib.Path(__file__).resolve().parent.parent

#: Инструменты, ОСОЗНАННО открытые безнадзорному пути, с основанием у каждого.
#: Основание — не украшение: список без него превращается в «так исторически
#: сложилось», и следующий читатель не сможет отличить решение от недосмотра.
_DELIBERATELY_OPEN: dict[str, str] = {
    "current_time": "часы; наружу ничего не отдаёт",
    "diff_file": "чтение двух файлов рабочего места",
    "file_read": "чтение внутри рабочего места",
    "file_write": "запись под откат и одобрение; ядро самой работы",
    "lesson_provenance": "чтение собственных записей об уроках",
    "list_dir": "перечисление внутри рабочего места",
    "read_logs": "чтение собственных журналов",
    "run_tests": "прогон своей же батареи; без него работа не проверяема",
    "shell_exec": "белый список команд, подкоманды git по умолчанию закрыты",
}


def _registered_tools() -> set[str]:
    """Имена инструментов, вычитанные из исходников, а не из памяти."""
    names: set[str] = set()
    for path in sorted((_REPO / "tools").glob("*.py")):
        if path.name in {"__init__.py", "base.py"}:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — живой код обязан разбираться
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and stmt.targets[0].id == "name"
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    names.add(stmt.value.value)
    return names


def test_every_registered_tool_is_classified() -> None:
    tools = _registered_tools()
    assert tools, "инструментов не найдено — сломан сам разбор, а не список"

    classified = set(BLOCKED) | set(_DELIBERATELY_OPEN)
    unclassified = sorted(tools - classified)

    assert not unclassified, (
        "новый инструмент не отнесён ни к закрытым, ни к осознанно открытым — "
        "а список безнадзорного пути БЛОКИРУЕТ названные, значит по умолчанию "
        f"он уже доступен агенту без единого решения: {unclassified}"
    )


def test_the_inventory_does_not_outlive_its_tools() -> None:
    """Обратная сторона: опись, пережившая свой инструмент, тоже лжёт.

    Без этой проверки удалённый инструмент остался бы в списке «осознанно
    открытых» и создавал бы впечатление разобранности там, где разбирать уже
    нечего.
    """
    tools = _registered_tools()
    stale = sorted(set(_DELIBERATELY_OPEN) - tools)

    assert not stale, f"в описи есть инструменты, которых больше нет: {stale}"


def test_the_egress_tools_are_all_closed() -> None:
    """Существо, а не бухгалтерия: выход в сеть закрыт для безнадзорного пути."""
    for name in ("web_fetch", "web_search", "rss_fetch", "semantic_scholar_search"):
        assert name in BLOCKED, (
            f"{name} выходит в сеть и открыт безнадзорному агенту"
        )


def test_the_code_executing_tool_is_closed() -> None:
    """`python_probe` исполняет код: его гейт признан защитой от случайности."""
    assert "python_probe" in BLOCKED
