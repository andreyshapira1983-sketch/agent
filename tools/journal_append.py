from pathlib import Path

from core.state_integrity import append_state_jsonl_unlocked, state_file_lock
from tools.base import Tool

#: Журналы, у которых ЕСТЬ читатель, и что этот читатель требует от записи.
#:
#: Живой вечер 2026-09-20/21, четыре случая подряд одной формы. Агент писал
#: верное содержание в склад, которого никто не открывает: сначала в
#: `source_registry.jsonl` («там же про источники»), потом в им же созданный
#: `defect_registry.jsonl`, потом в им же созданный `judgements.jsonl`, потом
#: снова в `defect_registry.jsonl` — и каждый раз инструмент отвечал
#: `appended: True`. Запись была, работы не было.
#:
#: Правило он знал и сам его повторял вслух: склад определяется ЧИТАТЕЛЕМ, а
#: не названием. Но знание жило в разговоре, а рука писала по названию.
#: Поэтому знание переехало сюда, к самой руке.
_KNOWN_JOURNALS: dict[str, dict] = {
    "data/self_improvement_issues.jsonl": {
        "reader": "core/campaign_io.py:114 — сборка контекста КАЖДОГО цикла",
        "required": ("fingerprint", "title"),
        "lists": ("evidence", "related_files"),
    },
    "data/episodic_memory.jsonl": {
        "reader": "core/loop_memory_read.py — опыт подаётся в каждый ответ",
        "required": ("goal", "question", "summary"),
        "lists": ("tags", "tools_used"),
    },
    "data/persistent_memory.jsonl": {
        "reader": "core/loop_memory_read.py — постоянная память",
        "required": ("text",),
        "lists": (),
    },
    "data/assumptions.jsonl": {
        "reader": "core/assumption_registry.py",
        "required": ("text",),
        "lists": (),
    },
    "data/causal_claims.jsonl": {
        "reader": "core/causal_store.py",
        "required": ("claim",),
        "lists": (),
    },
    "data/chat_outbox.jsonl": {
        "reader": "панель оператора (live_view) — разговор втроём",
        "required": ("author", "text"),
        "lists": (),
    },
}

#: Слова-заглушки. Поле с таким значением — это дырка, а не запись.
#:
#: Тот же вечер: решение о собственном весе было записано с полем
#: `decision: PENDING — filled from the measured file contents in this turn`.
#: Запись завели затем, чтобы прибор перестал врать молча, — и она молчала
#: о самом решении.
_PLACEHOLDERS: frozenset[str] = frozenset({
    "pending", "todo", "tbd", "fixme", "xxx", "n/a", "na", "none", "-", "—",
    "заполнить", "уточнить", "позже",
})

#: Файлы состояния, у которых есть ХОЗЯИН в коде: класс, который их пишет,
#: знает схему и отвечает за смысл строки. Сырая строка в таком файле — не
#: запись, а подделка состояния.
#:
#: Найдено 2026-09-21. Агент подал заявку на патч этим инструментом прямо в
#: ящик одобрений: строка без id и срока стала вечной висящей заявкой. Проверка
#: того же вечера — тем же вызовом в ящик ложится ОДОБРЕННАЯ заявка на
#: самоправку, подписанная «andre (operator)», и ApprovalInbox принимает её
#: как решение человека. Ворота «проверь, потом разреши» обходились одной
#: строкой.
#:
#: Список полный: `tests/test_an_agent_cannot_sign_its_own_approval.py`
#: сканирует код и падает, если файл, который называет код, не записан ни
#: сюда, ни в `_KNOWN_JOURNALS`.
_OWNED_STATE: dict[str, str] = {
    "data/approval_inbox.jsonl": (
        "это ящик одобрений, его хозяин ApprovalInbox. Решение в нём — "
        "полномочие человека, и сырая строка здесь подделывает его. Заявку "
        "подаёт полоса самоправки или человек. Если тебе нужно подать заявку, "
        "а инструмента для этого нет, — так и скажи человеку"
    ),
    **{f"data/{name}.jsonl": "его пишет код системы через свой класс"
       for name in (
           "alert_acknowledgements", "approval_outcomes", "budget_ledger",
           "burn_in_adoptions", "burn_in_offers", "campaign_ledger",
           "campaign_verdicts", "capability_events", "causal_observations",
           "charter_decisions", "conflict_episodes", "daemon_tick",
           "drive_decisions", "lesson_injections", "lesson_measurements",
           "memory_consolidation", "memory_writes", "mentor_questions",
           "model_routing_policy", "model_usage", "procedural_memory",
           "reasoning_roster", "runtime_schedules", "runtime_tasks",
           "self_build_lessons", "self_build_rules", "self_stops",
           "source_registry", "standing_grant_usage", "subagent_quarantine",
           "tool_receipts", "user_profile", "value_reviews",
       )},
}


def _refuse_owned_state(path: str) -> None:
    owner = _OWNED_STATE.get(path)
    if owner is not None:
        raise PermissionError(
            f"{path}: {owner}. Сюда journal_append не пишет. Журналы, в "
            f"которые писать можно: " + ", ".join(sorted(_KNOWN_JOURNALS))
        )


