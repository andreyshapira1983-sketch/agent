"""Tests for the subagent-backed self-apply proposal producer (TD-025).

Every dependency is faked: FakeLLM (no real provider/network), an in-memory
ApprovalInbox, a FakeVCS, and an in-memory file reader. The producer must:

* honour the four safety gates before any LLM-heavy work runs;
* run the Manager/Researcher/Builder/Critic/Reporter roles in order;
* reject diff-only / denylisted / low-confidence candidates via a Critic veto;
* create at most one ``self_apply_lane.run`` approval item whose payload
  round-trips through the TD-024 bridge;
* never apply the patch, commit, push, fetch, pull, merge, or touch git.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.approval_inbox import ApprovalInbox
from core.backlog_target_mapper import MODEL_DISCOVERY_TARGET
from core.self_apply_bridge import SELF_APPLY_OPERATION, rehydrate_proposal
from core.self_build_producer import (
    PRODUCER_ORIGIN,
    ProducerReport,
    produce_self_apply_proposal,
)


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeLLM:
    """Returns canned responses in order; records every call so a gate test can
    assert that no LLM-heavy work happened."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def complete(self, *, system: str, user: str, max_tokens: int = 2000,
                 temperature: float = 0.0) -> str:
        self.calls.append({"system": system, "user": user})
        if self.responses:
            return self.responses.pop(0)
        return "{}"


class FakeVCS:
    """Minimal SafeVCS stand-in. Records any mutating call so tests can prove the
    producer never touches git."""

    def __init__(self, clean: bool = True) -> None:
        self._clean = clean
        self.mutations: list[str] = []

    def is_clean(self) -> bool:
        return self._clean

    def create_temp_branch(self, name: str) -> None:  # pragma: no cover - guard
        self.mutations.append(f"create_temp_branch:{name}")

    def commit(self, message: str) -> str:  # pragma: no cover - guard
        self.mutations.append("commit")
        return "deadbeef"


class FakeKillSwitch:
    def __init__(self, active: bool, reason: str = "") -> None:
        self.active = active
        self.reason = reason


def _reader(files: dict[str, str]):
    def read(path: str) -> str | None:
        return files.get(path)
    return read


# ── canned role responses ────────────────────────────────────────────────────

_TARGET = "core/redaction.py"


def _manager_ok(target: str = _TARGET) -> str:
    return json.dumps({"target": target, "diagnosis": "tidy a helper"})


def _manager_none() -> str:
    return json.dumps({"target": None, "diagnosis": "nothing worth it"})


def _builder_ok(content: str = "VALUE = 1\n", confidence: float = 0.9) -> str:
    return json.dumps(
        {
            "content": content,
            "test_paths": ["tests/test_redaction.py"],
            "test_pattern": "redaction",
            "reason": "small tidy",
            "confidence": confidence,
        }
    )


def _near_exhaustion_budget() -> dict:
    return {
        "windows": [
            {
                "name": "hour",
                "counters": {
                    "llm_calls": {"used": 9, "limit": 10},
                    "model_tokens": {"used": 900, "limit": 1000},
                },
            }
        ]
    }


def _headroom_budget() -> dict:
    return {
        "windows": [
            {
                "name": "hour",
                "counters": {
                    "llm_calls": {"used": 1, "limit": 100},
                    "model_tokens": {"used": 10, "limit": 1000},
                },
            }
        ]
    }


def _produce(workspace: Path, **kwargs) -> ProducerReport:
    defaults = dict(
        workspace=workspace,
        inbox=ApprovalInbox(path=None),
        vcs=FakeVCS(clean=True),
        budget_snapshot=_headroom_budget(),
        kill_switch=FakeKillSwitch(active=False),
        file_reader=_reader({_TARGET: "OLD = 0\n"}),
    )
    # These legacy tests exercise the LLM Manager path directly. Since TD-036's
    # follow-up made the grounded selector the default, opt them back into the
    # legacy path explicitly unless the test drives a grounded selector itself.
    if "grounded_selector" not in kwargs:
        defaults["legacy_llm_manager"] = True
    defaults.update(kwargs)
    return produce_self_apply_proposal(**defaults)


# ── gate tests (no LLM-heavy work) ───────────────────────────────────────────


