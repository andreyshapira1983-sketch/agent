"""Текст записи собирается в момент исполнения, а не при планировании.

Корень, названный оператором 2026-09-22: аргументы шагов фиксируются планом,
поэтому текст файла сочиняется ДО того, как чтения вернулись, — отсюда
«тест импортировал несуществующую функцию» (10:45), «правка по памяти»
(14:37) и конспект-болванка (17:02). Откладывание записи на следующий круг
(`core/observation_round.defer_blind_writes`) было подпоркой: оно тратило
круги и не отменяло самого сочинения вслепую.

Здесь план может назвать не текст, а ЗАДАНИЕ на текст:
`file_write(path=..., write_instruction="...")`. Текст собирается перед самим
вызовом инструмента — уже по выводам шагов, исполненных в этом же пакете.
Пустой или шаблонный ответ не пишется: шаг проваливается с названной
причиной, как неразрешённая ссылка.
"""
from __future__ import annotations

import re
from typing import Any

#: Сколько вывода одного шага уходит в сборку текста. Было 4000 и 20000:
#: 24.09 писатель правки не видел конца compose_content (строка ~130 файла
#: в 18 тыс. знаков) и не мог вставить туда вызов — не видел места.
_PER_OUTPUT_CHARS = 12000
_TOTAL_CHARS = 48000
#: Чтения, которые писатель помнит весь ход, а не только текущий план.
_READ_TOOLS = frozenset({"file_read", "find_in_files", "list_dir", "read_logs",
                         "diff_file", "convert_file", "python_probe"})
_REMEMBERED_READS = 12
#: Файл правки для patch_check и признаки того, что в нём блоки, а не слова.
_EDITS_NAME = "edits.txt"
_FILE_LINE = re.compile(r"^FILE:\s*\S", re.MULTILINE)
_BLOCK_LINE = re.compile(r"^<<<<<<< (?:SEARCH|LINES \d+-\d+)", re.MULTILINE)
_EDITS_FORM = (
    "\n\nФорма файла правки (её читает patch_check). Для КАЖДОГО изменения:\n"
    "FILE:<путь>\n<<<<<<< SEARCH\n<строки из вывода чтения, дословно>\n=======\n"
    "<новые строки>\n>>>>>>> REPLACE\n"
    "Новый файл — тот же блок с пустым SEARCH, но только если файла ещё нет. Дописать в "
    "существующий: SEARCH — его последние 2–3 строки из вывода чтения, REPLACE — они же и "
    "новый текст после них; либо положи новый тест в новый файл. Не unified diff (---/+++/@@), не «SEARCH:» "
    "с двоеточием; метки замысла плана — не образец. Если нужных строк в выводах нет, "
    "ответь одной строкой «нужные строки не прочитаны» — запись не состоится, "
    "и план прочитает их заново."
)
_SYSTEM = (
    "Ты пишешь СОДЕРЖИМОЕ файла по заданию. В ответе — только текст файла, "
    "без объяснений, без ```-ограды и без вступления. Пиши по выводам шагов "
    "ниже: цитируй их дословно там, где задание просит цитату, и не выдумывай "
    "того, чего в них нет. Пустых мест-заглушек вроде «(заполняется)» быть не "
    "должно: чего нет в выводах, о том напиши прямо, что этого нет."
)


#: Потолок ответа модели на один файл. Файл правки для patch_check или
#: конспект укладываются с запасом; больше — почти всегда зацикливание.
_WRITE_MAX_TOKENS = 6000
#: Кусок из стольких строк, повторённый столько раз подряд или вразброс, —
#: зацикливание, а не текст.
_RUNAWAY_BLOCK_LINES = 4
_RUNAWAY_REPEATS = 4


def _was_truncated(llm: Any) -> bool:
    """Оборван ли последний ответ на пределе (у маршрутизатора — у его модели)."""
    for obj in (llm, getattr(llm, "_llm", None)):
        if obj is not None and getattr(obj, "last_answer_was_truncated", False) is True:
            return True
    return False


