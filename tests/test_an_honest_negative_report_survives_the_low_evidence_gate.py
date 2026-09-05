"""An honest negative report survives the low-evidence gate (work order 1, defect 3).

Pass 2: the synthesizer's draft said, correctly, «ни один из трёх проверенных
источников не предоставил проверяемых тарифов … все живые запросы были
заблокированы или вернули пустые страницы … задача не выполнена». The
verifier booked four chunks subagent_asserted and two topic-only; the gate
computed supported_ratio=0.11 and erased eight of nine claims, handing the
operator one Flydubai baggage fact. The gate did its formal job — «ship
nothing unsupported» — and broke the user's: «if nothing can be confirmed,
say so».

Pinned here: a claim that asserts blocking / non-confirmation / not-done is
supported by the ATTEMPT evidence in the chain (HTTP 4xx/5xx, unsupported,
blocked, empty results) and is not suppressed; the same claim with no
blocked attempt in the chain is treated as before; a positive fact is never
rescued by this rule.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.evidence import ProvenanceChain, make_evidence
from core.low_evidence_policy import count_blocked_attempts, evaluate_low_evidence_policy

CONCLUSION = (
    "Ни один из трёх проверенных источников не предоставил проверяемых тарифов по маршруту "
    "Тель-Авив → Берлин: все живые запросы были заблокированы или вернули пустые страницы, "
    "поэтому подтверждённых вариантов с полной ценой и длительностью нет."
)
NOT_DONE = "Задача не выполнена: ни один из 5 запрошенных вариантов не может быть представлен как подтверждённый факт."
POSITIVE = "Лучший вариант — El Al за 212 долларов с пересадкой в Вене."


def _chunk(text: str, verdict: str) -> SimpleNamespace:
    return SimpleNamespace(text=text, verdict=verdict, citations=(), matched_evidence_ids=())


def _report(chunks: list[SimpleNamespace]) -> SimpleNamespace:
    verdicts = [c.verdict for c in chunks]
    return SimpleNamespace(
        total_chunks=len(chunks),
        verified_chunks=verdicts.count("verified"),
        unverified_chunks=verdicts.count("unverified"),
        cited_but_unmatched_chunks=0,
        topic_supported_but_claim_unverified_chunks=verdicts.count("topic_supported_but_claim_unverified"),
        subagent_asserted_chunks=verdicts.count("subagent_asserted"),
        dialogue_supported_chunks=0, user_asserted_chunks=0, refuted_chunks=verdicts.count("refuted"),
        chunks=tuple(chunks), chain_was_empty=False,
    )


def _pass_two_report() -> SimpleNamespace:
    return _report([
        _chunk(CONCLUSION, "subagent_asserted"),
        _chunk(NOT_DONE, "subagent_asserted"),
        _chunk("Google Flights вернул страницу unsupported; Expedia ответил HTTP 429.", "subagent_asserted"),
        _chunk("Skyscanner перенаправил на динамическую страницу без тарифов.", "subagent_asserted"),
        _chunk("KAYAK и Kiwi отдали пустые страницы поиска.", "topic_supported_but_claim_unverified"),
        _chunk("Trip.com — динамическая страница без тарифов.", "topic_supported_but_claim_unverified"),
        _chunk("Ручная кладь Flydubai — 7 кг.", "verified"),
        _chunk("Цены не подтверждены на финальном шаге.", "unverified"),
        _chunk("Проверка выполнена 2026-09-05 06:49Z.", "unverified"),
    ])


def _blocked_chain() -> ProvenanceChain:
    chain = ProvenanceChain()
    chain.add(make_evidence(kind="web_page", source_id="web_page:https://www.google.com/travel/flights/unsupported",
                            obtained_via="subagent:GoogleFlightsCheck", claim="page", excerpt="unsupported", confidence=0.7))
    chain.add(make_evidence(kind="tool_output", source_id="tool_output:spawn_subagent", obtained_via="spawn_subagent",
                            claim="child", excerpt="[subagent-meta external_evidence_count=3] Expedia: HTTP 429 Too Many Requests → BLOCKED", confidence=0.5))
    return chain


def test_blocked_attempts_are_counted_from_the_chain():
    assert count_blocked_attempts(_blocked_chain()) == 2
    assert count_blocked_attempts(ProvenanceChain()) == 0


def test_the_honest_negative_report_ships_when_the_chain_shows_blocked_attempts():
    report = _pass_two_report()
    answer = "\n".join(c.text for c in report.chunks)
    result = evaluate_low_evidence_policy(answer=answer, report=report, question="найди перелёт", blocked_attempts=2)
    assert result.triggered is False, result.reason
    assert result.answer == answer
    assert "honest_negative" in result.reason


def test_without_blocked_attempts_the_gate_behaves_as_before():
    report = _pass_two_report()
    answer = "\n".join(c.text for c in report.chunks)
    result = evaluate_low_evidence_policy(answer=answer, report=report, question="найди перелёт", blocked_attempts=0)
    assert result.triggered is True


def test_a_positive_claim_is_never_rescued_by_the_rule():
    report = _report([
        _chunk(POSITIVE, "subagent_asserted"),
        _chunk("Второй вариант — Lufthansa за 240 долларов.", "subagent_asserted"),
        _chunk("Третий — Wizz за 190.", "subagent_asserted"),
        _chunk("Четвёртый — Ryanair за 150.", "unverified"),
        _chunk("Пятый — Austrian за 260.", "unverified"),
        _chunk("Шестой — ITA за 270.", "unverified"),
        _chunk("Седьмой — Aegean за 230.", "unverified"),
        _chunk("Ручная кладь Flydubai — 7 кг.", "verified"),
    ])
    answer = "\n".join(c.text for c in report.chunks)
    # Control first: the gate must be red on its own (eight chunks clear the
    # min_total_chunks=8 floor; a seven-chunk report is never truncated).
    control = evaluate_low_evidence_policy(answer=answer, report=report, question="найди перелёт", blocked_attempts=0)
    assert control.triggered is True, control.reason
    result = evaluate_low_evidence_policy(answer=answer, report=report, question="найди перелёт", blocked_attempts=3)
    assert result.triggered is True, "prices with no support stay suppressed even when other attempts were blocked"
    assert "honest_negative" not in result.reason
