import datetime as _dt
import re
from pathlib import Path

from core.state_integrity import (
    append_state_jsonl_unlocked,
    read_state_jsonl_unlocked,
    state_file_lock,
)
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
    "data/own_decisions.jsonl": {
        "reader": "core/patch_route.py — следующая цель починки того же дефекта (core/own_decisions.py)",
        "required": ("id", "about", "decision", "because"),
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
    "заполнить", "уточнить", "позже", "placeholder", "заглушка",
})

#: Журнал решений агента о своём коде (core/own_decisions.py). Решение — выбор по уже
#: собранным уликам, а не обещание решить, не план и не вставленный код.
_DECISIONS_PATH = "data/own_decisions.jsonl"
_NOT_A_DECISION = ("решение будет", "будет сформулировано", "сначала ")
_CODE_START = ('"""', "'''", "```", "def ", "class ", "import ", "from ")
_EVIDENCE_RE = re.compile(r"[\w./-]+\.(?:py|jsonl|json|md|txt|log|toml|yaml|yml)\b")

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
    "data/self_repair_log.jsonl": (
        "это журнал самопочинки, его пишет core/patch_route после настоящего "
        "прогона patch_check и пути применения. Строка «правка поставлена» от руки "
        "подделывала бы сводку, которую читает оператор."
    ),
    "data/approval_inbox.jsonl": (
        "это ящик одобрений, его хозяин ApprovalInbox. Решение в нём — "
        "полномочие человека, и сырая строка здесь подделывает его. Заявку "
        "подаёт полоса самоправки или человек. Если тебе нужно подать заявку, "
        "а инструмента для этого нет, — так и скажи человеку"
    ),
    "data/failure_cards.jsonl": (
        "это карточки прошлых ошибок, их хозяин CardStore (core/failure_cards.py). "
        "Счёт «урок помог N из M» ведёт среда по следующему вызову инструмента; "
        "строка от руки подделала бы этот счёт. Урок рождается сам в конце хода "
        "из пары «провал → удача»"
    ),
    "data/judge_rulings.jsonl": (
        "это решения Клода по очереди на суд (core/judge_queue.py). Их пишет "
        "только команда scripts/judge_queue.py мимо твоих инструментов: суд, "
        "который подсудимый может дописать, не суд"
    ),
    "data/judge_queue.jsonl": (
        "это очередь твоих «цель достигнута» на суд Клода; её пишет судья цели "
        "(core/campaign_verdict.py) по наблюдению. Строка от руки поставила бы "
        "на суд то, чего судья не видел"
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
           "source_registry", "splitter_refusals", "standing_grant_usage", "subagent_predictions", "subagent_quarantine",
           "tool_receipts", "user_profile", "value_reviews", "workflow_memory",
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


#: Поля времени, которые читатели разбирают как момент. `tests/
#: test_a_naive_timestamp_is_read_as_utc.py` меряет тот же список в живом
#: состоянии; 2026-09-20 сюда пришло `"ts": "2026-09-20"` — дата без часа и
#: зоны, и замер покраснел. Запись без зоны — другой момент для каждого читателя.
_STAMP_KEYS = ("created_at", "updated_at", "completed_at", "started_at",
               "heartbeat_at", "run_after", "first_seen", "last_seen", "ts")
_ZONE_RE = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")


def _refuse_hollow_decision(path: str, record: dict, root: Path) -> None:
    """Запись решения принимается, только если это решение и оно опирается на файл, который есть."""
    if path != _DECISIONS_PATH:
        return
    decision = str(record.get("decision") or "").strip()
    if decision.startswith(_CODE_START):
        raise ValueError("decision — это текст кода, а не решение: напиши, что делаешь и почему")
    if decision.casefold().startswith(_NOT_A_DECISION):
        raise ValueError("decision — обещание или план, а не решение: реши по уже собранным "
                         "уликам, что делаешь дальше, и запиши это")
    cited = _EVIDENCE_RE.findall(str(record.get("because") or ""))
    if not any((root / c.lstrip("./")).is_file() for c in cited):
        raise ValueError("because не называет ни одного существующего файла-улики "
                         f"(названо: {cited[:5] or 'ничего'}): укажи путь к журналу, коду или заметке")


def _refuse_naive_stamps(record: dict) -> None:
    for key in _STAMP_KEYS:
        value = record.get(key)
        if isinstance(value, str) and len(value) >= 10 and not _ZONE_RE.search(value):
            raise ValueError(
                f"поле {key!r} = {value!r}: время без часового пояса. Пиши "
                "полный момент в UTC, например 2026-09-20T17:17:00+00:00"
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


#: Путь ГОЛОСА — единственный журнал, который человек читает как обращение к
#: себе. Панель оператора показывает именно его.
VOICE_PATH = "data/chat_outbox.jsonl"

#: Сколько раз за сутки агент вправе заговорить ПЕРВЫМ.
#:
#: Открыто словом оператора 2026-09-23 вместе с потолком, а не после него.
#: Собрано из замеров внимания (см. заметку agent-autonomy-research-2):
#: восстановление после прерывания — около 23 минут, прерывание в пять секунд
#: утраивает число ошибок в сложной работе, а «умное» прерывание неврологически
#: не отличается от глупого — полезность повода не отменяет цены. Жёсткий
#: потолок 3-5 обращений в сутки на человека; взято верхнее.
#:
#: Потолок ЖЁСТКИЙ, а не мягкая цель: исчерпан — обращение ждёт следующих
#: суток. Считается по самому файлу, а не по счётчику в памяти: перезапуск
#: процесса не обнуляет чужое внимание.
VOICE_CALLS_PER_DAY = 5



def _stamp_voice_record(path: str, record: dict) -> dict:
    """Запись голоса получает время, если его не поставил вызывающий.

    Замер 2026-09-23 (через час после того, как потолок голоса был введён):
    потолок НЕ РАБОТАЛ. В живых записях ящика лежат только `author` и `text`
    — времени нет, — а счётчик отбирал записи по дате и потому видел ноль
    обращений за сутки при одиннадцати записях в файле и двух сделанных за
    этот же день. Мои тесты прошли, потому что в тестовых записях время было:
    третий случай за день, когда зелёные тесты не увидели живой поломки.

    Чинится У ИСТОКА, а не в счётчике. Считать записи без времени
    «сегодняшними» значило бы мгновенно съесть весь запас старыми записями и
    заткнуть агента; считать их «не сегодняшними» — то, что и происходило.
    Обращение к человеку обязано знать, когда оно сделано: без этого нельзя
    ни отмерить суточный запас, ни прочитать переписку по порядку.

    Поле не перезаписывается: время, названное вызывающим, остаётся его.
    """
    if path != VOICE_PATH or "ts" in record:
        return record
    stamped = dict(record)
    stamped["ts"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    return stamped


def _voice_calls_today(target: Path) -> int:
    """Сколько раз агент уже заговорил первым за нынешние сутки UTC."""
    if not target.exists():
        return 0
    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    count = 0
    try:
        for row in read_state_jsonl_unlocked(target):
            payload = row.get("payload", row) if isinstance(row, dict) else {}
            if not isinstance(payload, dict):
                continue
            if str(payload.get("author") or "").strip().lower() != "agent":
                continue
            stamp = str(payload.get("ts") or payload.get("timestamp") or "")
            if stamp[:10] == today:
                count += 1
    except Exception:  # noqa: BLE001 — нечитаемый журнал не запирает голос
        return 0
    return count


def _refuse_over_voice_budget(path: str, target: Path) -> None:
    """Громкий отказ, когда суточный запас обращений исчерпан.

    Отказ НАЗЫВАЕТ правило: агент, который не знает причины, угадывает её —
    и угадывает неверно (тот же довод, что у двери памяти).
    """
    if path != VOICE_PATH:
        return
    used = _voice_calls_today(target)
    if used >= VOICE_CALLS_PER_DAY:
        raise PermissionError(
            f"voice budget spent: {used} of {VOICE_CALLS_PER_DAY} calls to the "
            "human already made today (UTC). The ceiling is hard, not a target "
            "— speaking first costs another person's attention, and the "
            "recovery from one interruption is measured in ~23 minutes. Fold "
            "what you wanted to say into tomorrow's first call, or write it to "
            "data/self_improvement_issues.jsonl, which the context of EVERY "
            "cycle reads without costing anyone attention."
        )


#: Окно повтора (repeat_interval у Alertmanager): тот же случай не повторяется
#: человеку раньше, чем через сутки.
VOICE_REPEAT_WINDOW = _dt.timedelta(hours=24)
_VOICE_ID_RE = re.compile(r"sii_[0-9a-f]{8,}")
_VOICE_NAME_RE = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")
_VOICE_WORD_RE = re.compile(r"[a-zа-яё_]{4,}")


def _voice_fingerprint(text: str) -> tuple[set[str], set[str], set[str]]:
    low = (text or "").lower()
    return set(_VOICE_ID_RE.findall(low)), set(_VOICE_NAME_RE.findall(low)), set(_VOICE_WORD_RE.findall(low))


def _same_voice_case(a: str, b: str) -> bool:
    """Тот же случай: общий id дефекта, или общее имя сигнала при заметном
    совпадении слов, или просто почти тот же текст."""
    ids_a, names_a, words_a = _voice_fingerprint(a)
    ids_b, names_b, words_b = _voice_fingerprint(b)
    union = words_a | words_b
    jaccard = len(words_a & words_b) / len(union) if union else 0.0
    return bool(ids_a & ids_b) or (bool(names_a & names_b) and jaccard >= 0.2) or jaccard >= 0.5


def _refuse_repeated_voice(path: str, target: Path, record: dict) -> None:
    """Один случай — одно обращение (план субботы ж, 24.09).

    24.09 все пять суточных обращений ушли за две минуты, и все — одна
    жалоба на self_contradiction. Практика оповещений (Alertmanager:
    отпечаток + repeat_interval): повтор того же случая в окне не шлётся.
    """
    if path != VOICE_PATH or not target.exists():
        return
    text = str(record.get("text") or "")
    now = _dt.datetime.now(_dt.timezone.utc)
    try:
        rows = read_state_jsonl_unlocked(target)
    except Exception:  # noqa: BLE001 — нечитаемый журнал не запирает голос
        return
    for row in rows:
        payload = row.get("payload", row) if isinstance(row, dict) else {}
        if not isinstance(payload, dict) or str(payload.get("author") or "").lower() != "agent":
            continue
        try:
            said = _dt.datetime.fromisoformat(str(payload.get("ts") or ""))
        except ValueError:
            continue
        if said.tzinfo is None or now - said > VOICE_REPEAT_WINDOW:
            continue
        before = str(payload.get("text") or "")
        if _same_voice_case(text, before):
            raise PermissionError(
                f"already said at {said:%H:%M} UTC: «{before[:90]}…». The human has "
                "this case; one case is one call. Say it again only with something NEW "
                "(a result, a measured number, a decision you need) — or put the "
                "progress into data/self_improvement_issues.jsonl, which every cycle reads."
            )


#: Поводы заговорить первым — ровно эти. Первый шаг «звать ли» отдельно от
#: «что сказать» (ProAgentBench, arXiv 2602.04482: момент угадывается на 53–61%,
#: и почти половина обращений лишние); повод назван — значит, его можно
#: посчитать и потом сверить, отвечал ли человек делом (Proactive Agent,
#: arXiv 2410.12361). Слово оператора 25.09: «всё доделать».
VOICE_REASONS = {
    "wall": "стена, которую открывает только человек (одобрение, доступ, деньги)",
    "before_irreversible": "неясная цель перед необратимым шагом",
    "result": "готов результат, о котором человек сам просил",
    "stuck": "застрял после своих попыток — с их перечнем",
    "finding": "находка противоречит коду или прежним выводам",
}


def _refuse_unreasoned_voice(path: str, record: dict) -> None:
    """Обращение называет повод и, кроме доклада о результате, задаёт вопрос.

    Horvitz (CHI 1999): разговор — чтобы снять КЛЮЧЕВУЮ неопределённость, с
    учётом цены лишнего беспокойства; KnowNo (arXiv 2307.01928): спрашивать,
    когда вариантов больше одного. Обращение без вопроса — доклад, а доклад
    не стоит прерывания.
    """
    if path != VOICE_PATH or str(record.get("author") or "").strip().lower() != "agent":
        return
    reason = str(record.get("reason") or "").strip()
    if reason not in VOICE_REASONS:
        raise ValueError(
            "a call to the human names its reason: record['reason'] one of "
            + ", ".join(f"'{k}' ({v})" for k, v in VOICE_REASONS.items())
            + ". No reason from this list — it is not worth an interruption: "
            "put it into data/self_improvement_issues.jsonl, which every cycle reads."
        )
    if reason != "result" and "?" not in str(record.get("text") or ""):
        raise ValueError(
            "a call to the human carries a question they can answer (yes/no or a choice): "
            "without one it is a report, and a report is not worth an interruption."
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
            raise TypeError(f"record must be a dict, got {type(record).__name__}")
        target = self._resolve(str(path))
        _refuse_owned_state(str(path))
        record = _stamp_voice_record(str(path), record)
        _refuse_unreasoned_voice(str(path), record)
        _refuse_over_voice_budget(str(path), target)
        _refuse_repeated_voice(str(path), target, record)
        contract = _KNOWN_JOURNALS.get(str(path))
        _refuse_placeholders(record)
        _refuse_hollow_decision(str(path), record, Path(self._workspace_root))
        _refuse_naive_stamps(record)
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
