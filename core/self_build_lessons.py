"""Уроки самоправки с происхождением: что сломалось, что сделали, чем проверено.

Вынесено из core/self_build_rules.py: там живут узкие правила («символ X
обязан остаться импортируемым»), которые Критик исполняет сам, а здесь —
журнал уроков (``data/self_build_lessons.jsonl``) со своими воротами записи
(`lesson_write_verdict`) и своим читателем-запретом (`blocking_lesson`).
Урок шире правила и автоматически не исполняется — его читают перед тем, как
повторить работу по тому же адресу. Мост, который пишет и правила, и урок из
одного исхода, остался там: `record_lessons_from_result`.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LESSONS_FILENAME = "self_build_lessons.jsonl"


def default_lessons_path(workspace: Path) -> Path:
    return workspace / "data" / LESSONS_FILENAME


@dataclass
class Lesson:
    """Один урок с происхождением: что сломалось, что сделали, чем проверено.

    Правило (`Rule`) выше — узкое и детерминированно применимое: Критик умеет
    его исполнить сам. Урок шире и слабее: он НЕ исполняется автоматически, он
    читается перед тем, как повторить работу по тому же адресу. Разница
    намеренная — знание, которое машина применяет молча, обязано быть
    проверяемым, а знание с прозой внутри проверяемым не бывает.

    Пять полей происхождения обязательны, потому что урок без происхождения
    нельзя ни подтвердить, ни отозвать: непонятно, из какого сбоя он вырос.
    """

    created_at: str
    origin: str  # кто добыл: "rule_approved_apply" (без человека) или "cli:..."
    proposal_id: str
    failure: str  # исходный сбой — то, чем кончилась проверка
    change: str  # что менялось
    verification: str  # чем проверено: перечень тестов и исход отката
    outcome: str  # "verified_candidate" | "rolled_back"
    scope: tuple[str, ...] = ()  # адреса, к которым урок относится

    def key(self) -> tuple[str, str, str]:
        return (self.outcome, "|".join(self.scope), self.failure[:200])


#: Какое СЛОВО получает исход в уроке. `committed_local` умышленно не зовётся
#: `accepted`: ревизия PR #333 показала, что принятия в этот момент не
#: происходит — полоса делает локальный commit в боковой ветке и тут же
#: возвращает дерево на исходную (`core/self_apply_lane.py:740`). Работающий
#: агент остаётся на прежнем коде. Урок с неверным словом учит неверному: по
#: журналу выходило, что изменение принято, тогда как принято оно не было.
#: Принятие делает `core/burn_in_supervisor.adopt_offer`, и только он.
_TERMINAL_OUTCOMES = {
    "committed_local": "verified_candidate",
    "rolled_back": "rolled_back",
}

#: Что журнал согласен прочитать. Шире, чем то, что он теперь пишет: старое
#: слово `accepted` остаётся читаемым, иначе переименование стёрло бы уже
#: добытый опыт.
_KNOWN_OUTCOMES = frozenset({"verified_candidate", "rolled_back", "accepted"})


def lesson_from_apply_result(
    result: dict, *, origin: str, reason: str = ""
) -> Lesson | None:
    """Урок из исхода одного применения, или None, если учиться нечему.

    Учит только ТЕРМИНАЛЬНЫЙ исход: принято или откачено. Отказ ворот («риск
    отклонён», «шлюз закрыт») — это несостоявшаяся попытка, из неё не следует
    ничего о самом изменении, и записывать её уроком значило бы учиться на
    собственной осторожности.
    """
    status = str(result.get("status") or "")
    outcome = _TERMINAL_OUTCOMES.get(status)
    if outcome is None:
        return None
    if outcome == "rolled_back" and "timed out" in str(result.get("reason") or ""):
        # Не успевшая проверка ничего не говорит об изменении — как и отказ
        # ворот выше. Урок из неё навсегда запрещал бы повторить, возможно,
        # верную правку (суточный прогон 2026-09-19, core/step_sanitizer.py).
        return None
    files = tuple(
        str(p).replace("\\", "/") for p in (result.get("files_changed") or []) if p
    )
    tests = [str(t) for t in (result.get("tests_run") or []) if t]
    rollback = str(result.get("rollback_status") or "none")
    lane_says = str(result.get("reason") or "").strip()
    verification = "; ".join(
        [
            f"tests: {', '.join(tests) if tests else 'none'}",
            f"status: {status}",
            f"rollback: {rollback}",
        ]
        # Слово полосы о ПРИНЯТОМ кандидате — свидетельство проверки, и оно
        # переезжает сюда, а не пропадает: иначе починка поля `failure` просто
        # меняла бы одну потерю на другую. У отката это слово уходит в `failure`
        # ниже, потому что там оно и есть сбой.
        + ([lane_says] if lane_says and outcome != "rolled_back" else [])
    )
    # Сбой урока — то, ОТКУДА он вырос. Ревизия Copilot по PR #333: у принятого
    # кандидата `reason` полосы — это её успех («targeted + full tests passed»),
    # непустой всегда, поэтому запасной ход на повод заявки не срабатывал
    # никогда, и «сбой» с «проверкой» несли один текст. У отката наоборот:
    # повод полосы и ЕСТЬ сбой — что именно покраснело при проверке.
    if outcome == "rolled_back":
        failure = lane_says or str(reason or "").strip()
    else:
        failure = str(reason or "").strip() or lane_says
    if not failure:
        failure = f"no failure recorded ({status})"
    return Lesson(
        created_at=datetime.now(timezone.utc).isoformat(),
        origin=str(origin or "unknown"),
        proposal_id=str(result.get("proposal_id") or ""),
        failure=failure[:1000],
        change=(str(reason or "").strip() or f"{len(files)} file(s): {', '.join(files)}")[:500],
        verification=verification[:500],
        outcome=outcome,
        scope=files,
    )


#: Сток долговременной памяти, которым урок ЯВЛЯЕТСЯ по смыслу: обобщённый
#: опыт, меняющий поведение будущих прогонов. Имя взято из
#: `core/loop_memory_write.KNOWN_DURABLE_SINKS`, а не придумано рядом: два
#: словаря одного разрешения — это ноль разрешений.
LESSON_SINK = "procedure"


def lesson_write_verdict(durable_writes: Any) -> tuple[bool, str]:
    """Разрешена ли запись урока при этом наборе стоков, и если нет — почему.

    Ревизия PR #333: `LessonStore.add()` писала JSONL напрямую, мимо
    `MemoryWritePolicy`, и звалась в том числе с ПРОИЗВОДСТВЕННОГО
    `rule_approved_apply`. Требование «никаких неуправляемых прямых записей»
    фактически не выполнялось: профиль песочницы открывал `procedure` для
    одного пути, а урок ехал другим.

    Лестница повторена буква в букву (`_durable_learning_suppressed`, правила 4
    и 5), потому что вторая лестница разошлась бы с первой:

    * ``None`` — стоки не ограничены (человек за клавиатурой): разрешено;
    * набор со стоком урока — разрешено полномочием, которое его открыло;
    * набор без стока урока — отказ. Это и есть безнадзорное производство.
    """
    if durable_writes is None:
        return True, "интерактивный профиль: стоки не ограничены"
    try:
        allowed = LESSON_SINK in durable_writes
    except TypeError:
        return False, f"нечитаемый набор стоков: {durable_writes!r}"
    if allowed:
        return True, f"сток {LESSON_SINK!r} открыт профилем этого прогона"
    return False, (
        f"сток {LESSON_SINK!r} закрыт безнадзорным профилем памяти; "
        "урок не записан"
    )


class LessonStore:
    """Append-only JSONL уроков. Читается кодом, а не только человеком."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[Lesson]:
        lessons, _ = self.read()
        return lessons

    def read(self) -> tuple[list[Lesson], int]:
        """Уроки И число нечитаемых строк.

        Счёт нечитаемых строк нужен воротам: `load()` их молча пропускал, и
        испорченная строка могла оказаться ровно тем уроком, который запрещает
        повторить откат. Молчаливый пропуск в воротах — это разрешение
        (ревизия PR #333).
        """
        lessons: list[Lesson] = []
        bad = 0
        try:
            if not self.path.exists():
                return [], 0
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    lesson = Lesson(
                        created_at=str(raw.get("created_at") or ""),
                        origin=str(raw.get("origin") or ""),
                        proposal_id=str(raw.get("proposal_id") or ""),
                        failure=str(raw.get("failure") or ""),
                        change=str(raw.get("change") or ""),
                        verification=str(raw.get("verification") or ""),
                        outcome=str(raw.get("outcome") or ""),
                        scope=tuple(str(s) for s in (raw.get("scope") or [])),
                    )
                except (ValueError, TypeError, AttributeError):
                    bad += 1
                    continue
                # `accepted` оставлен ради уроков, записанных до ревизии
                # PR #333: журнал переживает переименование, и старые записи
                # не вправе становиться нечитаемыми задним числом.
                if lesson.outcome not in _KNOWN_OUTCOMES:
                    continue
                lessons.append(lesson)
        except OSError:
            return [], -1  # журнал не прочитан вовсе
        return lessons, bad

    def add(self, lesson: Lesson) -> bool:
        """Записать урок; дубликат того же вывода из того же сбоя не пишется."""
        try:
            if lesson.key() in {existing.key() for existing in self.load()}:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(lesson), ensure_ascii=False) + "\n")
        except OSError:
            return False
        else:
            return True


