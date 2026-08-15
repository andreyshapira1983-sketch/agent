"""Разрешившаяся ссылка не доказывает, что чего-то НЕТ.

Background: docs/CODE_NOTES.md, "Absence was certified by a resolved citation".
"""
from __future__ import annotations

import hashlib

import pytest

from core.evidence import Evidence, ProvenanceChain
from core.verifier_core import verify
from core.verifier_utils import absence_certifiable

#: Дословно из автономного прогона 2026-08-15. Обработка ЕСТЬ —
#: core/self_repair.py:117 ставит `low_confidence` и выходит, — но утверждение
#: прошло как подтверждённое, и эпизод записан успехом.
_FALSE_FINDING = "Код не обрабатывает случаи, когда уверенность предложения слишком низкая"

#: Настоящий код того же файла: механизм на месте.
_EXCERPT = (
    "        if proposal.confidence < self.min_confidence:\n"
    "            report.status = \"low_confidence\"\n"
    "            self.agent.log.log(\"self_repair_confidence_gate\", {...})\n"
    "            self._finish(report)\n"
    "            return report\n"
)


def _verdicts(claim: str, excerpt: str = _EXCERPT) -> list[str]:
    ev = Evidence(
        id="ev_1", kind="file", source_id="core/self_repair.py",
        obtained_via="file_read",
        content_hash=hashlib.sha256(excerpt.encode()).hexdigest(),
        fetched_at="2026-08-15T06:00:00Z", confidence=1.0,
        claim="self_repair source", excerpt=excerpt,
    )
    chain = ProvenanceChain()
    chain.add(ev)
    report = verify(
        answer=f"{claim} [file:core/self_repair.py]",
        chain=chain,
        expects_contract_headers=False,
    )
    return [c.verdict for c in report.chunks]


def test_the_measured_false_finding_is_not_certified():
    """Ровно то утверждение, что прошло 2026-08-15 с вердиктом `verified`."""
    verdicts = _verdicts(_FALSE_FINDING)

    assert "verified" not in verdicts, (
        "ложное отсутствие снова сертифицировано разрешившейся ссылкой"
    )


@pytest.mark.parametrize("claim", [
    "В core/self_repair.py не предусмотрена обработка низкой уверенности",
    "Модуль не поддерживает откат применённой правки",
    "Функция не учитывает пустой список предложений",
    "The module does not implement a rollback path",
    "There is no handling for the empty case",
])
def test_unseen_absence_shapes_are_not_certified_either(claim: str):
    """Формы, под которые правило не подгоняли: другие глаголы, другой язык.
    Класс один — утверждение о том, чего нет.
    """
    assert absence_certifiable(claim, "") is False


@pytest.mark.parametrize("claim", [
    "В методе run проверяется уверенность предложения",
    "Файл определяет класс SelfRepairController",
    "The gate sets status to low_confidence and returns",
])
def test_a_positive_claim_is_untouched(claim: str):
    """Улов не отдан: правило трогает только утверждения об отсутствии."""
    assert absence_certifiable(claim, "") is True


def test_a_positive_claim_still_gets_verified():
    """Живой путь целиком, а не только предикат: обычное утверждение по-прежнему
    проходит проверку.
    """
    verdicts = _verdicts("Статус устанавливается в low_confidence")

    assert "verified" in verdicts


def test_the_claim_is_not_called_a_lie():
    """Несертифицируемое — не опровергнутое. Объявить ложью то, чего не смогли
    проверить, было бы той же ошибкой с обратным знаком.
    """
    verdicts = _verdicts(_FALSE_FINDING)

    assert "refuted" not in verdicts
    assert "topic_supported_but_claim_unverified" in verdicts
