"""Self-build lessons with provenance (what broke, what was done, how it was verified), kept in ``data/self_build_lessons.jsonl``.

Split out of core/self_build_rules.py: rules there are narrow and executed by the Critic, while a lesson is broader and is only READ
before repeating work at the same address. Owns the write gate (`lesson_write_verdict`) and the blocking reader (`blocking_lesson`);
the bridge `record_lessons_from_result` stays in the rules module.
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
    """One lesson with provenance: what broke, what was done, how it was verified.

        Unlike a rule, a lesson is not executed automatically; it is read before repeating work at the same address.
        The five provenance fields are mandatory: a lesson without origin can be neither confirmed nor revoked.
    """

    created_at: str
    origin: str  # who produced it: "rule_approved_apply" (no human) or "cli:..."
    proposal_id: str
    failure: str  # the original failure: how the verification ended
    change: str  # what was changed
    verification: str  # how it was verified: list of tests and rollback outcome
    outcome: str  # "verified_candidate" | "rolled_back"
    scope: tuple[str, ...] = ()  # addresses the lesson applies to

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
    """Lesson from the outcome of one apply, or None when there is nothing to learn.

        Only a TERMINAL outcome teaches (accepted or rolled back). A gate refusal is an attempt that did not happen
        and says nothing about the change itself.
    """
    status = str(result.get("status") or "")
    outcome = _TERMINAL_OUTCOMES.get(status)
    if outcome is None:
        return None
    if outcome == "rolled_back" and "timed out" in str(result.get("reason") or ""):
        # A verification that did not finish says nothing about the change, like a gate refusal above: a lesson from it
        # would forbid repeating a possibly correct edit forever.
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
        # The lane's wording about an ACCEPTED candidate is verification evidence and moves here instead of being lost;
        # for a rollback it goes into `failure` below, where it is the failure.
        + ([lane_says] if lane_says and outcome != "rolled_back" else [])
    )
    # A lesson's failure is where it grew FROM. For an accepted candidate the lane's `reason` is its success text
    # (never empty), so a fallback to the request's reason never fired and `failure` and `verification` carried the same
    # text; for a rollback the lane's reason IS the failure: what turned red during verification.
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
    """Whether a lesson write is allowed under this set of sinks, and if not, why.

        The ladder repeats `_durable_learning_suppressed` (rules 4 and 5) letter for letter so a second ladder cannot diverge:

        * ``None``: sinks unrestricted (a human at the keyboard): allowed;
        * a set containing the lesson sink: allowed by the authority that opened it;
        * a set without the lesson sink: refused (unattended production).
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
    """Append-only JSONL of lessons, read by code and not only by humans."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[Lesson]:
        lessons, _ = self.read()
        return lessons

    def read(self) -> tuple[list[Lesson], int]:
        """Lessons AND the number of unreadable lines.

                The gate needs the count: `load()` skipped them silently, and a corrupted line could be exactly the lesson
                that forbids a repeat. A silent skip at a gate is a permission.
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
                # `accepted` is kept for lessons written before the rename: the journal outlives renames, and old records must
                # not become unreadable retroactively.
                if lesson.outcome not in _KNOWN_OUTCOMES:
                    continue
                lessons.append(lesson)
        except OSError:
            return [], -1  # journal not read at all
        return lessons, bad

    def add(self, lesson: Lesson) -> bool:
        """Record a lesson; a duplicate of the same conclusion from the same failure is not written."""
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
    """The lesson that forbids REPEATING an autonomous edit at these addresses.

        This closes the loop: a rollback leaves a lesson, and the next request for the same address READS it and does
        not enter the lane twice. A refusal is not forever: the request stays in the inbox and a human may approve it
        the usual way.

        An unreadable journal FORBIDS: at a gate, not knowing must mean refusal.
    """
    wanted = {str(t).replace("\\", "/").strip() for t in targets if t}
    if not wanted:
        return None
    try:
        store = LessonStore(default_lessons_path(workspace))
        lessons, bad = store.read()
    except Exception as exc:  # noqa: BLE001 — чтение не роняет вызывающего,
        return _unreadable_journal_lesson(  # but does not allow silently either
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
    """A refusal lesson: "I do not know what was here" in the gate's language."""
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