def _refuse_placeholders(record: dict) -> None:
    """Поле-заглушка — дырка, а не запись, и писать её значит терять работу."""
    for key, value in record.items():
        if isinstance(value, str) and value.strip().casefold().rstrip(".!") in _PLACEHOLDERS:
            raise ValueError(
                f"поле {key!r} осталось заглушкой ({value.strip()!r}): "
                "запись без него ничего не говорит читателю"
            )
        if isinstance(value, str) and value.strip().casefold().startswith(
                tuple(w + " " for w in _PLACEHOLDERS)):
            raise ValueError(
                f"поле {key!r} начинается заглушкой ({value.strip()[:40]!r}): "
                "допиши настоящее значение"
            )


def _refuse_broken_shape(path: str, record: dict, contract: dict) -> None:
    """Запись, которую читатель молча отбросит, лучше не писать вовсе.

    Читатель `self_improvement_issues.jsonl` отбрасывает запись без непустого
    `fingerprint` и раскладывает строку в поле-списке ПОСИМВОЛЬНО: улика из
    105 знаков стала 105 элементами, первый — буква `d` (замер 2026-09-20).
    Оба отказа были молчаливыми, и оба стоили целого вечера.
    """
    missing = [f for f in contract["required"]
               if not str(record.get(f) or "").strip()]
    if missing:
        raise ValueError(
            f"{path}: читатель ({contract['reader']}) отбросит эту запись — "
            f"не заполнено обязательное: {', '.join(missing)}"
        )
    flattened = [f for f in contract["lists"] if isinstance(record.get(f), str)]
    if flattened:
        raise ValueError(
            f"{path}: поля {', '.join(flattened)} читаются как СПИСОК строк, "
            "а переданы строкой — читатель разложит её посимвольно"
        )


class JournalAppendTool(Tool):
    name = "journal_append"
    description = (
        "Append a single JSON record to a data/*.jsonl journal file through the "
        "state-file lock. The path must live under data/ and end in .jsonl; the "
        "record must be a dict. Boundary violations raise ValueError WITHOUT writing. "
        "The result names the READER of that journal; a journal nobody reads "
        "comes back with a warning, because a record no one opens is not work. "
        "For a known journal the record is checked against what its reader "
        "requires (missing required field, or a string where a list is read), "
        "and a field left as a placeholder (PENDING, TODO) is refused."
    )
    # Append-only store; reversibility is guaranteed by the append-only journal semantics (no delete exists)
    risk = "reversible"
    arguments = (
        "path: str — data/<name>.jsonl, relative to the workspace root; "
        "record: dict — one JSON object to append"
    )

    def __init__(self, *, workspace_root):
        self._workspace_root = workspace_root

    def _resolve(self, path: str) -> Path:
        """The file under `<workspace>/data/` this path names — or ValueError.

        Block 7 (audit W2, 2026-09-03): `workspace_root` was stored and never
        read; the boundary was a string prefix, so `data/../../x.jsonl` passed
        and the file landed relative to the CURRENT DIRECTORY, not the
        workspace. The path is now resolved against the workspace and must
        stay inside its `data/` after resolution.
        """
        if not path.startswith("data/") or not path.endswith(".jsonl"):
            raise ValueError(f"path must live under data/ and end in .jsonl: {path}")
        root = Path(self._workspace_root).resolve()
        target = (root / path).resolve()
        data_dir = (root / "data").resolve()
        if target.parent != data_dir and data_dir not in target.parents:
            raise ValueError(f"path escapes the workspace data/ directory: {path}")
        return target

    def run(self, **kwargs):
        allowed = {"path", "record"}
        extra = set(kwargs) - allowed
        if extra:
            raise PermissionError(f"Unexpected arguments: {sorted(extra)}")
        path = kwargs["path"]
        record = kwargs["record"]
        if not isinstance(record, dict):
            raise ValueError(f"record must be a dict, got {type(record).__name__}")
        target = self._resolve(str(path))
        _refuse_owned_state(str(path))
        contract = _KNOWN_JOURNALS.get(str(path))
        _refuse_placeholders(record)
        if contract:
            _refuse_broken_shape(str(path), record, contract)
        with state_file_lock(target):
            append_state_jsonl_unlocked(target, [record])
        result = {"path": path, "appended": True}
        # Кто это прочтёт — часть ответа, а не украшение. Раньше инструмент
        # отвечал `appended: True` и на запись в файл, которого не открывает
        # никто, и отличить сделанную работу от потраченной было нельзя.
        if contract:
            result["reader"] = contract["reader"]
        else:
            result["reader"] = None
            result["warning"] = (
                f"никто не читает {path}: этот журнал не связан ни с одним "
                f"читателем в коде. Склад определяется читателем, а не "
                f"названием. Журналы с читателем: "
                + ", ".join(sorted(_KNOWN_JOURNALS))
            )
        return result

    def validate_output(self, output):
        reasons = []
        if not isinstance(output, dict):
            return False, [f"expected dict, got {type(output).__name__}"]
        if "path" not in output or not isinstance(output["path"], str):
            reasons.append("path must be a str")
        if "appended" not in output or output["appended"] is not True:
            reasons.append("appended must be True")
        return (not reasons), reasons