def test_kill_switch_active_refuses_before_any_subagent(workspace: Path):
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm, kill_switch=FakeKillSwitch(True, "day budget"))
    assert report.status == "budget_kill_switch"
    assert llm.calls == []  # no subagent ran
    assert report.role_outputs == []


def test_low_budget_returns_budget_wait(workspace: Path):
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm, budget_snapshot=_near_exhaustion_budget())
    assert report.status == "budget_wait"
    assert llm.calls == []


def test_pending_self_apply_returns_approval_wait(workspace: Path):
    inbox = ApprovalInbox(path=None)
    inbox.add(operation=SELF_APPLY_OPERATION, summary="existing")
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm, inbox=inbox)
    assert report.status == "approval_wait"
    assert llm.calls == []


def test_dirty_tree_refuses_before_proposal(workspace: Path):
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm, vcs=FakeVCS(clean=False))
    assert report.status == "dirty_tree_wait"
    assert llm.calls == []


# ── role pipeline ─────────────────────────────────────────────────────────────


def test_no_candidate_returns_no_patch(workspace: Path):
    llm = FakeLLM([_manager_none()])
    report = _produce(workspace, llm=llm)
    assert report.status == "no_patch"
    # Only the Manager ran; no builder work.
    assert [r.role for r in report.role_outputs] == ["manager"]


def test_selected_candidate_runs_roles_in_order_and_proposes(workspace: Path):
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm, inbox=inbox)
    assert report.status == "proposed"
    assert [r.role for r in report.role_outputs] == [
        "manager",
        "researcher",
        "builder",
        "critic",
        "reporter",
    ]
    assert report.target_path == _TARGET
    assert report.approval_id


def test_builder_diff_only_is_vetoed(workspace: Path):
    diff = "--- a/core/redaction.py\n+++ b/core/redaction.py\n@@ -1 +1 @@\n-OLD\n+NEW\n"
    llm = FakeLLM([_manager_ok(), _builder_ok(content=diff)])
    report = _produce(workspace, llm=llm)
    assert report.status == "critic_veto"
    assert any("diff" in r for r in report.veto_reasons)


def test_critical_target_is_denied_even_if_listed(workspace: Path):
    # Denylist wins before the allowlist: a critical organ can never be picked.
    llm = FakeLLM([_manager_ok(target="core/loop.py")])
    report = _produce(
        workspace,
        llm=llm,
        candidate_targets=("core/loop.py",),
        file_reader=_reader({"core/loop.py": "x=1\n"}),
    )
    assert report.status == "no_patch"


def test_off_allowlist_target_is_rejected(workspace: Path):
    llm = FakeLLM([_manager_ok(target="core/secret_stuff.py")])
    report = _produce(workspace, llm=llm, candidate_targets=(_TARGET,))
    assert report.status == "no_patch"


def test_low_confidence_is_vetoed_and_no_item_created(workspace: Path):
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([_manager_ok(), _builder_ok(confidence=0.1)])
    report = _produce(workspace, llm=llm, inbox=inbox)
    assert report.status == "critic_veto"
    assert inbox.list() == []  # veto blocks approval-item creation


def test_unparseable_builder_output_is_vetoed(workspace: Path):
    llm = FakeLLM([_manager_ok(), "totally not json"])
    report = _produce(workspace, llm=llm)
    assert report.status == "critic_veto"


def test_creates_exactly_one_self_apply_item(workspace: Path):
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm, inbox=inbox)
    assert report.status == "proposed"
    items = inbox.list()
    assert len(items) == 1
    item = items[0]
    assert item.operation == SELF_APPLY_OPERATION
    assert item.payload["origin"] == PRODUCER_ORIGIN


def test_payload_round_trips_through_bridge(workspace: Path):
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([_manager_ok(), _builder_ok(content="NEW = 2\n")])
    report = _produce(workspace, llm=llm, inbox=inbox)
    assert report.status == "proposed"
    payload = inbox.get(report.approval_id).payload
    proposal = rehydrate_proposal(payload)
    assert proposal.files[0].path == _TARGET
    assert proposal.files[0].content == "NEW = 2\n"


def test_producer_never_touches_git(workspace: Path):
    vcs = FakeVCS(clean=True)
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm, vcs=vcs)
    assert report.status == "proposed"
    assert vcs.mutations == []  # no branch/commit — producer only proposes


