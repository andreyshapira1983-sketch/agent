"""A terminal citation refusal must not inherit the rejected draft's success.

2026-09-19: a draft with ONE unmatched citation among verified claims is no longer
withheld: the claim is cut and the rest ships (`citation_excised`). The withheld
path is now exercised by a draft whose every claim cites a source this turn never
saw (`_ALL_BAD`); the excision path by `_BAD`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.answer_format import format_human_response
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.models import ToolCall, ToolResult
from core.policy import PolicyGate
from core.smart_memory import (
    EpisodeRecord,
    EpisodicMemoryStore,
    admit_for_storage,
    episode_from_agent_cycle,
)
from core.tool_receipts import receipt_context, record_tool_invoke_receipt
from core.unsupported_claims import apply_answer_enforcement
from core.verifier import verify
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.list_dir import ListDirTool

_FACTS = [f"Module {name} was found in the catalogue." for name in (
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
)]
_GOOD = (
    f"Conclusion: {_FACTS[0]} [file:doc.txt]\nFacts:\n"
    + "\n".join(f"- {fact} [file:doc.txt]" for fact in _FACTS[1:])
    + "\nSources:\n1. file:doc.txt\nConfidence: medium\n"
    "Unverified: nothing\nSafety: nothing"
)
_BAD = _GOOD.replace(
    "\nSources:",
    "\n- Missing module is available. [file:missing.txt]\nSources:",
)
#: Every claim cites a file this turn never read: nothing verified would remain.
_ALL_BAD = _GOOD.replace("[file:doc.txt]", "[file:missing.txt]")
_EXPECTED = {"good": (8, 0), "bad": (8, 1), "all_bad": (0, 8)}


class _DeclaringLLM(FakeLLM):
    def complete(self, system, user, **kwargs):
        body = super().complete(system, user, **kwargs)
        nonce = re.search(r"\[\[agent\.completion:([a-f0-9]+):<token>\]\]", system)
        assert nonce is not None
        return f"{body}\n[[agent.completion:{nonce[1]}:partially_achieved]]"


def _run(tmp_path: Path, monkeypatch, *, rejected=True, draft: str | None = None):
    draft = draft or ("all_bad" if rejected else "good")
    monkeypatch.setenv("AGENT_ENFORCE_UNSUPPORTED_CLAIMS", "off")
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=tmp_path))
    registry.register(ListDirTool(workspace_root=tmp_path))
    trace_id = new_trace_id()
    memory = WorkingMemory()
    sources = []
    for tool, path, output in (
        ("file_read", "doc.txt", "\n".join(_FACTS)),
        ("list_dir", "tools/", "alpha.py\n"),
        ("list_dir", "core/", "beta.py\n"),
    ):
        label = f"{'file' if tool == 'file_read' else tool}:{path}"
        args = {"path": path}
        with receipt_context(trace_id=trace_id, workspace=tmp_path):
            call = ToolCall(action_id="cached-read", tool_name=tool, arguments=args)
            record_tool_invoke_receipt(
                registry.get(tool), call,
                ToolResult(tool_call_id=call.id, status="success", output=output),
            )
        # 2026-09-19: кэш отдаёт результат, только пока мир тот же
        # (`core/cache_freshness.py`), — источники лежат на диске, а чтение
        # doc.txt по-прежнему приходит из кэша со свежим отпечатком.
        from core.cache_freshness import cache_stamp
        target = tmp_path / path
        if tool == "file_read":
            target.write_text(output, encoding="utf-8")
        else:
            target.mkdir(parents=True, exist_ok=True)
            (target / output.strip()).write_text("", encoding="utf-8")
        memory.cache_store(tool, args, output, label, stamp=cache_stamp(tool, args, tmp_path))
        sources.append({"tool": tool, "arguments": args, "label": label,
                        "expected_outcome": "Read the source."})
    llm = _DeclaringLLM(responses=[{"good": _GOOD, "bad": _BAD, "all_bad": _ALL_BAD}[draft]])
    agent = AgentLoop(
        registry=registry, policy=PolicyGate(registry), llm=llm,
        logger=TraceLogger(trace_id=trace_id, log_dir=tmp_path, verbose=False),
        planner=FakePlanner(sources=sources, reasoning="Read file and list directories."),
        memory=memory, episodic_store=EpisodicMemoryStore(tmp_path / "episodes.jsonl"),
        max_replan_attempts=1,
    )
    answer = agent.run("Which modules are available?")
    events = [
        json.loads(line) for line in Path(agent.log.path).read_text(encoding="utf-8").splitlines()
    ]
    assert len(llm.calls) == 1, "a local citation refusal must not buy extra model calls"
    verified, unmatched = _EXPECTED[draft]
    assert agent.last_verification.verified_chunks == verified
    assert agent.last_verification.cited_but_unmatched_chunks == unmatched
    return agent, answer, events


def _payload(events, name):
    return next(row["payload"] for row in events if row["event"] == name)


def test_terminal_refusal_does_not_deny_all_the_drafts_support(tmp_path, monkeypatch):
    _, answer, events = _run(tmp_path, monkeypatch)
    enforcement = _payload(events, "answer_enforcement")
    assert enforcement["outcome"] == "citation_integrity"
    assert enforcement["applied"] and enforcement["mode"] == "off"
    assert _FACTS[0] not in answer, "a claim with an unmatched citation shipped"
    assert "claims carried no honest support" not in answer


def test_terminal_refusal_labels_the_summary_as_rejected_draft(tmp_path, monkeypatch):
    _, answer, events = _run(tmp_path, monkeypatch)
    explained = _payload(events, "verification_explained")
    assert explained["verified_chunks"] == 0
    assert "отклонённого черновика" in explained["full_text"]
    printed = format_human_response(answer)
    assert "Проверка: отклонённый черновик" in printed
    assert _payload(events, "response_composed")["answer_withheld"] is True


def test_terminal_refusal_is_banked_as_blocked_not_success(tmp_path, monkeypatch):
    agent, answer, events = _run(tmp_path, monkeypatch)
    episode = agent.episodic_store.load()[-1]
    assert episode.full_answer == answer
    assert episode.verified_chunks == 0
    assert episode.declared_completion == "partially_achieved"
    assert episode.usage_eligible is False
    assert episode.outcome == "failed"
    assert episode.completion_state == "blocked"
    assert episode.completion_override == "citation_integrity"
    banked = _payload(events, "episodic_memory_write")
    assert banked["outcome"] == "failed"
    assert banked["completion_state"] == "blocked"


def test_draft_score_is_not_the_score_of_the_sent_refusal(tmp_path, monkeypatch):
    agent, _, events = _run(tmp_path, monkeypatch)
    episode = agent.episodic_store.load()[-1]
    assert episode.answer_quality_score is None
    assert episode.verification_subject == "rejected_draft"
    assert episode.draft_quality_score is not None
    banked = _payload(events, "episodic_memory_write")
    assert banked["answer_quality_score"] is None
    assert banked["draft_quality_score"] == episode.draft_quality_score
    assert banked["verification_subject"] == "rejected_draft"


@pytest.mark.parametrize("declared", [None, "achieved", "partially_achieved"])
def test_rejection_overrides_delivery_but_preserves_the_declaration(declared):
    episode = admit_for_storage(episode_from_agent_cycle(
        goal="g", question="q", answer="Answer withheld.",
        tools_used=["file_read"], source_labels=["file:doc.txt"],
        verified_chunks=8, weak_chunks=5, declared_completion=declared,
        defect_signals=["citation_fabricated"],
    ))
    restored = EpisodeRecord.from_dict(episode.to_dict())
    assert restored.declared_completion == declared
    assert restored.outcome == "failed"
    assert restored.completion_state == "blocked"
    assert restored.usage_eligible is False
    assert restored.verified_chunks == 8 and restored.weak_chunks == 5


def test_cached_sources_and_the_accepted_path_remain_intact(tmp_path, monkeypatch):
    agent, answer, events = _run(tmp_path, monkeypatch, rejected=False)
    assert _FACTS[0] in answer
    assert _payload(events, "answer_enforcement")["outcome"] != "citation_integrity"
    hits = [e["payload"]["label"] for e in events if e["event"] == "memory_cache_hit"]
    # 2026-09-19: листинг папки из кэша не отдаётся — её содержимое могло
    # измениться (`core/cache_freshness.py`); неизменный файл отдаётся.
    assert set(hits) == {"file:doc.txt"}
    prompt = agent.llm.calls[0]["user"]
    assert "[file:tools/]" in prompt and "[file:core/]" in prompt
    episode = agent.episodic_store.load()[-1]
    assert episode.outcome == "success"
    assert episode.completion_state == "partially_achieved"
    assert episode.answer_quality_score == 1.0
    assert "Проверка: подтверждено" in answer
    assert "отклонённый черновик" not in answer


def test_legacy_episode_status_is_not_reclassified_on_read():
    legacy = {
        "goal": "g", "question": "q", "outcome": "success",
        "verified_chunks": 8, "weak_chunks": 5,
        "declared_completion": "partially_achieved",
        "completion_state": "partially_achieved",
        "defect_signals": ["citation_fabricated"], "usage_eligible": False,
    }
    restored = EpisodeRecord.from_dict(legacy)
    assert restored.outcome == "success"
    assert restored.completion_state == "partially_achieved"
    assert restored.answer_quality_score == 0.615


def test_substitute_model_is_credited_for_the_draft_not_the_refusal(tmp_path, monkeypatch):
    agent, _, _ = _run(tmp_path, monkeypatch)
    agent.model_router = SimpleNamespace(usage_ledger=SimpleNamespace(records=[
        SimpleNamespace(
            role="synthesizer", provider="deepseek", model="deepseek-chat",
            status="success", run_id=None,
            route_reason="provider_unhealthy:openai:key_errors->deepseek",
        ),
    ]))
    draft = agent._build_response_draft(
        agent.last_verification.annotated_answer,
        user_question="Which modules are available?", artifacts={},
        replan_exhausted=False, local_critique_active=False, verifier_failure=False,
    )
    printed = format_human_response(draft.render())
    assert "deepseek/deepseek-chat" in printed
    assert "Качество этого ответа — её" not in printed
    assert "Модель подготовила отклонённый черновик" in printed


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_russian_refusal_remains_mandatory_without_denying_verified_claims(
    tmp_path, monkeypatch, mode,
):
    agent, _, _ = _run(tmp_path, monkeypatch)
    result = apply_answer_enforcement(
        answer=_ALL_BAD, report=agent.last_verification,
        question="Какие модули найдены?", mode=mode,
    )
    assert result.applied and result.outcome == "citation_integrity"
    assert "Черновик ответа не отправлен" in result.answer
    assert "Честного подтверждения у утверждений не было" not in result.answer
    assert "утверждений с неразрешившимися ссылками: 8" in result.answer


def test_both_directory_citations_resolve_from_the_cached_sources(tmp_path, monkeypatch):
    agent, _, _ = _run(tmp_path, monkeypatch, rejected=False)
    report = verify(
        answer=(
            "Conclusion: tools contains alpha.py. [file:tools/]\n"
            "Facts:\n- core contains beta.py. [file:core/]\n"
            "Sources: workspace listings\nConfidence: medium\nUnverified: nothing"
        ),
        chain=agent.last_provenance,
        user_question="What files are in the directories?",
        **agent._verification_receipt_kwargs(),
    )
    assert report.cited_but_unmatched_chunks == 0
    assert report.verified_chunks == 2


# -- 2026-09-19: one unmatched citation is cut, the verified rest ships --------


def test_one_fabricated_claim_is_cut_and_the_verified_rest_ships(tmp_path, monkeypatch):
    _, answer, events = _run(tmp_path, monkeypatch, draft="bad")
    enforcement = _payload(events, "answer_enforcement")
    assert enforcement["outcome"] == "citation_excised"
    assert "Missing module is available" not in answer, "the fabricated claim shipped"
    assert "missing.txt" not in answer
    assert all(fact in answer for fact in _FACTS), "a verified claim was lost with it"
    assert "Removed from this answer: 1 claim" in format_human_response(answer), (
        "the note that a claim was cut must reach the printed answer"
    )
    assert _payload(events, "response_composed")["answer_withheld"] is False


def test_an_excised_answer_is_delivered_but_never_learned_from(tmp_path, monkeypatch):
    agent, answer, _ = _run(tmp_path, monkeypatch, draft="bad")
    episode = agent.episodic_store.load()[-1]
    assert episode.full_answer == answer
    assert "citation_excised" in (episode.defect_signals or ())
    assert episode.usage_eligible is False, "a draft that fabricated may not become experience"
    assert episode.verification_subject == "answer"
    assert episode.completion_state != "achieved"


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_excision_does_not_depend_on_the_rollout_mode(tmp_path, monkeypatch, mode):
    agent, _, _ = _run(tmp_path, monkeypatch, draft="bad")
    result = apply_answer_enforcement(
        answer=_BAD, report=agent.last_verification,
        question="Какие модули найдены?", mode=mode,
    )
    assert result.applied and result.outcome == "citation_excised"
    assert "Missing module is available" not in result.answer
    assert "Черновик ответа не отправлен" not in result.answer


def test_a_claim_that_cannot_be_located_keeps_the_whole_refusal():
    """Nothing is cut by guesswork: an unlocatable claim means the old refusal."""
    from core.verifier_models import Citation, ClaimChunk

    cit = Citation(prefix="file", body="missing.txt", raw="[file:missing.txt]",
                   expected_kind="file")
    report = SimpleNamespace(
        cited_but_unmatched_chunks=1,
        chunks=(
            ClaimChunk(text="Alpha exists.", citations=(), matched_evidence_ids=("e1",),
                       verdict="verified"),
            ClaimChunk(text="Omega exists.", citations=(cit,), matched_evidence_ids=(),
                       verdict="cited_but_unmatched"),
        ),
        malformed_output=False,
    )
    result = apply_answer_enforcement(
        answer="Conclusion: Alpha exists. [file:doc.txt]\nFacts:\n- Omega is here.",
        report=report, question="What exists?",
    )
    assert result.outcome == "citation_integrity"