#: Служебные файлы, которые обновляет ЛЮБОЕ разбиение модуля: карта анатомии,
#: её генерат, карты census и потолки. Суточный прогон 2026-09-19: откат
#: правки core/model_router.py (упали тесты, зависящие от ключей установки)
#: записал урок с областью, включающей карту анатомии, — и все следующие
#: заявки на разбиение ДРУГИХ модулей (step_sanitizer, smart_memory, …)
#: отвергались «prior rollback lesson»: пересекались только по этим файлам.
#: Урок запрещает повторить ту же правку, а не любую, задевшую общий реестр.
SHARED_BOOKKEEPING = frozenset({
    "core/anatomy_groups.py",
    "knowledge/generated/AGENT_ANATOMY.md",
    "knowledge/maps/cns_census.json",
    "knowledge/maps/cns_model.json",
    "scripts/check_ceo_file_baseline.py",
    "scripts/check_function_length_baseline.py",
})


def blocking_lesson(workspace: Path, targets) -> Lesson | None:
    """Урок, запрещающий ПОВТОРИТЬ автономную правку по этим адресам.

    Здесь петля и замыкается: откат оставляет урок, а следующая заявка по тому
    же адресу этот урок ЧИТАЕТ и не уезжает в полосу второй раз. Без такого
    читателя откат ничему не учил — тик подавал ту же правку, полоса снова
    гоняла батарею и снова откатывала, и так до конца суток (аудит
    автономности 2026-09-17, находка 10).

    Отказ здесь — не запрет навсегда: заявка остаётся в ящике, и человек
    вправе одобрить её обычным путём. Автомат лишь перестаёт повторять то, что
    один раз уже не прошло проверку.

    Нечитаемый журнал ЗАПРЕЩАЕТ. До ревизии PR #333 любое исключение чтения
    отвечало `None`, то есть «препятствий нет»: испорченная строка снимала
    ворота, которые сама же и должна была держать. У ворот незнание обязано
    означать отказ.
    """
    wanted = {str(t).replace("\\", "/").strip() for t in targets if t}
    if not wanted:
        return None
    try:
        store = LessonStore(default_lessons_path(workspace))
        lessons, bad = store.read()
    except Exception as exc:  # noqa: BLE001 — чтение не роняет вызывающего,
        return _unreadable_journal_lesson(  # но и не разрешает молча
            wanted, f"журнал уроков не прочитан: {type(exc).__name__}: {exc}"
        )
    if bad != 0:
        return _unreadable_journal_lesson(
            wanted,
            "журнал уроков прочитан не целиком: "
            + ("файл недоступен" if bad < 0 else f"нечитаемых строк: {bad}"),
        )
    own = wanted - SHARED_BOOKKEEPING or wanted
    for lesson in reversed(lessons):
        if lesson.outcome != "rolled_back":
            continue
        lesson_own = set(lesson.scope) - SHARED_BOOKKEEPING or set(lesson.scope)
        if own & lesson_own:
            return lesson
    return None


def _unreadable_journal_lesson(scope: set[str], reason: str) -> Lesson:
    """Урок-отказ: «я не знаю, что тут было» на языке ворот."""
    return Lesson(
        created_at=datetime.now(timezone.utc).isoformat(),
        origin="lesson_store",
        proposal_id="",
        failure=reason,
        change="",
        verification="журнал уроков не подтверждает отсутствие запрета",
        outcome="rolled_back",
        scope=tuple(sorted(scope)),
    )
