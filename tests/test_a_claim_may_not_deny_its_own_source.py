"""Утверждение не может опираться на улику, которую само отрицает.

ЗАМЕР (MIR-141). По оси полярности дискриминация стояла на J = 0,00:
предложение, которое повторяет свой источник и тут же говорит «— неверно, это
не так», принималось как подтверждённое в 100 % случаев.

ПОЧЕМУ ОТКАЗ БЫЛ СНАЧАЛА. Наивное правило «в утверждении есть отрицание —
демотируй» ломается о русский язык: «не превысила», «не X, а Y», «не менее
20» — отрицание стоит всюду и почти никогда не спорит с источником. Такое
правило произвело бы ровно тот класс ложных отказов, на избегание которого
ушла вся доказательная работа этого дня.

ЧТО ИЗМЕНИЛО ДЕЛО. Полярность надо считать У ОБОИХ — у утверждения И у улики.
И судить не всякое отрицание, а МЕТА-ОТРИЦАНИЕ: «неверно», «это не так», «на
самом деле нет», «is false», «is not true». Это отрицание ПРОПОЗИЦИИ целиком, а
не члена предложения, и оно лексикон, а не вывод — тот же приём, каким
`claim_arithmetic` узнаёт свои формы.

ГРАНИЦА, КОТОРАЯ ДЕЛАЕТ ПРАВИЛО ЧЕСТНЫМ. Если улика САМА несёт отрицание,
утверждение его лишь передаёт, и демотировать нечего. Поэтому сравниваются
полярности, а не ищется маркер в одном тексте.
"""
from __future__ import annotations

import pytest

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import verify

URL = "https://example.org/report"
ASSERTS = "Средняя задержка составила 20 миллисекунд."
DENIES = "Утверждение о задержке в 20 миллисекунд неверно и не подтверждается."


def _verdict(claim: str, excerpt: str = ASSERTS) -> str:
    chain = ProvenanceChain()
    chain.add(make_evidence(
        kind="web_page", source_id=URL, obtained_via="web_fetch",
        claim="fetched page", excerpt=excerpt,
    ))
    report = verify(
        answer=f"{claim} [web:{URL}]", chain=chain, llm=None,
        expects_contract_headers=False,
    )
    return report.chunks[0].verdict


@pytest.mark.parametrize("claim", [
    "Средняя задержка составила 20 миллисекунд — неверно, это не так.",
    "Средняя задержка составила 20 миллисекунд, но на самом деле это не так.",
    "Средняя задержка составила 20 миллисекунд — данные неверны.",
])
def test_a_claim_that_denies_its_source_is_not_verified(claim: str) -> None:
    assert _verdict(claim) != "verified", (
        f"«{claim}» ссылается на улику, которую само объявляет неверной, "
        "и принято как подтверждённое"
    )


def test_reporting_a_sources_own_denial_survives() -> None:
    """Граница: улика сама отрицает, утверждение лишь передаёт."""
    claim = "Задержка в 20 миллисекунд неверна и не подтверждается."

    assert _verdict(claim, DENIES) == "verified", (
        "передача отрицания, которое несёт сама улика, демотирована — правило "
        "сравнивает полярности, а не ищет маркер в одном тексте"
    )


def test_the_gate_stays_silent_without_a_shared_subject() -> None:
    """Защита от чужого спора, закреплённая прямо на функции.

    Ломка этой ветки не покраснела ни на одном случае выше — значит она была
    незакреплена, а незакреплённая защита неотличима от мёртвого кода. Гейт
    судит спор утверждения СО СВОЕЙ уликой; отрицание чего-то постороннего —
    вопрос седьмого гейта (не та тема), а не этого.
    """
    from core.evidence import make_evidence
    from core.verifier_absence import denies_own_evidence_reason

    ev = make_evidence(
        kind="web_page", source_id=URL, obtained_via="web_fetch",
        claim="fetched page", excerpt=ASSERTS,
    )

    assert denies_own_evidence_reason(
        "Прогноз погоды в Мурманске неверный.", ev, "web"
    ) is None, "гейт вмешался в спор, к которому его улика не относится"

    assert denies_own_evidence_reason(
        "Средняя задержка в 20 миллисекунд — неверно.", ev, "web"
    ) is not None