def test_no_push_or_network_methods_in_producer_and_vcs():
    import core.self_build_producer as producer
    from core.safe_vcs import SafeVCS

    src = Path(producer.__file__).read_text(encoding="utf-8")
    import_lines = [
        ln for ln in src.splitlines()
        if ln.strip().startswith(("import ", "from "))
    ]
    for banned in ("requests", "urllib", "httpx", "socket", "subprocess"):
        assert not any(banned in ln for ln in import_lines), banned
    for banned in ("push", "fetch", "pull", "remote", "merge"):
        assert not hasattr(SafeVCS, banned), banned


def test_config_budget_limits_never_written(workspace: Path):
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm)
    assert report.status == "proposed"
    assert not (workspace / "config" / "budget_limits.json").exists()


# ── value gate (TD-035): pre-publish no-effect veto + soft flags ──────────────


def _builder_custom(content: str, reason: str, confidence: float = 0.9) -> str:
    return json.dumps(
        {
            "content": content,
            "test_paths": ["tests/test_redaction.py"],
            "test_pattern": "redaction",
            "reason": reason,
            "confidence": confidence,
        }
    )


def _self_build_doc_content() -> str:
    return """# Human-gated self-build loop

## What `:self-build-produce` does

`:self-build-produce` creates one approval item only in the approval inbox. It
does not apply the patch, does not commit, and does not run the lane.

## Human inspection

Inspect pending items with `:approval-list` or `:approval-triage`. Approve only
with `:approval-approve <id>` when the target, content, tests, and risk boundary
are correct. Deny with `:approval-deny <id>` when the proposal is wrong.

## Separate apply step

`:self-apply-run <id>` is separate and requires explicit human approval first.
There is no auto-apply and no auto-commit.

## Boundaries

Do not enable scheduler or allow-effects. This pilot does not authorize G6a,
gateway changes, runner work, or remote git.

## Safe operator checklist

- Confirm the target path is allowlisted and expected.
- Confirm the proposal describes the human-gated self-build loop.
- Confirm tests are relevant and no unrelated files are included.

## Reject criteria

Reject or deny if the proposal is a generic build from source guide, invents
permissions, skips approval review, claims auto-apply, claims auto-commit, or
touches scheduler, allow-effects, G6a, gateway, runner, or remote git work.
"""


def test_comment_only_change_is_value_vetoed_no_inbox_item(workspace: Path):
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([
        _manager_ok(),
        _builder_custom("VALUE = 1  # widest allowed span\n", "tidy comment"),
    ])
    report = _produce(
        workspace,
        llm=llm,
        inbox=inbox,
        file_reader=_reader({_TARGET: "VALUE = 1  # WIDEST allowed span\n"}),
    )
    assert report.status == "value_veto"
    assert report.veto_reasons
    assert report.approval_id is None
    assert inbox.list() == []  # value veto blocks approval-item creation
    # Critic still technically passed; the gate runs after it.
    assert [r.role for r in report.role_outputs] == [
        "manager", "researcher", "builder", "critic",
    ]


def test_widest_incident_is_value_vetoed_no_inbox_item(workspace: Path):
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([
        _manager_ok(),
        _builder_custom("MAX = 80  # widest\n", "robustness improvement"),
    ])
    report = _produce(
        workspace,
        llm=llm,
        inbox=inbox,
        file_reader=_reader({_TARGET: "MAX = 80  # WIDEST\n"}),
    )
    assert report.status == "value_veto"
    assert inbox.list() == []


def test_whitespace_only_change_is_value_vetoed(workspace: Path):
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([
        _manager_ok(),
        _builder_custom("def f():\n\n    return 1\n\n", "reflow"),
    ])
    report = _produce(
        workspace,
        llm=llm,
        inbox=inbox,
        file_reader=_reader({_TARGET: "def f():\n    return 1\n"}),
    )
    assert report.status == "value_veto"
    assert inbox.list() == []


def test_real_code_change_still_proposes(workspace: Path):
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([
        _manager_ok(),
        _builder_custom("def f():\n    return 2\n", "fix return value"),
    ])
    report = _produce(
        workspace,
        llm=llm,
        inbox=inbox,
        file_reader=_reader({_TARGET: "def f():\n    return 1\n"}),
    )
    assert report.status == "proposed"
    assert len(inbox.list()) == 1


