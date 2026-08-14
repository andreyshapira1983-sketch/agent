"""Verifier text utilities: chunk splitting, citation parsing and matching, and statistical-claim detection.

Extracted from `core/verifier` by autonomous self-build module split.
"""
from __future__ import annotations

import re
from typing import Any

from core.evidence import Evidence, ProvenanceChain

from .verifier_models import Citation, VerificationReport
from .verifier_patterns import (
    _BARE_LIST_MARKER_RE,
    _CITATION_BODY_TOKEN_RE,
    _CITATION_RE,
    _MAX_EXCERPT_FOR_NLI,
    _MD_HEADING_RE,
    _MIN_TOKEN_LEN,
    _NLI_SYSTEM,
    _NO_TOKEN_FALLBACK_PREFIXES,
    _OUTPUT_CONTRACT_HEADER_RE,
    _SENTENCE_SPLIT_RE,
    _STAT_FIGURE_MIN_LEN,
    _STAT_FIGURE_RE,
    _STAT_TRIGGER_RE,
    _SUBAGENT_META_RE,
    _TOKEN_STOPWORDS,
    CITATION_PREFIXES,
)


def _normalise_figure(fig: str) -> str:
    s = fig.lower()
    s = re.sub(r"\s+", "", s)
    return s.replace("\u2013", "-").replace("\u2014", "-")


def extract_statistical_figures(text: str) -> list[str]:
    if not text:
        return []
    if not _STAT_TRIGGER_RE.search(text):
        return []
    figures: list[str] = []
    seen: set[str] = set()
    for m in _STAT_FIGURE_RE.finditer(text):
        raw = m.group(0).strip()
        bare_num = re.fullmatch(r"\d+", raw)
        if bare_num and len(raw) < _STAT_FIGURE_MIN_LEN:
            continue
        norm = _normalise_figure(raw)
        if norm in seen:
            continue
        seen.add(norm)
        figures.append(raw)
    return figures


def is_statistical_claim(text: str) -> bool:
    return bool(text and _STAT_TRIGGER_RE.search(text))


def _excerpt_supports_figures(excerpt: str, figures: list[str]) -> bool:
    if not figures:
        return True
    if not excerpt:
        return False
    excerpt_norm = _normalise_figure(excerpt)
    return all(_normalise_figure(f) in excerpt_norm for f in figures)


def _output_contract_header_name(text: str) -> str | None:
    stripped = (text or "").strip()
    match = _OUTPUT_CONTRACT_HEADER_RE.match(stripped)
    if not match:
        return None
    return match.group(1).casefold()


def is_structural_chunk(text: str) -> bool:
    if not text or not text.strip():
        return True
    stripped = text.strip()
    if _output_contract_header_name(stripped) is not None:
        return True
    if _MD_HEADING_RE.match(stripped) and "[" not in stripped:
        return True
    return bool(_BARE_LIST_MARKER_RE.match(stripped))


def parse_citations(text: str) -> list[Citation]:
    cits: list[Citation] = []
    for m in _CITATION_RE.finditer(text):
        prefix = m.group(1)
        body = (m.group(2) or "").strip()
        cits.append(Citation(prefix=prefix, body=body, raw=m.group(0), expected_kind=CITATION_PREFIXES[prefix]))
    return cits


def _is_citation_only_chunk(text: str) -> bool:
    cits = parse_citations(text)
    if not cits:
        return False
    remainder = text
    for cit in cits:
        remainder = remainder.replace(cit.raw, "")
    return not remainder.strip(" \t\r\n.,;:-")


def _merge_citation_only_chunks(chunks: list[str]) -> list[str]:
    merged: list[str] = []
    for chunk in chunks:
        if _is_citation_only_chunk(chunk) and merged and not is_structural_chunk(merged[-1]):
            merged[-1] = f"{merged[-1].rstrip()} {chunk.strip()}"
            continue
        merged.append(chunk)
    return merged


