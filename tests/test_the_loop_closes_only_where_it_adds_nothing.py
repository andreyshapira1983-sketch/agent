"""Что агент вправе применить сам, без человека, — и почему именно это.

Замер, отвергнутые варианты и границы: MIR-173 в docs/audit/MASTER_ISSUE_REGISTRY.md.

Правило узкое намеренно. Оно НЕ даёт новых полномочий: создавать файлы агент уже
может через `file_write` — без тестов и без отката. Полоса делает то же самое с
прицельными тестами, полной батареей и автоматическим откатом, то есть добавляет
проверку к уже разрешённому действию.
"""
from __future__ import annotations

from core.self_apply_lane import FileChange, SelfApplyProposal, autonomous_execution_verdict


def _proposal(*files: FileChange) -> SelfApplyProposal:
    return SelfApplyProposal(files=files, reason="черновик", test_paths=("tests",))


def _new(path: str) -> FileChange:
    """Файл, которого на момент подачи НЕ БЫЛО, и это проверено."""
    return FileChange(path=path, content="# заметка\n", base_sha256="", base_checked=True)


def _existing(path: str) -> FileChange:
    return FileChange(path=path, content="x\n", base_sha256="deadbeef", base_checked=True)


def test_touching_an_existing_file_is_never_autonomous() -> None:
    """Перезапись — необратимое действие, и оно принадлежит человеку (I-1)."""
    ok, reason = autonomous_execution_verdict(
        _proposal(_existing("knowledge/doctrine/future/MIGRATION_PATH.md"))
    )

    assert ok is False
    assert "existed" in reason


def test_code_and_tests_are_never_autonomous() -> None:
    """Новый ТЕСТ — это новый судья, а приёмка идёт той же батареей.

    MIR-139 держит это открытым сознательно: политика, аршин и приёмка сходятся
    в одном действии. Нынешнее лечение — назвать риск ЧЕЛОВЕКУ, а автономное
    исполнение человека убирает. Значит код и тесты сюда не входят.
    """
    for path in ("core/thing.py", "tests/test_thing.py", "tools/thing.py"):
        ok, reason = autonomous_execution_verdict(_proposal(_new(path)))
        assert ok is False, path
        assert "document" in reason


def test_a_document_the_code_reads_is_never_autonomous() -> None:
    """Документ здесь бывает ВЛАСТЬЮ, и хартия лежит в той же папке.

    `knowledge/doctrine/future/CORPORATE_MODEL.md` выбирает агенту цели. Правило
    «только создание» её и так не пустит, но опираться на побочный эффект нельзя:
    запрет назван прямо.
    """
    ok, reason = autonomous_execution_verdict(
        _proposal(_new("knowledge/doctrine/future/CORPORATE_MODEL.md"))
    )

    assert ok is False
    assert "authority" in reason


def test_an_unstamped_proposal_is_refused() -> None:
    """«Не проверяли» — это не «можно». Незнание не даёт разрешения.

    Заявки, поданные до MIR-168, отметки происхождения не несут. Считать их
    созданием — значит выдать непроверенное за проверенное.
    """
    ok, reason = autonomous_execution_verdict(
        _proposal(FileChange(path="docs/x.md", content="x"))
    )

    assert ok is False
    assert "unstamped" in reason