def test_docs_target_text_change_is_not_value_vetoed(workspace: Path):
    inbox = ApprovalInbox(path=None)
    doc = "docs/self_build.md"
    llm = FakeLLM([
        _manager_ok(target=doc),
        _builder_custom(_self_build_doc_content(), "document human-gated loop"),
    ])
    report = _produce(
        workspace,
        llm=llm,
        inbox=inbox,
        candidate_targets=(doc,),
        file_reader=_reader({doc: "# Old self-build guide\n"}),
    )
    assert report.status == "proposed"
    assert len(inbox.list()) == 1


def test_docs_self_build_builder_prompt_names_operator_guide_contract(
    workspace: Path,
):
    doc = "docs/self_build.md"
    llm = FakeLLM([
        _manager_ok(target=doc),
        _builder_custom(_self_build_doc_content(), "document human-gated loop"),
    ])

    report = _produce(
        workspace,
        llm=llm,
        candidate_targets=(doc,),
        file_reader=_reader({doc: ""}),
    )

    assert report.status == "proposed"
    builder_call = next(c for c in llm.calls if "You are the Builder" in c["system"])
    system = builder_call["system"]
    assert "human-gated self-build loop" in system
    assert ":self-build-produce" in system
    assert ":self-apply-run" in system
    assert "not a generic build-from-source" in system


def test_docs_self_build_generic_source_build_guide_is_vetoed(
    workspace: Path,
):
    inbox = ApprovalInbox(path=None)
    doc = "docs/self_build.md"
    generic = (
        "# Build from source\n\n"
        "Clone the repository, install dependencies, run pip install -e ., "
        "compile assets, and run pytest before using the project.\n"
    )
    llm = FakeLLM([
        _manager_ok(target=doc),
        _builder_custom(generic, "add build-from-source guide"),
    ])

    report = _produce(
        workspace,
        llm=llm,
        inbox=inbox,
        candidate_targets=(doc,),
        file_reader=_reader({doc: ""}),
    )

    assert report.status == "critic_veto"
    assert any(
        "human-gated self-build operator guide" in r
        for r in report.veto_reasons
    )
    assert inbox.list() == []


def test_overclaim_summary_adds_soft_flag_without_blocking(workspace: Path):
    inbox = ApprovalInbox(path=None)
    hype = "revolutionary breakthrough that dramatically boosts performance"
    llm = FakeLLM([
        _manager_ok(),
        _builder_custom("NEW = 1\n", hype),
    ])
    report = _produce(
        workspace,
        llm=llm,
        inbox=inbox,
        file_reader=_reader({_TARGET: "OLD = 0\n"}),
    )
    assert report.status == "proposed"  # soft flag never blocks
    assert report.value_flags
    assert any("overclaim" in f for f in report.value_flags)
    # The flag is visible to a human reviewer on the approval item's reasons.
    item = inbox.list()[0]
    assert any("value-flag" in r for r in item.reasons)


# ── TD-036: grounded backlog selector integration ────────────────────────────


class _Candidate:
    """Minimal stand-in for a BacklogCandidate."""

    def __init__(
        self,
        target_path: str,
        problem_quote: str,
        evidence_ref: str = "ref",
        signal_source: str = "tech_debt",
    ):
        self.target_path = target_path
        self.signal_source = signal_source
        self.problem_quote = problem_quote
        self.evidence_ref = evidence_ref


_TD_011_012_TITLE = (
    "TD-011 / TD-012 \u2014 Live Model Discovery + Provider Catalog Refresh "
    "(read-only)"
)

_SELF_BUILD_DOC_TARGET = "docs/self_build.md"
_SELF_BUILD_PROPOSAL = """\
# TD-038 slice 2 - proposal: self-build grounded target coverage (docs only)

### D. Mapper coverage for a docs-only / operator-guide target **first**

- **What:** first grounded target is `docs/self_build.md` (already in
  `DEFAULT_CANDIDATE_TARGETS`).
"""