def split_into_chunks(answer: str) -> list[str]:
    if not answer or not answer.strip():
        return []
    parts = _SENTENCE_SPLIT_RE.split(answer)
    return [p.strip() for p in parts if p.strip()]


def extract_unresolved_web_urls(report: VerificationReport) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for chunk in report.chunks:
        if chunk.verdict != "cited_but_unmatched":
            continue
        for cit in chunk.citations:
            if cit.prefix != "web":
                continue
            url = cit.body.strip()
            if not url:
                continue
            lowered = url.lower()
            if not (lowered.startswith(("http://", "https://"))):
                continue
            if url in seen:
                continue
            seen.add(url)
            ordered.append(url)
    return ordered


def _tokenise_citation_body(body: str) -> list[str]:
    if not body:
        return []
    raw = [t for t in _CITATION_BODY_TOKEN_RE.split(body.lower()) if t]
    return [t for t in raw if len(t) >= _MIN_TOKEN_LEN and t not in _TOKEN_STOPWORDS]


def runtime_evidence_pool() -> list[Evidence]:
    """What this process measured about itself, as citable Evidence.

    Built on demand and NOT folded into the provenance chain. The first attempt
    did fold it, and 21 tests said why that is wrong: the chain is counted and
    ordered, and its contracts are real — a failed step yields no evidence, a
    `file_write` yields none, a zero-step plan yields an EMPTY chain. Five
    always-present entries break every one of those, and they also destroy
    `chain_was_empty`, which separates "the agent looked and found nothing" from
    "the agent did not look".

    So the runtime facts get their own pool, on the pattern `web` already uses
    below: a prefix may draw on candidates the chain does not hold.
    """
    from core.evidence import make_evidence
    from core.runtime_self import process_facts

    return [
        make_evidence(
            kind="runtime",
            source_id=f"runtime:{key}",
            obtained_via="process_self_measurement",
            claim=f"This run's {key}",
            excerpt=str(value),
        )
        for key, value in process_facts().items()
    ]


def match_citation(citation: Citation, chain: ProvenanceChain) -> Evidence | None:
    candidates = chain.by_kind(citation.expected_kind)  # type: ignore[arg-type]
    if citation.prefix == "runtime":
        # Measured, not gathered: the process reading its own interpreter,
        # version, pid and cwd. Its own execution is the proof, and until
        # 2026-08-14 there was no channel through which it could be offered.
        candidates = list(candidates) + runtime_evidence_pool()
    if citation.prefix == "web":
        search_hits = chain.by_kind("web_search_hit")  # type: ignore[arg-type]
        if search_hits:
            seen: set[str] = {ev.id for ev in candidates}
            candidates = list(candidates) + [ev for ev in search_hits if ev.id not in seen]
    if not candidates:
        return None
    if not citation.body:
        return candidates[0]
    body_lower = citation.body.lower()
    for ev in candidates:
        if body_lower in ev.source_id.lower():
            return ev
    if citation.prefix in _NO_TOKEN_FALLBACK_PREFIXES:
        if citation.prefix == "web":
            body_lower_full = citation.body.lower()
            body_is_url = body_lower_full.startswith(("http://", "https://")) or "://" in body_lower_full
            if not body_is_url:
                search_only = [ev for ev in candidates if ev.kind == "web_search_hit"]
                if search_only:
                    body_tokens = _tokenise_citation_body(citation.body)
                    if body_tokens:
                        best: Evidence | None = None
                        best_score = 0
                        for ev in search_only:
                            sid_lower = ev.source_id.lower()
                            score = sum(1 for tok in body_tokens if tok in sid_lower)
                            if score > best_score:
                                best = ev
                                best_score = score
                        if best_score >= 1:
                            return best
        return None
    body_tokens = _tokenise_citation_body(citation.body)
    if not body_tokens:
        return None
    best: Evidence | None = None
    best_score = 0
    for ev in candidates:
        sid_lower = ev.source_id.lower()
        score = sum(1 for tok in body_tokens if tok in sid_lower)
        if score > best_score:
            best = ev
            best_score = score
    if best_score >= 1:
        return best
    return None


