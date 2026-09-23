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

from typing import Any

#: Сколько вывода одного шага уходит в сборку текста.
_PER_OUTPUT_CHARS = 4000
_TOTAL_CHARS = 20000
_SYSTEM = (
    "Ты пишешь СОДЕРЖИМОЕ файла по заданию. В ответе — только текст файла, "
    "без объяснений, без ```-ограды и без вступления. Пиши по выводам шагов "
    "ниже: цитируй их дословно там, где задание просит цитату, и не выдумывай "
    "того, чего в них нет. Пустых мест-заглушек вроде «(заполняется)» быть не "
    "должно: чего нет в выводах, о том напиши прямо, что этого нет."
)


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
    outputs = _outputs_block(done)
    llm = _writer_llm(loop)
    if llm is None:
        raise RuntimeError("нет модели для сборки текста записи")
    user = (f"Файл: {path}\nЗадание: {instruction}\n\n"
            + (f"Выводы шагов этого хода:\n{outputs}" if outputs
               else "В этом ходе шаги ещё ничего не вернули."))
    text = str(llm.complete(system=_SYSTEM, user=user, temperature=0.2) or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if not text:
        raise RuntimeError("модель вернула пустой текст файла")
    if looks_like_unfilled_content(text):
        raise RuntimeError("собранный текст — незаполненный шаблон")
    _refuse_note_without_its_contract(loop, path, text)
    return text


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
