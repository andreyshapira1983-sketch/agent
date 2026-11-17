"""Два средних дефекта независимости улик (проверка 18.11, группа b).

A. Свой путь агента узнаётся после нормализации и по сравнению с настоящим
   корнем, а не по строке имени установки; shell_exec, find_in_files и
   list_dir, читающие data/ и logs/, тоже несут `agent-own`.
B. Слабый источник (forum/unknown, низкое доверие, `unverified`) не второй
   свидетель: то же правило слабости, что у KnowledgeWritePolicy.
"""
from __future__ import annotations

import pytest

from core.evidence import (
    _REPO_ROOT,
    _is_own_state_path,
    evidence_from_tool_result,
    evidence_is_independent,
)
from core.knowledge_pipeline import (
    ConflictResolver,
    KnowledgeWritePolicy,
    _claim_id,
    _is_own_artifact,
)
from core.source_registry import ClaimRecord, SourceRecord, SourceRegistry


# A: путь -------------------------------------------------------------------
@pytest.mark.parametrize("path", [
    "data/state.json",
    "./data/state.json",
    "docs/../data/state.json",
    "subdir/../logs/run.log",
    "DATA\\state.json",
    ".\\logs\\x.log",
    "data",
    "C:/x/agent-main/data/state.json",
    "/opt/agent/logs/a.log",
    (_REPO_ROOT / "data" / "state.json").as_posix(),
    str(_REPO_ROOT / "proposals" / "x.txt"),
])
def test_own_state_paths_after_normalisation(path):
    assert _is_own_state_path(path) is True


@pytest.mark.parametrize("path", [
    "build/server.log",
    "docs/data/notes.md",
    "data/../docs/a.md",
    "../data/state.json",           # вне рабочей папки
    "D:/work/agent/data/state.json",  # чужая папка data, не корень агента
    "/home/u/agent/data/x.json",
    "/x/opt/agent/data/x.json",
    "",
])
def test_foreign_paths_stay_foreign(path):
    assert _is_own_state_path(path) is False


def test_configured_workspace_is_a_root(monkeypatch, tmp_path):
    ws = tmp_path / "myws"
    monkeypatch.setenv("AGENT_WORKSPACE", str(ws))
    assert _is_own_state_path((ws / "data" / "state.json").as_posix()) is True
    assert _is_own_state_path((tmp_path / "other" / "data" / "s.json").as_posix()) is False


def test_knowledge_pipeline_uses_the_same_path_rule():
    assert _is_own_artifact("docs/../data/state.md") is True
    assert _is_own_artifact("D:/work/agent/data/state.md") is False


# A: дороги улик --------------------------------------------------------------
def _indep(tool, args, out):
    ev = evidence_from_tool_result(tool_name=tool, arguments=args, output=out)
    assert ev is not None
    return evidence_is_independent(ev)


def test_file_read_through_dotdot_is_own():
    assert _indep("file_read", {"path": "docs/../data/state.json"}, "budget is 5") is False


@pytest.mark.parametrize("argv", [
    ["type", "data\\state.json"],
    ["cat", "./logs/run.log"],
    ["python", "-c", "print(open('data/state.json').read())"],
    ["grep", "--file=proposals/x.txt", "y"],
    ["cmd", "/c", "type data\\state.json"],
])
def test_shell_reading_own_state_is_not_independent(argv):
    assert _indep("shell_exec", {}, {"argv": argv, "stdout": "budget is 5", "exit_code": 0}) is False


def test_shell_command_in_args_is_judged_too():
    ev = evidence_from_tool_result(tool_name="shell_exec", arguments={"command": "cat data/state.json"},
                                   output={"command": "cat data/state.json", "stdout": "x", "exit_code": 0})
    assert ev.origin == "agent-own"


@pytest.mark.parametrize("argv", [["cat", "docs/guide.md"], ["git", "log", "-1"], ["python", "--version"]])
def test_ordinary_shell_stays_independent(argv):
    assert _indep("shell_exec", {}, {"argv": argv, "stdout": "ok", "exit_code": 0}) is True


def test_find_in_files_under_data_is_own():
    assert _indep("find_in_files", {"path": "data"}, "data/state.json:3: budget is 5") is False


def test_find_in_files_hit_in_own_file_is_own():
    assert _indep("find_in_files", {"path": "."}, "proposals/x/edits.txt:1: budget is 5") is False


def test_find_in_files_over_docs_stays_independent():
    assert _indep("find_in_files", {"path": "."}, "docs/a.md:3: budget is 5") is True


def test_list_dir_of_own_folder_is_own():
    assert _indep("list_dir", {"path": "data"}, "state.json") is False
    assert _indep("list_dir", {"path": "docs"}, "a.md") is True


# B: слабый свидетель ---------------------------------------------------------
def _resolve(weak_type, weak_status, weak_trust):
    r = SourceRegistry()
    strong = SourceRecord(id="web_page:https://docs.python.org/a", type="documentation", title="d",
                          locator="https://docs.python.org/a", trust_level=0.9)
    weak = SourceRecord(id="web_page:https://forum.example/t/1", type=weak_type, title="f",
                        locator="https://forum.example/t/1", trust_level=weak_trust)
    text = "The default port is 8080."
    r.add_source(strong)
    r.add_source(weak)
    r.add_claim(ClaimRecord(id=_claim_id(strong.locator, text), source_id=strong.id, text=text,
                            locator=strong.locator, confidence=0.9, status="extracted"))
    r.add_claim(ClaimRecord(id=_claim_id(weak.locator, text), source_id=weak.id, text=text,
                            locator=weak.locator, confidence=0.9, status=weak_status))
    out, _ = ConflictResolver().resolve(r)
    return {c.source_id: c.status for c in out.claims}[strong.id]


@pytest.mark.parametrize("weak_type,status,trust", [
    ("forum", "unverified", 0.3),
    ("unknown", "unverified", 0.2),
    ("forum", "extracted", 0.9),          # тип слаб при любом доверии
    ("documentation", "extracted", 0.3),  # доверие ниже порога политики
    ("documentation", "unverified", 0.9),  # статус, который политика отвергает
])
def test_weak_witness_does_not_verify(weak_type, status, trust):
    assert _resolve(weak_type, status, trust) == "extracted"


def test_strong_witness_still_verifies():
    assert _resolve("documentation", "extracted", 0.9) == "verified"


def test_witness_threshold_is_the_policy_threshold():
    """Порог один: строгая политика, переданная резолверу, режет и свидетеля."""
    r = SourceRegistry()
    a = SourceRecord(id="s:a", type="documentation", title="a", locator="https://a.org/x", trust_level=0.9)
    b = SourceRecord(id="s:b", type="documentation", title="b", locator="https://b.org/x", trust_level=0.7)
    text = "The default port is 8080."
    for s in (a, b):
        r.add_source(s)
        r.add_claim(ClaimRecord(id=_claim_id(s.locator, text), source_id=s.id, text=text,
                                locator=s.locator, confidence=0.9, status="extracted"))
    strict = ConflictResolver(policy=KnowledgeWritePolicy(min_source_trust=0.8))
    out, _ = strict.resolve(r)
    status = {c.source_id: c.status for c in out.claims}
    assert status["s:a"] == "extracted"  # b (0.7) слаб для этой политики
    assert status["s:b"] == "extracted"  # и сам не повышается: слишком слаб, чтобы сохранить (18.11)
