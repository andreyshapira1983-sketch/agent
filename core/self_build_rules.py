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
from typing import TYPE_CHECKING

# Уроки с происхождением — отдельный журнал со своими воротами записи и своим
# блокирующим читателем — живут в core/self_build_lessons.py. Здесь остаётся
# мост `record_lessons_from_result`, который пишет и правила, и урок; имена
# уроков реэкспортируются, чтобы прежние импорты отсюда не ломались.
from core.self_build_lessons import (  # noqa: F401
    LESSON_SINK,
    LESSONS_FILENAME,
    SHARED_BOOKKEEPING,
    Lesson,
    LessonStore,
    blocking_lesson,
    default_lessons_path,
    lesson_from_apply_result,
    lesson_write_verdict,
)

if TYPE_CHECKING:
    from typing import Any

RULES_FILENAME = "self_build_rules.jsonl"

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
    workspace: Path,
    result: dict,
    *,
    origin: str,
    reason: str = "",
    durable_writes: Any = None,
    log: Any = None,
) -> dict:
    """Записать всё, чему учит один исход: узкие правила И урок с происхождением.

    Зовётся с ОБОИХ путей применения — из CLI и из автономного слива
    (`core/rule_approved_apply.py`). До 2026-09-17 второй путь не звал ничего,
    поэтому ни одна починка без человека за клавиатурой не превращалась в
    знание: `record_rules_from_result` имела ровно одного вызывающего —
    `cli/commands_self_apply.py`.

    `durable_writes` — набор открытых стоков этого прогона, ровно тот, что у
    сборки агента. `None` означает «человек за клавиатурой» и разрешает, как
    правило 4 лестницы. Безнадзорное производство приходит сюда с набором без
    стока урока и получает ОТКАЗ — записанный в журнал, а не молчаливый
    (ревизия PR #333: запись урока обходила ту самую политику памяти, которую
    полномочие песочницы открывает только для песочницы).

    Best-effort по образцу соседа: сбой записи не вправе ронять применение.
    """
    added: dict[str, Any] = {"rules": 0, "lessons": 0}
    allowed, why = lesson_write_verdict(durable_writes)
    if not allowed:
        added["refused"] = why
        if log is not None:
            try:
                log("lesson_write_refused", {
                    "sink": LESSON_SINK, "origin": origin, "reason": why,
                })
            except Exception:  # noqa: BLE001 — журнал не роняет применение
                pass
        return added
    try:
        added["rules"] = record_rules_from_result(workspace, result)
        lesson = lesson_from_apply_result(result, origin=origin, reason=reason)
        if lesson is not None and LessonStore(default_lessons_path(workspace)).add(lesson):
            added["lessons"] = 1
    except Exception:  # noqa: BLE001 — запись опыта не вправе ронять вызывающего
        return added
    return added

