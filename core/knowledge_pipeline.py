"""Knowledge pipeline integration."""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from core.evidence import Evidence, ProvenanceChain
from core.evidence_budget import carries_framework_notice
from core.memory_policy import MemoryWriteDecision
from core.models import MemoryRecord
from core.secret_scanner import contains_secret
from core.source_ranker import SourceRank, SourceRankingReport
from core.source_registry import (
    ClaimRecord,
    SourceRecord,
    SourceRegistry,
    claim_from_evidence,
)
from core.source_registry_store import SourceRegistryStore
from core.truth_hype_filter import evaluate as evaluate_truth_hype
from core.unit_score import clamp_unit as _bounded

KnowledgeDecision = Literal["save", "reject"]


#: Токен, которым `core/redaction.py` заменяет вырезанный секрет. Его наличие
#: означает, что текст — ОСТАТОК секретного материала, даже если сам секрет уже
#: не читается. Форма фиксирована там: `[REDACTED:<kind>]`, kind в нижнем
#: регистре через дефис. Слово «redacted» в прозе под шаблон не подходит —
#: иначе документация о самой редактуре перестала бы быть знанием.
_REDACTION_MARKER_RE = re.compile(r"\[REDACTED:[a-z0-9-]+\]")


@dataclass(frozen=True)
class KnowledgeWriteDecision:
    decision: KnowledgeDecision
    reasons: tuple[str, ...] = ()
    policy_id: str = "knowledge-write-mvp"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reasons": list(self.reasons),
            "policy_id": self.policy_id,
        }


@dataclass(frozen=True)
class ConflictRecord:
    subject: str
    claim_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    values: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "claim_ids": list(self.claim_ids),
            "source_ids": list(self.source_ids),
            "values": list(self.values),
        }


@dataclass(frozen=True)
class ConflictReport:
    conflicts: tuple[ConflictRecord, ...] = ()

    @property
    def count(self) -> int:
        return len(self.conflicts)

    def conflicted_claim_ids(self) -> set[str]:
        out: set[str] = set()
        for conflict in self.conflicts:
            out.update(conflict.claim_ids)
        return out

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
        }


@dataclass
class KnowledgePipelineResult:
    registry: SourceRegistry
    conflicts: ConflictReport
    source_store: dict[str, int] = field(default_factory=dict)
    memory_saved: int = 0
    memory_rejected: int = 0
    memory_skipped: int = 0
    decisions: list[dict[str, Any]] = field(default_factory=list)

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "source_count": len(self.registry.sources),
            "claim_count": len(self.registry.claims),
            "conflicts": self.conflicts.to_log_payload(),
            "source_store": dict(self.source_store),
            "memory_saved": self.memory_saved,
            "memory_rejected": self.memory_rejected,
            "memory_skipped": self.memory_skipped,
            "decisions": list(self.decisions),
        }


RememberFn = Callable[
    [str, list[str], str, str, str],
    tuple[MemoryWriteDecision, MemoryRecord | None],
]


class ClaimExtractor:
    """Deterministic first-pass claim extractor from Evidence excerpts."""

    def __init__(
        self,
        *,
        max_claims_per_source: int = 5,
        min_chars: int = 18,
        max_chars: int = 320,
    ):
        self.max_claims_per_source = max_claims_per_source
        self.min_chars = min_chars
        self.max_chars = max_chars

    def extract(
        self,
        evidence: Evidence,
        *,
        source: SourceRecord,
        rank: SourceRank | None = None,
    ) -> list[ClaimRecord]:
        claims: list[ClaimRecord] = []
        if _is_meaningful_claim(evidence.claim):
            claims.append(claim_from_evidence(evidence, source_id=source.id, rank=rank))

        # Hidden text never becomes a claim — structural, so it does not depend
        # on the attacker's wording. Measured 2026-08-15: eight rephrasings of
        # the same planted order scored 0 blocked out of 8 against the pattern
        # table, while every one of them sat in an HTML comment the operator
        # would never see in the rendered file. See docs/CODE_NOTES.md,
        # "Concealment, not vocabulary".
        from core.injection_guard import (
            strip_concealed,
            strip_suspicious_annotation,
        )

        # MIR-011: the guard's verdict used to live ONLY in the wrapper, and
        # the strip below destroyed it — a sentence from scanner-flagged
        # content was indistinguishable from clean. Detect BEFORE stripping;
        # what gets minted from a flagged excerpt is quarantined as `suspect`
        # (stored and auditable, refused by the write policy, never upgraded
        # by corroboration — quarantine, not blunt exclusion).
        # AUDIT of this repair, 2026-08-22: the first version tainted EVERY
        # claim from a flagged excerpt. Measured on the live registry, 11% of
        # sources carry at least one tripping sentence and they hold 22% of
        # all claims — so document-level taint would have quarantined a fifth
        # of the agent's knowledge to catch the planted lines. That is the
        # field's named failure for this shape: scanner false positives
        # strangle legitimate sources. The taint is per-SENTENCE now: only
        # text the guard actually pointed at is suspect, and the rest of the
        # document keeps its ordinary standing.
        suspect_spans = _suspicious_spans(evidence.excerpt)

        # Голос охранника (обёртка annotate_suspicious) — аннотация для
        # синтезатора, не содержимое источника: снимается до нарезки на
        # предложения, иначе предупреждение становится «фактом» (живой прогон
        # 2026-08-16: 7 записей в постоянную память).
        for sentence in _statements(
            strip_suspicious_annotation(strip_concealed(evidence.excerpt))
        ):
            if len(claims) >= self.max_claims_per_source:
                break
            if not self._accept_sentence(sentence):
                continue
            confidence = rank.final_score if rank is not None else evidence.confidence
            tainted = any(span in sentence or sentence in span
                          for span in suspect_spans)
            status = "suspect" if tainted else _status_from_rank(rank)
            claims.append(ClaimRecord(
                id=_claim_id(),
                source_id=source.id,
                text=sentence,
                locator=source.locator,
                confidence=_bounded(confidence),
                status=status,
                extracted_at=evidence.fetched_at,
                metadata={
                    "evidence_id": evidence.id,
                    "evidence_kind": evidence.kind,
                    "extraction": "sentence",
                },
            ))

        # Stable dedup inside one source.
        out: list[ClaimRecord] = []
        seen: set[str] = set()
        for claim in claims:
            key = " ".join(claim.text.casefold().split())
            if key in seen:
                continue
            seen.add(key)
            out.append(claim)
        return out

    def _accept_sentence(self, sentence: str) -> bool:
        text = sentence.strip()
        if len(text) < self.min_chars or len(text) > self.max_chars:
            return False
        if contains_secret(text)[0]:
            return False
        # MIR-097: a sentence carrying a trimmer's notice was never fully
        # written by the source — refused, not cleaned. The grammar lives
        # beside the writers (`core/evidence_budget.py`), keyed on shape, so an
        # unseen notice of the same class is refused too. Twenty corrupted
        # claims leaked over seventeen days, and a data-only cleanup on
        # 2026-08-15 lasted exactly one day because this line was missing.
        if carries_framework_notice(text):
            return False
        if text.count("{") + text.count("[") > 4:
            return False
        if not re.search(r"[A-Za-zА-Яа-я]", text):
            return False
        if _is_broken_encoding(text) or _looks_like_code_fragment(text):
            # Mojibake and raw source-code / CLI / mid-sentence fragments are not
            # facts — they are file chunks that flooded memory as distractors.
            return False
        if _is_truncated_text(text):
            # Та же семья: обрезанное предложение — кусок файла, не факт.
            return False
        words = re.findall(r"[\w]+", text, flags=re.UNICODE)
        return len(words) >= 4