def _runaway_block(text: str) -> int | None:
    """Сколько раз повторён самый частый непустой кусок из нескольких строк, если это зацикливание."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    step = _RUNAWAY_BLOCK_LINES
    if len(lines) < step * _RUNAWAY_REPEATS:
        return None
    counts: dict[str, int] = {}
    for i in range(len(lines) - step + 1):
        block = "\n".join(lines[i:i + step])
        if len(block) >= 80:
            counts[block] = counts.get(block, 0) + 1
    top = max(counts.values(), default=0)
    return top if top >= _RUNAWAY_REPEATS else None


def _outputs_block(done: list[tuple[Any, dict[str, Any] | None, Any]]) -> str:
    from core.observation_round import _as_text

    parts: list[str] = []
    budget = _TOTAL_CHARS
    for step, outcome, _trigger in done:
        if not outcome or budget <= 0:
            continue
        spec = getattr(step, "action_spec", None) or {}
        label = spec.get("source_label") or spec.get("tool_name") or "шаг"
        text = _as_text(outcome.get("output") if isinstance(outcome, dict) else outcome)
        shown = text[:min(_PER_OUTPUT_CHARS, budget)]
        budget -= len(shown)
        parts.append(f"--- вывод шага {getattr(step, 'order', '?')} ({label}):\n{shown}")
    return "\n\n".join(parts)


def _writer_llm(loop: Any) -> Any:
    """Модель для сборки текста: синтезатор, иначе та, что есть у цикла."""
    router = getattr(loop, "model_router", None)
    if router is not None:
        try:
            from core.models import ModelRole

            routed = router.for_role(ModelRole.SYNTHESIZER)
        except Exception:  # noqa: BLE001 — нет маршрута роли: пишем тем, что есть
            routed = None
        if routed is not None:
            return routed
    return getattr(loop, "llm", None)


def compose_content(loop: Any, step: Any, done: list[tuple[Any, dict[str, Any] | None, Any]]) -> str:
    """Собрать текст файла по заданию шага и выводам уже исполненных шагов."""
    from core.placeholder_text import looks_like_unfilled_content

    spec = step.action_spec or {}
    arguments = spec.get("arguments") or {}
    instruction = str(arguments.get("write_instruction") or "").strip()
    path = str(arguments.get("path") or "")
    outputs = _outputs_block(_with_earlier_reads(loop, done))
    llm = _writer_llm(loop)
    if llm is None:
        raise RuntimeError("нет модели для сборки текста записи")
    user = (f"Файл: {path}\nЗадание: {instruction}"
            + (_EDITS_FORM if _is_edits(path) else "") + "\n\n"
            + (f"Выводы шагов этого хода:\n{outputs}" if outputs
               else "В этом ходе шаги ещё ничего не вернули."))
    # Предел и БЕЗ продолжения. Замер 2026-09-24: дважды за ночь модель
    # зациклилась на одном блоке правки, а обёртка честно «продолжила» её
    # четыре раза с растущим бюджетом — 159k токенов на вход, 116 736 на
    # выход, $0.58 за вызов и edits.txt на 568 КБ из сотен повторов.
    text = str(llm.complete(system=_SYSTEM, user=user, max_tokens=_WRITE_MAX_TOKENS,
                            temperature=0.2, allow_continuation=False) or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if not text:
        raise RuntimeError("модель вернула пустой текст файла")
    if _was_truncated(llm):
        raise RuntimeError(
            f"текст записи оборван на пределе {_WRITE_MAX_TOKENS} токенов — "
            "задание слишком большое или модель зациклилась; раздели запись на части")
    if (repeated := _runaway_block(text)) is not None:
        raise RuntimeError(
            f"текст записи повторяет один и тот же кусок {repeated} раз — "
            "модель зациклилась, файл не записан")
    if looks_like_unfilled_content(text):
        raise RuntimeError("собранный текст — незаполненный шаблон")
    _refuse_edits_without_blocks(path, text)
    _refuse_note_without_its_contract(loop, path, text)
    return text


def _is_edits(path: str) -> bool:
    return path.replace("\\", "/").rsplit("/", 1)[-1] == _EDITS_NAME


def _refuse_edits_without_blocks(path: str, text: str) -> None:
    """В файл правки идут только блоки: отказ словами — провал шага, не содержимое.

    24.09: из шести записей edits.txt за 50 секунд три были текстом отказа
    («нужные строки не прочитаны», «Файл не найден.») — и затирали прошлую,
    почти готовую правку.
    """
    if _is_edits(path) and not (_FILE_LINE.search(text) and _BLOCK_LINE.search(text)):
        raise RuntimeError(
            "в файл правки пошёл текст без блоков (FILE: и <<<<<<< SEARCH или LINES a-b) — "
            f"файл не перезаписан; начало текста: {text[:160]!r}")


def remember_read(loop: Any, step: Any, outcome: Any) -> None:
    """Запомнить вывод чтения на весь ход (след = ход): после перепланирования
    писатель его видит. 24.09 после каждого перепланирования писатель писал
    «нужные строки не прочитаны» — прочитанное в прошлом круге для него исчезало."""
    spec = getattr(step, "action_spec", None) or {}
    if spec.get("tool_name") not in _READ_TOOLS or not isinstance(outcome, dict):
        return
    if outcome.get("status", "success") != "success":
        return
    turn = str(getattr(getattr(loop, "log", None), "trace_id", "") or "")
    store = getattr(loop, "_writer_reads", None)
    if not store or store[0] != turn:
        store = (turn, [])
        loop._writer_reads = store
    store[1].append((step, outcome, None))
    del store[1][:-_REMEMBERED_READS]


def _with_earlier_reads(loop: Any, done: list[Any]) -> list[Any]:
    """Выводы текущего плана, затем — чтения прошлых кругов этого же хода."""
    store = getattr(loop, "_writer_reads", None)
    turn = str(getattr(getattr(loop, "log", None), "trace_id", "") or "")
    if not store or store[0] != turn:
        return list(done)
    here = {id(item[0]) for item in done}
    return list(done) + [item for item in store[1] if id(item[0]) not in here]


def _refuse_note_without_its_contract(loop: Any, path: str, text: str) -> None:
    """Конспект без источника, цитаты и проверки не становится файлом.

    Тот же договор, что судит цель (`core.note_contract`), только спрошенный
    ДО записи. Замер 2026-09-23: договор проверялся лишь при судействе, и
    пустышка всё равно ложилась на диск — 40 конспектов из 127 (31%). Цикл
    помечен `empty`, система знает, что работы не было, а файл лежит рядом с
    настоящими.

    Отказ НАЗЫВАЕТ, чего не хватает: провал без причины агент разгадывает
    сам и разгадывает неверно. Пустая правка проваливается так же.
    """
    from core.note_contract import NOTES_DIR, contract_gaps

    if not str(path).replace("\\", "/").startswith(NOTES_DIR + "/"):
        return
    root = None
    getter = getattr(loop, "_file_read_workspace_root", None)
    if callable(getter):
        try:
            root = getter()
        except Exception:  # noqa: BLE001 — недоступный корень не запирает запись
            root = None
    if root is None:
        return
    gaps = contract_gaps(text, root)
    if gaps:
        raise RuntimeError("конспект не выполняет свой договор: " + "; ".join(gaps))
