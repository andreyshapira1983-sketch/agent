"""Low-evidence answer policy: keeps only chunks the verifier marked ``verified``, states that evidence was insufficient (EN or RU), downgrades the Output Contract ``Confidence`` line to ``low``, and routes the suppressed bulk into the ``Unverified`` section.

``dialogue_supported`` counts as support (not verification, never folded into ``verified_chunks``) because a claim about this session's own exchange is backed by the transcript; a world claim never earns it (see :mod:`core.evidence_classes`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.lang_match import looks_russian

_DEFAULT_MIN_TOTAL = 8
_DEFAULT_MAX_VERIFIED_RATIO = 0.20
_DEFAULT_UNVERIFIED_FLOOR = 6


# Roles whose deliverable is NEW synthesized code / docstring / diff, not factual claims: it can never appear verbatim in its source, so the gate would delete exactly what was asked for; evidence is not expected.
_GENERATIVE_ROLES: frozenset[str] = frozenset({"programmer"})

#: Markers of an actually generated artifact: a fenced block or unified-diff header is newly synthesized output; prose about a repository is not, however programmer-ish the role.
_FENCE_MARKER = "```"
_DIFF_MARKERS: tuple[str, ...] = ("--- ", "+++ ", "@@ ")


#: Genre, not form: code in a fence was exempt from evidence but a PLAN in prose was not, though both are synthesis; the demand to wrap prose in a code fence masked intent as code.
#: Watershed: a parameter-number does not exist outside the decision and cannot be checked; a claim-number speaks of what exists independently. A world claim must carry a source; a proposal about future work cannot, the work does not exist yet.
#: Observable signs of an external fact: a world unit of measure; a date; an affirmative link with an external entity name; an external entity name without an intent word nearby. Safeguard: an own quantity in world units ("budget 5 dollars") counts as a fact, since an extra source request is cheaper than a miss.
_WORLD_UNIT_MARKERS: tuple[str, ...] = (
    "доллар", "usd", "$", "евро", "eur", "рубл", "процент", "%", "годовых",
)
_ASSERTIVE_COPULAS: tuple[str, ...] = (
    "стоит", "стоил", "вышел", "вышла", "вышло", "составляет", "составлял",
    "равен", "равна", "равно", "опубликован", "достиг", "превысил",
)
_EXTERNAL_ENTITY_MARKERS: tuple[str, ...] = (
    "deepseek", "gpt", "claude", "llama", "gemini", "openai", "anthropic",
    "google", "microsoft", "apple", "nvidia", "windows", "linux", "android",
    "iphone", "python", "java", "rust", "pytorch", "docker", "aws", "azure",
)
_INTENT_WORD_MARKERS: tuple[str, ...] = (
    "порог", "шаг", "длина", "окно", "лимит", "попыт", "символ", "цикл",
    "размер", "количеств", "значени", "параметр", "настройк", "бюджет",
    "таймаут", "задержк", "глубин",
)
_DATE_PATTERNS: tuple[str, ...] = (
    r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b",
    r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b",
    (
        r"\b\d{1,2}\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|"
        r"сентябр|октябр|ноябр|декабр)\w*\b"
    ),
)


#: An external entity as a separate word, not part of an identifier (an estimate like "python_probe will count" was read as a claim about python).
_ENTITY_RE = re.compile(
    r"(?<![\w.])(?:" + "|".join(map(re.escape, _EXTERNAL_ENTITY_MARKERS)) + r")(?![\w])"
)


def _names_entity(text: str) -> bool:
    return _ENTITY_RE.search(text) is not None


#: A number together with a world unit: "$500", "500 dollars", "12%", "7 euro".
_UNIT_EXPR_RE = re.compile(
    r"\$\s?\d[\d.,]*|\d[\d.,]*\s?(?:доллар\w*|usd|евро|eur|рубл\w*|%|процент\w*|годовых)"
)
#: A line referencing a measurement about self is not a world claim. The unit is a line, not a sentence: the dot inside "3.11.9" cut a sentence in half.
_SELF_MEASURED_RE = re.compile(r"(?m)^.*\[runtime:[^\]]*\].*$")


def _states_external_fact(text: str, known: str = "") -> bool:
    """True when the text asserts something about the outside world and so must carry evidence.

    A feature already in the user's question (`known`) is a retelling, and a phrase with [runtime:...] is measured about self; neither is a new world claim.
    """
    import re
    text = _SELF_MEASURED_RE.sub(" ", text or "")
    if not text or not re.search(r"\d", text):
        return False
    lowered = text.lower()
    known_low = (known or "").lower()
    if known_low:
        # Compares the EXPRESSION "number + unit": "$500" from the question is a retelling, "$900" absent from it is a new world claim.
        def exprs(t: str) -> set[str]:
            return {re.sub(r"\s+", "", m.group(0)) for m in _UNIT_EXPR_RE.finditer(t)}
        if exprs(lowered) - exprs(known_low):
            return True
    elif any(unit in lowered for unit in _WORLD_UNIT_MARKERS):
        return True
    if any(m.group(0) not in known_low for pat in _DATE_PATTERNS for m in re.finditer(pat, lowered)):
        return True
    has_entity = _names_entity(lowered)
    if has_entity and any(cop in lowered for cop in _ASSERTIVE_COPULAS):
        return True
    if has_entity:
        for sentence in re.split(r"[.!?]\s*", lowered):
            if _names_entity(sentence) and not any(
                word in sentence for word in _INTENT_WORD_MARKERS
            ):
                return True
    # All external-fact signals are silent: a design parameter, not a fact.
    return False


#: Signs of a proposal about future work: intent mood and numbered steps. One sign is not enough: it must be in text that does NOT assert an external fact (`_states_external_fact`) and does NOT report done work (`_reports_a_result`).
_PLAN_MARKERS: tuple[str, ...] = (
    "шаг ", "шаг:", "сначала", "затем", "потом", "предлагаю", "сделаю",
    "план:", "план ", "поставк",
    # A forecast or estimate is the same genre: a statement about future work, with no source by nature.
    "прогноз", "оценк", "оцениваю", "справлюсь", "шагов", "forecast", "estimate",
    # A decision about its own work is the same genre.
    "беру", "берусь", "выбираю", "рекомендую", "i take", "i choose", "i recommend",
)
#: A numbered list only at line start (`"1."` matched the fraction "601.23 s" and a measurement report was exempted as a "plan").
_NUMBERED_LINE_RE = re.compile(r"(?m)^\s*\d{1,2}\.\s")

#: A result report is not a proposal. Signs: a verb of a completed action or a `field=value` record from tool output.
_REPORT_VERB_MARKERS: tuple[str, ...] = (
    "вернул", "выполнен", "выполнил", "получен", "прочитан", "найден", "найдено",
    "показал", "завершил", "завершён", "обнаружен", "составил", "выдал", "записан",
    "измерил", "измерен",
    "returned", "executed", "found ", "reported", "yielded", "completed", "measured",
)
_KEY_VALUE_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*=\S")


def _reports_a_result(text: str) -> bool:
    """True when the text reports something done rather than proposing to do it."""
    lowered = text.lower()
    if any(marker in lowered for marker in _REPORT_VERB_MARKERS):
        return True
    return _KEY_VALUE_RE.search(text) is not None or _CODE_LINE_RE.search(text) is not None


#: A reference to a code line is a report of what was read (an answer built wholly on read files was exempted as a "plan" via numbering, "then" and "estimate").
_CODE_LINE_RE = re.compile(r"(?i)\bстрок[аеиу]?\s+\d+|\bline\s+\d+|\.py:\d+")


def _carries_plan_proposal(answer: str, question: str = "") -> bool:
    """True when the answer is a proposal about future work, not a report about the world."""
    if not answer:
        return False
    lowered = answer.lower()
    has_marker = any(marker in lowered for marker in _PLAN_MARKERS)
    if not has_marker and _NUMBERED_LINE_RE.search(answer) is None:
        return False
    if _reports_a_result(answer):
        return False
    return not _states_external_fact(answer, question)


def _carries_generated_artifact(answer: str) -> bool:
    """Whether *answer* contains generated code / a diff / a plan.

    Deterministic and regex-free for code paths. A fenced block anywhere counts; diff markers count only at line start. A plan counts too (synthesis, not a claim about the world) but never when the text states an external fact.
    """

    if not answer:
        return False
    if _FENCE_MARKER in answer:
        return True
    if any(
        line.startswith(_DIFF_MARKERS)
        for line in answer.splitlines()
    ):
        return True
    return _carries_plan_proposal(answer)


def is_evidence_expected(
    *,
    role: str = "",
    chain_was_empty: bool = False,
    realtime_required: bool = True,
    answer: str | None = None,
    question: str = "",
) -> bool:
    """Whether the low-evidence truncation gate applies to this turn.

    Evidence is NOT expected (gate disabled) when either the deliverable is generative code (``role`` in ``_GENERATIVE_ROLES``) AND the answer carries a generated artifact, or the evidence chain is empty AND the question has no realtime intent. Factual / realtime turns keep the full gate.
    The exemption keys on WHAT was produced, not WHO answered; ``answer`` is optional (without it, the old role-only behaviour).
    """
    if role in _GENERATIVE_ROLES:
        if answer is None:
            return False
        return not _carries_generated_artifact(answer)
    # Genre decides regardless of role: a proposal about future work is synthesis, not a world claim; the exemption lifts once the text asserts an external fact (`_carries_plan_proposal`).
    if answer is not None and _carries_plan_proposal(answer, question):
        return False
    return not (chain_was_empty and not realtime_required)


@dataclass(frozen=True)
class LowEvidencePolicyResult:
    """Outcome of one evaluation."""

    triggered: bool
    answer: str
    verified_chunks: int
    total_chunks: int
    verified_ratio: float
    unverified_total: int
    reason: str = ""
    locale: str = "en"
    suppressed_chars: int = 0
    #: The head of what was suppressed, journaled so a censored answer can be read afterwards.
    suppressed_head: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)
    dialogue_supported_chunks: int = 0
    #: Never part of `supported_chunks`: the user's words prove the words, not the world.
    user_asserted_chunks: int = 0

    @property
    def supported_chunks(self) -> int:
        """Claims the answer is entitled to keep: verified + dialogue-scoped."""
        return self.verified_chunks + self.dialogue_supported_chunks

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "verified_chunks": self.verified_chunks,
            "dialogue_supported_chunks": self.dialogue_supported_chunks,
            "user_asserted_chunks": self.user_asserted_chunks,
            "supported_chunks": self.supported_chunks,
            "total_chunks": self.total_chunks,
            # `verified_ratio` is kept for existing log consumers; the value is the ratio the gate DECIDES on, (verified + dialogue_supported) / total. Prefer `supported_ratio` in new consumers.
            "verified_ratio": round(self.verified_ratio, 3),
            "supported_ratio": round(self.verified_ratio, 3),
            "unverified_total": self.unverified_total,
            "reason": self.reason,
            "locale": self.locale,
            "suppressed_chars": self.suppressed_chars,
            "suppressed_head": self.suppressed_head,
        }


def _insufficient_data_notice(locale: str) -> str:
    if locale == "ru":
        return (
            "Недостаточно данных для развёрнутого ответа: проверенных "
            "источников слишком мало. Полный план/анализ скрыт, чтобы "
            "не выдавать предположения за факты."
        )
    return (
        "Insufficient evidence to ship a full answer: too few claims "
        "could be backed by the gathered sources. The long planned "
        "response was suppressed to avoid presenting guesses as facts."
    )


def _conclusion_stub(
    verified_count: int, locale: str, dialogue_count: int = 0
) -> str:
    """Conclusion line for the rebuilt short answer; ``dialogue_count`` is stated separately, never merged into the verified tally."""
    if locale == "ru":
        if verified_count == 0 and dialogue_count == 0:
            return (
                "Conclusion: ни одно утверждение не подтверждено "
                "источниками этого цикла."
            )
        if verified_count == 0:
            return (
                "Conclusion: внешние источники не подтвердили ничего; "
                f"сохранено {dialogue_count} утверждений о самом диалоге "
                "(по стенограмме сессии)."
            )
        tail = (
            f" плюс {dialogue_count} о самом диалоге;"
            if dialogue_count
            else ";"
        )
        return (
            f"Conclusion: подтверждено {verified_count} утверждений{tail} "
            "остальное скрыто как недостаточно обоснованное."
        )
    if verified_count == 0 and dialogue_count == 0:
        return (
            "Conclusion: no claim could be backed by the sources "
            "gathered this cycle."
        )
    if verified_count == 0:
        return (
            "Conclusion: no external source confirmed anything; "
            f"{dialogue_count} claim(s) about this session's own exchange "
            "were kept (backed by the transcript)."
        )
    tail = (
        f", plus {dialogue_count} about this session's own exchange;"
        if dialogue_count
        else ";"
    )
    return (
        f"Conclusion: {verified_count} claim(s) verified{tail} the rest of "
        "the planned reply was suppressed for lack of support."
    )


def _facts_block(verified_chunks: list[str], locale: str) -> str:
    if not verified_chunks:
        if locale == "ru":
            return "Facts: (нет утверждений, прошедших проверку)"
        return "Facts: (no verified claim from the suppressed draft)"
    body = "\n".join(f"  - {chunk}" for chunk in verified_chunks)
    return f"Facts:\n{body}"


def _unverified_block(
    suppressed_count: int, notice: str, locale: str
) -> str:
    if locale == "ru":
        return (
            f"Unverified: подавлено {suppressed_count} непроверенных "
            f"утверждений из исходного черновика. {notice}"
        )
    return (
        f"Unverified: {suppressed_count} unverified claim(s) suppressed "
        f"from the original draft. {notice}"
    )


def _fenced_code_blocks(answer: str) -> list[str]:
    """Fenced code blocks of the answer: proposals, not claims. Born code has no evidence by definition; tests judge it, so the suppressor must not bury it."""
    import re

    return [m.group(1).strip()
            for m in re.finditer(r"(?s)(```.*?```)", answer or "")]


def _code_proposal_block(code_blocks: list[str], locale: str) -> str:
    if locale == "ru":
        head = ("Код-предложение (не утверждение: судится тестами, "
                "не цитатами — сохранено при усечении):")
    else:
        head = ("Proposed code (a proposal, not a claim: judged by tests, "
                "kept through truncation):")
    return head + "\n" + "\n\n".join(code_blocks)


def _build_short_answer(
    *,
    verified_claim_texts: list[str],
    suppressed_count: int,
    locale: str,
    dialogue_count: int = 0,
    code_blocks: list[str] | None = None,
) -> str:
    """Assemble a deterministic short reply in Output Contract section order, rebuilt from scratch (never paraphrasing the LLM) so the truncation is visibly mechanical."""
    notice = _insufficient_data_notice(locale)
    stub = _conclusion_stub(len(verified_claim_texts) - dialogue_count, locale, dialogue_count)
    if verified_claim_texts:
        # Trailing colon: conversational display prints Facts only after it.
        stub += " Оставлено:" if locale == "ru" else " Kept:"
    parts = [
        stub,
        _facts_block(verified_claim_texts, locale),
        *([_code_proposal_block(code_blocks, locale)] if code_blocks else []),
        "Sources: only verified claims listed above (if any)",
        "Confidence: low",
        _unverified_block(suppressed_count, notice, locale),
        "Safety: ok",
    ]
    return "\n\n".join(parts)


# Verdict labels routed to "needs evidence support" buckets; mirrors core/verifier.py strings so drift raises an import-test failure.
_UNSUPPORTED_VERDICTS: frozenset[str] = frozenset({
    "unverified",
    "cited_but_unmatched",
    "topic_supported_but_claim_unverified",
    "subagent_asserted",
})


#: A claim that says "could not confirm / blocked / not done": the honest conclusion must not be erased by this gate, which would have broken the user's goal ("if nothing can be confirmed, say so").
_HONEST_NEGATIVE_RE = re.compile(
    r"(?i)(заблокирован|не пуска|BLOCKED|429|не подтверж|неподтверж|не удалось|"
    r"нет подтверждённ|не выполнен|пуст(ые|ая|ой) страниц|не предоставил|"
    r"blocked|rate[- ]limit|could not (?:be )?(?:verif|confirm)|unconfirmed|"
    r"no confirmed|not (?:done|completed)|unsupported)"
)

#: What an ATTEMPT looks like in the chain: a fetch answered with an HTTP error, an unsupported/blocked page, a captcha wall, an empty result, or a subagent that reported the same. Counted from excerpt and source id.
_BLOCKED_ATTEMPT_RE = re.compile(
    r"(?i)(HTTP\s*(?:4\d\d|5\d\d)|\b(?:403|429|503)\b|Too Many Requests|unsupported|"
    r"\bBLOCKED\b|captcha|rate[- ]limit|access denied|forbidden|заблокирован|"
    r"пуст(?:ые|ая|ой) страниц|empty page)"
)


def count_blocked_attempts(chain: Any) -> int:
    """How many evidences in the chain record a blocked or empty attempt —
    the support an honest negative conclusion stands on."""
    n = 0
    for ev in getattr(chain, "evidences", ()) or ():
        text = f"{getattr(ev, 'excerpt', '') or ''}\n{getattr(ev, 'source_id', '') or ''}"
        if _BLOCKED_ATTEMPT_RE.search(text):
            n += 1
    return n


def _honest_negative_chunks(report: Any) -> list[str]:
    """Texts of chunks that assert blocking / non-confirmation / not-done and
    were left unsupported by the verifier (never a verified or refuted one)."""
    out: list[str] = []
    for c in getattr(report, "chunks", ()) or ():
        verdict = str(getattr(c, "verdict", "") or "")
        text = str(getattr(c, "text", "") or "")
        if verdict in ("subagent_asserted", "topic_supported_but_claim_unverified", "unverified") \
                and _HONEST_NEGATIVE_RE.search(text):
            out.append(text.strip())
    return out


def _surviving_texts(report: Any) -> tuple[list[str], list[str]]:
    """Texts the truncation keeps: verified chunks and dialogue-scoped ones, so a self-correction is never erased wholesale."""
    verified_texts: list[str] = []
    dialogue_texts: list[str] = []
    for c in getattr(report, "chunks", ()) or ():
        verdict = getattr(c, "verdict", "")
        text = getattr(c, "text", "")
        if not text.strip():
            continue
        if verdict == "verified":
            verified_texts.append(text.strip())
        elif verdict == "dialogue_supported":
            dialogue_texts.append(text.strip())
    return verified_texts, dialogue_texts


def evaluate_low_evidence_policy(
    *,
    answer: str,
    report: Any,
    question: str = "",
    min_total_chunks: int = _DEFAULT_MIN_TOTAL,
    max_verified_ratio: float = _DEFAULT_MAX_VERIFIED_RATIO,
    unverified_floor: int = _DEFAULT_UNVERIFIED_FLOOR,
    evidence_expected: bool = True,
    local_critique_active: bool = False,
    blocked_attempts: int = 0,
) -> LowEvidencePolicyResult:
    """Decide whether to truncate the answer because evidence is too thin.

    ``blocked_attempts``: a chunk honestly saying "blocked / not confirmed / not done" is supported by the chain's blocked attempts; a positive claim is never rescued by this rule.
    """
    if report is None:
        return LowEvidencePolicyResult(
            triggered=False, answer=answer,
            verified_chunks=0, total_chunks=0,
            verified_ratio=0.0, unverified_total=0,
            reason="no_report",
        )

    total = int(getattr(report, "total_chunks", 0) or 0)
    verified = int(getattr(report, "verified_chunks", 0) or 0)
    dialogue = int(getattr(report, "dialogue_supported_chunks", 0) or 0)
    unverified = int(getattr(report, "unverified_chunks", 0) or 0)
    cited_unmatched = int(getattr(report, "cited_but_unmatched_chunks", 0) or 0)
    topic_supported = int(
        getattr(
            report, "topic_supported_but_claim_unverified_chunks", 0
        ) or 0
    )
    subagent_asserted = int(
        getattr(report, "subagent_asserted_chunks", 0) or 0
    )
    # `user_asserted` joins neither `supported` (the user's words never verify their own content) nor `unverified_total` (nothing was fabricated). It still sits in `total_chunks`, so it DOES drag supported_ratio down; its main weight lands in episode banking (`weak_chunks`) and the evidence-support score.
    user_asserted = int(getattr(report, "user_asserted_chunks", 0) or 0)
    # Content-refuted chunks press on the evidence floor no less than unverified ones (they already sat in `topic_supported`).
    refuted = int(getattr(report, "refuted_chunks", 0) or 0)
    unverified_total = (
        unverified + cited_unmatched + topic_supported + subagent_asserted
        + refuted
    )
    honest_negative_texts = (
        _honest_negative_chunks(report) if blocked_attempts > 0 else []
    )
    supported = verified + dialogue + len(honest_negative_texts)
    unverified_total = max(0, unverified_total - len(honest_negative_texts))
    supported_ratio = (supported / total) if total > 0 else 0.0

    def _result(*, triggered: bool, answer_out: str, reason: str,
                locale: str = "en", suppressed_chars: int = 0,
                suppressed_head: str = "",
                ) -> LowEvidencePolicyResult:
        return LowEvidencePolicyResult(
            triggered=triggered, answer=answer_out,
            verified_chunks=verified, total_chunks=total,
            verified_ratio=supported_ratio,
            unverified_total=unverified_total,
            dialogue_supported_chunks=dialogue,
            user_asserted_chunks=user_asserted,
            reason=reason, locale=locale,
            suppressed_chars=suppressed_chars,
            suppressed_head=suppressed_head,
        )

    if local_critique_active:
        return _result(
            triggered=False, answer_out=answer,
            reason="local_critique_skip_empty_rewrite",
        )

    if not evidence_expected:
        # The task never needed external evidence (pure reasoning/design, empty chain, no realtime intent); truncating would suppress a legitimate answer. Factual / realtime questions keep the full gate.
        return _result(
            triggered=False, answer_out=answer, reason="no_evidence_expected",
        )

    if total < min_total_chunks:
        return _result(
            triggered=False, answer_out=answer,
            reason="too_few_chunks_to_truncate",
        )

    if supported_ratio > max_verified_ratio:
        return _result(
            triggered=False, answer_out=answer,
            reason=(
                f"verified_ratio_above_threshold|honest_negative={len(honest_negative_texts)}"
                f"|blocked_attempts={blocked_attempts}"
                if honest_negative_texts else "verified_ratio_above_threshold"
            ),
        )

    if supported > 0 and unverified_total < unverified_floor:
        # Borderline: some support exists but the unverified mass is too small to justify truncation; telemetry still surfaces it.
        return _result(
            triggered=False, answer_out=answer,
            reason="unverified_mass_below_floor",
        )

    locale = "ru" if (
        looks_russian(question) or looks_russian(answer)
    ) else "en"

    verified_texts, dialogue_texts = _surviving_texts(report)

    suppressed_count = total - supported
    # An honest negative claim backed by blocked attempts survives even when the gate fires on the rest.
    short_answer = _build_short_answer(
        verified_claim_texts=verified_texts + dialogue_texts + honest_negative_texts,
        suppressed_count=suppressed_count,
        locale=locale,
        dialogue_count=len(dialogue_texts),
        code_blocks=_fenced_code_blocks(answer),
    )
    reason = (
        f"supported_ratio={supported_ratio:.2f} <= {max_verified_ratio} "
        f"and unverified_total={unverified_total} >= {unverified_floor}"
    )
    return _result(
        triggered=True,
        answer_out=short_answer,
        reason=reason,
        locale=locale,
        suppressed_chars=max(0, len(answer) - len(short_answer)),
        suppressed_head=answer[:800],
    )
