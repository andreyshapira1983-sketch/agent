"""The verifier's `verify()` entry point: turns a draft answer and its evidence chain into a per-claim verdict report.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from core.evidence import Evidence, ProvenanceChain, make_evidence
from core.evidence_classes import (
    classify_evidence,
    dialogue_evidence_present,
    is_dialogue_scoped_claim,
)

from .claim_arithmetic import evaluate as evaluate_claim_arithmetic
from .verifier_absence import (
    absence_certifiable,
    absence_certified_by_search,
    absence_reason,
    absent_literal_reason,
    denies_own_evidence_reason,
    off_topic_reason,
    restated_number_reason,
)
from .verifier_models import ClaimChunk, ClaimReason, VerificationReport
from .verifier_patterns import (
    _NON_CLAIM_SECTIONS,
    DISCLAIMER_ALL_SELF_DECLARED,
    DISCLAIMER_FULLY_UNVERIFIED,
    DISCLAIMER_NO_CHAIN,
    DISCLAIMER_SESSION_MEMORY,
    DISCLAIMER_USER_ASSERTED,
    SELF_DECLARED_PREFIXES,
)
from .verifier_utils import (
    _find_semantic_support,
    _find_structured_support,
    _is_derivative_subagent_evidence,
    _merge_citation_only_chunks,
    _output_contract_header_name,
    _tool_citation_for,
    best_run_of_source,
    enumeration_count_reason,
    extract_statistical_figures,
    is_statistical_claim,
    is_structural_chunk,
    literal_covered_by_union,
    match_citation,
    parse_citations,
    split_into_chunks,
    truth_excerpt,
)

# Memory records whose provenance makes a citation to them independent
# evidence. Only an explicit human write qualifies: the operator asserting a
# fact is a source, whereas the agent's own auto-written record is a copy of
# something it already said.
_INDEPENDENT_MEMORY_ORIGINS: frozenset[str] = frozenset({"user-explicit"})


def _memory_citation_is_independent(ev: Evidence) -> bool:
    """May a citation resolving to this evidence count as `verified`?

    Only memory evidence is questioned here; every other kind keeps its
    existing treatment. Two memory shapes are deliberately distinguished:

    - a **persistent record** (`obtained_via="memory"`) is judged on its
      `origin`: a `user-explicit` record is a human assertion, while an
      `agent-auto` or ingested one is not independent of the agent;
    - a **working-memory artifact** (`obtained_via="working_memory"`) is a
      cached tool observation — real output that was actually fetched — so it
      keeps counting as evidence.
    """
    if ev.kind != "memory":
        return True
    if ev.obtained_via != "memory":
        return True
    return ev.origin in _INDEPENDENT_MEMORY_ORIGINS


def _dialogue_verdict_for(chunk_text: str, chain: ProvenanceChain) -> Evidence | None:
    """Session-dialogue evidence that legitimately supports this chunk."""
    if not is_dialogue_scoped_claim(chunk_text):
        return None
    for ev in chain.evidences:
        if classify_evidence(ev) == "session_dialogue":
            return ev
    return None


#: Причины, которые НЕ доказывают лжи: улика просто не подпирает утверждение.
#:
#: Замечание оператора 2026-09-21: «у него то восемь из двенадцати, то семь из
#: десяти, и никогда десять из десяти — он никогда не уверен». Замер по чату
#: того вечера: 53 ответа с хвостом проверки, ПОЛНЫХ ровно два, суммарно 377
#: утверждений из 549. Клеймо `claim-refuted` стояло 37 раз; пять взятых
#: наугад образцов проверены руками, и все пять оказались ИСТИННЫМИ докладами
#: агента о собственной работе: «прогон завершился exit_code 1, passed 0,
#: failed 2», «ворота одобрения вернули unavailable для file_write».
#:
#: Механизм: отличительные слова утверждения обязаны дословно встретиться в
#: вырезке улики; агент ссылался на самое близкое, что у него было, улика по
#: теме верная, а его точных слов в ней нет — и вердикт становился `refuted`.
#: То есть честный доклад о своей неудаче получал клеймо лжи.
#:
#: Различение назвал сам агент, когда с ним об этом спорили:
#: `absence_refuted_by_evidence` — настоящее опровержение (сказал «ничего
#: нет», а улика показывает, что есть), и такие коды остаются ложью. А
#: `cited_literal_absent` доказывает только одно — что слов нет в вырезке.
#: Между «улика не содержит этих слов» и «утверждение ложно» лежит пропасть.
_UNSUPPORTED_REASONS: frozenset[str] = frozenset({"cited_support_missing"})


def _admitted_unverified(chunks: list[str]) -> int:
    """Сколько строк ответ сам вынес в раздел «Unverified» («Не подтверждено»).

    Раздел не-утвердительный, и в счёт утверждений он не идёт — но это
    непроверенное, названное вслух, и уверенность обязана его видеть
    (2026-09-21: «я не проверял X» и ниже «9 из 9, уверенность высокая»).
    """
    section: str | None = None
    count = 0
    for text in chunks:
        header = _output_contract_header_name(text)
        if header is not None:
            section = header
        elif (section == "unverified" and not is_structural_chunk(text)
              and not _NOTHING_UNVERIFIED_RE.match(text)):
            count += 1
    return count


#: «Не подтверждено: Ничего — всё измерено…» — это не непроверенный пункт.
#: 2026-09-21, ответ «пересчитай функции»: такая строка засчитывалась, и хвост
#: говорил «(1 — ответ сам назвал непроверенными)» при пустом разделе.
_NOTHING_UNVERIFIED_RE = re.compile(
    r"^[\s\-•*]*(?:ничего|нет|none|nothing|n/a)\b", re.IGNORECASE)


def _judge_uncited(chunk_text: str, chain: ProvenanceChain, chain_empty: bool,
                   has_dialogue_evidence: bool) -> tuple[str, str, list[str]]:
    """Вердикт куска без ссылок: (вердикт, размеченный текст, id улик).

    Вынесено из `verify` 2026-09-21 дословно: счётчики теперь считаются
    по итоговым вердиктам (равенство проверено на всех 10 283 тестах).
    """
    matched_ids: list[str] = []
    # `_find_structured_support` is tool_output-only by construction
    # (kind filter in verifier_utils), so a user_explicit result is
    # impossible here — no MIR-028 interception needed on this path.
    struct_ev = _find_structured_support(chunk_text, chain) if not chain_empty else None
    if struct_ev is not None:
        verdict = "verified"
        matched_ids.append(struct_ev.id)
        annotated = chunk_text.rstrip() + " " + _tool_citation_for(struct_ev)
    else:
        dlg_ev = (
            _dialogue_verdict_for(chunk_text, chain)
            if has_dialogue_evidence
            else None
        )
        if dlg_ev is not None:
            # A statement about this session's own exchange. The
            # synthesizer routinely writes these without a citation
            # ("Мой предыдущий ответ не отвечал на вопрос") and the
            # bare `unverified` verdict is what fed the truncation
            # that deleted a valid self-correction (issue #119).
            verdict = "dialogue_supported"
            matched_ids.append(dlg_ev.id)
            annotated = chunk_text.rstrip() + " [dialogue-supported]"
        else:
            verdict = "unverified"
            annotated = chunk_text.rstrip() + " [unverified]"
    return verdict, annotated, matched_ids


def _gate_citation(
    chunk_text: str, c: Any, ev: Any, chunk_reason: ClaimReason | None, *,
    stat_claim: bool, stat_figures: Any,
) -> tuple[bool, ClaimReason | None]:
    """Гейты над ОДНОЙ разрешённой ссылкой: (выдержала ли, причина).

    Понижают `verified`, никогда его не создают. Вынесено из вердикта
    куска 2026-09-21 дословно.
    """
    strict_ok = True
    if not _memory_citation_is_independent(ev):
        # The citation resolves — the record exists and matches the
        # id — but resolution is not verification. An agent-auto
        # record is the agent's own earlier output; counting it as
        # `verified` lets the agent confirm itself by having
        # remembered something (MIR-046). Demoted to topic-only, so
        # it still shows as supporting context but never as proof.
        strict_ok = False
    # MIR-060 (b): the third content gate. The other two ask WHOSE
    # evidence this is and whether a figure appears in it; this one
    # COMPUTES. Where the claim is a sum, a count, a comparison or
    # a multiple over `key=value` lines it is decided arithmetically
    # and, when refuted, says so with its working — a stamp tells
    # the agent it was wrong, this tells it what to change.
    # Silent by construction on every shape it does not recognise,
    # so it can only ever remove a false `verified`, never create
    # one.
    arith = evaluate_claim_arithmetic(chunk_text, truth_excerpt(ev.excerpt or ""))
    if arith.refutes:
        strict_ok = False
        chunk_reason = chunk_reason or ClaimReason(
            code=arith.code, expected=arith.expected,
            actual=arith.actual, explanation=arith.explanation,
            computed_from=arith.computed_from,
        )
    # MIR-060 (c): четвёртый гейт — отличительные литералы утверждения
    # обязаны быть в улике. Тело в `verifier_utils.absent_literal_reason`.
    _lit = absent_literal_reason(chunk_text, ev, c.prefix)
    if _lit is not None:
        strict_ok = False
        chunk_reason = chunk_reason or _lit
    # MIR-060 (d): пятый гейт, обратное правило четвёртого —
    # утверждение «этого там нет», опровергнутое собственной уликой.
    _abs = absence_reason(chunk_text, ev, c.prefix)
    if _abs is not None:
        strict_ok = False
        chunk_reason = chunk_reason or _abs
    # MIR-060 (f) / MIR-141: шестой гейт — цитата обязана быть ПРО
    # это утверждение. Пятеро выше немы на прозе, потому что судят
    # литералы код-образной формы; замер дал по оси темы J = 0.00.
    # Шестой гейт, понижение БЕЗ обвинения:
    # docs/CODE_NOTES.md, «A citation that is not about the claim».
    # Уступает вычисляющему гейту: если он ПОДТВЕРДИЛ форму, кусок
    # говорит об улике арифметически, и сравнение слов тут не судья.
    if arith.outcome != "supports" and off_topic_reason(
        chunk_text, ev, c.prefix
    ) is not None:
        strict_ok = False
    # Девятый гейт: ссылка на улику, объявленную неверной, не может
    # быть поддержкой. Понижение, не обвинение — маркер лексический.
    if denies_own_evidence_reason(chunk_text, ev, c.prefix) is not None:
        strict_ok = False
    # Десятый гейт: дословный пересказ не вправе менять число.
    _restated = restated_number_reason(chunk_text, ev, c.prefix)
    if _restated is not None:
        strict_ok = False
        chunk_reason = chunk_reason or _restated
    if stat_claim and c.prefix not in {"user", "memory", "general-knowledge"}:
        excerpt = truth_excerpt(ev.excerpt or "")
        if stat_figures:
            from .verifier_absence import _APPROXIMATION_RE
            from .verifier_utils import _excerpt_supports_figures
            if not _excerpt_supports_figures(
                excerpt, stat_figures,
                approximate=bool(_APPROXIMATION_RE.search(chunk_text)),
            ):
                strict_ok = False
        elif ev.kind == "web_search_hit":
            strict_ok = False
    return strict_ok, chunk_reason


def _entailment_denied(
    chunk_text: str, chunk_evs: list[Any], matched_ids: list[str], llm: Any, *, stat_figures: Any,
) -> bool:
    """Процитированное не влечёт утверждение — проверка по смыслу (MIR-060).

    Отказ ведёт в «источник есть, утверждение не подтверждено» — НЕ в
    «выдуманную ссылку»: та терминальна и уничтожает ответ, а здесь источник
    есть и прочитан, он просто не подтверждает сказанного.

    До 24.09 найденная ссылка сама была вердиктом: проверка по смыслу шла
    лишь для утверждений, у которых ссылка НЕ нашлась. Замер 24.09: ответ
    «второй свидетель op=office даёт улику» — теста не существовало — получил
    «подтверждено 8 из 8» по ссылке на вывод patch_check. ALCE (Gao et al.,
    2023): утверждение подтверждено, если процитированные отрывки его ВЛЕКУТ.
    Числа уже сверены строже (гейт цифр) — для них не зовём. Нет модели или
    нет вырезок — прежнее поведение.
    """
    if llm is None or stat_figures:
        return False
    excerpts = [ev.excerpt for ev in chunk_evs if ev.id in matched_ids and getattr(ev, "excerpt", "")]
    if not excerpts:
        return False
    from .verifier_utils import _semantic_nli_check
    # Окно шире, чем у поиска по всей цепочке (600): здесь смотрят только
    # процитированные улики, а подтверждение в выводе инструмента часто стоит
    # дальше первых сотен знаков — узкое окно ложно отказывало бы верному.
    return not _semantic_nli_check(chunk_text, "\n---\n".join(excerpts), llm, max_chars=6000)


def _judge_unmatched(
    chunk_text: str, cits: list[Any], chain: ProvenanceChain, *, chain_empty: bool,
    llm: Any, annotated: str,
) -> tuple[str, str, list[str], bool]:
    """Ни одна ссылка куска не подтвердила его: последний шанс по структуре
    вывода инструмента или по смыслу, иначе `cited_but_unmatched`.
    Вынесено из вердикта куска 2026-09-21 дословно.
    """
    matched_ids: list[str] = []
    memory_only = False
    # tool_output-only by construction — cannot return user_explicit.
    struct_ev = _find_structured_support(chunk_text, chain) if chain.evidences and not chain_empty else None
    if struct_ev is not None:
        verdict = "verified"
        matched_ids.append(struct_ev.id)
        for c in cits:
            if c.prefix not in SELF_DECLARED_PREFIXES:
                body_part = f":{c.body}" if c.body else ""
                annotated = annotated.replace(c.raw, f"[verified:{c.prefix}{body_part}]")
    elif llm is not None and chain.evidences and not chain_empty:
        sem_ev = _find_semantic_support(chunk_text, chain, llm)
        if sem_ev is not None and getattr(sem_ev, "kind", "") == "user_explicit":
            verdict = "user_asserted"
            matched_ids.append(sem_ev.id)
            annotated = annotated.rstrip() + " [user-asserted]"
        elif sem_ev is not None:
            verdict = "verified"
            matched_ids.append(sem_ev.id)
            for c in cits:
                if c.prefix not in SELF_DECLARED_PREFIXES:
                    body_part = f":{c.body}" if c.body else ""
                    annotated = annotated.replace(c.raw, f"[verified:{c.prefix}{body_part}]")
        else:
            verdict = "cited_but_unmatched"
    else:
        verdict = "cited_but_unmatched"
        if cits and all(c.prefix == "memory" or c.prefix in SELF_DECLARED_PREFIXES for c in cits):
            memory_only = True
    return verdict, annotated, matched_ids, memory_only


def _relabel(annotated: str, pairs: list[tuple[str, str]]) -> str:
    """Заменить сырые ссылки куска их метками вердикта."""
    for raw, rewrite in pairs:
        annotated = annotated.replace(raw, rewrite)
    return annotated


def _promote_topic_only(annotated: str, replacements: list[tuple[str, str]]) -> str:
    """Ссылки, не выдержавшие порознь, подтверждены объединением: метка — verified."""
    return _relabel(annotated, [
        (raw, label.replace("[topic-only:", "[verified:", 1)) for raw, label in replacements])


def _union_judgement(
    chunk_text: str, cits: list[Any], chunk_evs: list[Any], *, stat_claim: bool, stat_figures: Any,
) -> bool:
    """Те же гейты — над ОБЪЕДИНЕНИЕМ процитированных улик.

    24.09, живой прогон приёмки: «в DOCX 48 750, в скане 47 250 [file:a][file:b]»
    — верно, но каждое число только в одной улике, а гейты судят улики порознь:
    любое сравнение двух источников навсегда «не подтверждено». ALCE (Gao et
    al., 2023): утверждение подтверждено, если его влечёт объединение
    процитированного. Зовётся, только когда ни одна улика не выдержала одна;
    не выдержало объединение — всё как раньше.
    """
    from dataclasses import replace
    unique = list({ev.id: ev for ev in chunk_evs}.values())
    if len(unique) < 2 or not cits:
        return False
    merged = replace(unique[0], excerpt="\n".join(ev.excerpt or "" for ev in unique))
    ok, reason = _gate_citation(chunk_text, cits[0], merged, None,
                                stat_claim=stat_claim, stat_figures=stat_figures)
    return ok and reason is None


def _union_clears_literal(
    chunk_reason: ClaimReason | None, chunk_evs: list[Any], chain: ProvenanceChain,
) -> ClaimReason | None:
    """R3 (2026-08-13, живой B3): литералы куска накрываются ОБЪЕДИНЕНИЕМ
    процитированных улик (текст + адрес), а не каждой порознь — перекрёстное
    утверждение цитирует два источника по половине. Снимает иск
    `cited_literal_absent`, если объединение его накрывает.
    """
    if (
        chunk_reason is not None
        and chunk_reason.code == "cited_literal_absent"
        and literal_covered_by_union(
            chunk_reason.expected, chunk_evs,
            [e.source_id for e in chain.evidences],
        )
    ):
        return None
    return chunk_reason


def _absence_proven(chunk_text: str, chunk_evs: list) -> bool:
    """Отсутствие не утверждается — или доказано полным поиском названной области
    (исключение из стены (e): отчёт поиска — не усечённая выдержка)."""
    return absence_certifiable(chunk_text, "") or absence_certified_by_search(chunk_text, chunk_evs)


def _judge_cited(
    chunk_text: str, cits: list[Any], chain: ProvenanceChain, *, chain_empty: bool,
    llm: Any, chunk_reason: ClaimReason | None,
) -> tuple[str, str, list[str], ClaimReason | None, bool]:
    """Вердикт куска со ссылками: (вердикт, размеченный текст, id улик,
    причина, «только память без совпадения»).

    Вынесено из `verify` 2026-09-21 дословно; счётчики — по итоговым
    вердиктам в `verify`.
    """
    chunk_evs: list[Any] = []
    matched_ids: list[str] = []
    annotated = chunk_text
    memory_only = False
    stat_figures = extract_statistical_figures(chunk_text)
    stat_claim = is_statistical_claim(chunk_text)
    any_matched = any_self_declared = any_topic_only = False
    any_dialogue = False
    any_user_asserted = False
    topic_only_replacements: list[tuple[str, str]] = []
    dialogue_replacements: list[tuple[str, str]] = []
    dialogue_ids: list[str] = []
    user_asserted_replacements: list[tuple[str, str]] = []
    user_asserted_ids: list[str] = []
    for c in cits:
        if c.prefix in SELF_DECLARED_PREFIXES:
            any_self_declared = True
            body_part = f":{c.body}" if c.body else ""
            annotated = annotated.replace(c.raw, f"[declared:{c.prefix}{body_part}]")
            continue
        ev = match_citation(c, chain)
        if ev is None:
            continue
        ev = best_run_of_source(ev, chain, chunk_text)
        if getattr(ev, "kind", "") == "user_explicit":
            # Operator ruling 2026-08-03 (MIR-028): a citation of the
            # operator's own turn confirms the words were said — it is
            # never `verified`, whatever the claim's shape (this also
            # supersedes the stat-figure exemption for the `user`
            # prefix below: the branch is intercepted here first).
            any_user_asserted = True
            user_asserted_ids.append(ev.id)
            body_part = f":{c.body}" if c.body else ""
            user_asserted_replacements.append(
                (c.raw, f"[user-asserted:{c.prefix}{body_part}]")
            )
            continue
        if classify_evidence(ev) == "session_dialogue":
            # The recording of the exchange proves what was SAID. It is
            # never promoted to `verified`, and it credits only claims
            # that are themselves about the exchange — a world claim
            # citing [dialogue:…] stays unsupported (issue #119).
            body_part = f":{c.body}" if c.body else ""
            if is_dialogue_scoped_claim(chunk_text):
                any_dialogue = True
                dialogue_ids.append(ev.id)
                dialogue_replacements.append(
                    (c.raw, f"[dialogue-supported:{c.prefix}{body_part}]")
                )
            else:
                any_topic_only = True
                topic_only_replacements.append(
                    (c.raw, f"[topic-only:{c.prefix}{body_part}]")
                )
            continue
        chunk_evs.append(ev)
        strict_ok, chunk_reason = _gate_citation(
            chunk_text, c, ev, chunk_reason,
            stat_claim=stat_claim, stat_figures=stat_figures)
        body_part = f":{c.body}" if c.body else ""
        if strict_ok:
            matched_ids.append(ev.id)
            any_matched = True
            annotated = annotated.replace(c.raw, f"[verified:{c.prefix}{body_part}]")
        else:
            any_topic_only = True
            topic_only_replacements.append((c.raw, f"[topic-only:{c.prefix}{body_part}]"))
    if not any_matched and _union_judgement(chunk_text, cits, chunk_evs,
                                            stat_claim=stat_claim, stat_figures=stat_figures):
        any_matched, chunk_reason = True, None
        matched_ids.extend(ev.id for ev in chunk_evs if ev.id not in matched_ids)
        annotated, topic_only_replacements = _promote_topic_only(annotated, topic_only_replacements), []
    # R3: литералы накрываются объединением улик (см. _union_clears_literal).
    chunk_reason = _union_clears_literal(chunk_reason, chunk_evs, chain)
    # MIR-060 (e): у утверждения об ОТСУТСТВИИ сертификата быть не
    # может — гейт (d) его опровергает, этот не даёт подтвердить
    # (docs/CODE_NOTES.md, «Absence was certified by a resolved citation»).
    _abs_uncert = any_matched and not _absence_proven(chunk_text, chunk_evs)
    if any_matched and not _abs_uncert and not (
        chunk_reason is not None and chunk_reason.code == "count_mismatch"
    ) and not _entailment_denied(chunk_text, chunk_evs, matched_ids, llm, stat_figures=stat_figures):
        verdict = "verified"
        # Иск снят: другая из процитированных улик подтвердила кусок.
        chunk_reason = None
        annotated = _relabel(annotated, topic_only_replacements + dialogue_replacements + user_asserted_replacements)
    elif any_matched and not _abs_uncert and chunk_reason is None:  # не влечёт: см. _entailment_denied
        verdict = "topic_supported_but_claim_unverified"
        annotated = _relabel(annotated, topic_only_replacements).rstrip() + " [цитата-не-подтверждает]"
    elif _abs_uncert and chunk_reason is None:
        # Ниже опровержения намеренно: опровергнутое — доказанная ложь,
        # а это лишь несертифицируемое.
        verdict = "topic_supported_but_claim_unverified"
        annotated = annotated.rstrip() + " [absence-unverifiable]"
    elif chunk_reason is not None and chunk_reason.code in _UNSUPPORTED_REASONS:
        # Та же полярность, применённая в ДРУГУЮ сторону: «в вырезке
        # улики нет этих слов» — не разновидность доказанной лжи.
        #
        # И НЕ разновидность выдуманной ссылки. Первая редакция этой
        # правки (2026-09-21, час спустя) увела такие куски в
        # `cited_but_unmatched` — а `core/unsupported_claims.py`
        # считает именно этот счётчик ФАБРИКАЦИЕЙ цитат, и фабрикация
        # терминальна: ответ не отправляется вовсе. Живая цена: два
        # ответа подряд уничтожены целиком, человек получил канцелярскую
        # записку вместо работы, причём один раз — из-за ОДНОЙ ссылки
        # на восемь утверждений.
        #
        # Разница существенная. Выдуманная ссылка НЕ РАЗРЕШАЕТСЯ НИ ВО
        # ЧТО: источника нет. Здесь источник есть, он открыт и прочитан,
        # в нём просто нет дословных слов утверждения. Первое — ложь о
        # происхождении, второе — нехватка подпорки.
        verdict = "topic_supported_but_claim_unverified"
        annotated = _relabel(annotated, topic_only_replacements)
        annotated = annotated.rstrip() + " [улика-без-этих-слов]"
        chunk_reason = None
    elif chunk_reason is not None:
        # Полярность: доказанная ложь — не разновидность «не подтверждено»
        # (2026-08-12, docs/CODE_NOTES.md «REFUTED is a polarity»).
        verdict = "refuted"
        annotated = _relabel(annotated, topic_only_replacements)
        annotated = annotated.rstrip() + " [claim-refuted]"
    elif any_dialogue:
        verdict = "dialogue_supported"
        matched_ids.extend(dialogue_ids)
        annotated = _relabel(annotated, topic_only_replacements + dialogue_replacements + user_asserted_replacements)
    elif any_user_asserted:
        verdict = "user_asserted"
        matched_ids.extend(user_asserted_ids)
        annotated = _relabel(annotated, topic_only_replacements + user_asserted_replacements)
    elif any_self_declared:
        verdict = "self_declared"
    elif any_topic_only:
        verdict = "topic_supported_but_claim_unverified"
        annotated = _relabel(annotated, topic_only_replacements)
        annotated = annotated.rstrip() + " [claim-figure-unverified]"
    else:
        verdict, annotated, _found, memory_only = _judge_unmatched(
            chunk_text, cits, chain, chain_empty=chain_empty, llm=llm,
            annotated=annotated)
        matched_ids.extend(_found)
    return verdict, annotated, matched_ids, chunk_reason, memory_only


def verify(*, answer: str, chain: ProvenanceChain, llm: Any = None, user_question: str | None = None, receipt_ledger: Any = None, trace_id: str | None = None, expects_contract_headers: bool = True) -> VerificationReport:
    chain_empty = len(chain) == 0
    if user_question and user_question.strip():
        user_ev = make_evidence(kind="user_explicit", source_id="user:current_turn", obtained_via="user_input", claim="Operator-provided text for the current turn", excerpt=user_question.strip())
        local_chain = ProvenanceChain()
        for ev in chain.evidences:
            local_chain.add(ev)
        local_chain.add(user_ev)
        chain = local_chain
    all_chunks_text = _merge_citation_only_chunks(split_into_chunks(answer))
    if not all_chunks_text:
        return VerificationReport(total_chunks=0, verified_chunks=0, unverified_chunks=0, cited_but_unmatched_chunks=0, self_declared_chunks=0, structural_chunks=0, chunks=(), annotated_answer=answer, fully_unverified=True, chain_was_empty=chain_empty, disclaimer=(DISCLAIMER_NO_CHAIN if chain_empty else DISCLAIMER_FULLY_UNVERIFIED))
    examined_chunks: list[ClaimChunk] = []
    structural = memory_only_unmatched = 0
    has_dialogue_evidence = dialogue_evidence_present(chain)
    annotated_chunks: list[str] = []
    # Index into ``annotated_chunks`` for each examined (non-structural) chunk,
    # so the downgrade pass below can patch the exact rendered line instead of
    # fuzzy string-matching (which silently failed when a claim began with its
    # own citation).
    examined_annotated_idx: list[int] = []
    current_section: str | None = None
    for chunk_text in all_chunks_text:
        header = _output_contract_header_name(chunk_text)
        if header is not None:
            current_section = header
            structural += 1
            annotated_chunks.append(chunk_text)
            continue
        if is_structural_chunk(chunk_text):
            structural += 1
            annotated_chunks.append(chunk_text)
            continue
        if current_section in _NON_CLAIM_SECTIONS:
            structural += 1
            annotated_chunks.append(chunk_text)
            continue
        cits = parse_citations(chunk_text)
        # R2 (2026-08-13): внутренний гейт — заявленный счёт против собственного
        # перечисления. Улика о споре предложения с самим собой ничего не знает,
        # поэтому подтверждённая цитата этот иск НЕ снимает (ветка ниже).
        chunk_reason: ClaimReason | None = enumeration_count_reason(chunk_text)
        if not cits:
            verdict, annotated, matched_ids = _judge_uncited(
                chunk_text, chain, chain_empty, has_dialogue_evidence)
        else:
            verdict, annotated, matched_ids, chunk_reason, _memory_only = _judge_cited(
                chunk_text, cits, chain, chain_empty=chain_empty, llm=llm,
                chunk_reason=chunk_reason)
            memory_only_unmatched += _memory_only
        examined_chunks.append(ClaimChunk(text=chunk_text, citations=tuple(cits),
                                         matched_evidence_ids=tuple(matched_ids),
                                         verdict=verdict, reason=chunk_reason))
        annotated_chunks.append(annotated)
        examined_annotated_idx.append(len(annotated_chunks) - 1)
    if examined_chunks:
        from core.receipt_consumer import matched_evidence_lacks_receipt
        ev_by_id: dict[str, Evidence] = {ev.id: ev for ev in chain.evidences}
        rebuilt_chunks: list[ClaimChunk] = []
        for ch, ann_idx in zip(examined_chunks, examined_annotated_idx, strict=False):
            if ch.verdict != "verified" or not ch.matched_evidence_ids:
                rebuilt_chunks.append(ch)
                continue
            evs = [ev_by_id.get(eid) for eid in ch.matched_evidence_ids]
            evs = [e for e in evs if e is not None]
            if not evs:
                rebuilt_chunks.append(ch)
                continue
            if all(_is_derivative_subagent_evidence(e) for e in evs):
                rebuilt_chunks.append(ClaimChunk(text=ch.text, citations=ch.citations,
                                                 matched_evidence_ids=ch.matched_evidence_ids,
                                                 verdict="subagent_asserted", reason=ch.reason))
                line = annotated_chunks[ann_idx]
                if "[subagent-asserted]" not in line:
                    annotated_chunks[ann_idx] = line.rstrip() + " [subagent-asserted]"
                continue
            if matched_evidence_lacks_receipt(evs, ledger=receipt_ledger, trace_id=trace_id):
                rebuilt_chunks.append(ClaimChunk(text=ch.text, citations=ch.citations, matched_evidence_ids=ch.matched_evidence_ids, verdict="receipt_missing"))
                line = annotated_chunks[ann_idx]
                if "[no-receipt]" not in line:
                    annotated_chunks[ann_idx] = line.replace("[verified:", "[unverified:").rstrip() + " [no-receipt]"
                continue
            rebuilt_chunks.append(ch)
        examined_chunks = rebuilt_chunks
    # Счёт — по итоговым вердиктам, после понижений (субагент, квитанция).
    # Прежде десяток счётчиков правился по ходу в каждой ветке; равенство
    # проверено на всех 10 283 тестах перед выносом судей, 2026-09-21.
    # MIR-028: слова оператора — свой вердикт `user_asserted`, не `verified`.
    n = Counter(ch.verdict for ch in examined_chunks)
    verified, unverified, refuted = n["verified"], n["unverified"], n["refuted"]
    cited_unmatched, self_declared = n["cited_but_unmatched"], n["self_declared"]
    topic_supported = n["topic_supported_but_claim_unverified"]
    dialogue_supported, user_asserted = n["dialogue_supported"], n["user_asserted"]
    subagent_asserted, receipt_missing = n["subagent_asserted"], n["receipt_missing"]
    annotated_answer = "\n".join(annotated_chunks)
    headers_found = any(_output_contract_header_name((t or "").strip()) is not None for t in all_chunks_text)
    # A task-specific/structured output contract (e.g. table-only) legitimately
    # omits the generic Conclusion/Facts headers. Only flag malformed output
    # when the generic prose contract is actually in force.
    malformed_output = bool(all_chunks_text) and not headers_found and expects_contract_headers
    fully_unverified = (verified == 0 and self_declared == 0)
    disclaimer: str | None = None
    if fully_unverified:
        if dialogue_supported > 0:
            # Honest on both counts: nothing external was verified, and the
            # support the answer does have is this session's own transcript.
            disclaimer = DISCLAIMER_SESSION_MEMORY
        elif user_asserted > 0:
            # The only support is the operator's own words this turn — say
            # exactly that (operator ruling, MIR-028).
            disclaimer = DISCLAIMER_USER_ASSERTED
        elif cited_unmatched > 0 and cited_unmatched == memory_only_unmatched:
            disclaimer = DISCLAIMER_SESSION_MEMORY
        elif chain_empty:
            disclaimer = DISCLAIMER_NO_CHAIN
        else:
            disclaimer = DISCLAIMER_FULLY_UNVERIFIED
    elif verified == 0 and self_declared > 0:
        disclaimer = DISCLAIMER_ALL_SELF_DECLARED
    if disclaimer is not None:
        annotated_answer = annotated_answer.rstrip() + "\n\n" + disclaimer
    return VerificationReport(total_chunks=len(examined_chunks), verified_chunks=verified, unverified_chunks=unverified, cited_but_unmatched_chunks=cited_unmatched, self_declared_chunks=self_declared, structural_chunks=structural, chunks=tuple(examined_chunks), annotated_answer=annotated_answer, fully_unverified=fully_unverified, chain_was_empty=chain_empty, disclaimer=disclaimer, malformed_output=malformed_output, topic_supported_but_claim_unverified_chunks=topic_supported, subagent_asserted_chunks=subagent_asserted, receipt_missing_chunks=receipt_missing, dialogue_supported_chunks=dialogue_supported, user_asserted_chunks=user_asserted, refuted_chunks=refuted, admitted_unverified_chunks=_admitted_unverified(all_chunks_text))
