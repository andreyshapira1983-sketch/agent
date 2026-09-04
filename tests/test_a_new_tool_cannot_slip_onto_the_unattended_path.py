"""Новый инструмент не попадает к безнадзорному агенту молча.

Замер, отвергнутые варианты и границы: F-3 в docs/audit/FIELD_CHECK_QUEUE.md.
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
    "model_roster": "чтение собственного реестра моделей: ключ есть/нет без значения, роли, здоровье, расход; открыт 2026-09-04 словом оператора «ставь глаз» — показывает, не переключает",
    "list_dir": "перечисление внутри рабочего места",
    "memory_bank": "запись в собственную долговременную память; открыта 2026-09-04 словом оператора «Надо» — за техническими стенами (политика записи, low-trust, readback до слова «сохранено», потолок 12 за процесс, память не может быть источником памяти); чтение — отдельная дверь",
    "web_search": "чтение внешнего мира; открыто 2026-09-01 словом оператора — "
    "принесённое остаётся гипотезой с источником, не истиной",
    "web_fetch": "выкачка названной страницы; та же дверь, что web_search",
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
    """Существо, а не бухгалтерия: выход НАРУЖУ сверх чтения закрыт.

    2026-09-01, слово оператора: «разреши ему смотреть в интернет, когда он
    сам захотел». Чтение веба (web_search/web_fetch) выведено из блокировки —
    агент, впервые увидев свои открытые дела, взял целью собственный дефект и
    сам потянулся за чужим решением, а ключ висел на формулировке цели.
    Открыто ровно чтение: остальной выход наружу закрыт, и одни ворота за раз.
    """
    for name in ("rss_fetch", "semantic_scholar_search"):
        assert name in BLOCKED, (
            f"{name} выходит в сеть и открыт безнадзорному агенту"
        )


def test_the_code_executing_tool_is_closed() -> None:
    """`python_probe` исполняет код: его гейт признан защитой от случайности."""
    assert "python_probe" in BLOCKED
