"""C6 — one-shot construction policy, and the two pre-dotenv fast paths.

Two different things are recorded here:

*Public contract* — one-shot is memory-free (`with_memory=False`,
`with_persistent=False`) and its approval policy follows `--auto-approve`, with
`off` meaning "no provider wired, escalated tools blocked".

*Implementation behavior frozen only for extraction* — an ordinary one-shot
meta-command builds the full agent **before** `handle_meta_command` runs, so a
local, no-LLM command still constructs a provider-backed agent. That is recorded
with call-order fakes so extraction cannot change it silently; whether it
*should* stay that way is a separate, explicit behavior-change decision.

The two pre-dotenv fast paths (`:self-build-propose`, `:schedule-disable`) return
before `load_dotenv()` and before any agent build. Their no-side-effect
behavior with the real handlers is already covered by
tests/test_cli.py::test_self_build_propose_one_shot_short_circuits_before_agent_build
and ::test_schedule_disable_one_shot_short_circuits_before_agent_build; here the
handlers are faked so the *ordering* is what is asserted.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import app.budget_guard as budget_guard_module
import cli.app as app_module
import cli.command_dispatch as dispatch_module
import cli.intent_bridge as bridge_module
import main as main_module


def _fake_agent() -> SimpleNamespace:
    return SimpleNamespace(log=SimpleNamespace(log=lambda *a, **k: None, trace_id="trace-fake"))


def _patch(monkeypatch: pytest.MonkeyPatch, order: list[str]) -> list[dict]:
    build_calls: list[dict] = []

    def fake_load_dotenv(*a, **k):
        order.append("load_dotenv")

    def fake_build_agent(workspace, **kwargs):
        order.append("build_agent")
        build_calls.append({"workspace": workspace, **kwargs})
        return _fake_agent()

    monkeypatch.setattr(app_module, "load_dotenv", fake_load_dotenv)
    monkeypatch.setattr(app_module, "build_agent", fake_build_agent)
    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", lambda *a, **k: False)
    monkeypatch.setattr(bridge_module, "handle_conversational_operator_input", lambda *a, **k: False)
    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard", lambda *a, **k: "answer")
    return build_calls


@pytest.mark.parametrize(
    "flag,expected_type,expected_default",
    [
        ("approve", "AutoApprover", "approve"),
        ("deny", "AutoApprover", "deny"),
    ],
)
def test_auto_approve_builds_an_autoapprover_with_that_default(
    flag, expected_type, expected_default, tmp_path, monkeypatch
):
    order: list[str] = []
    build_calls = _patch(monkeypatch, order)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--workspace", str(tmp_path), "--auto-approve", flag, "--ask", "q"],
    )

    assert main_module.main() == 0

    provider = build_calls[0]["approval_provider"]
    assert type(provider).__name__ == expected_type
    assert provider.default == expected_default


def test_auto_approve_off_wires_no_provider_in_one_shot(tmp_path, monkeypatch):
    order: list[str] = []
    build_calls = _patch(monkeypatch, order)
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", "q"])

    assert main_module.main() == 0

    # 'off' in one-shot = no provider wired = escalated tools blocked.
    assert build_calls[0]["approval_provider"] is None


def test_one_shot_is_memory_free(tmp_path, monkeypatch):
    order: list[str] = []
    build_calls = _patch(monkeypatch, order)
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", "q"])

    assert main_module.main() == 0

    assert build_calls[0]["with_memory"] is False
    assert build_calls[0]["with_persistent"] is False


def test_dotenv_is_loaded_before_the_agent_is_built(tmp_path, monkeypatch):
    order: list[str] = []
    _patch(monkeypatch, order)
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", "q"])

    assert main_module.main() == 0
    assert order == ["load_dotenv", "build_agent"]


def test_ordinary_meta_command_builds_the_agent_before_dispatch(tmp_path, monkeypatch):
    """Frozen for extraction — NOT an endorsement of building an agent for a
    local command. See docs/refactor/CLI_BASELINE.md §3."""
    order: list[str] = []
    _patch(monkeypatch, order)

    def fake_meta(cmd, agent, workspace):
        order.append(f"handle_meta_command:{cmd}")
        return True

    monkeypatch.setattr(dispatch_module, "handle_meta_command", fake_meta)
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", ":models"])

    assert main_module.main() == 0
    assert order == ["load_dotenv", "build_agent", "handle_meta_command::models"]


def test_meta_command_one_shot_is_still_memory_free(tmp_path, monkeypatch):
    order: list[str] = []
    build_calls = _patch(monkeypatch, order)
    monkeypatch.setattr(dispatch_module, "handle_meta_command", lambda *a, **k: True)
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", ":models"])

    assert main_module.main() == 0
    assert build_calls[0]["with_memory"] is False
    assert build_calls[0]["with_persistent"] is False


def test_deep_escalation_is_opt_in_via_reason_and_expect(tmp_path, monkeypatch):
    order: list[str] = []
    _patch(monkeypatch, order)
    seen: list[object] = []

    def fake_run(agent, **kwargs):
        seen.append(kwargs["deep_escalation"])
        return "answer"

    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard", fake_run)

    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", "q"])
    assert main_module.main() == 0
    assert seen[-1] is None, "no --reason/--expect must mean no escalation object"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--workspace",
            str(tmp_path),
            "--ask",
            "q",
            "--reason",
            "operator_explicitly_requested_opus",
            "--expect",
            "minimal_patch_plan",
        ],
    )
    assert main_module.main() == 0
    escalation = seen[-1]
    assert escalation is not None
    assert escalation.reason == "operator_explicitly_requested_opus"
    assert escalation.expected_output == "minimal_patch_plan"


# ── pre-dotenv fast paths ─────────────────────────────────────────────────────

def test_self_build_propose_returns_before_dotenv_and_agent(tmp_path, monkeypatch):
    order: list[str] = []
    build_calls = _patch(monkeypatch, order)
    seen: list[tuple] = []

    def fake_propose(rest, agent, workspace):
        seen.append((rest, agent, workspace))
        return True

    monkeypatch.setattr(app_module, "_handle_self_build_propose", fake_propose)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--workspace", str(tmp_path), "--ask", ":self-build-propose --large-files"],
    )

    assert main_module.main() == 0

    assert order == [], "no dotenv load and no agent build on this path"
    assert build_calls == []
    assert seen == [("--large-files", None, tmp_path.resolve())]
    for runtime_dir in ("data", "logs", "config"):
        assert not (tmp_path / runtime_dir).exists()


def test_schedule_disable_returns_before_dotenv_and_agent(tmp_path, monkeypatch, capsys):
    order: list[str] = []
    build_calls = _patch(monkeypatch, order)
    monkeypatch.setattr(app_module, "_schedule_disable_message", lambda rest, ws: f"SCHEDULE_DISABLED {rest}"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--workspace", str(tmp_path), "--ask", ":schedule-disable sch_1"],
    )

    assert main_module.main() == 0

    assert order == []
    assert build_calls == []
    out = capsys.readouterr()
    assert out.err.strip() == "SCHEDULE_DISABLED sch_1"
    assert out.out == ""
    for runtime_dir in ("data", "logs", "config"):
        assert not (tmp_path / runtime_dir).exists()


@pytest.mark.parametrize("spelling", [":SELF-BUILD-PROPOSE", ":Self-Build-Propose"])
def test_fast_path_matching_is_case_insensitive(spelling, tmp_path, monkeypatch):
    order: list[str] = []
    _patch(monkeypatch, order)
    seen: list[str] = []
    monkeypatch.setattr(app_module, "_handle_self_build_propose",
        lambda rest, agent, workspace: seen.append(rest) or True,
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", spelling])

    assert main_module.main() == 0
    assert order == []
    assert seen == [""]


def test_fast_path_only_applies_to_the_head_token(tmp_path, monkeypatch):
    """A mention of the command mid-sentence must not trigger the fast path."""
    order: list[str] = []
    _patch(monkeypatch, order)
    monkeypatch.setattr(app_module, "_handle_self_build_propose",
        lambda *a, **k: pytest.fail("fast path must not fire for a mid-sentence mention"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--workspace", str(tmp_path), "--ask", "tell me about :self-build-propose"],
    )

    assert main_module.main() == 0
    assert order == ["load_dotenv", "build_agent"]


def test_the_encoding_fix_runs_before_argparse_can_print(tmp_path, monkeypatch):
    """`--help` is printed and exited from INSIDE `parse_args`.

    Measured 2026-08-05: with `_force_utf8_io()` placed after the parse it was
    invoked zero times on that path, and the em dash in the parser's own
    description reached a cp1251 console as a replacement char — a broken
    glyph in the first line the operator ever sees. Nothing in the call
    depends on the parsed arguments, so the only thing keeping it late was
    that no test asked.

    Guarding the ORDER, not the call: a test that merely asserts the function
    runs somewhere would stay green with the defect back in place.
    """
    calls: list[str] = []
    monkeypatch.setattr(app_module, "_force_utf8_io", lambda: calls.append("utf8"))

    real_parser = app_module.build_parser

    def _recording_parser():
        calls.append("parse")
        return real_parser()

    monkeypatch.setattr(app_module, "build_parser", _recording_parser)
    monkeypatch.setattr(sys, "argv", ["main.py", "--help"])

    with pytest.raises(SystemExit):
        app_module.run_cli()

    assert calls == ["utf8", "parse"], (
        "the encoding fix must run before the parser is even built; "
        f"got {calls}"
    )