def claim_provenance_tag(claim_id: str) -> str:
    """Tag linking a memory record back to the claim it came from."""
    return f"claim:{claim_id}"


def quarantine_conflicted_records(
    records: list[MemoryRecord], *, conflicted_claim_ids: set[str]
) -> tuple[list[MemoryRecord], dict]:
    """Tag records whose originating claim is now contradicted.

    Idempotent — a record already carrying the tag is counted, not re-
    tagged. Resolution is operator-only: removing the tag restores the
    record.
    """
    report = {"quarantined": 0, "already_quarantined": 0, "unlinked": 0, "unaffected": 0}
    if not conflicted_claim_ids:
        report["unaffected"] = len(records)
        return list(records), report

    wanted = {claim_provenance_tag(cid) for cid in conflicted_claim_ids}
    out: list[MemoryRecord] = []
    for record in records:
        tags = list(record.tags or [])
        if not any(t.startswith("claim:") for t in tags):
            report["unlinked"] += 1
            out.append(record)
            continue
        if not (set(tags) & wanted):
            report["unaffected"] += 1
            out.append(record)
            continue
        if "conflicted" in tags:
            report["already_quarantined"] += 1
            out.append(record)
            continue
        report["quarantined"] += 1
        out.append(record.model_copy(update={"tags": [*tags, "conflicted"]}))
    return out, report


# Source types where "X is Y" is genuinely a proposition about the world, and
# two of them disagreeing is a real contradiction worth raising.
#
# Everything else is excluded on purpose (MIR-054). `file` is source code and
# project documents: `path = ...` is an assignment, not an assertion, and every
# module docstring opens "This module is ...". `memory` is the agent's own
# prior output: two goals recorded by two runs are two tasks, and citing
# yourself is not a second source. `log`, `tool_output`, `test_result` are
# observations of one moment, not standing claims.
#
# Measured on the live registry before this rule: 16 conflicts, all false,
# across 52 claims sourced entirely from `file` (36) and `memory` (16) --
# not one from a prose source.
# Source types that do not ASSERT anything, so "X is Y" read out of them is not
# a proposition and two of them differing is not a contradiction (MIR-054).
#
#   memory       the agent's own prior output -- two goals recorded by two runs
#                are two tasks, and citing yourself is not a second source
#   log/tool_output/test_result
#                observations of one moment, not standing claims
#   code_repository
#                programs, same reason as code files below
#
# Everything else stays in scope, including low-trust sources like `forum`:
# a forum post does assert things, it just asserts them unreliably, and that
# is what `trust_level` is for. Excluding it would silently drop a real
# disagreement instead of resolving it.
_NON_ASSERTING_SOURCE_TYPES = frozenset({
    "memory", "log", "tool_output", "test_result", "code_repository",
    # dialogue — дословная реплика разговора. Две реплики об одном предмете
    # это ход беседы, а не спор двух источников о мире.
    "dialogue",
})

# Схема адреса, под которой лежат реплики разговора. Проверяется ОТДЕЛЬНО от
# типа, потому что строки, записанные до появления типа "dialogue"
# (2026-09-23), уже лежат на диске с типом "unknown", и починка у истока их не
# перепишет. Тот же приём, что `_is_code_locator` ниже: адрес говорит о природе
# источника даже когда тип потерян.
_DIALOGUE_LOCATOR_PREFIX = "session_dialogue:"


def _is_dialogue_locator(locator: str) -> bool:
    return (locator or "").strip().casefold().startswith(_DIALOGUE_LOCATOR_PREFIX)

