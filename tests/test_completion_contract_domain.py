"""Ноль обязательств обязан объяснить, чей он: запроса или извлекателя.

ЖИВОЙ СЛУЧАЙ 2026-08-10. Запрос на 4155 знаков перечислял двенадцать разделов
отчёта, требовал провести эксперимент и запрещал обращаться к другой модели.
`completion_contract` доложил `obligations=[] ambiguities=[]
needs_clarification=False` — то же самое, что он докладывает на «привет».

Причина не в поломке: `derive_completion_contract` строит обязательства ТОЛЬКО
по путям к файлам с действием create/modify. Двенадцать разделов отчёта, опыт и
запрет он не умеет представлять вовсе. Но «обязательств нет» и «этот класс
обязательств я не умею видеть» выходили одним и тем же пустым результатом, и
потребитель читал второе как первое.

Правило программы картирования: любой ноль объясняется ДО того, как его
истолковали. Здесь и пинится это объяснение.
"""
from __future__ import annotations

from core.completion_contract import derive_completion_contract

_REPORT_REQUEST = (
    "Prove that you exist. Perform at least one fresh challenge-response "
    "experiment. Do not use another LLM or external reviewer. At the end report "
    "separately: what you can prove exists; what you can prove is executing now; "
    "what persists when this process dies; the precise referent of 'I'."
)
_RU_REPORT_REQUEST = (
    "Проанализируй сообщение и определи: на каком языке оно написано; какую роль "
    "играет латинский алфавит; меняет ли транслитерация значение. "
    "Не исправляй систему и не меняй файлы. Отвечай по-русски."
)


def test_a_file_request_still_produces_its_obligation() -> None:
    """ПРЕДУСЛОВИЕ: прежняя область не сузилась ни на шаг."""
    contract = derive_completion_contract("создай core/foo.py")
    assert contract.obligations
    assert contract.obligations[0].deliverable == "file_exists"
    assert not contract.unsupported_deliverables


def test_an_empty_request_is_honestly_empty() -> None:
    """Ноль без причины остаётся нулём: не всякий запрос что-то должен."""
    contract = derive_completion_contract("привет, как дела")
    assert not contract.obligations
    assert not contract.unsupported_deliverables
    assert contract.coverage == "complete"


def test_report_sections_are_named_as_unsupported_not_absent() -> None:
    """ГЛАВНОЕ: то, что видно глазами, обязано быть видно и в записи."""
    contract = derive_completion_contract(_REPORT_REQUEST)
    assert not contract.obligations, (
        "извлекатель начал выдумывать обязательства, которых не умеет проверять"
    )
    kinds = {u.kind for u in contract.unsupported_deliverables}
    assert "report_sections" in kinds, (
        "двенадцать затребованных разделов отчёта исчезли бесследно"
    )
    assert "experiment" in kinds
    assert "prohibition" in kinds
    assert contract.coverage == "partial"


def test_russian_requests_are_read_too() -> None:
    """Оператор пишет по-русски; извлекатель, слепой к его языку, бесполезен."""
    contract = derive_completion_contract(_RU_REPORT_REQUEST)
    kinds = {u.kind for u in contract.unsupported_deliverables}
    assert "report_sections" in kinds
    assert "prohibition" in kinds


def test_a_transliterated_request_is_read_as_well() -> None:
    """Латиница — способ записи, не другой язык (замер 2026-08-09/10)."""
    contract = derive_completion_contract(
        "Opredeli na kakom yazyke napisano soobshchenie. "
        "Ne ispravlyay sistemu i ne menyay fayly. Otvechay po-russki."
    )
    kinds = {u.kind for u in contract.unsupported_deliverables}
    assert "prohibition" in kinds


def test_the_unsupported_entries_quote_their_own_evidence() -> None:
    """Запись без цитаты недоказуема — потребитель обязан видеть, на чём она."""
    contract = derive_completion_contract(_REPORT_REQUEST)
    for entry in contract.unsupported_deliverables:
        assert entry.evidence.strip(), f"{entry.kind} записан без улики"
        assert entry.evidence.lower() in _REPORT_REQUEST.lower()


def test_coverage_is_in_the_log_payload() -> None:
    """Различие обязано пережить переход в журнал, иначе его снова не видно."""
    payload = derive_completion_contract(_REPORT_REQUEST).to_log_payload()
    assert payload["coverage"] == "partial"
    assert payload["unsupported_deliverables"]
    assert payload["obligations"] == []


def test_unsupported_deliverables_do_not_become_a_clarification_request() -> None:
    """Не путать «не умею проверить» с «не понял, о чём речь».

    Первое — признание границы извлекателя, и уточнять у оператора нечего.
    Второе — настоящая двусмысленность. Слить их значило бы спрашивать
    оператора о том, что он сформулировал совершенно ясно.
    """
    contract = derive_completion_contract(_REPORT_REQUEST)
    assert contract.unsupported_deliverables
    assert not contract.needs_clarification
