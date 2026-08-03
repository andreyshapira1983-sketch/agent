"""MIR-069, phase 1 — the five-point verification explanation.

Operator ruling (2026-08-03, recorded verbatim in MIR-028/MIR-069): for every
confirmation the agent must state in human language (1) what it checked,
(2) by what method, (3) on what evidence, (4) what remains unverified, and
(5) how confident it is. Before this module the loop *had* every ingredient —
per-claim verdicts, matched evidence ids, disclaimers — but never composed
them into an explanation a human can read; the numbers lived only in JSONL
counters.

Contract under test:

* `build_verification_summary` is a PURE function of the report (+ optional
  chain) — no LLM, no I/O;
* the full text carries the same five Russian markers the daemon liveness
  probe (MIR-070) already uses, so every self-explanation in the system reads
  the same way;
* the numbers are the report's own numbers — verified counts as confirmed,
  everything else (including `user_asserted` and `dialogue_supported`, per the
  MIR-028 ruling) is named in point 4 as not externally confirmed;
* the compact tail survives body rewrites because it rides the ResponseDraft
  notice ledger, not the body string;
* a report with nothing examined yields NO tail (the disclaimers already
  cover that case) but still explains itself honestly in the journal.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.approval import AutoApprover
from core.evidence import Evidence, ProvenanceChain
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from core.response_draft import ResponseDraft
from core.verification_summary import (
    _VERDICT_RU,
    FIVE_POINT_MARKERS,
    build_verification_summary,
)
from core.verifier_models import ClaimChunk, VerificationReport
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Report fixtures — built directly, the same shapes verify() emits.
# ---------------------------------------------------------------------------

def _chunk(verdict: str, ev_ids: tuple[str, ...] = ()) -> ClaimChunk:
    return ClaimChunk(
        text=f"утверждение с вердиктом {verdict}",
        citations=(),
        matched_evidence_ids=ev_ids,
        verdict=verdict,
    )


def _report(chunks: tuple[ClaimChunk, ...], **overrides) -> VerificationReport:
    verdicts = [c.verdict for c in chunks]
    fields = dict(
        total_chunks=len(chunks),
        verified_chunks=verdicts.count("verified"),
        unverified_chunks=verdicts.count("unverified"),
        cited_but_unmatched_chunks=verdicts.count("cited_but_unmatched"),
        self_declared_chunks=verdicts.count("self_declared"),
        structural_chunks=verdicts.count("structural"),
        chunks=chunks,
        annotated_answer="\n".join(c.text for c in chunks),
        fully_unverified=(verdicts.count("verified") == 0),
        chain_was_empty=False,
        dialogue_supported_chunks=verdicts.count("dialogue_supported"),
        user_asserted_chunks=verdicts.count("user_asserted"),
        topic_supported_but_claim_unverified_chunks=verdicts.count(
            "topic_supported_but_claim_unverified"
        ),
        subagent_asserted_chunks=verdicts.count("subagent_asserted"),
        receipt_missing_chunks=verdicts.count("receipt_missing"),
    )
    fields.update(overrides)
    return VerificationReport(**fields)


def _evidence(ev_id: str, source_id: str = "doc.txt") -> Evidence:
    return Evidence(
        id=ev_id,
        kind="file",
        source_id=source_id,
        obtained_via="file_read",
        content_hash="0" * 64,
        fetched_at="2026-08-03T00:00:00+00:00",
        confidence=0.9,
        claim="файл прочитан",
        excerpt="hello",
    )


# ---------------------------------------------------------------------------
# The five points
# ---------------------------------------------------------------------------

class TestFivePoints:
    def test_full_text_carries_all_five_markers(self):
        summary = build_verification_summary(
            _report((_chunk("verified", ("ev1",)), _chunk("unverified")))
        )
        text = summary.full_text()
        for marker in FIVE_POINT_MARKERS:
            assert marker in text, f"нет пункта: {marker}"

    def test_markers_match_the_liveness_probe_vocabulary(self):
        # One vocabulary for every self-explanation (MIR-070 set it first).
        assert FIVE_POINT_MARKERS == (
            "Проверял:",
            "Способ:",
            "Доказательство:",
            "Непроверенным осталось:",
            "Уверенность:",
        )

    def test_counts_are_the_reports_own_numbers(self):
        summary = build_verification_summary(
            _report((
                _chunk("verified", ("ev1",)),
                _chunk("user_asserted"),
                _chunk("unverified"),
            ))
        )
        text = summary.full_text()
        assert "подтверждено 1 из 3" in summary.tail
        # user_asserted is named as NOT externally confirmed (MIR-028 ruling),
        # never silently folded into the confirmed bucket.
        assert "слов" in text and "оператора" in text
        assert "низкая" in summary.tail

    def test_all_verified_reads_as_high_confidence(self):
        summary = build_verification_summary(
            _report((_chunk("verified", ("ev1",)), _chunk("verified", ("ev2",))))
        )
        assert "высокая" in summary.tail
        assert "подтверждено 2 из 2" in summary.tail
        assert "ничего" in summary.unverified

    def test_zero_verified_reads_as_zero_confidence(self):
        summary = build_verification_summary(
            _report((_chunk("unverified"), _chunk("self_declared")))
        )
        assert "нулевая" in summary.confidence

    def test_evidence_point_names_the_matched_sources(self):
        chain = ProvenanceChain(evidences=[_evidence("ev1", source_id="journal.txt")])
        summary = build_verification_summary(
            _report((_chunk("verified", ("ev1",)),)), chain=chain
        )
        assert "file_read" in summary.evidence
        assert "journal.txt" in summary.evidence

    def test_no_matches_says_so_instead_of_listing_nothing(self):
        summary = build_verification_summary(
            _report((_chunk("unverified"),)),
            chain=ProvenanceChain(),
        )
        assert summary.evidence.strip()

    def test_nothing_examined_yields_no_tail_but_an_honest_text(self):
        report = _report((), chain_was_empty=True, fully_unverified=True)
        summary = build_verification_summary(report)
        assert summary.tail == ""
        text = summary.full_text()
        for marker in FIVE_POINT_MARKERS:
            assert marker in text

    def test_log_payload_carries_text_and_numbers(self):
        summary = build_verification_summary(
            _report((_chunk("verified", ("ev1",)), _chunk("unverified")))
        )
        payload = summary.to_log_payload()
        assert payload["verified_chunks"] == 1
        assert payload["examined_chunks"] == 2
        for marker in FIVE_POINT_MARKERS:
            assert marker in payload["full_text"]


class TestVerdictVocabularyIsCovered:
    def test_every_verdict_the_verifier_assigns_has_russian_wording(self):
        """Self-maintaining coverage: a new verdict added to verifier_core
        without wording here means the explanation would silently lie by
        omission. Same scrape the INV-4 guard uses."""
        src = (_REPO_ROOT / "core" / "verifier_core.py").read_text(encoding="utf-8")
        verdicts = set(re.findall(r'verdict\s*=\s*"([a-z_]+)"', src))
        verdicts |= set(re.findall(r'verdict="([a-z_]+)"', src))
        missing = sorted(v for v in verdicts if v not in _VERDICT_RU)
        assert not missing, f"вердикты без русской формулировки: {missing}"


# ---------------------------------------------------------------------------
# The append notice channel (ResponseDraft)
# ---------------------------------------------------------------------------

class TestAppendChannel:
    def test_append_renders_below_the_body(self):
        draft = ResponseDraft(body="тело ответа")
        assert draft.add_notice(
            author="verification_summary", channel="append", text="Проверка: хвост."
        )
        rendered = draft.render()
        assert rendered.index("тело ответа") < rendered.index("Проверка: хвост.")

    def test_append_survives_a_body_rewrite(self):
        draft = ResponseDraft(body="исходные утверждения")
        draft.add_notice(
            author="verification_summary", channel="append", text="Проверка: хвост."
        )
        draft.set_body("усечённый ответ", by="answer_enforcement")
        assert "Проверка: хвост." in draft.render()

    def test_append_coexists_with_prepend(self):
        draft = ResponseDraft(body="тело")
        draft.add_notice(author="gate", channel="prepend", text="вопрос сверху")
        draft.add_notice(author="vs", channel="append", text="хвост снизу")
        rendered = draft.render()
        assert rendered.index("вопрос сверху") < rendered.index("тело") < rendered.index(
            "хвост снизу"
        )


# ---------------------------------------------------------------------------
# Wiring through the real loop
# ---------------------------------------------------------------------------

def _events(p: Path) -> list[dict]:
    out: list[dict] = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _agent(
    workspace: Path,
    *,
    llm_response: str,
    canned_sources: list[dict],
    verifier_enabled: bool = True,
) -> tuple[AgentLoop, Path]:
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    for src in canned_sources:
        src.setdefault("expected_outcome", "executes the planned step")
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=FakeLLM(responses=[llm_response]),
        logger=logger,
        planner=FakePlanner(canned_sources),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
        verifier_enabled=verifier_enabled,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


class TestHumanFormatterKeepsTheTail:
    def test_tail_survives_the_output_contract_strip(self):
        """Found live (2026-08-03): the tail reached the rendered draft
        (`response_composed` showed the contribution, missing=[]) but
        `format_human_response` walks the Output Contract sections and drops
        everything after Sources/Confidence — so the operator never saw it.
        The formatter must bucket the tail explicitly."""
        from core.loop import format_human_response

        answer = (
            "Conclusion: в файле три строки [file:жур.txt].\n"
            "Facts:\n"
            "- строк ровно три [file:жур.txt].\n"
            "Sources: жур.txt\n"
            "Confidence: high\n"
            "\n"
            "Проверка: подтверждено 1 из 1 утверждений; "
            "без внешнего подтверждения: 0; уверенность: высокая."
        )
        human = format_human_response(answer)
        assert "Проверка: подтверждено 1 из 1" in human
        # And it reads LAST — after the claims it talks about.
        assert human.rstrip().endswith("уверенность: высокая.")

    def test_non_contract_answer_is_untouched(self):
        from core.loop import format_human_response

        text = "обычный ответ\n\nПроверка: подтверждено 1 из 1; уверенность: высокая."
        assert format_human_response(text) == text


class TestLoopWiring:
    def test_verified_turn_explains_itself_in_answer_and_journal(
        self, workspace: Path
    ):
        (workspace / "doc.txt").write_text("hello world", encoding="utf-8")
        agent, log_path = _agent(
            workspace,
            llm_response=(
                "Conclusion: file says hello [file:doc.txt].\n"
                "Facts: hello is in doc.txt [file:doc.txt].\n"
                "Sources: doc.txt"
            ),
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "doc.txt"},
                "label": "file:doc.txt",
            }],
        )
        answer = agent.run("what does doc.txt say", file_hint="doc.txt")
        # The compact tail reached the operator.
        assert "Проверка: подтверждено" in answer
        # The full five points reached the journal.
        events = [e for e in _events(log_path) if e.get("event") == "verification_explained"]
        assert len(events) == 1
        full_text = events[0]["payload"]["full_text"]
        for marker in FIVE_POINT_MARKERS:
            assert marker in full_text

    def test_a_composer_failure_is_journaled_not_swallowed(
        self, workspace: Path, monkeypatch
    ):
        """Review round #283: a broken summary must not vanish silently —
        the loop keeps answering, and the journal says WHY there is no
        explanation this turn."""
        import core.loop as loop_mod

        def _boom(report, chain=None):
            raise RuntimeError("схема вердиктов изменилась")

        monkeypatch.setattr(loop_mod, "build_verification_summary", _boom)
        (workspace / "doc.txt").write_text("hello", encoding="utf-8")
        agent, log_path = _agent(
            workspace,
            llm_response="Conclusion: hello [file:doc.txt].\nFacts: hi [file:doc.txt].",
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "doc.txt"},
                "label": "file:doc.txt",
            }],
        )
        answer = agent.run("read", file_hint="doc.txt")
        assert answer  # the turn still completes
        events = _events(log_path)
        assert not [e for e in events if e.get("event") == "verification_explained"]
        failed = [
            e for e in events if e.get("event") == "verification_explained_failed"
        ]
        assert len(failed) == 1
        assert failed[0]["payload"]["error_type"] == "RuntimeError"

    def test_disabled_verifier_adds_neither_tail_nor_event(self, workspace: Path):
        agent, log_path = _agent(
            workspace,
            llm_response="ответ без проверки",
            canned_sources=[],
            verifier_enabled=False,
        )
        answer = agent.run("вопрос")
        assert "Проверка: подтверждено" not in answer
        assert not [
            e for e in _events(log_path) if e.get("event") == "verification_explained"
        ]
