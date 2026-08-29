from types import SimpleNamespace

import core.loop_verify_replan as verify_replan_module
from core.evidence import ProvenanceChain
from core.loop_verify_replan import AgentLoopVerifyReplan, VerifyState

URL = "https://example.test/unresolved"


class _Payload:
    def to_log_payload(self):
        return {}


class _KnowledgePayload(_Payload):
    def __init__(self):
        self.registry = _Payload()


class _Report:
    chunks: tuple = ()
    annotated_answer = "annotated draft"

    def to_log_payload(self):
        return {}


class _Log:
    def log(self, *_args, **_kwargs):
        pass


class _Decision:
    action = "continue"
    reason = "retry"
    failure_counts: dict = {}  # noqa: RUF012 — фейк полигона, один экземпляр
    forbidden_actions = ()

    def to_log_payload(self):
        return {}


class _Planner:
    def __init__(self):
        self.calls = 0

    def plan(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(
            sources=[{
                "tool": "web_fetch",
                "arguments": {"url": URL},
                "label": "initial-fetch",
                "expected_outcome": "Open the cited URL.",
            }],
            reasoning="fetch the cited URL",
            warnings=[],
            raw_response="{}",
        )


class _Loop(AgentLoopVerifyReplan):
    verifier_enabled = True

    def __init__(self, report):
        self.report = report
        self.log = _Log()
        self.planner = _Planner()
        self.replan_policy = SimpleNamespace(
            max_total_replans=4,
            budgets={"unresolved_citation": SimpleNamespace(advice="fetch it")},
            decide=lambda **_kwargs: _Decision(),
        )
        self._current_attempt = 0
        self._executed_tools = []
        self.seen_tools = []
        self.last_verification = None
        self.last_provenance = None
        self.last_source_ranking = None
        self.last_source_registry = None
        self.last_knowledge_pipeline = None
        self._synthesis_expects_contract_headers = False

    def _verify_draft(self, *_args, **_kwargs):
        return self.report, False

    def _build_plan(self, _goal, sources):
        return SimpleNamespace(
            steps=[
                SimpleNamespace(
                    action_spec={"tool_name": source["tool"]},
                    status=None,
                )
                for source in sources
            ],
        )

    def _execute_steps_parallel(self, steps):
        for step in steps:
            self.seen_tools.append(step.action_spec["tool_name"])
            yield step, None, None

    def _catalogue_chain(self, *_args, **_kwargs):
        return SimpleNamespace(
            ranking=_Payload(),
            knowledge=_KnowledgePayload(),
        )

    def _quarantine_conflicted_memory(self, _knowledge):
        pass

    def _verification_receipt_kwargs(self):
        return {}


def test_terminal_empty_fetch_plan_gets_one_search_fallback(monkeypatch):
    report = _Report()
    loop = _Loop(report)
    monkeypatch.setattr(
        verify_replan_module,
        "force_file_hint_read_when_explicit",
        lambda planner_out, **_kwargs: planner_out,
    )

    from core import verifier

    monkeypatch.setattr(
        verifier,
        "extract_unresolved_web_urls",
        lambda _report: [URL],
    )
    monkeypatch.setattr(verifier, "verify", lambda **_kwargs: report)

    state = VerifyState(
        draft_answer="draft",
        user_question="question",
        file_hint=None,
        goal=SimpleNamespace(id="goal"),
        chain=ProvenanceChain(),
        artifacts={},
        attempt=0,
        plan=None,
        planner_history="",
        failure_history=[],
        source_ranking=_Payload(),
        source_registry=_Payload(),
        may_knowledge=False,
        may_source_registry=False,
        _task_planner_llm=None,
        _disagreement_shadow=[],
        _cp=None,
    )

    loop._verify_and_settle_answer(state)

    assert loop.planner.calls == 1
    assert loop.seen_tools == ["web_fetch", "web_search"]
    assert state.answer == "annotated draft"
