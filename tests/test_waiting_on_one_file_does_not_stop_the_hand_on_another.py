"""WAITING ≠ EXHAUSTED for the engineering hand (audit L9, block 3, 2026-09-03).

Before: ANY pending or approved self-apply item stopped the producer for
EVERY goal (`_has_pending_self_apply`), and the campaign stored the refusal
string `engineering_declined:approval_wait` as a proposal — a useful cycle
made of waiting. Now a candidate waits only when a waiting item already
touches the same file; a file a human denied recently is on cooldown; and
when the producer chooses, both sets are excluded so it advances. A refusal
or a wait is logged by name and is not a proposal.
"""
from __future__ import annotations

import core.self_build_producer as prod
from core.approval_inbox import ApprovalInbox
from core.self_apply_bridge import SELF_APPLY_OPERATION
from tests.test_self_build_producer import (
    _TARGET,
    FakeLLM,
    _builder_ok,
    _manager_ok,
    _produce,
)


def _waiting(inbox: ApprovalInbox, path: str):
    return inbox.add(
        operation=SELF_APPLY_OPERATION, summary=f"waiting on {path}",
        payload={"files": [{"path": path, "content": "x"}]}, dedup_key=f"t:{path}",
    )


def test_a_wait_on_another_file_does_not_hold_the_hand(tmp_path) -> None:
    inbox = ApprovalInbox(path=None)
    _waiting(inbox, "core/other.py")
    llm = FakeLLM([_manager_ok(), _builder_ok()])

    report = _produce(tmp_path, llm=llm, inbox=inbox, candidate_targets=(_TARGET,))

    assert report.status != "approval_wait", report.status
    assert "approval" in report.checked_gates


def test_a_wait_on_the_same_file_still_holds(tmp_path) -> None:
    """Control: the wait is real when it is about THIS file."""
    inbox = ApprovalInbox(path=None)
    _waiting(inbox, _TARGET)
    llm = FakeLLM([_manager_ok(), _builder_ok()])

    report = _produce(tmp_path, llm=llm, inbox=inbox, candidate_targets=(_TARGET,))

    assert report.status == "approval_wait"
    assert _TARGET in report.reason
    assert llm.calls == []


def test_an_item_that_names_no_files_holds_everything(tmp_path) -> None:
    """Unknown is not nothing: the pre-block-3 global wait stays for it."""
    inbox = ApprovalInbox(path=None)
    inbox.add(operation=SELF_APPLY_OPERATION, summary="existing, no files")
    llm = FakeLLM([_manager_ok(), _builder_ok()])

    report = _produce(tmp_path, llm=llm, inbox=inbox, candidate_targets=(_TARGET,))

    assert report.status == "approval_wait"
    assert "names no files" in report.reason


def test_a_recent_denial_puts_the_file_on_cooldown(tmp_path) -> None:
    inbox = ApprovalInbox(path=None)
    item = _waiting(inbox, _TARGET)
    inbox.deny(item.id, reason="not this way", actor="operator")
    llm = FakeLLM([_manager_ok(), _builder_ok()])

    report = _produce(tmp_path, llm=llm, inbox=inbox, candidate_targets=(_TARGET,))

    assert report.status == "denied_cooldown", report.status
    assert llm.calls == []


def test_the_chooser_excludes_waiting_and_denied_files(tmp_path, monkeypatch) -> None:
    """When the producer picks the target itself, both sets are cooldowns."""
    captured: dict = {}

    def _fake_selector(workspace, *, exclude_targets=frozenset()):
        captured["exclude"] = frozenset(exclude_targets)
        return lambda: None

    monkeypatch.setattr(prod, "_default_grounded_selector", _fake_selector)
    inbox = ApprovalInbox(path=None)
    _waiting(inbox, "core/a.py")
    denied = _waiting(inbox, "core/b.py")
    inbox.deny(denied.id, reason="no", actor="operator")

    report = _produce(tmp_path, llm=FakeLLM([]), inbox=inbox, legacy_llm_manager=False)

    assert {"core/a.py", "core/b.py"} <= captured["exclude"], captured
    assert report.status == "no_grounded_target"


def _campaign_agent():
    class _Router:
        def for_role(self, _role):
            return object()

    class _Log:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict]] = []

        def log(self, event, payload=None, **extra) -> None:
            self.events.append((event, dict(payload or {})))

    class _Agent:
        def __init__(self) -> None:
            self.model_router = _Router()
            self.log = _Log()

    return _Agent()


def _stub_lane(monkeypatch, report: prod.ProducerReport) -> dict:
    import core.safe_vcs as vcs_mod

    seen: dict = {}

    class _Vcs:
        def __init__(self, **_kw) -> None:
            pass

        def is_clean(self) -> bool:
            return True

    def _fake_produce(**kw):
        seen.update(kw)
        return report

    monkeypatch.setattr(vcs_mod, "SafeVCS", _Vcs)
    monkeypatch.setattr(prod, "produce_self_apply_proposal", _fake_produce)
    return seen


def test_the_campaign_does_not_call_a_wait_a_proposal(tmp_path, monkeypatch) -> None:
    """The live path: the ledger row must not carry a proposal made of waiting."""
    import core.campaign_io as mod

    _stub_lane(monkeypatch, prod.ProducerReport(status="approval_wait", reason="held"))
    agent = _campaign_agent()

    out = mod._propose_engineering_step(
        agent=agent, workspace=tmp_path, approval_inbox=ApprovalInbox(path=None),
    )

    assert out is None, f"a wait was reported as a proposal: {out}"
    statuses = [p.get("status") for e, p in agent.log.events
                if e == "campaign_engineering_proposed"]
    assert statuses == ["approval_wait"], agent.log.events


def test_the_campaign_still_reports_a_real_proposal(tmp_path, monkeypatch) -> None:
    """Positive control, and the vetoed-targets cooldown reaches the producer."""
    import core.campaign_io as mod

    seen = _stub_lane(monkeypatch, prod.ProducerReport(
        status="proposed", approval_id="ain_1", target_path="core/x.py",
    ))

    out = mod._propose_engineering_step(
        agent=_campaign_agent(), workspace=tmp_path,
        approval_inbox=ApprovalInbox(path=None),
    )

    assert out == "engineering_proposed:ain_1"
    assert "recently_vetoed_targets" in seen
