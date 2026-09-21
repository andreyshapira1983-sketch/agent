"""Предмет отрицания — то, что отрицается, а не подлежащее соседней части.

2026-09-21, 05:36. Агент честно доложил о собственном сломанном приборе:

    «python_probe запускается в отдельном временном каталоге (temp cwd), а не
    в корне рабочей области, поэтому sys.path не содержит каталога с пакетом
    core — замер показал cwd=…\\python_probe__dlg_gs4 и core FAIL
    ModuleNotFoundError No module named 'core' [tool:python_probe]»

и получил `[claim-refuted]` с кодом `absence_refuted_by_evidence`, «пропавшим»
назван `python_probe`. Фраза отрицает «каталог с пакетом core в sys.path».
Ворота взяли литералы из ВСЕХ частей предложения без глагола наличия — и
подлежащее первой части, `python_probe`, стало предметом отрицания. Вывод
самого python_probe его, разумеется, содержит. Правда объявлена ложью; то же
со вторым куском того ответа («это решение, а не факт… python_probe падает»).

Эти ворота латали уже шесть раз, и каждый раз исключали один сорт соседей:
найденное, перечисленное, место поиска, часть с глаголом наличия. Правило,
закрывающее класс: голые литералы берутся только из части, где стоит само
отрицание; из соседних — лишь названное явно (в апострофах или кавычках),
как в «`foo` — нет такой функции». Хвост сообщения об ошибке в соседней части —
чужой голос, не предмет.

Настоящие опровержения остаются опровержениями — они проверены ниже.
"""
from __future__ import annotations

from core.verifier_absence import absence_refuted_by_excerpt

_PROBE_OUTPUT = (
    "exit_code: 1\nstdout: CWD C:\\Users\\andre\\AppData\\Local\\Temp\\python_probe_7z4\n"
    "stderr: Traceback (most recent call last):\n"
    "ModuleNotFoundError: No module named 'core'\n"
    "tool: python_probe"
)


def test_the_honest_report_about_the_probe_is_not_refuted() -> None:
    claim = (
        "python_probe запускается в отдельном временном каталоге (temp cwd), а не в "
        "корне рабочей области, поэтому sys.path не содержит каталога с пакетом core — "
        "замер показал cwd=C:\\Users\\andre\\AppData\\Local\\Temp\\python_probe__dlg_gs4 "
        "и core FAIL ModuleNotFoundError No module named 'core' [tool:python_probe]"
    )
    assert not absence_refuted_by_excerpt(claim, _PROBE_OUTPUT)


def test_a_decision_that_names_the_broken_probe_is_not_refuted() -> None:
    claim = (
        "Заменять меру на контрактный сигнал — это решение, а не факт, и его надо "
        "принимать по замеру, которого у меня сейчас нет: я не могу исполнить свой "
        "код, потому что python_probe падает с ModuleNotFoundError: No module named "
        "'core' [tool:python_probe]"
    )
    assert not absence_refuted_by_excerpt(claim, _PROBE_OUTPUT)


def test_a_denied_field_that_is_present_is_still_refuted() -> None:
    claim = 'В записи нет поля "lesson" [file:data/x.jsonl]'
    assert absence_refuted_by_excerpt(claim, '{"lesson": "урок", "id": 1}')


def test_a_denied_backticked_name_that_is_present_is_still_refuted() -> None:
    claim = "`itertools.batched` не существует в этой версии [tool:python_probe]"
    assert absence_refuted_by_excerpt(claim, "itertools.batched <function batched>")


def test_a_subject_named_in_the_neighbouring_part_is_still_judged() -> None:
    """«`foo` — нет такой функции»: предмет назван явно в соседней части."""
    claim = "`resolve_target` — нет такой функции в модуле [file:core/x.py]"
    assert absence_refuted_by_excerpt(claim, "def resolve_target(path):\n    return path")


def test_a_bare_literal_in_the_denying_part_is_still_judged() -> None:
    claim = "в модуле не определена функция resolve_target [file:core/x.py]"
    assert absence_refuted_by_excerpt(claim, "def resolve_target(path):\n    return path")