def _semantic_nli_check(claim: str, excerpt: str, llm: Any) -> bool:
    try:
        prompt = f"Source excerpt:\n{excerpt[:_MAX_EXCERPT_FOR_NLI]}\n\nClaim: {claim[:300]}\n\nDoes the source excerpt support the claim? Answer yes or no."
        answer = llm.complete(system=_NLI_SYSTEM, user=prompt, max_tokens=4, temperature=0.0)
        return answer.strip().lower().startswith("yes")
    except Exception:  # noqa: BLE001
        return False


def _find_semantic_support(claim: str, chain: ProvenanceChain, llm: Any) -> Evidence | None:
    candidates = sorted(chain.evidences, key=lambda e: e.confidence, reverse=True)
    for ev in candidates:
        if not ev.excerpt:
            continue
        if _semantic_nli_check(claim, ev.excerpt, llm):
            return ev
    return None


def _find_structured_support(claim: str, chain: ProvenanceChain) -> Evidence | None:
    from core.structured_facts import claim_supported_by, extract_facts
    candidates = [ev for ev in chain.evidences if ev.kind == "tool_output"]
    candidates.sort(key=lambda e: e.confidence, reverse=True)
    for ev in candidates:
        if not ev.excerpt:
            continue
        facts = extract_facts(ev.excerpt)
        if facts.is_empty():
            continue
        if claim_supported_by(claim, facts):
            return ev
    return None


def _tool_citation_for(ev: Evidence) -> str:
    sid = ev.source_id or ""
    if sid.startswith("tool_output:"):
        body = sid[len("tool_output:"):]
    else:
        body = sid or "structured"
    return f"[verified:tool:{body}]"


def _is_derivative_subagent_evidence(ev: Evidence) -> bool:
    """Is this evidence merely a sub-agent's own synthesis (a witness)?

    The parent loop embeds only a sub-agent's *answer text* (via
    ``SubAgentRunResult.to_evidence_text()``) into its provenance chain —
    never the sub-agent's raw file / web evidences. So the parent can
    never independently inspect what a sub-agent looked at; it holds only
    the child's prose conclusion.

    Therefore any sub-agent-origin evidence is derivative: promoting it to
    ``verified`` would be "trusting one LLM that trusted another LLM".

    The ``external_evidence_count`` in the meta marker is the child's own
    self-report — it proves the child *looked at* N external sources, not
    that the child's conclusion is faithful to them. A real trace showed a
    sub-agent read 7 repo files and still asserted a non-existent code bug
    (``EpisodeRecord.to_dict`` "dropping" ``full_answer``); with the old
    ``count > 0 -> not derivative`` shortcut that fabrication was minted as
    ``verified``. Likewise, ``[web:...]`` / ``[file:...]`` tokens a
    sub-agent embeds in its answer are still the child's unverifiable
    claims, not evidence the parent holds. Both are treated as derivative.
    """
    excerpt = ev.excerpt or ""
    if _SUBAGENT_META_RE.search(excerpt) is not None:
        return True
    sid = ev.source_id or ""
    return bool("subagent_" in sid or sid == "tool_output:spawn_subagent")


#: Отличительные литералы: идентификаторы моделей и прогонов, SHA, пути к файлам.
#: Голые числа СОЗНАТЕЛЬНО не входят — счёт, сумма и сравнение принадлежат
#: `evaluate_claim_arithmetic`, и второй судья над той же областью спорил бы с
#: первым. Форма собрана по предметам, наблюдавшимся живьём: `claude-…`,
#: `run_…`, `trace_…`, SHA коммита, `core/…​.py`.
#: Через дефис — ТОЛЬКО с цифрой внутри. Измерено: без этого условия обычная
#: проза («goal-directed», «read-only», «fail-before») читается как
#: идентификатор, и гейт демотирует верные утверждения. Через подчёркивание
#: цифра не нужна: `AWS_SECRET_KEY` словом не бывает.
_SALIENT_LITERAL_RE = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+"
    r"|[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]*\d[A-Za-z0-9]*)+(?:-[A-Za-z0-9]+)*"
    r"|[0-9a-f]{7,40}"
    r"|[\w./\-]+\.(?:py|md|json|jsonl|txt|yaml|yml|cmd|toml))\b"
)


