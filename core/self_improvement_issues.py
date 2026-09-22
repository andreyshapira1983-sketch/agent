"""Durable lifecycle registry for self-improvement failures."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from core.state_integrity import read_state_jsonl, rewrite_state_jsonl

IssueStatus = Literal["open", "verified", "resolved"]
#: Тяжесть, которую запись вправе объявить о себе. `critical` сюда НЕ входит
#: намеренно: он принадлежит объективной неживости, которую видно снаружи.
IssueSeverity = Literal["low", "medium", "high"]
_ISSUE_SEVERITIES: frozenset[str] = frozenset({"low", "medium", "high"})
DEFAULT_ISSUE_PATH = Path("data") / "self_improvement_issues.jsonl"
_DUPLICATE_MIXIN_KEY = "incremental_splitter:duplicate_mixin_base_class"
_GENERIC_ACTION = "improve_failure_to_idea_pipeline"
_PROPOSAL_ID_RE = re.compile(r"\bain_[a-z0-9]+\b", re.IGNORECASE)
_ROLLBACK_BRANCH_RE = re.compile(r"\bself-apply/[a-z0-9._/-]+\b", re.IGNORECASE)
_ERROR_TEXT_RE = re.compile(
    r"\b([a-z_][a-z0-9_]*(?:error|exception)):\s*([^;(|\n]{1,200})",
    re.IGNORECASE,
)
_REJECTED_REPAIR_RE = re.compile(
    r"repair proposal rejected:\s*([^\n]{1,300})",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


#: Text prefix the detector route writes (`core/self_build_memory.py`).
_DETECTOR_PREFIX = "detectors "


def _detector_signals(text: str) -> tuple[str, ...]:
    """Signal names from a detector-route failure text, or () for other texts."""
    lowered = str(text or "").casefold()
    if not lowered.startswith(_DETECTOR_PREFIX):
        return ()
    head = lowered[len(_DETECTOR_PREFIX):].split(":", 1)[0]
    return tuple(sorted(s.strip() for s in head.split(",") if s.strip()))


def failure_fingerprint(text: str) -> str:
    """Stable fingerprint for equivalent failure evidence."""
    lowered = str(text or "").casefold()
    if "duplicate base class" in lowered or ("duplicate" in lowered and "mixin" in lowered):
        key = _DUPLICATE_MIXIN_KEY
    elif signals := _detector_signals(text):
        # MIR-035: a detector-minted issue is identified by its SIGNAL CLASS,
        # never by the turn's text. The old key hashed the full failure text —
        # which embeds the campaign question — so one detector class on N
        # questions minted N permanent open issues (13 copies of one signal
        # pair measured live). The repair ladder repairs classes, not turns;
        # instances accumulate as evidence on one issue instead.
        key = "detector-class:" + ",".join(signals)
    else:
        key = re.sub(r"\b(?:ain|run|ep)_[a-f0-9]+\b", "<id>", lowered)
        key = re.sub(r"\b\d+\b", "#", key)
        key = " ".join(key.split())[:500]
    return "sii_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SelfImprovementIssue:
    fingerprint: str
    title: str
    action: str
    status: IssueStatus
    first_seen: str
    last_seen: str
    evidence: tuple[str, ...]
    related_files: tuple[str, ...]
    related_error_text: str
    suggested_next_action: str
    #: НИЖНЯЯ ГРАНИЦА числа срабатываний класса, а не точный счёт: он ведётся
    #: с того момента, как поле появилось (2026-08-22), поэтому у записей
    #: старше этой даты он занижен. Потребитель обязан показывать `>=`.
    occurrences: int = 1
    #: Насколько дефект мешает РАБОТЕ, по суждению того, кто его завёл.
    #:
    #: Слово оператора 2026-09-20: «почини фильтр, пусть его собственные
    #: дефекты проходят». Замер того вечера: 117 решений из 117 приняты по
    #: операторской цели, НИ ОДНОГО по собственному реестру. Причина —
    #: `_partition_by_subject` в `core/best_next_action.py`: кандидат остаётся
    #: в гонке, только если у него критическая или высокая тяжесть, либо его
    #: предмет совпадает с предметом цели, либо он пришёл из цели. А кандидату
    #: из этого реестра тяжесть была ПРИБИТА как `medium`, поэтому всё, что
    #: агент узнаёт о себе, выметалось любой целью, называющей другой файл.
    #:
    #: Дверь «объективная поломка не выметается» существовала и работала — её
    #: заварили изнутри одной константой. Это поле её отпирает: запись сама
    #: говорит, мешает она работе или только досаждает.
    #:
    #: Потолок `high` намеренный. `critical` остаётся за объективной
    #: неживостью (демон упал, тик сломан) — тем, что видно снаружи и не
    #: зависит от самооценки. Иначе самооценка стала бы способом всегда
    #: объявлять себя главным делом, а цель потеряла бы то влияние, ради
    #: которого фильтр и написан (MIR-162).
    severity: IssueSeverity = "medium"

    def to_dict(self) -> dict:
        data = dict(self.__dict__)
        data["evidence"] = list(self.evidence)
        data["related_files"] = list(self.related_files)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> SelfImprovementIssue:
        status = str(data.get("status") or "open")
        if status not in {"open", "verified", "resolved"}:
            status = "open"
        # Незнакомое слово — не повод поверить записи на слово: неизвестная
        # тяжесть читается как `medium`, то есть как прежнее поведение. Так же
        # читается и `critical`: объявить себя аварией эта запись не вправе.
        severity = str(data.get("severity") or "medium").strip().casefold()
        if severity not in _ISSUE_SEVERITIES:
            severity = "medium"
        raw_evidence = data.get("evidence")
        if isinstance(raw_evidence, str):
            evidence = (raw_evidence,)
        else:
            evidence = tuple(str(x) for x in raw_evidence or ())
        return cls(
            fingerprint=str(data.get("fingerprint") or ""),
            title=str(data.get("title") or "Unresolved self-improvement failure"),
            action=str(data.get("action") or "improve_failure_to_idea_pipeline"),
            status=status,  # type: ignore[arg-type]
            first_seen=str(data.get("first_seen") or _now()),
            last_seen=str(data.get("last_seen") or _now()),
            evidence=evidence,
            related_files=tuple(str(x) for x in data.get("related_files") or ()),
            related_error_text=str(data.get("related_error_text") or ""),
            suggested_next_action=str(data.get("suggested_next_action") or ""),
            occurrences=max(1, int(data.get("occurrences") or 1)),
            severity=severity,  # type: ignore[arg-type]
        )


def _root_evidence_keys(issue: SelfImprovementIssue) -> frozenset[str]:
    """Return explicit correlation keys shared by records from one failure."""
    keys: set[str] = set()
    texts = (*issue.evidence, issue.related_error_text)
    for text in texts:
        normalized = " ".join(str(text).casefold().split())
        keys.update(f"proposal:{value}" for value in _PROPOSAL_ID_RE.findall(normalized))
        keys.update(f"branch:{value}" for value in _ROLLBACK_BRANCH_RE.findall(normalized))
        keys.update(
            f"error:{kind}:{message.strip()}"
            for kind, message in _ERROR_TEXT_RE.findall(normalized)
        )
        keys.update(
            f"rejected-repair:{message.strip()}"
            for message in _REJECTED_REPAIR_RE.findall(normalized)
        )
    return frozenset(keys)


def suppress_generic_issue_duplicates(
    issues: Iterable[SelfImprovementIssue],
) -> list[SelfImprovementIssue]:
    """Hide generic issues already covered by a linked specific issue."""
    materialized = list(issues)
    specific_keys = [
        _root_evidence_keys(issue)
        for issue in materialized
        if issue.action != _GENERIC_ACTION
    ]
    return [
        issue
        for issue in materialized
        if not (
            issue.action == _GENERIC_ACTION
            and any(
                keys and keys.intersection(_root_evidence_keys(issue))
                for keys in specific_keys
            )
        )
    ]


def issue_from_failure(text: str, observed_at: str) -> SelfImprovementIssue:
    lowered = text.casefold()
    duplicate_mixin = "duplicate base class" in lowered or (
        "duplicate" in lowered and "mixin" in lowered
    )
    if duplicate_mixin:
        title = "Fix the incremental splitter duplicate mixin base class issue"
        action = "repair_incremental_splitter_duplicate_mixin"
        files = ("core/incremental_splitter.py", "tests/test_incremental_splitter.py")
        suggested = "inspect core/incremental_splitter.py tests/test_incremental_splitter.py"
    elif signals := _detector_signals(text):
        joined = ", ".join(signals)
        title = f"Investigate recurring detector signal: {joined}"
        action = "investigate_detector_signal"
        files = tuple(dict.fromkeys(re.findall(r"[A-Za-z0-9_./-]+\.py", text)))[:6]
        suggested = (f"count occurrences of {joined} across recent episodes, "
                     "then decide whether the defect is in the answer or the detector")
    else:
        title = "Turn the self-improvement failure into a bounded repair"
        action = "improve_failure_to_idea_pipeline"
        files = tuple(dict.fromkeys(re.findall(r"[A-Za-z0-9_./-]+\.py", text)))[:6]
        suggested = "review the issue evidence and propose one small read-only fix"
    return SelfImprovementIssue(
        fingerprint=failure_fingerprint(text),
        title=title,
        action=action,
        status="open",
        first_seen=observed_at,
        last_seen=observed_at,
        evidence=(text[:500],),
        related_files=files,
        related_error_text=text[:500],
        suggested_next_action=suggested,
    )


class SelfImprovementIssueRegistry:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def list(self) -> list[SelfImprovementIssue]:
        issues = [SelfImprovementIssue.from_dict(row) for row in read_state_jsonl(self.path)]
        return [issue for issue in issues if issue.fingerprint]

    def unresolved(self) -> list[SelfImprovementIssue]:
        return sorted(
            (
                issue
                for issue in suppress_generic_issue_duplicates(self.list())
                if issue.status != "resolved"
            ),
            key=lambda issue: issue.last_seen,
            reverse=True,
        )

    def upsert_failure(self, text: str, observed_at: str) -> SelfImprovementIssue:
        incoming = issue_from_failure(text, observed_at)
        issues = self.list()
        for index, current in enumerate(issues):
            if current.fingerprint != incoming.fingerprint:
                continue
            newer = _stamp(observed_at) > _stamp(current.last_seen)
            status: IssueStatus = "open" if newer and current.status == "resolved" else current.status
            merged = replace(
                current,
                status=status,
                last_seen=max((current.last_seen, observed_at), key=_stamp),
                evidence=tuple(dict.fromkeys((*current.evidence, *incoming.evidence)))[-8:],
                related_error_text=incoming.related_error_text or current.related_error_text,
                # MIR-035 audit: merging by class keeps the class but LOSES its
                # magnitude — evidence is capped at 8, so a signal seen 50 times
                # is indistinguishable from one seen 8. Recurrence is the whole
                # reason a class is worth investigating, so it is counted.
                # Counted only on a STRICTLY NEWER stamp, because the producer
                # re-reads the last 7 days on every sweep: a naive +1 would
                # count sweeps instead of failures. Two failures sharing one
                # timestamp undercount by one — conservative on purpose, a
                # sensor that inflates is worse than one that lags.
                occurrences=current.occurrences + (1 if newer else 0),
            )
            issues[index] = merged
            self._save(issues)
            return merged
        issues.append(incoming)
        self._save(issues)
        return incoming

    def transition(
        self,
        *,
        status: IssueStatus,
        observed_at: str,
        fingerprint: str = "",
        action: str = "",
        evidence: str = "",
    ) -> SelfImprovementIssue | None:
        issues = self.list()
        for index, current in enumerate(issues):
            matches = (
                current.fingerprint == fingerprint
                if fingerprint
                else bool(action and current.action == action)
            )
            if not matches:
                continue
            if _stamp(observed_at) < _stamp(current.last_seen):
                return None
            extra = (evidence[:500],) if evidence else ()
            updated = replace(
                current,
                status=status,
                last_seen=observed_at,
                evidence=tuple(dict.fromkeys((*current.evidence, *extra)))[-8:],
            )
            issues[index] = updated
            self._save(issues)
            return updated
        return None

    def _save(self, issues: list[SelfImprovementIssue]) -> None:
        rewrite_state_jsonl(self.path, [issue.to_dict() for issue in issues])


# Единственное место, где применяется закон свидетеля: `transition` — слепой
# сеттер и доказанную починку от произвольного перехода отличить не может.
# Проект охраны авторства агента (docs/CODE_NOTES.md, «The learning organ was
# starved, not broken»).
_WITNESS_FIELDS = 3


def _witness_runs(evidence: tuple[str, ...]) -> list[list[str]]:
    """Только записи протокола `статус|время|метка`.

    В улике лежит и сырой текст провала, положенный при заведении: разделителей
    в нём нет, и разбор на нём падает.
    """
    return [
        parts for parts in (entry.split("|") for entry in evidence)
        if len(parts) == _WITNESS_FIELDS
    ]


def close_proven_issue(registry: SelfImprovementIssueRegistry, fingerprint: str) -> bool:
    """Закрыть проблему, если улика доказывает починку. Иначе не трогать.

    Доказательством считаются ДВА прогона свидетеля: красный на объекте,
    сломанном именно этой проблемой, и зелёный после починки. Свидетель,
    зелёный на сломанном объекте, ничего не доказывает; чужой уже зелёный тест
    красного прогона предъявить не может.
    """
    problem = None
    for item in registry.list():
        if item.fingerprint == fingerprint:
            problem = item
            break
    if problem is None:
        return False

    runs = _witness_runs(problem.evidence)
    labels = [parts[2] for parts in runs]
    if "red-before-fix" not in labels or "green-after-fix" not in labels:
        return False

    result = registry.transition(
        status="resolved",
        observed_at=max(parts[1] for parts in runs),
        fingerprint=fingerprint,
        evidence="closed by close_proven_issue: red-before-fix and green-after-fix present",
    )
    return result is not None


def retire_issue(registry, fingerprint, *, reason) -> bool:
    """Retire an issue only via the operator CLI; the verdict line starts with retired-by-operator, which close_proven_issue cannot confuse with a witness label, and autonomous ticks have no path to this function.

    Retire an issue by fingerprint, appending a retired-by-operator evidence line.

    Behavior:
    1. If reason is empty after strip -> return False, change nothing.
    2. If no issue with this fingerprint exists -> return False.
    3. If the issue is already resolved -> return False, do not touch evidence.
    4. Otherwise: transition to resolved, appending 'retired-by-operator|<ISO time>|<reason>' to the evidence. Return True.
    """
    if not reason.strip():
        return False

    issue = None
    for candidate in registry.list():
        if candidate.fingerprint == fingerprint:
            issue = candidate
            break

    if issue is None:
        return False

    if issue.status == "resolved":
        return False

    now = datetime.now(timezone.utc).isoformat()
    evidence_line = f"retired-by-operator|{now}|{reason}"
    updated = registry.transition(
        status="resolved",
        observed_at=now,
        fingerprint=fingerprint,
        evidence=evidence_line,
    )
    return updated is not None
