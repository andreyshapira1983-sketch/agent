"""A rejection reason must come from the component that rejected, not from
arithmetic performed by the observer.

MIR-055 gave retrieval a `rejected_by` trace, but the persistent path built it
by subtracting list lengths:

    ("role_scope",      len(records) - len(use_report.allowed))
    ("below_threshold", len(use_report.allowed) - len(selected))

Subtraction cannot separate causes it never saw. Measured on the live store
(103 records), 4 of 6 realistic questions were reported with the wrong reason —
worst case `procedural confidence beta smoothing`, logged as
`below_threshold: 100` when the truth was `below_threshold: 28, over_limit: 72`.
"almost nothing here is relevant" and "72 relevant records were thrown away by
a cap of 3" call for opposite responses, which is the very sentence MIR-055
was written to make sayable.

The same shape on the experience path: the loop counts only what
`EpisodicMemoryStore.search` handed back, so everything the search itself
dropped is invisible. On the live store (200 episodes) the question
"quarterly revenue in singapore" logs `episodes_selected=0, rejected_by={}` —
the unreadable line MIR-055 set out to eliminate, still reachable.

So the fix is not more subtraction. Each decider reports its own reasons, and
the observer merges what it was told.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bootstrap import build_agent
from core.loop import AgentLoop
from core.memory_policy import MemoryRetrievalPolicy
from core.models import MemoryRecord

QUESTION = "how do I deploy the service"
STOPWORDS_ONLY = "и в"


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _agent(workspace: Path) -> AgentLoop:
    return build_agent(workspace, with_memory=True, approval_provider=None)


def _record(content: str, *, tags: tuple[str, ...] = ("fact",)) -> MemoryRecord:
    return MemoryRecord(
        type="semantic", content=content, tags=list(tags),
        owner="self", source="agent-auto",
    )


def _event(agent: AgentLoop, name: str) -> dict:
    import json
    for line in reversed(Path(agent.log.path).read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("event") == name:
            return event.get("payload", {})
    raise AssertionError(f"event {name!r} was never logged")


def test_a_question_with_no_tokens_is_not_the_records_fault() -> None:
    policy = MemoryRetrievalPolicy()
    records = [_record(f"deploy the service step {i}") for i in range(3)]

    report = policy.select_with_report(records, STOPWORDS_ONLY)

    assert report.selected == []
    assert report.rejected_by == {"no_query_tokens": 3}, (
        "the question produced no searchable tokens; blaming the records "
        "sends the reader to the wrong store"
    )


def test_a_quarantined_record_is_not_logged_as_role_scope(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    agent.persistent_store.save(
        _record("deploy the service via the old runbook", tags=("fact", "obsolete"))
    )

    agent._retrieve_persistent(QUESTION)

    data = _event(agent, "persistent_memory_inject")
    assert data["rejected_by"] == {"quarantined": 1}, (
        "a record withheld because it is marked obsolete is not a record "
        "outside the role's scope — the responses differ"
    )