#: Маркер ссылки в тексте ответа: `[file:core/loop.py]`, `[memory:mem_…]`.
_CITATION_TOKEN_RE = re.compile(r"\[[^\[\]]*\]")


def salient_literals(text: str) -> set[str]:
    """Отличительные литералы текста, в нижнем регистре."""
    return {m.group(0).lower() for m in _SALIENT_LITERAL_RE.finditer(text or "")}


def literals_absent_from_excerpt(
    claim: str, excerpt: str, source_id: str = ""
) -> set[str]:
    """Литералы утверждения, которых НЕТ в цитируемой улике.

    MIR-060, доказанный класс. Эксперимент 2026-08-10 показал: разрешение
    ссылки само по себе даёт `verified`, а мутация `strict_ok = True -> False`
    перевела в неподтверждённые ВСЕ шесть случаев, включая истинный контроль —
    значит истинность не устанавливал никто.

    Живой вред этого класса: ответ назвал `claude-3-7-sonnet-20250219`,
    телеметрия того же прогона — `claude-sonnet-4-5`, верификация доложила
    24 из 24. Литерал, которого в улике нет, ссылкой не подтверждается.

    Пустое множество означает «этот гейт возражений не имеет», а не «истина
    установлена»: как и соседние три, он умеет только отнимать.
    """
    if not claim or not excerpt:
        return set()
    # Маркеры ссылок вырезаются ПЕРВЫМ делом. Внутри `[file:core/loop.py]`
    # лежит адрес источника, а не утверждение о мире; оставь его — и всякая
    # цитата, чей source_id не встречается в собственной выдержке, оказалась
    # бы «литералом, которого нет в улике». Измерено: без этой строки гейт
    # демотировал истинный контроль.
    body = _CITATION_TOKEN_RE.sub(" ", claim)
    # Имя СОБСТВЕННОГО источника чужим литералом не считается: «набор прошёл в
    # tests/bug_lab [test:run_tests:bug_lab]» называет адрес, который цитата и
    # устанавливает. Измерено: без этого гейт демотировал три существующие
    # фикстуры, где проза повторяла имя цитируемого источника.
    known = salient_literals(excerpt) | salient_literals(source_id or "")
    return {
        lit for lit in salient_literals(body)
        if lit not in known and not any(lit in k or k in lit for k in known)
    }


def absent_literal_reason(chunk_text: str, ev: Evidence, prefix: str) -> Any | None:
    """Причина демоции, если утверждение называет то, чего нет в улике.

    Возвращает `ClaimReason` или `None`. Отдельная функция, а не строки внутри
    `verify`: там уже 68 ветвей, и четвёртый гейт обязан читаться так же, как
    три соседних, — одним вопросом и одним ответом.
    """
    if prefix in {"user", "memory", "general-knowledge"}:
        return None
    # Утверждение об отсутствии судит ПЯТЫЙ гейт, и по обратному правилу:
    # там литерал в улике отсутствует именно потому, что его нет. Проверять
    # его присутствием значило бы демотировать верное утверждение — ложное
    # срабатывание, внесённое вместе с этим гейтом 2026-08-10.
    if asserts_absence(chunk_text):
        return None
    # R8 (2026-08-13, живой bd02fff1): усечённая бюджетом улика не доказывает
    # отсутствия — литерал мог жить в отрезанной части (семь ложных REFUTED за
    # один ход). Присутствие в вырезке она доказывает по-прежнему.
    excerpt_raw = ev.excerpt or ""
    if ("[INTENT-BUDGET:" in excerpt_raw or "[TOTAL-BUDGET:" in excerpt_raw
            or excerpt_raw.rstrip().endswith("...[truncated]")):  # R8b: срез при создании, evidence.py:_truncate
        return None
    absent = literals_absent_from_excerpt(
        chunk_text, ev.excerpt or "", ev.source_id or ""
    )
    if not absent:
        return None
    from .verifier_models import ClaimReason
    return ClaimReason(
        code="cited_literal_absent",
        expected=", ".join(sorted(absent)[:3]),
        actual="",
        explanation="утверждение называет то, чего нет в цитируемой улике",
        computed_from=ev.source_id or "",
    )


