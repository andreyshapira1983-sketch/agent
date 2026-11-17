"""Три средних дефекта знаний и улик (проверка 16.11, средняя, группа g3)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.evidence import ProvenanceChain, evidence_from_tool_result
from core.injection_guard import annotate_suspicious_output, carries_suspicious_annotation
from core.knowledge_pipeline import KnowledgePipeline, _is_own_artifact, _values_equivalent


class _Saved:
    def __init__(self):
        self.rows = []

    def __call__(self, content, tags, source, rtype, scope):
        self.rows.append((content, tags))
        dec = SimpleNamespace(decision="save", reasons=(), policy_id="test")
        return dec, SimpleNamespace(id=f"mem_{len(self.rows)}", tags=tags)


def _fread(path, text):
    return evidence_from_tool_result(tool_name="file_read", arguments={"path": path}, output=text)


def _run(evs):
    chain = ProvenanceChain()
    for e in evs:
        chain.add(e)
    rem = _Saved()
    res = KnowledgePipeline().run(chain, remember=rem, auto_write_memory=True, require_verified=True)
    return res, rem


# 1 -------------------------------------------------------------------------
@pytest.mark.parametrize("a,b", [
    ("$5 per month", "€5 per month"),
    ("5%", "5"),
    ("available in all regions", "available in all regions except the eu"),
    ("supported on windows and linux", "supported on windows and linux only until 2025"),
    ("доступно во всех странах мира", "доступно во всех странах мира кроме ес"),
])
def test_different_facts_are_not_equivalent(a, b):
    assert _values_equivalent(a, b) is False


@pytest.mark.parametrize("a,b", [
    ("$5 per month", "$5  per month"),
    ("5 %", "5%"),
    ("a compiled language", "a compiled language used for systems"),  # обрезанная цитата
])
def test_same_facts_stay_equivalent(a, b):
    assert _values_equivalent(a, b) is True


def test_currency_and_qualifier_conflicts_are_seen_by_pipeline():
    res, _ = _run([_fread("docs/a.md", "The monthly fee is $5 per user.\n"),
                   _fread("docs/b.md", "The monthly fee is €5 per user.\n")])
    assert res.conflicts.count >= 1
    res, _ = _run([_fread("docs/a.md", "The service is available in all regions.\n"),
                   _fread("docs/b.md", "The service is available in all regions except the EU.\n")])
    assert res.conflicts.count >= 1


# 2 -------------------------------------------------------------------------
@pytest.mark.parametrize("loc", [
    "/opt/agent/data/state.md",
    "/opt/agent/logs/run.txt",
    "/opt/agent/proposals/selffix/x/edits.txt",
    "C:/x/agent-main/data/state.md",
    "data/state.md",
])
def test_own_artifact_recognised_in_ubuntu_install(loc):
    assert _is_own_artifact(loc) is True


@pytest.mark.parametrize("loc", [
    "/opt/agent/docs/guide.md",
    "/opt/agentx/data/state.md",
    "/home/user/data/state.md",
    "C:/work/agent/data/state.md",
])
def test_foreign_paths_stay_foreign(loc):
    assert _is_own_artifact(loc) is False


# 3 -------------------------------------------------------------------------
_CASES = {
    "python_probe": ({"code": "print(1)"},
                     {"exit_code": 0, "stdout": "port=8080\n", "duration_ms": 5}, "tool_output"),
    "read_logs": ({}, {"trace_id": "t1", "events": [{"event": "x", "msg": "hi"}]}, "log_event"),
    "diff_file": ({"path": "a.py"}, {"diff": "+x", "additions": 1, "deletions": 0}, "diff_preview"),
    "rss_fetch": ({"url": "https://e.x/feed"},
                  {"url": "https://e.x/feed", "entries": [{"title": "T", "url": "https://e.x/1"}]},
                  "web_page"),
    "memory_recall": ({"term": "x"},
                      {"records": [{"content": "c", "source": "user-explicit"}]}, "tool_output"),
}


@pytest.mark.parametrize("tool", sorted(_CASES))
def test_flagged_output_keeps_its_evidence(tool):
    args, out, kind = _CASES[tool]
    flagged = annotate_suspicious_output(tool, out, tool)
    assert isinstance(flagged, str)  # конверта нет — пришла строка-обёртка
    ev = evidence_from_tool_result(tool_name=tool, arguments=args, output=flagged)
    assert ev is not None
    assert ev.kind == kind
    assert ev.obtained_via == tool
    assert carries_suspicious_annotation(ev.excerpt)  # предупреждение едет дальше


def test_flagged_memory_recall_is_never_human():
    args, out, _ = _CASES["memory_recall"]
    flagged = annotate_suspicious_output("memory_recall", out, "memory_recall")
    ev = evidence_from_tool_result(tool_name="memory_recall", arguments=args, output=flagged)
    assert ev.origin == "agent-auto"


def test_plain_string_without_wrapper_still_refused():
    assert evidence_from_tool_result(tool_name="diff_file", arguments={}, output="+x") is None
