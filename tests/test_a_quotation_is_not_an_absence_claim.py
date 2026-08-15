"""Цитата ошибки — не утверждение агента, что чего-то нет.

Background: docs/CODE_NOTES.md, "A quotation is not an assertion".
"""
from __future__ import annotations

import hashlib

import pytest

from core.evidence import Evidence, ProvenanceChain
from core.verifier_core import verify
from core.verifier_utils import absence_refuted_by_excerpt, asserts_absence

#: Дословно из живой проверки 2026-08-15: три ВЕРНЫХ утверждения получили
#: `[claim-refuted]`, потому что пересказывали записанную ошибку.
_QUOTED = (
    "В журнале найдено событие error с code=file_not_found и сообщением вида "
    "FileNotFoundError: File not found: core/diagnostics.py"
)
_EXCERPT = (
    '{"event": "error", "payload": {"message": "FileNotFoundError: '
    'File not found: core/diagnostics.py", "code": "file_not_found"}}'
)


def test_the_measured_report_is_not_an_absence_claim():
    """Маркер «not found» лежит в хвосте исключения — чужим голосом."""
    assert asserts_absence(_QUOTED) is False


def test_the_measured_report_is_not_refuted_by_its_own_quote():
    """Имя файла неизбежно есть в улике — внутри той же цитаты. Опровергать
    этим значит наказывать правдивый пересказ собственной ошибки.
    """
    assert absence_refuted_by_excerpt(_QUOTED, _EXCERPT) is False


@pytest.mark.parametrize("claim", [
    "FileNotFoundError: File not found: core/diagnostics.py",
    "Прогон упал с ModuleNotFoundError: No module named 'requests'",
    "В логе стоит «файл не найден» рядом с trace_36b9d8df",
    'Сообщение гласит "handler not implemented", см. журнал',
    "Тест упал: `AssertionError: not present in output`",
])
def test_unseen_quotation_shapes_are_not_absence_claims(claim: str):
    """Формы, под которые правило не подгоняли: эхо строки, другое исключение,
    ёлочки, прямые кавычки, апострофы. Класс один — маркер внутри цитаты.
    """
    assert asserts_absence(claim) is False


@pytest.mark.parametrize("claim", [
    "В файле отсутствует поле с именем `quantum_flux`.",
    "Код не обрабатывает случаи, когда уверенность слишком низкая",
    "The module does not exist in this build",
    "Не найдено ни одного совпадения по запросу",
])
def test_own_voice_absence_still_fires(claim: str):
    """Улов не отдан: отсутствие собственным голосом распознаётся как раньше —
    и утренние гейты (d)/(e) продолжают на нём работать.
    """
    assert asserts_absence(claim) is True


def _verdicts(claim: str) -> list[str]:
    # Ярлык file:… разрешается тем же путём, что в проверенной оснастке
    # tests/test_absence_is_not_certified_by_a_citation.py — иначе цитата не
    # разрешилась бы вовсе, и оба теста ниже прошли бы впустую, ни разу не
    # дойдя до гейтов, которые здесь проверяются.
    ev = Evidence(
        id="ev_1", kind="file", source_id="logs/trace_x.jsonl",
        obtained_via="file_read",
        content_hash=hashlib.sha256(_EXCERPT.encode()).hexdigest(),
        fetched_at="2026-08-15T11:00:00Z", confidence=1.0,
        claim="audit log excerpt", excerpt=_EXCERPT,
    )
    chain = ProvenanceChain()
    chain.add(ev)
    report = verify(
        answer=f"{claim} [file:logs/trace_x.jsonl]",
        chain=chain,
        expects_contract_headers=False,
    )
    return [c.verdict for c in report.chunks]


def test_the_live_path_no_longer_calls_the_true_report_a_lie():
    """Живой путь целиком: тот же кусок через `verify()` не получает ни
    `refuted`, ни `[absence-unverifiable]`.
    """
    verdicts = _verdicts(_QUOTED)

    assert "refuted" not in verdicts, "правдивый пересказ ошибки снова объявлен ложью"
    assert "topic_supported_but_claim_unverified" not in verdicts


def test_an_own_voice_false_absence_is_still_refuted_by_the_evidence():
    """Ломка наоборот: своё «в журнале нет события error», когда событие в
    улике есть, обязано опровергаться как раньше.
    """
    verdicts = _verdicts("В журнале нет события error про `core/diagnostics.py`")

    assert "refuted" in verdicts