def _write_model_discovery_mapping_evidence(workspace: Path) -> None:
    (workspace / "TECH_DEBT.md").write_text(
        f"{_TD_011_012_TITLE}\nStatus: Partial.\n",
        encoding="utf-8",
    )
    (workspace / "core").mkdir()
    (workspace / "core" / "model_discovery.py").write_text(
        '"""Live Model Discovery + Provider Catalog diff -- read-only / dry-run (TD-011/012).\n'
        "Uses build_discovery_audit and build_discovery_report.\n"
        "It NEVER writes the catalog.\n"
        '"""\n'
        "OLD = 0\n",
        encoding="utf-8",
    )
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_model_discovery.py").write_text(
        '"""Tests for TD-011/012 read-only Live Model Discovery + catalog diff.\n'
        "The discovery never writes and exposes no secret values.\n"
        '"""\n'
        "from core.model_discovery import build_discovery_audit, build_discovery_report\n",
        encoding="utf-8",
    )


def _write_self_build_docs_pilot_signal(
    workspace: Path,
    *,
    write_anatomy: bool = True,
) -> None:
    proposal = (
        workspace
        / "docs"
        / "proposals"
        / "self-build-grounded-target-coverage-proposal.md"
    )
    proposal.parent.mkdir(parents=True)
    proposal.write_text(_SELF_BUILD_PROPOSAL, encoding="utf-8")
    if write_anatomy:
        (workspace / "docs" / "AGENT_ANATOMY.md").write_text(
            "## Candidate follow-ups (TD-030+) -- advisory only\n\n"
            "1. **TD-030 (candidate): Unify the role mechanisms.** Later.\n",
            encoding="utf-8",
        )


def _write_anatomy_candidate(workspace: Path) -> None:
    (workspace / "docs").mkdir(parents=True, exist_ok=True)
    (workspace / "docs" / "AGENT_ANATOMY.md").write_text(
        "## Candidate follow-ups (TD-030+) -- advisory only\n\n"
        "1. **TD-030 (candidate): Unify the role mechanisms.** Later.\n",
        encoding="utf-8",
    )


def test_legacy_llm_manager_flag_preserves_llm_selection(workspace: Path):
    # Explicit legacy opt-in: Manager selects via the LLM exactly as before.
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm)  # helper injects legacy_llm_manager=True
    assert report.status == "proposed"
    assert [r.role for r in report.role_outputs][0] == "manager"
    # The manager consulted the LLM (first canned response consumed).
    assert any("Manager" in c["system"] for c in llm.calls)


def test_default_uses_grounded_selector_not_llm(workspace: Path):
    # TD-036 follow-up: with neither a grounded_selector nor the legacy flag, the
    # producer builds the default workspace-backed grounded selector. The tmp
    # workspace has no TECH_DEBT.md / anatomy backlog, so the closed set is empty
    # and the Manager refuses WITHOUT ever consulting the LLM.
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([_manager_ok(), _builder_ok()])  # must never be consulted
    report = produce_self_apply_proposal(
        workspace=workspace,
        inbox=inbox,
        llm=llm,
        vcs=FakeVCS(clean=True),
        budget_snapshot=_headroom_budget(),
        kill_switch=FakeKillSwitch(active=False),
    )
    assert report.status == "no_patch"
    assert [r.role for r in report.role_outputs] == ["manager"]
    assert report.role_outputs[0].decision == "no_target"
    assert llm.calls == []  # no LLM invention, no builder work
    assert inbox.list() == []


def test_default_grounded_selector_is_read_only(workspace: Path, monkeypatch):
    # The default selector reads TECH_DEBT.md / anatomy / value-reviews strictly
    # read-only; a broken backlog loader must degrade to "no candidate", never
    # crash the producer and never reach the LLM.
    import core.self_build_producer as mod

    def _boom_loader(*a, **k):
        raise RuntimeError("backlog load exploded")

    # backlog_selector.load_backlog is imported lazily inside the selector.
    import core.backlog_selector as bl
    monkeypatch.setattr(bl, "load_backlog", _boom_loader)

    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = produce_self_apply_proposal(
        workspace=workspace,
        inbox=inbox,
        llm=llm,
        vcs=FakeVCS(clean=True),
        budget_snapshot=_headroom_budget(),
        kill_switch=FakeKillSwitch(active=False),
    )
    assert report.status == "no_patch"
    assert report.role_outputs[0].decision == "no_target"
    assert llm.calls == []