# Extensions whose contents are programs, not assertions. `x = y` there is an
# assignment, and every module docstring opens "This module is ...". A prose
# file (.md, .txt) is left in scope -- it really does state things.
_CODE_SUFFIXES = frozenset({
    ".py", ".pyi", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".sh", ".ps1", ".bat", ".sql", ".toml",
    ".ini", ".cfg", ".yaml", ".yml", ".json",
})


#: Украшения адреса, которые не меняют его природы: диапазон строк, номер
#: строки, метка фрагмента. `file_read` с окном отдаёт адрес вида
#: `core/x.py:284-292`, и проверка окончания на нём не срабатывает.
_LOCATOR_DECORATION_RE = re.compile(r":\d+(?:-\d+)?$|#L\d+(?:-L?\d+)?$")


def _bare_locator(locator: str) -> str:
    """Адрес без украшений: `core/x.py:284-292` -> `core/x.py`."""
    text = (locator or "").strip().casefold()
    while True:
        stripped = _LOCATOR_DECORATION_RE.sub("", text)
        if stripped == text:
            return text
        text = stripped


def _is_code_locator(locator: str) -> bool:
    """Адрес указывает на программу, а не на прозу.

    Замер 2026-09-23: правило «программы не утверждают фактов» обходилось
    номерами строк. `core/loop_response_deciders.py` узнавался как код,
    а `core/loop_response_deciders.py:265-285` — нет, потому что на `.py` не
    кончается. В памяти агента из-за этого легли 18 записей, собранных из
    его же кода и комментариев и помеченных как факты о мире с уверенностью
    0.85 — например обрывок «SEARCH/REPLACE в Aider).» из шапки
    tools/patch_check.py, записанный дважды.

    Тот же класс, что денежное число со знаком валюты (7c4c957) и урезанное
    имя следа (df8c2cb): украшенная строка ломает сверку по окончанию.
    Лечится нормализацией адреса ДО проверки, а не новым списком исключений.
    """
    return any(_bare_locator(locator).endswith(suffix) for suffix in _CODE_SUFFIXES)


#: Места, где лежит то, что агент написал САМ: черновики правок, журналы и
#: состояние, следы, документы, собранные скриптом из кода.
_OWN_ARTIFACT_PREFIXES = ("proposals/", "data/", "logs/", "knowledge/generated/")
#: Журнальные форматы — запись одного момента, не утверждение о мире.
_OWN_ARTIFACT_SUFFIXES = (".jsonl", ".log")


def _is_own_artifact(locator: str) -> bool:
    """Адрес указывает на файл, который агент написал сам.

    Замер 2026-09-23 на живой памяти: фактами «о мире» с уверенностью 0.85
    ложились его же черновики правок (`proposals/selffix/…/edits.txt` —
    вместе с маркером `<<<<<<< LINES`), его же ящик голоса
    (`data/chat_outbox.jsonl` — прямо со скобками JSON) и строки таблицы
    `knowledge/generated/AGENT_ANATOMY.md`, собранного скриптом из кода. При
    чистке памяти убрано 44 таких обрывка; пробой на нынешнем коде показал,
    что все три входа открыты.

    Это класс OWASP ASI06 «отравление памяти» в его тихой форме: своё же,
    пересказанное своими словами, выглядит как независимое знание, и проверка
    по содержанию его не отличает — отличает только происхождение. Отсюда
    правило по адресу, а не по словам. Правило то же, что MIR-054 для
    журналов и собственной памяти: запись одного момента не утверждает фактов.

    Документы, написанные людьми (`knowledge/doctrine/`, `docs/`), и книги
    (`knowledge_library/`, `math_study/`) остаются в силе.
    """
    bare = _bare_locator(locator).replace("\\", "/")
    marker = "/agent-main/"
    if marker in bare:
        bare = bare.split(marker, 1)[1]
    while bare.startswith("./"):
        bare = bare[2:]
    return bare.startswith(_OWN_ARTIFACT_PREFIXES) or bare.endswith(_OWN_ARTIFACT_SUFFIXES)


class ConflictResolver:
    """Detect obvious contradictory claims over the same subject."""

    def resolve(self, registry: SourceRegistry) -> tuple[SourceRegistry, ConflictReport]:
        grouped: dict[str, list[tuple[ClaimRecord, str]]] = {}
        sources_by_id = {source.id: source for source in registry.sources}
        for claim in registry.claims:
            # Contradiction is only meaningful between sources that assert
            # things. Code, the agent's own memory, logs and tool output do
            # not, and reading them as propositions produced only false
            # positives (MIR-054).
            source = sources_by_id.get(claim.source_id)
            if source is None or source.type in _NON_ASSERTING_SOURCE_TYPES:
                continue
            if _is_code_locator(source.locator):
                continue
            if _is_dialogue_locator(source.locator):
                continue
            parsed = _subject_value(claim.text)
            if parsed is None:
                continue
            subject, value = parsed
            grouped.setdefault(subject, []).append((claim, value))

        conflicts: list[ConflictRecord] = []
        conflicted_ids: set[str] = set()
        # claim_id -> the OTHER independent source_ids that agree with it.
        # A claim corroborated by >=2 independent sources (single agreed value,
        # no contradiction) is promoted to "verified" below — this is the
        # honest "усвоил и проверил" step: a fact is verified only when more
        # than one independent source attests it.
        corroborated: dict[str, tuple[str, ...]] = {}
        for subject, pairs in grouped.items():
            values = _distinct_values(value for _claim, value in pairs)
            source_ids = sorted({claim.source_id for claim, _value in pairs})
            if len(source_ids) < 2:
                continue
            if len(values) >= 2:
                claim_ids = tuple(claim.id for claim, _value in pairs)
                conflicted_ids.update(claim_ids)
                conflicts.append(ConflictRecord(
                    subject=subject,
                    claim_ids=claim_ids,
                    source_ids=tuple(source_ids),
                    values=tuple(values),
                ))
            else:
                # One agreed value from >=2 independent sources -> corroboration.
                for claim, _value in pairs:
                    others = tuple(s for s in source_ids if s != claim.source_id)
                    if others:
                        corroborated[claim.id] = others

        if not conflicted_ids and not corroborated:
            return registry, ConflictReport()

        resolved = SourceRegistry()
        for source in registry.sources:
            resolved.add_source(source)
        for claim in registry.claims:
            if claim.id in conflicted_ids:
                conflict_sources = sorted({
                    conflict_source
                    for conflict in conflicts
                    if claim.id in conflict.claim_ids
                    for conflict_source in conflict.source_ids
                    if conflict_source != claim.source_id
                })
                resolved.add_claim(replace(
                    claim,
                    status="conflicted",
                    conflict_source_ids=tuple(conflict_sources),
                ))
            elif claim.id in corroborated and claim.status == "extracted":
                # Only a normally-extracted claim is promoted; a weak-source
                # "unverified" claim stays weak even if echoed, so two weak
                # sources cannot manufacture a "verified" fact.
                merged_support = tuple(sorted(
                    set(claim.support_source_ids) | set(corroborated[claim.id])
                ))
                resolved.add_claim(replace(
                    claim,
                    status="verified",
                    support_source_ids=merged_support,
                ))
            else:
                resolved.add_claim(claim)
        return resolved, ConflictReport(tuple(conflicts))


