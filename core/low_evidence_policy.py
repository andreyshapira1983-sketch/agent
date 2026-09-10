"""Low-evidence answer policy.

* keeps only the chunks the verifier marked ``verified``; * states
explicitly that evidence was insufficient (in the user's locale, EN or RU);
* downgrades the Output Contract ``Confidence`` line to ``low``; * routes
the suppressed bulk into the ``Unverified`` section so the count is visible
to the operator.

``dialogue_supported`` joins the numerator because of issue #119: a claim
about *this session's own exchange* is backed by the verbatim transcript, so
counting it as unsupported mass is what let the gate delete a valid self-
correction. It is support, not verification — it is never folded into
``verified_chunks``, and a world claim never earns it (the scoping test
lives in :mod:`core.evidence_classes`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_DEFAULT_MIN_TOTAL = 8
_DEFAULT_MAX_VERIFIED_RATIO = 0.20
_DEFAULT_UNVERIFIED_FLOOR = 6


# Roles whose deliverable is NEW synthesized code / docstring / diff rather than
# factual claims about the world. Such output can never appear verbatim in the
# source file it was derived from, so the factual verifier marks it "unverified"
# and the low-evidence gate would delete exactly what the user asked to create.
# For these roles the synthesis IS the deliverable — evidence is not expected.
_GENERATIVE_ROLES: frozenset[str] = frozenset({"programmer"})

#: Markers of an actually generated artifact in the answer text. A fenced block
#: or a unified-diff header is newly synthesized output; ordinary prose about a
#: repository is not, however programmer-ish the role that produced it.
_FENCE_MARKER = "```"
_DIFF_MARKERS: tuple[str, ...] = ("--- ", "+++ ", "@@ ")


#: --- Жанр, а не форма (авторство агента, 2026-08-29) -------------------------
#: Измерено: код в заборе освобождался от улик, а ПЛАН прозой — нет, хотя оба
#: суть синтез. За день это задушило пять его собственных планов (поставка MVP,
#: карантин, оценка вакансии, выбор органа, реплика спора), и дважды курьеру
#: приходилось просить заворачивать прозу в кодовый забор — то есть маскировать
#: замысел под код, чтобы он прошёл.
#:
#: Водораздел его словами: «цифра-параметр не существует вне моего решения и её
#: нельзя пойти и проверить; цифра-утверждение говорит о том, что существует
#: независимо от меня». Утверждение о мире обязано нести источник; предложение о
#: будущей работе источника иметь не может по природе — работы ещё нет.
#:
#: Наблюдаемые признаки внешнего факта (его мера, замер 9/9 + атака 7/7):
#: единица измерения мира; дата; утвердительная связка при имени внешней
#: сущности; имя внешней сущности без слова замысла рядом. Его же страховка:
#: собственная величина в мировых единицах («бюджет 5 долларов») считается
#: фактом — лишний запрос источника дешевле пропуска.
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


def _states_external_fact(text: str) -> bool:
    """True, когда текст утверждает о внешнем мире и потому обязан нести улику."""
    import re
    if not text or not re.search(r"\d", text):
        return False
    lowered = text.lower()
    if any(unit in lowered for unit in _WORLD_UNIT_MARKERS):
        return True
    if any(re.search(pat, lowered) for pat in _DATE_PATTERNS):
        return True
    has_entity = any(name in lowered for name in _EXTERNAL_ENTITY_MARKERS)
    if has_entity and any(cop in lowered for cop in _ASSERTIVE_COPULAS):
        return True
    if has_entity:
        for sentence in re.split(r"[.!?]\s*", lowered):
            if any(name in sentence for name in _EXTERNAL_ENTITY_MARKERS) and not any(
                word in sentence for word in _INTENT_WORD_MARKERS
            ):
                return True
    # Все признаки внешнего факта промолчали — это параметр замысла, не факт.
    return False


#: Признаки предложения о будущей работе: наклонение намерения и нумерованные
#: шаги. Одного признака мало — он должен встретиться в тексте, который НЕ
#: утверждает о внешнем мире (см. `_states_external_fact`) и НЕ отчитывается
#: о сделанном (см. `_reports_a_result`).
_PLAN_MARKERS: tuple[str, ...] = (
    "шаг ", "шаг:", "сначала", "затем", "потом", "предлагаю", "сделаю",
    "план:", "план ", "поставк",
)
#: Нумерованный список — только в начале строки. Экзамен 2026-09-05 (ход 43):
#: `"1."` совпадало с дробью «601.23 с», и отчёт об измерении освобождался от
#: улик как «план».
_NUMBERED_LINE_RE = re.compile(r"(?m)^\s*\d{1,2}\.\s")

#: Отчёт о результате — не предложение. Экзамен 2026-09-05 (ход 41): «Шаг 1
#: (last_n=60) вернул events_returned=4» прошёл как план, ворота улик отключились,
#: и ложное число (инструмент вернул 60) ушло оператору как проверенное.
#: Признаки: глагол свершившегося действия или запись `поле=значение` из вывода
#: инструмента.
_REPORT_VERB_MARKERS: tuple[str, ...] = (
    "вернул", "выполнен", "выполнил", "получен", "прочитан", "найден", "найдено",
    "показал", "завершил", "завершён", "обнаружен", "составил", "выдал", "записан",
    "измерил", "измерен",
    "returned", "executed", "found ", "reported", "yielded", "completed", "measured",
)
_KEY_VALUE_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*=\S")


def _reports_a_result(text: str) -> bool:
    """True, когда текст отчитывается о сделанном, а не предлагает сделать."""
    lowered = text.lower()
    if any(marker in lowered for marker in _REPORT_VERB_MARKERS):
        return True
    return _KEY_VALUE_RE.search(text) is not None


def _carries_plan_proposal(answer: str) -> bool:
    """True, когда ответ — предложение о будущей работе, а не отчёт о мире."""
    if not answer:
        return False
    lowered = answer.lower()
    has_marker = any(marker in lowered for marker in _PLAN_MARKERS)
    if not has_marker and _NUMBERED_LINE_RE.search(answer) is None:
        return False
    if _reports_a_result(answer):
        return False
    return not _states_external_fact(answer)


def _carries_generated_artifact(answer: str) -> bool:
    """Whether *answer* actually contains generated code / a diff / a plan.

    Deterministic and regex-free for the code paths, matching the rest of this
    module. A fenced block anywhere counts; diff markers count only at the start
    of a line, so a sentence containing "---" in prose does not qualify. A plan
    counts too: it is synthesis, not a claim about the world (see the genre note
    above) — that is the only addition, and it never fires when the text states
    an external fact.
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
) -> bool:
    """Whether the low-evidence truncation gate should apply to this turn.

    Evidence is NOT expected (gate disabled) when either:

      * the deliverable is generative code (``role`` in ``_GENERATIVE_ROLES``)
        **and the answer actually carries a generated artifact** — a
        docstring/diff/code turn that read a file as *input* still produces new
        text that cannot be verbatim-verified against that file; or
      * the evidence chain is empty AND the question carries no realtime intent
        (a pure reasoning/design answer where the model's synthesis is the whole
        deliverable and there is nothing to cite).

    Factual / realtime turns (researcher, technical_report, operator_chat, …)
    keep the full gate, so unsupported factual answers are still suppressed.

    The role exemption used to key on WHO answered rather than WHAT was
    produced. Measured live: "In one sentence: what does core/model_router.py
    do?" read the file, produced 7 chunks of which 6 verified, and still logged
    ``no_evidence_expected`` — because it ran under ``role=programmer``. The
    same class was already recorded in ``docs/COGNITIVE_CORE.md`` (open
    question 3) for "how many TODO/FIXME are in core/?". Counting and
    describing are not code generation, and the exemption's own rationale —
    generated text "can never appear verbatim in the source file" — reaches
    exactly as far as answers that carry a generated artifact.

    ``answer`` is optional: a caller that does not hand over the text gets the
    previous role-only behaviour, so no existing call site changes silently.
    """
    if role in _GENERATIVE_ROLES:
        if answer is None:
            return False
        return not _carries_generated_artifact(answer)
    # Жанр решает независимо от роли: предложение о будущей работе — синтез, а
    # не утверждение о мире, кем бы оно ни было произнесено. Планы пишутся и в
    # роли собеседника оператора, и там их душило ровно так же (замер
    # 2026-08-29). Освобождение снимается, как только текст начинает утверждать
    # о внешнем мире — это проверяет `_carries_plan_proposal`.
    if answer is not None and _carries_plan_proposal(answer):
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
    #: The head of what was suppressed — journaled, so a censored answer can
    #: be read afterwards (exam 2026-09-04, turn 3: 15 claims vanished unseen).
    suppressed_head: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)
    dialogue_supported_chunks: int = 0
    #: Never part of `supported_chunks` (operator ruling 2026-08-03, MIR-028):
    #: the user's words prove the words, not the world.
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
            # `verified_ratio` is kept for existing log consumers, but the value
            # is and always was the ratio the gate DECIDES on — since issue #119
            # that is (verified + dialogue_supported) / total. `supported_ratio`
            # is the name that says so; prefer it in new consumers.
            "verified_ratio": round(self.verified_ratio, 3),
            "supported_ratio": round(self.verified_ratio, 3),
            "unverified_total": self.unverified_total,
            "reason": self.reason,
            "locale": self.locale,
            "suppressed_chars": self.suppressed_chars,
            "suppressed_head": self.suppressed_head,
        }