def test_grounded_candidate_used_without_inventing_diagnosis(workspace: Path):
    inbox = ApprovalInbox(path=None)
    candidate = _Candidate(_TARGET, "grounded: fix the real bug", "TECH_DEBT.md:42")
    # No manager response provided: if the LLM were consulted for selection this
    # would fall through to the {} default. We assert the LLM is NOT asked to
    # select a manager target.
    llm = FakeLLM([_builder_custom("VALUE = 2\n", "small fix")])
    report = _produce(
        workspace,
        llm=llm,
        inbox=inbox,
        grounded_selector=lambda: candidate,
    )
    assert report.status == "proposed"
    manager = report.role_outputs[0]
    assert manager.role == "manager" and manager.decision == "selected"
    assert manager.data["grounded"] is True
    assert manager.data["diagnosis"] == "grounded: fix the real bug"
    assert manager.data["evidence_ref"] == "TECH_DEBT.md:42"
    # The Manager never invented a diagnosis via the LLM.
    assert not any("Manager" in c["system"] for c in llm.calls)


def test_grounded_td_011_012_maps_to_concrete_target_without_manager_llm(
    workspace: Path,
):
    _write_model_discovery_mapping_evidence(workspace)
    inbox = ApprovalInbox(path=None)
    candidate = _Candidate(
        "TD-011 / TD-012",
        _TD_011_012_TITLE,
        "TECH_DEBT.md:1",
    )
    llm = FakeLLM([_builder_custom("NEW = 1\n", "preserve read-only discovery")])

    report = _produce(
        workspace,
        llm=llm,
        inbox=inbox,
        grounded_selector=lambda: candidate,
        file_reader=_reader({MODEL_DISCOVERY_TARGET: "OLD = 0\n"}),
    )

    assert report.status == "proposed"
    assert report.target_path == MODEL_DISCOVERY_TARGET
    manager = report.role_outputs[0]
    assert manager.data["mapping_decision"] == "mapped"
    assert manager.data["mapping_rule"] == "td_011_012_model_discovery"
    assert manager.data["source_target_path"] == "TD-011 / TD-012"
    assert manager.data["diagnosis"] == _TD_011_012_TITLE
    assert not any("Manager" in c["system"] for c in llm.calls)
    assert len(inbox.list()) == 1


def test_default_grounded_selector_proposes_docs_pilot_without_manager_llm(
    workspace: Path,
):
    _write_self_build_docs_pilot_signal(workspace)
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM(
        [
            _builder_custom(
                _self_build_doc_content(),
                "document the self-build pilot",
            )
        ]
    )

    report = produce_self_apply_proposal(
        workspace=workspace,
        inbox=inbox,
        llm=llm,
        vcs=FakeVCS(clean=True),
        budget_snapshot=_headroom_budget(),
        kill_switch=FakeKillSwitch(active=False),
        file_reader=_reader({}),
    )

    assert report.status == "proposed"
    assert report.target_path == _SELF_BUILD_DOC_TARGET
    manager = report.role_outputs[0]
    assert manager.data["mapping_decision"] == "concrete"
    assert manager.data["source_target_path"] == _SELF_BUILD_DOC_TARGET
    assert manager.data["evidence_ref"].startswith(
        "docs/proposals/self-build-grounded-target-coverage-proposal.md:"
    )
    assert not any("Manager" in c["system"] for c in llm.calls)
    assert len(inbox.list()) == 1


def test_default_grounded_selector_does_not_repeat_docs_pilot_after_doc_exists(
    workspace: Path,
):
    _write_self_build_docs_pilot_signal(workspace, write_anatomy=False)
    (workspace / "docs" / "self_build.md").write_text(
        "# Self-build\n\nAlready created.\n",
        encoding="utf-8",
    )
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM(
        [
            _builder_custom(
                "# Self-build\n\nRepeat proposal that must not happen.\n",
                "repeat docs pilot",
            )
        ]
    )

    report = produce_self_apply_proposal(
        workspace=workspace,
        inbox=inbox,
        llm=llm,
        vcs=FakeVCS(clean=True),
        budget_snapshot=_headroom_budget(),
        kill_switch=FakeKillSwitch(active=False),
        file_reader=_reader({_SELF_BUILD_DOC_TARGET: "# Self-build\n"}),
    )

    assert report.status == "no_patch"
    assert report.role_outputs[0].decision == "no_target"
    assert report.reason == "no grounded backlog candidate"
    assert llm.calls == []
    assert inbox.list() == []


