"""Compose file text at execution time, not at planning: a plan may carry a write_instruction instead of the text."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# How much of one step's output goes into composing the text.
_PER_OUTPUT_CHARS = 12000
_TOTAL_CHARS = 48000
# Reads the writer remembers for the whole turn, not only the current plan.
_READ_TOOLS = frozenset({"file_read", "find_in_files", "list_dir", "read_logs",
                         "diff_file", "convert_file", "python_probe"})
_REMEMBERED_READS = 12
# Edit file for patch_check and markers showing it holds blocks, not words.
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


# Ceiling of the model reply for one file; more is almost always a loop.
_WRITE_MAX_TOKENS = 6000
# A chunk of this many lines repeated this many times in a row or scattered is a loop, not text.
_RUNAWAY_BLOCK_LINES = 4
_RUNAWAY_REPEATS = 4


def _was_truncated(llm: Any) -> bool:
    """Whether the last reply was cut off at the limit (for a router, at its model's limit)."""
    for obj in (llm, getattr(llm, "_llm", None)):
        if obj is not None and getattr(obj, "last_answer_was_truncated", False) is True:
            return True
    return False


def _runaway_block(text: str) -> int | None:
    """How many times the most frequent non-empty multi-line chunk repeats, if that is a loop."""
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
    """Model for composing the text: the synthesizer, else the loop's own."""
    router = getattr(loop, "model_router", None)
    if router is not None:
        try:
            from core.models import ModelRole

            routed = router.for_role(ModelRole.SYNTHESIZER)
        except Exception:  # noqa: BLE001 — no role route: write with what we have
            routed = None
        if routed is not None:
            return routed
    return getattr(loop, "llm", None)


def compose_content(loop: Any, step: Any, done: list[tuple[Any, dict[str, Any] | None, Any]]) -> str:
    """Compose file text from the step's instruction and the outputs of steps already executed."""
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
            + (_EDITS_FORM if _is_edits(path) else "") + _current_edits(loop, path) + "\n\n"
            + (f"Выводы шагов этого хода:\n{outputs}" if outputs
               else "В этом ходе шаги ещё ничего не вернули."))
    # A limit and NO continuation: continuing a looping reply multiplied tokens and produced a huge
    # edits file.
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


def _current_edits(loop: Any, path: str) -> str:
    getter = getattr(loop, "_file_read_workspace_root", None)
    if not _is_edits(path) or not callable(getter):
        return ""
    try:
        root = Path(getter()).resolve()
        target = (root / path).resolve()
        text = target.read_text(encoding="utf-8", errors="replace") if target.is_relative_to(root) else ""
    except (OSError, TypeError, ValueError):
        return ""
    if not text.strip():
        return ""
    from core.redaction import prepare_text_for_llm_boundary

    shown, _meta = prepare_text_for_llm_boundary(text[:_PER_OUTPUT_CHARS])
    cut = f" (показано {_PER_OUTPUT_CHARS} из {len(text)} знаков)" if len(text) > _PER_OUTPUT_CHARS else ""
    return ("\n\nСейчас в этом файле лежит (запись заменит его ЦЕЛИКОМ; блоки, которые остаются "
            f"в силе, перенеси дословно){cut}:\n{shown}")


def _refuse_edits_without_blocks(path: str, text: str) -> None:
    """Only blocks go into the edit file: a refusal in words fails the step and is not content."""
    if _is_edits(path) and not (_FILE_LINE.search(text) and _BLOCK_LINE.search(text)):
        raise RuntimeError(
            "в файл правки пошёл текст без блоков (FILE: и <<<<<<< SEARCH или LINES a-b) — "
            f"файл не перезаписан; начало текста: {text[:160]!r}")


def remember_read(loop: Any, step: Any, outcome: Any) -> None:
    """Remember a read's output for the whole turn (trace = turn), so the writer still sees it after a replan."""
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
    """Outputs of the current plan, then reads from earlier rounds of the same turn."""
    store = getattr(loop, "_writer_reads", None)
    turn = str(getattr(getattr(loop, "log", None), "trace_id", "") or "")
    if not store or store[0] != turn:
        return list(done)
    here = {id(item[0]) for item in done}
    return list(done) + [item for item in store[1] if id(item[0]) not in here]


def _refuse_note_without_its_contract(loop: Any, path: str, text: str) -> None:
    """A note without source, quote and check does not become a file.

    The same contract that judges the goal (`core.note_contract`), asked BEFORE the write.
    The refusal NAMES what is missing; an empty edit fails the same way.
    """
    from core.note_contract import NOTES_DIR, contract_gaps

    if not str(path).replace("\\", "/").startswith(NOTES_DIR + "/"):
        return
    root = None
    getter = getattr(loop, "_file_read_workspace_root", None)
    if callable(getter):
        try:
            root = getter()
        except Exception:  # noqa: BLE001 — an unreachable root must not lock the write
            root = None
    if root is None:
        return
    gaps = contract_gaps(text, root)
    if gaps:
        raise RuntimeError("конспект не выполняет свой договор: " + "; ".join(gaps))