def _looks_russian(text: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", text or ""))


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
    """Conclusion line for the rebuilt short answer.

    ``dialogue_count`` is stated separately, never merged into the verified
    tally: claims about this session's own exchange are supported by the
    transcript, not confirmed by a source (issue #119).
    """
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
    """Кодовые заборы ответа — предложения, не утверждения (2026-08-29).

    У рождённого кода улик нет по определению; его судья — тесты, не цитаты.
    Подавитель гасит бездоказательную прозу, но хоронить вместе с ней код
    значило душить творчество (живое удушение run_e4d9a8e8).
    """
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
    """Assemble a deterministic short reply that follows the Output
    Contract section order. We rebuild from scratch — never paraphrase
    the LLM's prose — so the truncation is visibly mechanical."""
    notice = _insufficient_data_notice(locale)
    parts = [
        _conclusion_stub(
            len(verified_claim_texts) - dialogue_count, locale, dialogue_count
        ),
        _facts_block(verified_claim_texts, locale),
        *([_code_proposal_block(code_blocks, locale)] if code_blocks else []),
        "Sources: only verified claims listed above (if any)",
        "Confidence: low",
        _unverified_block(suppressed_count, notice, locale),
        "Safety: ok",
    ]
    return "\n\n".join(parts)


# Verdict labels routed to "needs evidence support" buckets. Mirrors
# the strings produced by core/verifier.py so a downstream change there
# raises a single import-test failure if the names drift.
_UNSUPPORTED_VERDICTS: frozenset[str] = frozenset({
    "unverified",
    "cited_but_unmatched",
    "topic_supported_but_claim_unverified",
    "subagent_asserted",
})


#: A claim that says «could not confirm / blocked / not done». Work order 1,
#: pass 2 (2026-09-05): the draft's honest conclusion — sources blocked, no
#: confirmed fares, task not done — was booked subagent_asserted / topic-only
#: and erased by this gate, which then shipped one baggage fact. The gate did
#: its formal job («ship nothing unsupported») and broke the user's («if
#: nothing can be confirmed, say so»).
_HONEST_NEGATIVE_RE = re.compile(
    r"(?i)(заблокирован|не пуска|BLOCKED|429|не подтверж|неподтверж|не удалось|"
    r"нет подтверждённ|не выполнен|пуст(ые|ая|ой) страниц|не предоставил|"
    r"blocked|rate[- ]limit|could not (?:be )?(?:verif|confirm)|unconfirmed|"
    r"no confirmed|not (?:done|completed)|unsupported)"
)

#: What an ATTEMPT looks like in the chain: a fetch answered with an HTTP
#: error, an unsupported/blocked page, a captcha wall, an empty result, or a
#: subagent that reported the same. Counted from excerpt and source id.
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
    """Texts the truncation keeps: verified chunks, and dialogue-scoped ones.

    Dialogue-scoped claims survive the truncation alongside verified ones: the
    answer loses its unsupported world claims and keeps the part the session
    transcript backs (issue #119). Even when the gate fires, a self-correction
    is never erased wholesale."""
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

    ``blocked_attempts`` (work order 1, defect 3): a chunk that honestly says
    «blocked / not confirmed / not done» is SUPPORTED by the chain's blocked
    attempts; a positive claim is never rescued by this rule."""
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
    # `user_asserted` (operator ruling 2026-08-03, MIR-028) joins neither
    # `supported` (the user's words never verify their own content, so an
    # all-echo answer must not score supported_ratio=1.0 and slip past this
    # gate) nor `unverified_total` (nothing was fabricated, so it does not
    # push the floor). It still sits in `total_chunks`, i.e. it DOES drag
    # supported_ratio down — "neutral" means neutral between those two
    # counters, not absent from the trigger math. Its main weight lands in
    # episode banking (`weak_chunks`) and the evidence-support score.
    user_asserted = int(getattr(report, "user_asserted_chunks", 0) or 0)
    # Опровергнутое содержимым (2026-08-12) давит на пол улик не слабее
    # непроверенного: раньше эти куски сидели в `topic_supported` и уже
    # входили в сумму — выделение полярности не должно было их из неё вынуть.
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
        # The task never needed external evidence (pure reasoning / design
        # question with an empty evidence chain and no realtime intent).
        # Truncating here would suppress a legitimate answer just because it
        # has nothing to cite — the model's synthesis IS the deliverable.
        # Factual / realtime questions keep the full gate (evidence_expected
        # stays True for them).
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
        # Borderline: some support exists but the unverified mass is too small
        # to justify the truncation hammer. The evidence-support
        # telemetry will still surface it.
        return _result(
            triggered=False, answer_out=answer,
            reason="unverified_mass_below_floor",
        )

    locale = "ru" if (
        _looks_russian(question) or _looks_russian(answer)
    ) else "en"

    verified_texts, dialogue_texts = _surviving_texts(report)

    suppressed_count = total - supported
    # An honest negative claim backed by blocked attempts survives even when
    # the gate still fires on the rest: «nothing could be confirmed» is the
    # one sentence the user must not lose.
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