_INTERNET_SOURCE_PREFIXES = ("web_page:", "web_search:", "web:", "search:", "rss:", "semantic_scholar:")


def _is_internet_source(source_id: str, source: SourceRecord | None) -> bool:
    """Пришло ли утверждение из сети — по адресу улики или по локатору источника."""
    sid = (source_id or "").lower()
    locator = (getattr(source, "locator", "") or "").lower()
    return sid.startswith(_INTERNET_SOURCE_PREFIXES) or locator.startswith(("http://", "https://"))


class KnowledgeWritePolicy:
    """Gate source-backed claims before they become long-term memory."""

    def __init__(
        self,
        *,
        min_claim_confidence: float = 0.65,
        min_source_trust: float = 0.55,
        max_chars: int = 900,
    ):
        self.min_claim_confidence = min_claim_confidence
        self.min_source_trust = min_source_trust
        self.max_chars = max_chars

    def decide(  # noqa: PLR0911 — flat: depth 1, all 15 returns are guard clauses
        self,
        claim: ClaimRecord,
        *,
        source: SourceRecord | None,
    ) -> KnowledgeWriteDecision:
        reasons: list[str] = []
        text = (claim.text or "").strip()
        if source is None:
            return KnowledgeWriteDecision("reject", ("source not registered",))
        if not text:
            return KnowledgeWriteDecision("reject", ("empty claim",))
        if len(text) > self.max_chars:
            return KnowledgeWriteDecision("reject", (f"claim too long (>{self.max_chars})",))
        if contains_secret(text)[0]:
            return KnowledgeWriteDecision("reject", ("claim contains secret material",))
        # Остаток редактуры. Проверка выше судит ТЕКСТ, а текст сюда приходит
        # уже вычищенным, поэтому `TELEGRAM=[REDACTED:telegram-bot-token]` под
        # шаблон ключа не подходит и проезжает. Замер 2026-08-10: такие строки
        # из `.env` легли в долговечную семантическую память с тегами
        # `fact, knowledge` — значение не утекло, утёк инвентарь секретов
        # установки, живущий дольше прогона. Маркер редактуры и есть
        # доказательство, что материал был секретным: вердикт источника
        # (`class=secret`) до этой политики не доезжает, а маркер доезжает.
        if _REDACTION_MARKER_RE.search(text):
            return KnowledgeWriteDecision(
                "reject", ("claim is redacted secret residue",)
            )
        # Truth/Hype filter (first LEARNING antibody): promotional content with
        # no checkable substance is "шумиха", not knowledge — never absorb it.
        _th = evaluate_truth_hype(text)
        if _th.is_hype:
            return KnowledgeWriteDecision(
                "reject",
                ((f"claim is promotional hype (no checkable substance): "
                 f"{'; '.join(_th.reasons[:2])}"),),
            )
        # A label WE wrote for a tool result is not an assertion about the
        # world. `Evidence.claim` is framework-authored in every case — fifteen
        # templates in `evidence_from_tool_result` plus the ingestion ones —
        # while the source's own words live in `excerpt`. Decided on provenance,
        # not vocabulary: `_is_meaningful_claim` blacklists six opening phrases,
        # so which label survived depended on whether two authors happened to
        # pick the same first words. Measured 2026-08-15: «Fetched RSS/Atom feed
        # …» and «User explicitly directed» reached `save`; nine others were
        # stopped by unrelated gates, which is luck, not a rule.
        # `getattr`, not attribute access: `decide` is fed duck-typed claims by
        # callers and tests, and a missing optional field must skip this check,
        # not crash the write path.
        if (getattr(claim, "metadata", None) or {}).get("extraction") == "evidence_claim":
            return KnowledgeWriteDecision(
                "reject",
                (
                    ("claim is our own label for a tool result, not an "
                     "assertion by the source (source words live in the "
                     "excerpt)"),
                ),
            )
        if claim.status == "suspect":
            # MIR-011 quarantine: the injection guard flagged the content this
            # claim was extracted from. It stays in the registry for audit and
            # review, and never becomes durable knowledge.
            return KnowledgeWriteDecision(
                "reject",
                (("claim was extracted from scanner-flagged content "
                  "(injection guard: suspicious) — quarantined, not knowledge"),),
            )
        if claim.status in {"unverified", "conflicted"}:
            return KnowledgeWriteDecision("reject", (f"claim status is {claim.status}",))
        if claim.confidence < self.min_claim_confidence:
            return KnowledgeWriteDecision(
                "reject",
                (f"claim confidence {claim.confidence:.2f} < {self.min_claim_confidence:.2f}",),
            )
        if source.trust_level < self.min_source_trust:
            return KnowledgeWriteDecision(
                "reject",
                (f"source trust {source.trust_level:.2f} < {self.min_source_trust:.2f}",),
            )
        if source.type in _NON_ASSERTING_SOURCE_TYPES:
            # Same doctrine the conflict resolver applies (MIR-054): a log
            # line, a test verdict, a tool dump or the agent's own memory is
            # an observation of one moment, not a standing claim about the
            # world. Trust cannot rescue it — DEFAULT_SOURCE_TRUST rates
            # `log` 0.88 and `test_result` 0.95, which is how a diagnostic
            # run (run_ab3f4bb672…, 2026-07-31) restated five of its own log
            # events as durable Confidence-0.85 "facts". Reject by assertion
            # class, before the trust ladder is even consulted for weakness.
            return KnowledgeWriteDecision(
                "reject",
                ((f"source type {source.type} does not assert facts "
                 f"(observation of one moment, not a standing claim)"),),
            )
        if source.type in {"forum", "unknown"}:
            return KnowledgeWriteDecision("reject", (f"source type {source.type} is too weak",))
        if source.type == "file" and _is_code_locator(source.locator):
            # Same rule the conflict resolver applies (MIR-054): programs do
            # not assert. `x = y` in a .py file is an assignment, an `assert`
            # line is a test statement — reading either as a world-fact is how
            # the 2026-07-25 wave banked 776 file rows, 26 of them raw asserts.
            # Prose files (.md, .txt) stay in scope: they really state things.
            return KnowledgeWriteDecision(
                "reject",
                ((f"source is a code file ({source.locator}): programs do not "
                 f"assert facts"),),
            )
        if source.type == "file" and _is_own_artifact(source.locator):
            return KnowledgeWriteDecision(
                "reject",
                ((f"source is the agent's own artifact ({source.locator}): its "
                  f"drafts, logs and generated files are not evidence about the "
                  f"world"),),
            )
        reasons.append(f"claim confidence={claim.confidence:.2f}")
        reasons.append(f"source trust={source.trust_level:.2f}")
        reasons.append(f"source type={source.type}")
        return KnowledgeWriteDecision("save", tuple(reasons))

    def memory_content(self, claim: ClaimRecord, source: SourceRecord) -> str:
        return (
            f"{claim.text}\n"
            f"Source: {source.type}:{source.locator}\n"
            f"Confidence: {claim.confidence:.2f}"
        )

    def memory_tags(self, claim: ClaimRecord, source: SourceRecord) -> list[str]:
        # `claim:<id>` is provenance, not description: it is what lets a
        # record be found again if its claim is later contradicted (MIR-047).
        return [
            "fact", "knowledge", "source-backed", source.type,
            claim_provenance_tag(claim.id),
        ]