#: Утверждение ОБ ОТСУТСТВИИ: «нет X», «X отсутствует», «не найдено X»,
#: «not implemented», «no X exists». Маркер ищется по всему куску, а не в
#: начале: отрицание в русском и английском стоит где угодно.
_ABSENCE_ASSERTION_RE = re.compile(
    r"(?:\bотсутству\w*|\bне\s+(?:найден\w*|реализован\w*|существу\w*|"
    r"содерж\w*|определ\w*)|\bнет\b|\bни\s+одного\b"
    r"|\bno\s+(?:\S+\s+){1,3}(?:exists?|found|implemented)|\bnot\s+(?:implemented|"
    r"found|present|defined|exist)|\bdoes\s+not\s+(?:exist|contain|define)"
    r"|\babsent\b|\bmissing\b)",
    re.IGNORECASE,
)

#: Предмет утверждения об отсутствии: то, что названо ЯВНО — в кавычках,
#: обратных апострофах, либо отличительным литералом. Прозаический предмет
#: («нет обработки ошибок») сюда не попадает намеренно: это суждение о смысле,
#: и ловить его сверкой подстрок значило бы выдумать судью.
_NAMED_SUBJECT_RE = re.compile(r"[`\"']([A-Za-z_][A-Za-z0-9_.]{2,})[`\"']")


def absence_subjects(claim: str) -> set[str]:
    """Явно названные предметы утверждения об отсутствии."""
    named = {m.group(1).lower() for m in _NAMED_SUBJECT_RE.finditer(claim or "")}
    return named | salient_literals(_CITATION_TOKEN_RE.sub(" ", claim or ""))


def asserts_absence(claim: str) -> bool:
    """Утверждает ли кусок, что чего-то НЕТ."""
    return bool(_ABSENCE_ASSERTION_RE.search(claim or ""))


def absence_refuted_by_excerpt(claim: str, excerpt: str) -> bool:
    """Опровергает ли цитируемая улика утверждение об отсутствии.

    ОДНОСТОРОННЕ. Присутствие предмета в выдержке опровергает «его нет».
    Отсутствие в выдержке не доказывает ничего: выдержка усечена по
    построению, и трактовать её как полноту — ровно та ошибка, ради которой
    гейт заводится («неполный поиск» выдан за «доказательство отсутствия»,
    живой случай 2026-08-10).
    """
    if not claim or not excerpt or not asserts_absence(claim):
        return False
    body = excerpt.lower()
    return any(subject in body for subject in absence_subjects(claim))


def absence_reason(chunk_text: str, ev: Evidence, prefix: str) -> Any | None:
    """Причина демоции, если улика содержит то, чего утверждение не нашло."""
    if prefix in {"user", "memory", "general-knowledge"}:
        return None
    if not absence_refuted_by_excerpt(chunk_text, ev.excerpt or ""):
        return None
    present = sorted(
        s for s in absence_subjects(chunk_text) if s in (ev.excerpt or "").lower()
    )
    from .verifier_models import ClaimReason
    return ClaimReason(
        code="absence_refuted_by_evidence",
        expected="",
        actual=", ".join(present[:3]),
        explanation="утверждение об отсутствии опровергнуто собственной уликой",
        computed_from=ev.source_id or "",
    )


