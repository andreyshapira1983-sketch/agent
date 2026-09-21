"""Файл своей папки, вынесенный в «Не подтверждено», открывается, а не остаётся.

Замер 2026-09-21: 44 из 74 блоков «Не подтверждено» в чате были о его же
файлах, журналах и инструментах. Эпизод 08:41 — «не проверял, требует ли
file_write подтверждения» при tools/file_write.py рядом; эпизод 08:48 — «не
открывал полный текст core/smart_memory.py», хотя вопрос прямо просил открыть.
Это дверь, а не стена: такой файл дочитывается и черновик собирается заново.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.answer_format import unverified_own_paths
from core.loop_synthesis import AgentLoopSynthesis
from core.replan import ReplanTrigger
from tools.file_read import FileReadTool

_DRAFT = (
    "Conclusion:\nЧастично.\n\n"
    "Facts:\n- Инструмент записи существует [file:tools/file_write.py:1-60].\n\n"
    "Unverified:\n"
    "- Я не проверял, требует ли `tools/file_write.py` подтверждения оператора.\n"
    "- Не открывал полный текст core/memory.py.\n"
    "- Не сверял data/nothing_here.jsonl.\n"
)


def _workspace(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "core").mkdir()
    (tmp_path / "tools" / "file_write.py").write_text("NEEDS_APPROVAL = True\n", encoding="utf-8")
    (tmp_path / "core" / "memory.py").write_text("def recall():\n    return 1\n", encoding="utf-8")
    return tmp_path


def test_only_existing_unread_own_files_are_named(tmp_path) -> None:
    ws = _workspace(tmp_path)
    got = unverified_own_paths(_DRAFT, root=ws, already_read=["file:tools/file_write.py:1-60"])
    assert got == ["tools/file_write.py", "core/memory.py"], "окно — не чтение целиком"
    assert unverified_own_paths(_DRAFT, root=ws,
                                already_read=["file:tools/file_write.py", "file:core/memory.py"]) == []
    assert unverified_own_paths("Conclusion:\nВсё проверено.\n", root=ws) == []


class _Chain(list):
    def add(self, ev) -> None:
        self.append(ev)


def test_the_named_files_are_read_and_the_answer_rebuilt(tmp_path) -> None:
    ws = _workspace(tmp_path)
    logged: list[tuple[str, dict]] = []
    loop = SimpleNamespace(
        registry=SimpleNamespace(get=lambda name: FileReadTool(workspace_root=ws)),
        _last_synth_degraded=False,
        _sensor_failed=lambda *a: (_ for _ in ()).throw(AssertionError(a)),
        log=SimpleNamespace(log=lambda event, payload: logged.append((event, payload))),
        last_provenance=_Chain(),
    )
    st = SimpleNamespace(draft_answer=_DRAFT, artifacts={}, failure_history=[])
    AgentLoopSynthesis._read_what_it_left_unverified(
        loop, st, lambda attempt: "Conclusion:\nПодтверждение требуется [file:tools/file_write.py].")

    assert set(st.artifacts) == {"file:tools/file_write.py", "file:core/memory.py"}
    assert "NEEDS_APPROVAL" in str(st.artifacts["file:tools/file_write.py"]["output"])
    assert len(loop.last_provenance) == 2
    trigger = st.failure_history[-1]
    assert isinstance(trigger, ReplanTrigger) and trigger.code == "unverified_own_file"
    assert st.draft_answer.startswith("Conclusion:\nПодтверждение требуется")
    assert logged == [("unverified_own_files_read",
                       {"paths": ["tools/file_write.py", "core/memory.py"], "rewritten": True})]


def test_nothing_named_nothing_done(tmp_path) -> None:
    ws = _workspace(tmp_path)
    loop = SimpleNamespace(registry=SimpleNamespace(get=lambda name: FileReadTool(workspace_root=ws)),
                           _last_synth_degraded=False)
    st = SimpleNamespace(draft_answer="Conclusion:\nГотово.\n", artifacts={}, failure_history=[])
    AgentLoopSynthesis._read_what_it_left_unverified(
        loop, st, lambda attempt: (_ for _ in ()).throw(AssertionError("лишний вызов модели")))
    assert st.artifacts == {} and st.failure_history == []