class KnowledgePipeline:
    """End-to-end knowledge integration for one agent cycle."""

    def __init__(
        self,
        *,
        claim_extractor: ClaimExtractor | None = None,
        conflict_resolver: ConflictResolver | None = None,
        write_policy: KnowledgeWritePolicy | None = None,
    ):
        self.claim_extractor = claim_extractor or ClaimExtractor()
        self.conflict_resolver = conflict_resolver or ConflictResolver()
        self.write_policy = write_policy or KnowledgeWritePolicy()

    def build_registry(
        self,
        chain: ProvenanceChain,
        *,
        ranking: SourceRankingReport | None = None,
    ) -> tuple[SourceRegistry, ConflictReport]:
        registry = SourceRegistry.from_provenance(
            chain,
            ranking=ranking,
            claim_extractor=self.claim_extractor,
        )
        return self.conflict_resolver.resolve(registry)

    def run(
        self,
        chain: ProvenanceChain,
        *,
        ranking: SourceRankingReport | None = None,
        source_store: SourceRegistryStore | None = None,
        remember: RememberFn | None = None,
        auto_write_memory: bool = False,
        require_verified: bool = False,
        admit_internet: bool = True,
    ) -> KnowledgePipelineResult:
        registry, conflicts = self.build_registry(chain, ranking=ranking)
        result = KnowledgePipelineResult(registry=registry, conflicts=conflicts)

        if source_store is not None:
            result.source_store = source_store.save_registry(registry)

        if not auto_write_memory or remember is None:
            # A row per claim here too. The `require_verified` skip below
            # got one and this one did not, so a live run reported
            # `memory_skipped=45, decisions=[]` — the counter said 45
            # claims went nowhere and nothing said why. Fixing one of two
            # silent paths is not fixing the class.
            #
            # The rule that refused is named too, not just described. Both
            # branches once reported `policy_id="auto_write_memory"`, so a
            # reader filtering decisions by rule — the machine-readable half
            # of the row — saw the operator blamed for a run where the
            # operator had opted in and nothing was wired to write. The two
            # facts were distinguishable in prose and identical in metadata,
            # which is the same invisible failure one field further down.
            reason, policy_id = (
                ("auto_write_memory is off", "auto_write_memory")
                if not auto_write_memory
                else ("no memory writer is wired", "memory_writer_missing")
            )
            for claim in registry.claims:
                result.memory_skipped += 1
                result.decisions.append({
                    "claim_id": claim.id,
                    "source_id": claim.source_id,
                    "knowledge_decision": {
                        "decision": "skip",
                        "reasons": [reason],
                        "policy_id": policy_id,
                    },
                })
            return result

        for claim in registry.claims:
            if not admit_internet and _is_internet_source(claim.source_id, registry.get_source(claim.source_id)):
                # Два мира (оператор, 2026-09-19): локальная библиотека —
                # источник истины, интернет — источник, который ещё надо
                # квалифицировать. Четвёртый веб-прогон, N06: страница
                # rfc-editor.org (тип «documentation», доверие 0.75) записала
                # в долговременную память четыре «факта», среди них
                # «0 Authorization Framework D.». Утверждение из сети остаётся
                # в реестре источников — для аудита и квалификации; фактом
                # памяти его делает осознанная загрузка (`ingestion.py`), не ход.
                result.memory_skipped += 1
                result.decisions.append({
                    "claim_id": claim.id,
                    "source_id": claim.source_id,
                    "knowledge_decision": {
                        "decision": "skip",
                        "reasons": [("internet source: kept in the source registry to be qualified, "
                                     "not promoted to durable memory by an ordinary turn")],
                        "policy_id": "two_worlds",
                    },
                })
                continue
            if require_verified and claim.status != "verified":
                # SKIPPED, not rejected: the claim was not judged bad, it
                # was never corroborated. `verified` is set in
                # `build_registry` only when a second source says the same
                # thing AND the claim was normally extracted, so this gate
                # means "one source is not enough to become a memory".
                #
                # It exists for the UNATTENDED path. A human running
                # `:ingest` is present and can judge; a campaign running
                # overnight cannot, and a single file asserting something
                # about itself is not evidence that it is true.
                #
                # A decision row all the same. A claim that vanishes
                # without one leaves the reader a counter and no reason —
                # the invisible-failure shape MIR-077 was closed for, and
                # a gate that cannot say why it refused is the worst place
                # to reintroduce it.
                result.memory_skipped += 1
                result.decisions.append({
                    "claim_id": claim.id,
                    "source_id": claim.source_id,
                    "knowledge_decision": {
                        "decision": "skip",
                        "reasons": [
                            (f"claim status is {claim.status!r}, not "
                             "'verified': an unattended run writes only "
                             "what a second source corroborated"),
                        ],
                        "policy_id": "require_verified",
                    },
                })
                continue
            if claim_source_is_untrusted(claim.text):
                # Barred, not merely rejected: this text tried to give orders.
                result.memory_rejected += 1
                result.decisions.append({
                    "claim_id": claim.id,
                    "source_id": claim.source_id,
                    "knowledge_decision": {
                        "decision": "reject",
                        "reasons": [
                            ("injection guard flagged the claim text; "
                             "an instruction is not a fact"),
                        ],
                        "policy_id": "injection_guard",
                    },
                })
                continue
            source = registry.get_source(claim.source_id)
            decision = self.write_policy.decide(claim, source=source)
            row: dict[str, Any] = {
                "claim_id": claim.id,
                "source_id": claim.source_id,
                "knowledge_decision": decision.to_dict(),
            }
            if decision.decision == "reject" or source is None:
                result.memory_rejected += 1
                result.decisions.append(row)
                continue
            memory_decision, record = remember(
                self.write_policy.memory_content(claim, source),
                self.write_policy.memory_tags(claim, source),
                "agent-auto",
                "semantic",
                "self",
            )
            row["memory_decision"] = {
                "decision": memory_decision.decision,
                "reasons": list(memory_decision.reasons),
                "policy_id": memory_decision.policy_id,
                "record_id": record.id if record is not None else None,
            }
            if memory_decision.decision == "save":
                result.memory_saved += 1
            else:
                result.memory_rejected += 1
            result.decisions.append(row)
        return result