# ── R2/R3 (2026-08-13, probe_r1) ──────────────────────────────────────────────

_COUNT_WORDS: dict[str, int] = {
    "один": 1, "одна": 1, "две": 2, "два": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

#: Число (цифрой или ИМЕННО числительным — свободная словесная ветка ловила
#: любое слово, и нулевой матч съедал скобки) + существительное + перечисление
#: в скобках того же предложения; точка в зазоре запрещена.
_ENUM_COUNT_RE = re.compile(
    r"(?:\b(\d{1,2})\b|\b(" + "|".join(_COUNT_WORDS) + r")\b)\s+[\wЀ-ӿ-]+"
    r"[^().\n]{0,60}\(([^()]{2,200})\)",
    re.IGNORECASE,
)

#: Пункт, который предложение само же отвергло, — не перечислен, а исключён.
_ENUM_EXCLUDED_RE = re.compile(
    r"не\s+подход|не\s+подошл|не\s+входит|исключ|not\s+match|excluded",
    re.IGNORECASE,
)


def enumeration_count_reason(text: str) -> Any:
    """R2: заявленный счёт против СОБСТВЕННОГО перечисления того же предложения.

    Живой случай B1 (probe_r1): «пять полок (A1, B2, C4, D0)» и «три позиции
    (…, stator-6: 7 шт. не подходит)» ушли `unverified` и забанковались
    success/eligible — счёт не покрывал ни один гейт. Противоречие внутреннее,
    улике о нём нечего сказать, поэтому иск не снимается подтверждённой
    цитатой (ветка в `verifier_core`). Молчит на всём, чего не распознал.
    """
    from .verifier_models import ClaimReason

    for m in _ENUM_COUNT_RE.finditer(text or ""):
        claimed = int(m.group(1)) if m.group(1) else _COUNT_WORDS.get((m.group(2) or "").lower(), 0)
        if claimed < 2:
            continue
        items = [p.strip() for p in re.split(r"[;,]", m.group(3)) if p.strip()]
        if len(items) < 2:
            continue
        counted = sum(1 for item in items if not _ENUM_EXCLUDED_RE.search(item))
        if counted != claimed:
            return ClaimReason(
                code="count_mismatch",
                expected=str(claimed),
                actual=str(counted),
                explanation=(
                    "заявленный счёт противоречит собственному перечислению "
                    "в том же предложении"
                ),
                computed_from="собственное перечисление куска",
            )
    return None


def literal_covered_by_union(
    expected: str,
    evidences: list[Any],
    chain_source_ids: list[str] | None = None,
) -> bool:
    """R3: литерал накрыт ОБЪЕДИНЕНИЕМ процитированных улик (текст + адрес).

    Живой случай B3: перекрёстное утверждение цитировало обе улики по половине,
    а гейт литералов мерил каждую цитату против всего куска и опроверг истину
    дважды зеркально (`expected=notes_a` из notes_b и наоборот).
    """
    # `expected` — склейка до трёх литералов через ", " (см. absent_literal_
    # reason): живой прогон a5813910 показал, что поиск склейки как одной
    # подстроки не находил НИЧЕГО, и объединение не снимало ни одного иска.
    needles = [n.strip().lower() for n in (expected or "").split(",") if n.strip()]
    if not needles:
        return False
    haystacks = [
        (getattr(ev, "excerpt", "") or "").lower()
        + "\n" + (getattr(ev, "source_id", "") or "").lower()
        for ev in evidences or []
    ]
    # Живой 0d88ba79: кусок цитировал ОДИН файл, называя другие, прочитанные
    # тем же ходом. Литерал, совпадающий с АДРЕСОМ улики цепи, не выдуман —
    # его референт открывался. Только адреса, не тексты: содержимое чужой
    # улики отмывало бы значения обратно (MIR-060).
    haystacks.extend((sid or "").lower() for sid in chain_source_ids or [])
    return all(any(n in h for h in haystacks) for n in needles)
