"""Hard rules learned from self-build rollbacks.

becomes the rule "symbol X must remain importable from core/verifier.py". On
the next produce run for that target the Critic re-parses the proposed
content and vetoes BEFORE apply if the symbol is neither defined nor re-
exported — no LLM judgement involved, so the same rollback can never happen
twice for the same symbol.

Storage is one JSONL file (``data/self_build_rules.jsonl``) next to the
other agent state stores; loading and recording are best-effort and never
raise into the caller.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

RULES_FILENAME = "self_build_rules.jsonl"
LESSONS_FILENAME = "self_build_lessons.jsonl"

# "cannot import name 'X' from 'core.verifier'" — the canonical CPython
# ImportError wording; the optional trailing "(path)" is ignored.
_IMPORT_ERROR_RE = re.compile(
    r"cannot import name '(?P<symbol>[^']+)' from '(?P<module>[^']+)'"
)


@dataclass
class Rule:
    """One enforceable lesson: symbol must stay importable from target."""

    target: str  # repo-relative path, forward slashes
    kind: str  # currently always "keep_importable"
    symbol: str
    source: str  # where the rule came from (proposal id / reason snippet)
    created_at: str = ""

    def key(self) -> tuple[str, str, str]:
        return (self.target, self.kind, self.symbol)


def _module_to_path(module: str) -> str:
    """core.verifier -> core/verifier.py (best-effort dotted-to-path)."""
    mod = module.strip()
    if not mod or "/" in mod or "\\" in mod:
        return mod.replace("\\", "/")
    return mod.replace(".", "/") + ".py"


def extract_rules_from_apply_result(result: dict) -> list[Rule]:
    """Parse a self-apply run result into zero or more hard rules.

    Only ``rolled_back`` outcomes produce rules; every distinct
    ``cannot import name 'X' from 'M'`` occurrence in the reason yields one
    ``keep_importable`` rule for the module's file path.
    """
    if str(result.get("status") or "") != "rolled_back":
        return []
    reason = str(result.get("reason") or "")
    proposal_id = str(result.get("proposal_id") or "")
    now = datetime.now(timezone.utc).isoformat()
    rules: list[Rule] = []
    seen: set[tuple[str, str, str]] = set()
    for match in _IMPORT_ERROR_RE.finditer(reason):
        symbol = match.group("symbol")
        target = _module_to_path(match.group("module"))
        rule = Rule(
            target=target,
            kind="keep_importable",
            symbol=symbol,
            source=f"rollback {proposal_id or '?'}: ImportError".strip(),
            created_at=now,
        )
        if rule.key() not in seen:
            seen.add(rule.key())
            rules.append(rule)
    return rules


class RuleStore:
    """Append-only JSONL store of hard rules, deduplicated on load."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[Rule]:
        rules: list[Rule] = []
        seen: set[tuple[str, str, str]] = set()
        try:
            if not self.path.exists():
                return []
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    rule = Rule(
                        target=str(raw.get("target") or ""),
                        kind=str(raw.get("kind") or ""),
                        symbol=str(raw.get("symbol") or ""),
                        source=str(raw.get("source") or ""),
                        created_at=str(raw.get("created_at") or ""),
                    )
                except (ValueError, TypeError):
                    continue
                if not rule.target or not rule.symbol or not rule.kind:
                    continue
                if rule.key() in seen:
                    continue
                seen.add(rule.key())
                rules.append(rule)
        except OSError:
            return []
        return rules

    def add(self, rule: Rule) -> bool:
        """Persist one rule; returns False for duplicates or write failures."""
        try:
            existing = {r.key() for r in self.load()}
            if rule.key() in existing:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(rule), ensure_ascii=False) + "\n")
        except OSError:
            return False
        else:
            return True

    def rules_for(self, target: str) -> list[Rule]:
        norm = target.replace("\\", "/").strip()
        return [r for r in self.load() if r.target == norm]


def default_rules_path(workspace: Path) -> Path:
    return workspace / "data" / RULES_FILENAME


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
    outcome: str  # "accepted" | "rolled_back"
    scope: tuple[str, ...] = ()  # адреса, к которым урок относится

    def key(self) -> tuple[str, str, str]:
        return (self.outcome, "|".join(self.scope), self.failure[:200])


_TERMINAL_OUTCOMES = {"committed_local": "accepted", "rolled_back": "rolled_back"}


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
    files = tuple(
        str(p).replace("\\", "/") for p in (result.get("files_changed") or []) if p
    )
    tests = [str(t) for t in (result.get("tests_run") or []) if t]
    rollback = str(result.get("rollback_status") or "none")
    verification = "; ".join(
        [
            f"tests: {', '.join(tests) if tests else 'none'}",
            f"status: {status}",
            f"rollback: {rollback}",
        ]
    )
    failure = str(result.get("reason") or "").strip()
    if not failure:
        # Принятая правка не несёт текста провала — исходным сбоем для неё
        # служит повод, по которому её вообще предложили.
        failure = str(reason or "").strip() or f"no failure recorded ({status})"
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


class LessonStore:
    """Append-only JSONL уроков. Читается кодом, а не только человеком."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[Lesson]:
        lessons: list[Lesson] = []
        try:
            if not self.path.exists():
                return []
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
                except (ValueError, TypeError):
                    continue
                if lesson.outcome not in {"accepted", "rolled_back"}:
                    continue
                lessons.append(lesson)
        except OSError:
            return []
        return lessons

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

    def lessons_for(self, target: str) -> list[Lesson]:
        norm = str(target).replace("\\", "/").strip()
        return [lesson for lesson in self.load() if norm in lesson.scope]


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
    """
    try:
        store = LessonStore(default_lessons_path(workspace))
        wanted = {str(t).replace("\\", "/").strip() for t in targets if t}
        if not wanted:
            return None
        for lesson in reversed(store.load()):
            if lesson.outcome == "rolled_back" and wanted & set(lesson.scope):
                return lesson
    except Exception:  # noqa: BLE001 — чтение урока не вправе ронять вызывающего
        return None
    return None


def record_rules_from_result(workspace: Path, result: dict) -> int:
    """Extract and persist rules from one apply result; returns count added.

    Best-effort: any failure returns 0 and never raises into the caller.
    """
    try:
        rules = extract_rules_from_apply_result(result)
        if not rules:
            return 0
        store = RuleStore(default_rules_path(workspace))
        return sum(1 for rule in rules if store.add(rule))
    except Exception:  # noqa: BLE001 — rule recording must never break the caller
        return 0


def record_lessons_from_result(
    workspace: Path, result: dict, *, origin: str, reason: str = ""
) -> dict:
    """Записать всё, чему учит один исход: узкие правила И урок с происхождением.

    Зовётся с ОБОИХ путей применения — из CLI и из автономного слива
    (`core/rule_approved_apply.py`). До 2026-09-17 второй путь не звал ничего,
    поэтому ни одна починка без человека за клавиатурой не превращалась в
    знание: `record_rules_from_result` имела ровно одного вызывающего —
    `cli/commands_self_apply.py`.

    Best-effort по образцу соседа: сбой записи не вправе ронять применение.
    """
    added = {"rules": 0, "lessons": 0}
    try:
        added["rules"] = record_rules_from_result(workspace, result)
        lesson = lesson_from_apply_result(result, origin=origin, reason=reason)
        if lesson is not None and LessonStore(default_lessons_path(workspace)).add(lesson):
            added["lessons"] = 1
    except Exception:  # noqa: BLE001 — запись опыта не вправе ронять вызывающего
        return added
    return added

