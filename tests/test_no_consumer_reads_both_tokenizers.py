"""Два токенизатора памяти расходятся осознанно — но ни одно решение не читает оба.

Замер, отвергнутые варианты и границы: MIR-008 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

import ast
import pathlib

_CORE = pathlib.Path(__file__).resolve().parents[1] / "core"

#: Токенизатор памяти опыта: стоп-слова НЕ режет намеренно («почему тест
#: падает» и «почему тест НЕ падает» иначе совпадают дословно).
_EPISODIC = "topic_tokens"

#: Токенизатор постоянной памяти: стоп-слова режет по общему словарю.
_PERSISTENT = frozenset({"_tokens", "_query_tokens", "_tag_tokens"})


def _called_names(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            out.add(node.func.id)
    return out


def _modules_using(name_test) -> set[str]:
    found: set[str] = set()
    for path in sorted(_CORE.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        if name_test(_called_names(tree)):
            found.add(path.name)
    return found


def test_the_scan_can_find_a_tokenizer_at_all() -> None:
    """Контроль: без него «пересечения нет» означало бы сломанный обход."""
    episodic = _modules_using(lambda names: _EPISODIC in names)

    assert episodic, (
        "обход не нашёл НИ ОДНОГО потребителя токенизатора памяти опыта — "
        "значит он ничего не видит, и вывод об отсутствии пересечения пуст"
    )


def test_no_module_reads_both_tokenizers() -> None:
    """Граница, на которой держится MIR-008.

    Расхождение измерено: на 70 % живых текстов памяти (257 записей) два
    токенизатора дают разные наборы. Вреда нет ровно потому, что они обслуживают
    разные хранилища и ни одно решение не сравнивает обе версии ОДНОГО текста.
    Если этот тест покраснеет — условие вреда выполнено, и запись открывается
    заново.
    """
    both = _modules_using(
        lambda names: _EPISODIC in names and bool(_PERSISTENT & names)
    )

    assert not both, (
        "один модуль читает оба токенизатора памяти: "
        + ", ".join(sorted(both))
        + " — теперь одно решение может видеть один и тот же текст двумя "
          "разными наборами слов, и MIR-008 перестал быть безвредным"
    )