def claim_source_is_untrusted(text: str) -> bool:
    """True when the injection guard refuses this text."""
    from core.injection_guard import scan_for_injection

    return scan_for_injection(text or "").is_blocked


_WINDOW_LINE_NO = re.compile(r"^\s*\d+:\s?")
_LIST_ITEM = re.compile(r"^(?:[-*•]|\d+[.)])\s+")
_TERMINAL = re.compile(r"[.!?][\"'»”)\]]*$")


def _statements(text: str) -> list[str]:
    """Законченные утверждения источника — кандидаты в факты.

    Замер 2026-09-19 (опыт с библиотекой книг): из 30 записей долговременной
    памяти знанием не было ни одной — «320: Consequence is that (fig 1.5)…»,
    «5: Michaelmas term 2021/22», «7: 1 Introduction 7», «5304: eβ(ℏ2k2/2m−µ) + 1».
    Нарезка шла ПО СТРОКАМ: в книге из PDF строка рвёт предложение, окно
    file_read добавляет номер строки, и каждая строка титула, оглавления или
    формулы становилась «фактом» с уверенностью 0.85; такие записи потом
    доставались в ответы до семи раз. Здесь: шапка окна и номера строк
    снимаются, перенесённые строки склеиваются обратно (продолжение начинается
    со строчной), а фактом становится только законченное предложение или пункт
    списка. Титулы, заголовки и обрывки — нет.
    """
    lines = (text or "").splitlines()
    if lines and _READ_WINDOW_HEADER.match(lines[0].strip()):
        lines = [_WINDOW_LINE_NO.sub("", ln, count=1) for ln in lines[1:]]
    units: list[tuple[str, bool]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            units.append(("", False))
            continue
        item = bool(_LIST_ITEM.match(line))
        structural = item or line.startswith(("#", "|", ">"))
        prev, prev_item = units[-1] if units else ("", False)
        if (prev and not structural and not re.search(r"[.!?:]$", prev)
                and re.match(r"^[a-zа-яё(]", line)):
            units[-1] = (f"{prev}{line}" if prev.endswith("-") else f"{prev} {line}", prev_item)
        else:
            units.append((line, item))
    out: list[str] = []
    for unit, item in units:
        for part in re.split(r"(?<=[.!?])\s+|[•;]\s+", unit):
            part = part.strip(" -\t")
            if part and (item or _TERMINAL.search(part)):
                out.append(part)
    return out


def _sentences(text: str) -> list[str]:
    pieces: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"(?<=[.!?])\s+|[•;]\s+", line)
        pieces.extend(part.strip(" -\t") for part in parts if part.strip(" -\t"))
    return pieces