def test_default_grounded_selector_keeps_anatomy_candidates_blocked(
    workspace: Path,
):
    _write_anatomy_candidate(workspace)
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([_builder_custom("VALUE = 2\n", "must not run")])

    report = produce_self_apply_proposal(
        workspace=workspace,
        inbox=inbox,
        llm=llm,
        vcs=FakeVCS(clean=True),
        budget_snapshot=_headroom_budget(),
        kill_switch=FakeKillSwitch(active=False),
    )

    assert report.status == "no_patch"
    manager = report.role_outputs[0]
    assert manager.decision == "no_target"
    assert manager.data["mapping_decision"] == "no_target"
    assert manager.data["rejected_target"].startswith("anatomy:")
    assert llm.calls == []
    assert inbox.list() == []


def test_grounded_selector_none_returns_no_target_not_invented(workspace: Path):
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([_manager_ok(), _builder_ok()])  # would be used only if invented
    report = _produce(
        workspace,
        llm=llm,
        inbox=inbox,
        grounded_selector=lambda: None,
    )
    assert report.status == "no_patch"
    assert [r.role for r in report.role_outputs] == ["manager"]
    assert report.role_outputs[0].decision == "no_target"
    assert llm.calls == []  # no LLM invention, no builder work
    assert inbox.list() == []


def test_grounded_off_allowlist_target_is_no_target(workspace: Path):
    inbox = ApprovalInbox(path=None)
    candidate = _Candidate("TD-060", "an open backlog item")  # not a code target
    llm = FakeLLM([])
    report = _produce(
        workspace,
        llm=llm,
        inbox=inbox,
        candidate_targets=(_TARGET,),
        grounded_selector=lambda: candidate,
    )
    assert report.status == "no_patch"
    assert report.role_outputs[0].decision == "no_target"
    assert inbox.list() == []


def test_grounded_critical_target_is_no_target(workspace: Path):
    inbox = ApprovalInbox(path=None)
    candidate = _Candidate("core/loop.py", "touch the brain")
    report = _produce(
        workspace,
        llm=FakeLLM([]),
        inbox=inbox,
        candidate_targets=("core/loop.py",),
        grounded_selector=lambda: candidate,
        file_reader=_reader({"core/loop.py": "x = 1\n"}),
    )
    assert report.status == "no_patch"
    assert report.role_outputs[0].decision == "no_target"


def test_grounded_ambiguous_mapping_refuses_without_legacy_or_approval(workspace: Path):
    inbox = ApprovalInbox(path=None)
    candidate = _Candidate("TD-999", "TD-999 \u2014 vague work", "TECH_DEBT.md:1")
    llm = FakeLLM([_manager_ok(), _builder_ok()])

    report = _produce(
        workspace,
        llm=llm,
        inbox=inbox,
        grounded_selector=lambda: candidate,
    )

    assert report.status == "no_patch"
    assert report.role_outputs[0].decision == "no_target"
    assert report.role_outputs[0].data["mapping_decision"] == "no_target"
    assert llm.calls == []
    assert inbox.list() == []


def test_grounded_mapping_missing_evidence_refuses_without_approval(workspace: Path):
    inbox = ApprovalInbox(path=None)
    (workspace / "TECH_DEBT.md").write_text(
        f"{_TD_011_012_TITLE}\nStatus: Partial.\n",
        encoding="utf-8",
    )
    candidate = _Candidate(
        "TD-011 / TD-012",
        _TD_011_012_TITLE,
        "TECH_DEBT.md:1",
    )
    llm = FakeLLM([_manager_ok(), _builder_ok()])

    report = _produce(
        workspace,
        llm=llm,
        inbox=inbox,
        grounded_selector=lambda: candidate,
    )

    assert report.status == "no_patch"
    assert report.role_outputs[0].decision == "no_target"
    assert report.role_outputs[0].data["mapping_decision"] == "unknown"
    assert "missing_evidence" in report.reason
    assert llm.calls == []
    assert inbox.list() == []


def test_broken_selector_does_not_break_producer(workspace: Path):
    def _boom():
        raise RuntimeError("selector exploded")

    report = _produce(
        workspace,
        llm=FakeLLM([]),
        grounded_selector=_boom,
    )
    # A raising selector is treated as "no grounded candidate", never a crash.
    assert report.status == "no_patch"
    assert report.role_outputs[0].decision == "no_target"
