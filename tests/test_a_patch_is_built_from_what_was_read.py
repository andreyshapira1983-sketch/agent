"""Блоки правки собираются по прочитанному, а не сочиняются планом.

Замер 2026-09-23 утром. За прогон агент написал 28 файлов правки, и ВСЕ 28
пришли с готовым `content` от планировщика — ни один через `write_instruction`.
Из тринадцати правок пять умерли с «the patch did not apply»: сторона SEARCH
не совпала ни с одним куском файла.

Последний случай под запись. Агенту дали починить регистрацию веб-источников;
он написал для `tools/web_fetch.py` блок с `import requests` и функцией
`fetch()` — в настоящем файле нет ни того, ни другого, там `urllib` и класс
`WebFetchTool`. Файл он в том ходе не читал НИ РАЗУ; след это подтверждает.

Планировщик дословного текста чужого файла знать не может: он его не видел.
Ночью этот корень закрыли для обычных записей — текст собирается перед самим
вызовом инструмента, по выводам уже исполненных чтений
(`core/write_at_execution.py`). На файлы правки лекарство не
распространялось, потому что план всегда передавал готовый текст.
"""
from __future__ import annotations

from core.step_sanitizer import sanitize_step

_PATCH = "proposals/selffix/web_source_not_registered/edits.txt"
_INVENTED = ("FILE:tools/web_fetch.py\n<<<<<<< SEARCH\nimport requests\n"
             "=======\nimport requests\nfrom core.ingestion import ingest_web_topic\n"
             ">>>>>>> REPLACE\n")


def _step(path: str, **extra):
    warnings: list[str] = []
    step = sanitize_step("file_write", {"path": path, **extra}, None, 0, warnings)
    return step, warnings


def test_a_planned_patch_text_becomes_a_task() -> None:
    """Дословно тот случай: выдуманный SEARCH для непрочитанного файла."""
    step, warnings = _step(_PATCH, content=_INVENTED)

    assert step is not None, "шаг выброшен — правку стало нечем писать"
    args = step["arguments"]
    assert "content" not in args, "готовый текст правки всё ещё уходит в запись"
    assert args["write_instruction"], "задание не поставлено"
    assert "ДОСЛОВНО" in args["write_instruction"]
    assert any("собираются по ПРОЧИТАННОМУ" in w for w in warnings), warnings


def test_the_planners_intent_is_not_thrown_away() -> None:
    """Замысел плана переживает замену: он и есть содержание задания."""
    step, _ = _step(_PATCH, content="FILE:core/ingestion.py\nзаменить X на Y")

    assert "core/ingestion.py" in step["arguments"]["write_instruction"]


def test_an_ordinary_write_keeps_its_text() -> None:
    """Граница: конспект по-прежнему пишется тем текстом, что дал план."""
    step, warnings = _step("data/notes/20260923_competence_math.md",
                           content="# Теорема\n\nИсточник: книга, строка 5.")

    assert step["arguments"]["content"].startswith("# Теорема")
    assert "write_instruction" not in step["arguments"]
    assert warnings == []


def test_a_patch_written_by_task_passes_through() -> None:
    """Если план сразу дал задание, ничего подменять не нужно."""
    step, warnings = _step(_PATCH, write_instruction="собери блоки по прочитанному")

    assert step["arguments"]["write_instruction"] == "собери блоки по прочитанному"
    assert warnings == []


def test_only_the_patch_file_of_the_self_repair_route_is_covered() -> None:
    """Правило узкое: соседний файл в той же папке под него не подпадает."""
    step, warnings = _step("proposals/selffix/case/notes.md", content="произвольный текст")

    assert step["arguments"]["content"] == "произвольный текст"
    assert warnings == []