def _is_broken_encoding(text: str) -> bool:
    """True when the text carries mojibake / decode-failure markers.

    The Unicode replacement char (``�``) is the unambiguous signal that
    bytes were decoded with the wrong codec upstream; such content is garbage,
    never a fact.
    """
    if "�" in text:
        return True
    # A high density of replacement chars or lone control bytes also indicates
    # corruption even if a few survived.
    bad = sum(1 for ch in text if ch == "�" or (ord(ch) < 0x20 and ch not in "\t\n\r"))
    return bad > 0


# Prefixes that mark a line as source code, a shell/REPL transcript, or a
# doc/CLI example rather than a natural-language fact.
_CODE_LINE_PREFIXES = (
    "import ", "from ", "def ", "class ", "return ", "raise ", "yield ",
    "async def ", "await ", "@", "#", "//", "/*", "*", '"""', "'''",
    ">>>", ">>> ", "> ", "$ ", "python ", "pip ", "uvicorn ", "pytest ",
    "git ", "cd ", "curl ", "npm ", "--", "```",
    # Statement keywords — but ONLY the ones prose never opens with.
    # `assert len(records) == 2` has no call-heavy shape and no code suffix,
    # so none of the structural rules below fired — which is how 26 raw
    # assert lines from tests/*.py entered the live store as Confidence-0.85
    # "facts" in the 2026-07-25 ingestion wave. Deliberately NOT listed:
    # "if ", "for ", "while ", "with ", "except ", "global ", "pass" — real
    # prose facts open with those ("If the budget is exceeded, the run
    # stops."); "del " (Del Toro, Del Monte, Spanish "del"), "finally:"
    # ("Finally: the last step is testing.") and "lambda " ("Lambda is an
    # AWS compute service.") for the same reason — a classifier that
    # rejects prose is a worse defect than the one it fixes.
    "assert ", "elif ", "else:", "try:",
    "self.",
)

# Dangling trailing tokens that reveal a mid-sentence chunk cut, not a whole fact.
_DANGLING_TAIL = {
    "and", "or", "but", "the", "a", "an", "of", "to", "for", "with", "in",
    "on", "at", "as", "by", "from", "that", "which", "и", "или", "в", "на",
    "с", "по", "что", "для",
}


#: Шапка окна чтения, которую file_read ставит перед куском файла.
_READ_WINDOW_HEADER = re.compile(r"^\[[^\]]*\blines?\s+\d+\s*-\s*\d+\s+of\s+\d+\]$", re.IGNORECASE)
#: Знаки, на которых законченная мысль не кончается.
_CUT_PUNCTUATION = (",", ":", ";", "—", "–", "(", "⟹", "→", "⇒")


def _looks_like_code_fragment(text: str) -> bool:
    """True when the line is source code / CLI / a mid-sentence file chunk.

    Deterministic, high-precision heuristics — the goal is to keep raw file
    fragments out of long-term memory without rejecting genuine prose facts.
    """
    stripped = text.strip()
    low = stripped.casefold()
    if any(low.startswith(p.casefold()) for p in _CODE_LINE_PREFIXES):
        return True
    # Замер 2026-09-19: шапка окна file_read («[data.csv lines 1-20 of 61]»)
    # сохранялась фактом с уверенностью 0.85 — 11 из 75 записей учебного опыта.
    # В опыте «одна задача пять раз» она пережила смену данных, всплыла в
    # следующем ответе как «в памяти 61 строка» и сняла верный эпизод с опыта.
    if _READ_WINDOW_HEADER.match(stripped):
        return True
    # Обрыв на знаке, после которого мысль обязана продолжиться:
    # «…, library/txt/Beck.txt —», «Карта строк txt:».
    if stripped.endswith(_CUT_PUNCTUATION):
        return True
    # Assignment / call-heavy code without sentence structure.
    if ("=" in stripped or "()" in stripped) and stripped.endswith((":", ")", "}", ";")):
        return True
    if stripped.count("(") + stripped.count(")") >= 4:
        return True
    # A chunk cut mid-sentence: ends on a conjunction/preposition/article and
    # has no terminal punctuation.
    last = re.findall(r"[\w']+", low)
    return bool(last and last[-1] in _DANGLING_TAIL and not stripped.endswith((".", "!", "?")))


def _is_meaningful_claim(text: str) -> bool:
    lowered = (text or "").casefold()
    generic = (
        "contents of workspace file",
        "fetched page",
        "search for",
        "tool ",
        "read ",
        "ran `",
    )
    return bool(text and not any(lowered.startswith(prefix) for prefix in generic))


