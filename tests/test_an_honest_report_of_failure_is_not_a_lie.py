"""«Слов нет в вырезке» — не то же самое, что «ты соврал».

Замечание оператора 2026-09-21, и оно оказалось про механику, а не про
характер: «у него то восемь из двенадцати, то семь из десяти, и никогда
десять из десяти — он никогда не уверен».

Замер по разговору того вечера: 53 ответа несут хвост проверки, ПОЛНЫХ (где
подтверждено всё) ровно два, суммарно 377 утверждений из 549. Клеймо
`claim-refuted` стояло 37 раз. Пять образцов взяты наугад и проверены
руками — все пять оказались ИСТИННЫМИ докладами агента о собственной
работе:

    «прогон pytest завершился с exit_code 1: passed=0, failed=2»
    «ворота одобрения вернули unavailable для file_write»
    «запуск plan_split не удался: ModuleNotFoundError»

Механизм: отличительные слова утверждения обязаны ДОСЛОВНО встретиться в
вырезке улики (`absent_literal_reason`). Агент ссылался на самое близкое,
что у него было; улика по теме верная, а его точных слов в ней нет — и
`verifier_core` ставил `refuted`. То есть честный доклад о собственной
неудаче получал клеймо лжи, двадцать раз за вечер.

Различение назвал сам агент, когда с ним об этом спорили, и оно точное:
`absence_refuted_by_evidence` — настоящее опровержение, потому что улика
СОДЕРЖИТ то, чьё отсутствие утверждали. А `cited_literal_absent` доказывает
ровно одно: слов нет в вырезке. Между «улика не содержит этих слов» и
«утверждение ложно» лежит пропасть, и до этой правки код её не видел.

Полярность в файле объявлена верно («доказанная ложь — не разновидность
"не подтверждено"») и до сих пор применялась только в одну сторону.
"""
from __future__ import annotations

from core.verifier_core import _UNSUPPORTED_REASONS


def test_a_missing_support_does_not_prove_a_lie() -> None:
    """Нехватка подпорки — не ложь; приписывание чужого источника — ложь.

    Разделено 2026-09-21 после того, как прогон поймал: прежняя редакция
    этой правки переехала решение оператора, у которого был свой тест.
    Там разбирался ДРУГОЙ случай — «producer живёт в X [file:Y]», где Y
    такого не говорит. Это приписывание, ложь о происхождении, и оно
    осталось опровержением. Код `cited_support_missing` описывает только
    случай, когда источник тот самый, а дословных слов в вырезке нет.
    """
    assert "cited_support_missing" in _UNSUPPORTED_REASONS
    assert "cited_literal_absent" not in _UNSUPPORTED_REASONS


def test_a_real_contradiction_stays_a_lie() -> None:
    """Улика, СОДЕРЖАЩАЯ то, чьё отсутствие утверждали, — опровержение."""
    assert "absence_refuted_by_evidence" not in _UNSUPPORTED_REASONS


def test_arithmetic_contradictions_stay_lies() -> None:
    """Числа, которые не сходятся, доказывают ложь, а не её отсутствие."""
    for code in ("count_mismatch", "sum_mismatch", "average_mismatch",
                 "restated_number_changed", "comparison_false"):
        assert code not in _UNSUPPORTED_REASONS, code


def test_the_agents_own_failure_report_is_not_refuted() -> None:
    """Живой случай того вечера, целиком.

    Доклад о собственной неудаче с ссылкой, чья вырезка этих слов не несёт,
    больше не получает `claim-refuted`: он остаётся неподтверждённым, и это
    честная полярность.
    """
    from core.evidence import ProvenanceChain
    from core.verifier import verify

    claim = ("Ворота одобрения вернули unavailable для file_write "
             "[runtime:approval_provider].")
    report = verify(answer=claim, chain=ProvenanceChain(),
                    expects_contract_headers=False)
    assert report.refuted_chunks == 0, report.to_log_payload()
    assert "[claim-refuted]" not in report.annotated_answer
