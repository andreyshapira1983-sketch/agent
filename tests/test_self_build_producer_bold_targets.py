"""Tests for Provod #2: widened grounded self-build target acceptance.

The producer no longer limits grounded candidates to a hardcoded trio. A
grounded backlog candidate may now target any file that clears BOTH hard safety
layers — the critical-organ denylist and the self-apply lane's low-risk
classifier — while apply authority (dry-run + human approval + passing tests)
stays exactly the same.
"""
from __future__ import annotations

from core.self_build_producer import (
    DEFAULT_CANDIDATE_TARGETS,
    _is_self_build_target_allowed,
    _manager_from_grounded,
)


class _Candidate:
    def __init__(
        self,
        target_path: str,
        problem_quote: str = "a grounded problem",
        evidence_ref: str = "architecture_audit:x",
        signal_source: str = "architecture_audit",
    ):
        self.target_path = target_path
        self.signal_source = signal_source
        self.problem_quote = problem_quote
        self.evidence_ref = evidence_ref


# ── acceptance policy ─────────────────────────────────────────────────────────


def test_policy_accepts_lane_low_risk_files():
    # core/cli/tools/tests *.py, docs/**, *.md are low-risk in the lane.
    assert _is_self_build_target_allowed("core/release_hygiene.py")
    assert _is_self_build_target_allowed("cli/commands_self_build.py")
    assert _is_self_build_target_allowed("tools/anything.py")
    assert _is_self_build_target_allowed("tests/test_something.py")
    assert _is_self_build_target_allowed("docs/whatever.md")
    assert _is_self_build_target_allowed("AGENT_DOCTRINE.md")


def test_policy_rejects_critical_organs():
    for organ in (
        "main.py",
        "core/loop.py",
        "core/autonomous_runtime.py",
        "core/safe_vcs.py",
        "core/self_apply_lane.py",
        "core/self_build_producer.py",
        "config/anything.yaml",
    ):
        assert not _is_self_build_target_allowed(organ), organ


def test_policy_rejects_lane_denied_files():
    for denied in (
        ".github/workflows/ci.yml",
        "secrets/token.txt",
        "requirements.lock",
        "server.pem",
        "id_rsa.key",
        "TD-060",              # not a real file path at all
        "core/data.json",     # not .py / .md / docs
    ):
        assert not _is_self_build_target_allowed(denied), denied


# ── manager wiring: audit target now selectable ───────────────────────────────


def test_grounded_audit_target_now_selected(tmp_path):
    # A real architecture-audit finding (release_hygiene.py) is NOT in the seed
    # trio, yet the manager now selects it because it clears both safety layers.
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "release_hygiene.py").write_text("x = 1\n", encoding="utf-8")
    candidate = _Candidate("core/release_hygiene.py", "Release / Supply-Chain Guard")

    out = _manager_from_grounded(
        lambda: candidate,
        DEFAULT_CANDIDATE_TARGETS,
        workspace=tmp_path,
    )

    assert out.decision == "selected"
    assert out.data["target"] == "core/release_hygiene.py"
    assert out.data["grounded"] is True


def test_grounded_doctrine_doc_target_now_selected(tmp_path):
    (tmp_path / "AGENT_DOCTRINE.md").write_text("# Doctrine\n", encoding="utf-8")
    candidate = _Candidate(
        "AGENT_DOCTRINE.md", "Doctrine and Architecture Source of Truth"
    )

    out = _manager_from_grounded(
        lambda: candidate,
        DEFAULT_CANDIDATE_TARGETS,
        workspace=tmp_path,
    )

    assert out.decision == "selected"
    assert out.data["target"] == "AGENT_DOCTRINE.md"


def test_grounded_critical_target_still_refused(tmp_path):
    candidate = _Candidate("core/loop.py", "touch the brain")

    out = _manager_from_grounded(
        lambda: candidate,
        DEFAULT_CANDIDATE_TARGETS,
        workspace=tmp_path,
    )

    assert out.decision == "no_target"


def test_grounded_denied_config_target_still_refused(tmp_path):
    candidate = _Candidate("config/policy.yaml", "tweak the policy")

    out = _manager_from_grounded(
        lambda: candidate,
        DEFAULT_CANDIDATE_TARGETS,
        workspace=tmp_path,
    )

    assert out.decision == "no_target"


def test_grounded_abstract_non_file_target_still_refused(tmp_path):
    candidate = _Candidate("TD-060", "an open backlog item", evidence_ref="ref")

    out = _manager_from_grounded(
        lambda: candidate,
        DEFAULT_CANDIDATE_TARGETS,
        workspace=tmp_path,
    )

    assert out.decision == "no_target"


# ── selector prefers the first ACTIONABLE ranked candidate ────────────────────


def test_default_selector_skips_non_actionable_higher_ranked(tmp_path, monkeypatch):
    # A higher-ranked but denied target (.github/...) must not shadow a lower-
    # ranked but actionable one (README.md). The selector returns the actionable.
    import core.backlog_selector as bs
    from core.self_build_producer import _default_grounded_selector

    denied = _Candidate(".github/workflows/ci.yml", "Release / Supply-Chain Guard")
    actionable = _Candidate("README.md", "Doctrine and Architecture Source of Truth")
    monkeypatch.setattr(bs, "load_backlog", lambda *a, **k: [denied, actionable])

    selected = _default_grounded_selector(tmp_path)()

    assert selected.target_path == "README.md"


def test_default_selector_falls_back_to_top_when_none_actionable(tmp_path, monkeypatch):
    import core.backlog_selector as bs
    from core.self_build_producer import _default_grounded_selector

    denied1 = _Candidate(".github/workflows/ci.yml", "gap one")
    denied2 = _Candidate("requirements.lock", "gap two")
    monkeypatch.setattr(bs, "load_backlog", lambda *a, **k: [denied1, denied2])
    monkeypatch.setattr(bs, "select_top", lambda c: c[0] if c else None)

    selected = _default_grounded_selector(tmp_path)()

    assert selected.target_path == ".github/workflows/ci.yml"  # honest #1 refusal