def _suspicious_spans(excerpt: str) -> list[str]:
    """Sentences the injection guard actually pointed at.

    The guard reports each finding with a byte offset, so the taint can follow
    the evidence instead of the document. Returns the wrapper-stripped
    sentences containing a finding; empty when the excerpt is clean or was
    never wrapped.
    """
    from core.injection_guard import (
        carries_suspicious_annotation,
        scan_for_injection,
        strip_suspicious_annotation,
    )

    if not carries_suspicious_annotation(excerpt):
        return []
    body = strip_suspicious_annotation(excerpt)
    result = scan_for_injection(body)
    offsets = [int(getattr(f, "offset", -1)) for f in result.findings]
    offsets = [o for o in offsets if o >= 0]
    if not offsets:
        # Flagged on our own wrapper but the body scans clean now — treat the
        # WHOLE body as suspect rather than nothing: losing the reason for a
        # flag must not silently clear the flag.
        return _sentences(body)
    spans: list[str] = []
    cursor = 0
    for sentence in _sentences(body):
        start = body.find(sentence, cursor)
        if start < 0:
            continue
        cursor = start + len(sentence)
        if any(start <= o < cursor for o in offsets):
            spans.append(sentence)
    return spans or _sentences(body)


def _status_from_rank(rank: SourceRank | None) -> str:
    if rank is None:
        return "extracted"
    if rank.support_level in {"weak", "insufficient_for_realtime"}:
        return "unverified"
    return "extracted"


def _is_truncated_text(text: str) -> bool:
    """Обрезок не утверждает ничего целого. Живой реестр 2026-08-16 читал
    «...capability is comp...[truncated]» как другое значение того же субъекта
    и чеканил ложный конфликт с целым предложением из зеркального документа.
    """
    tail = (text or "").rstrip()
    return "[truncated]" in tail or tail.endswith(("...", "…"))


#: Вопрос — не утверждение. Живой реестр 2026-09-20: оглавления двух книг,
#: «1.1 What is a compiler?» и «1.1 What is a plasma?», разобрались как
#: «предмет `11 what` = компилятор / плазма» и объявились противоречием, а
#: конфликт сажает на карантин запись памяти, из которой он вырос (MIR-047).
#: Ни «X is Y» из вопроса, ни вопросительное слово в предмете утверждением о
#: мире не являются.
_INTERROGATIVE_SUBJECT_RE = re.compile(
    r"(?:^|\s)(?:what|who|which|where|when|why|how|что|кто|какой|какая|какие|где|когда|почему|как)$",
    re.IGNORECASE)


def _subject_value(text: str) -> tuple[str, str] | None:
    if _is_truncated_text(text):
        return None
    compact = " ".join((text or "").strip().rstrip(".").split())
    if len(compact) < 8 or compact.endswith("?"):
        return None
    patterns = (
        r"^(.{3,80}?)\s+(?:is|are|=|:)\s+(.{1,120})$",
        r"^(.{3,80}?)\s+(?:это|является|=|:)\s+(.{1,120})$",
    )
    for pattern in patterns:
        match = re.match(pattern, compact, flags=re.IGNORECASE)
        if not match:
            continue
        raw_subject = match.group(1).strip()
        # MIR-076 (measured live): a DEICTIC noun phrase — «this document»,
        # «этот документ» — names a different referent in every source, so
        # grouping it across sources manufactures conflicts out of unrelated
        # self-descriptions. The bare-pronoun deny-list below cannot catch
        # the phrase form, and the old subject normaliser even STRIPPED
        # «этот», collapsing Russian deixis into a groupable common noun.
        if _DEICTIC_SUBJECT_RE.match(raw_subject) or _INTERROGATIVE_SUBJECT_RE.search(raw_subject):
            continue
        subject = _normalise_subject(raw_subject)
        value = _normalise_value(match.group(2))
        if subject in _GENERIC_CONFLICT_SUBJECTS:
            continue
        if subject and value:
            return subject, value
    return None


#: Deictic determiners open a context-bound subject; articles (the/a/an) do
#: not and stay strippable in `_normalise_subject`.
_DEICTIC_SUBJECT_RE = re.compile(
    r"^(this|that|these|those|этот|эта|это|эти|тот|та|то|те|данный|данная|данное|данные)\b",
    re.IGNORECASE,
)


def _normalise_subject(text: str) -> str:
    text = re.sub(r"^(the|a|an)\s+", "", text.strip().casefold())
    return re.sub(r"[^0-9a-zа-яё _-]+", "", text).strip()


def _normalise_value(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().casefold())


def _value_tokens(value: str) -> list[str]:
    """Word tokens for equivalence comparison, negation canonicalised."""
    tokens = re.sub(r"[^\w\s-]", " ", value, flags=re.UNICODE).casefold().split()
    return ["not" if t in ("never", "никогда") else t for t in tokens]


def _values_equivalent(a: str, b: str) -> bool:
    """Two claim values that agree, possibly in different words.

    Equivalent when their token forms match, or when the SHORTER one is a
    token-prefix of the longer AND is at least three tokens long — long
    enough that «good» never glues to «good for nothing», while a clipped
    quotation of the same sentence still counts as the same statement.
    """
    ta, tb = _value_tokens(a), _value_tokens(b)
    if ta == tb:
        return True
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return len(short) >= 3 and long_[: len(short)] == short


def _distinct_values(values: Iterable[str]) -> list[str]:
    """Cluster values by equivalence; one representative per cluster."""
    reps: list[str] = []
    for value in sorted(set(values)):
        if not any(_values_equivalent(value, rep) for rep in reps):
            reps.append(value)
    return reps


def _claim_id() -> str:
    from core.ids import new_id

    return new_id("claim")


_GENERIC_CONFLICT_SUBJECTS = {
    "it",
    "this",
    "that",
    "these",
    "those",
    "here",
    "there",
    "он",
    "она",
    "оно",
    "это",
    "этот",
    "эта",
    "эти",
    "то",
}
