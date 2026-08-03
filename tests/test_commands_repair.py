"""`:repair` / `:propose-repair` — the REPL door to the self-repair lane.

This module rewrites files in the operator's own project, so the handler's
argument parsing is safety-relevant: which token is read as the *proposal
file*, which as a *test path*, and whether the lane is entered at all. Those
branches were 9% covered.

Everything here drives `cli/commands_repair.py` with a fake agent. The handler
only ever touches two agent methods and two attributes of what they return
(`.proposal`, `.user_summary()`), so a fake keeps the tests about the handler's
own decisions — and `test_report_contracts_match_the_fakes` pins the fakes to
the real classes so the shortcut cannot rot.

No test writes outside `tmp_path`, and no test reaches the real repair
controller: `agent.repair` is never the real one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cli.commands_repair import _handle_propose_repair, _handle_repair
from core.self_repair import RepairProposal


class FakeReport:
    """Stands in for RepairReport / ProposalGenerationReport."""

    def __init__(self, summary: str = "SUMMARY", proposal: object | None = None):
        self._summary = summary
        self.proposal = proposal

    def user_summary(self) -> str:
        return self._summary


class FakeAgent:
    """Records calls; raises on demand. Never touches the repair controller."""

    def __init__(
        self,
        *,
        gen_report: object | None = None,
        apply_report: object | None = None,
        gen_error: Exception | None = None,
        apply_error: Exception | None = None,
    ):
        self.gen_calls: list[dict] = []
        self.apply_calls: list[dict] = []
        self._gen_report = gen_report
        self._apply_report = apply_report
        self._gen_error = gen_error
        self._apply_error = apply_error

    def propose_repair(self, **kwargs):
        self.gen_calls.append(kwargs)
        if self._gen_error is not None:
            raise self._gen_error
        return self._gen_report

    def repair(self, proposal, **kwargs):
        self.apply_calls.append({"proposal": proposal, **kwargs})
        if self._apply_error is not None:
            raise self._apply_error
        return self._apply_report


def _proposal_file(workspace: Path, rel: str = "tmp/foo.proposed.py", body: str = "x = 1\n") -> str:
    p = workspace / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return rel


# ── argument surface ─────────────────────────────────────────────────────────

def test_bare_repair_prints_usage_and_touches_no_agent(tmp_path, capsys):
    agent = FakeAgent()
    assert _handle_repair("", agent, tmp_path) is True
    err = capsys.readouterr().err
    assert "Usage: :repair <target_path>" in err
    assert agent.gen_calls == [] and agent.apply_calls == []


def test_pattern_without_a_value_is_refused_before_the_lane(tmp_path, capsys):
    agent = FakeAgent()
    assert _handle_repair("core/foo.py --pattern", agent, tmp_path) is True
    assert "--pattern requires a value" in capsys.readouterr().err
    assert agent.gen_calls == [], "no proposal may be generated on a usage error"


def test_pattern_is_extracted_and_removed_from_the_positional_tokens(tmp_path):
    agent = FakeAgent(gen_report=FakeReport(proposal=None))
    _handle_repair("core/foo.py --pattern test_x tests/test_foo.py", agent, tmp_path)
    call = agent.gen_calls[0]
    assert call["test_pattern"] == "test_x"
    assert call["test_paths"] == ("tests/test_foo.py",), "the flag pair must not leak into test paths"


# ── one-click mode (no proposal file) ────────────────────────────────────────

def test_one_click_defaults_to_the_tests_directory(tmp_path):
    agent = FakeAgent(gen_report=FakeReport(proposal=None))
    _handle_repair("core/foo.py", agent, tmp_path)
    call = agent.gen_calls[0]
    assert call["target_path"] == "core/foo.py"
    assert call["test_paths"] == ("tests",)
    assert call["test_pattern"] is None
    assert call["workspace_root"] == tmp_path


def test_one_click_generates_then_applies(tmp_path, capsys):
    proposal = RepairProposal(path="core/foo.py", proposed_content="y = 2\n")
    agent = FakeAgent(
        gen_report=FakeReport("GEN-OK", proposal=proposal),
        apply_report=FakeReport("APPLY-OK"),
    )
    assert _handle_repair("core/foo.py", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "GEN-OK" in err and "APPLY-OK" in err
    assert agent.apply_calls[0]["proposal"] is proposal, "the generated proposal is applied as-is"
    assert agent.apply_calls[0]["workspace_root"] == tmp_path


def test_one_click_stops_when_generation_produces_no_proposal(tmp_path, capsys):
    agent = FakeAgent(gen_report=FakeReport("GEN-EMPTY", proposal=None))
    assert _handle_repair("core/foo.py", agent, tmp_path) is True
    err = capsys.readouterr().err
    assert "GEN-EMPTY" in err
    assert "no proposal generated" in err
    assert agent.apply_calls == [], "nothing may be applied when there is no proposal"


def test_generation_failure_is_reported_and_nothing_is_applied(tmp_path, capsys):
    agent = FakeAgent(gen_error=RuntimeError("llm exploded"))
    assert _handle_repair("core/foo.py", agent, tmp_path) is True
    err = capsys.readouterr().err
    assert "proposal generation failed: RuntimeError: llm exploded" in err
    assert agent.apply_calls == []


def test_apply_failure_is_reported_after_a_successful_generation(tmp_path, capsys):
    proposal = RepairProposal(path="core/foo.py", proposed_content="y = 2\n")
    agent = FakeAgent(
        gen_report=FakeReport("GEN-OK", proposal=proposal),
        apply_error=OSError("disk gone"),
    )
    assert _handle_repair("core/foo.py", agent, tmp_path) is True
    err = capsys.readouterr().err
    assert "GEN-OK" in err
    assert "apply failed: OSError: disk gone" in err


# ── which token becomes the proposal file ────────────────────────────────────

def test_existing_py_file_outside_tests_is_read_as_the_proposal(tmp_path, capsys):
    rel = _proposal_file(tmp_path, body="patched = True\n")
    agent = FakeAgent(apply_report=FakeReport("APPLY-OK"))

    assert _handle_repair(f"core/foo.py {rel}", agent, tmp_path) is True

    assert agent.gen_calls == [], "two-step mode must not generate a new proposal"
    applied = agent.apply_calls[0]["proposal"]
    assert isinstance(applied, RepairProposal)
    assert applied.path == "core/foo.py"
    assert applied.proposed_content == "patched = True\n"
    assert applied.test_paths == ("tests",)
    assert rel in applied.reason
    assert "APPLY-OK" in capsys.readouterr().err


def test_two_step_keeps_explicit_test_paths_after_the_proposal_file(tmp_path):
    rel = _proposal_file(tmp_path)
    agent = FakeAgent(apply_report=FakeReport())
    _handle_repair(f"core/foo.py {rel} tests/test_a.py tests/test_b.py", agent, tmp_path)
    applied = agent.apply_calls[0]["proposal"]
    assert applied.test_paths == ("tests/test_a.py", "tests/test_b.py")


def test_two_step_carries_the_pattern_into_the_proposal(tmp_path):
    rel = _proposal_file(tmp_path)
    agent = FakeAgent(apply_report=FakeReport())
    _handle_repair(f"core/foo.py {rel} --pattern test_only_this", agent, tmp_path)
    assert agent.apply_calls[0]["proposal"].test_pattern == "test_only_this"


@pytest.mark.parametrize(
    "second_token,why",
    [
        ("tests/test_foo.py", "a path under tests/ is a test path even if it exists"),
        ("core/missing.py", "a .py path that does not exist is not a proposal file"),
        ("notes.txt", "a non-.py file is not a proposal file"),
    ],
)
def test_second_token_that_is_not_a_proposal_file_becomes_a_test_path(
    tmp_path, second_token, why
):
    # Make every candidate exist so only the rule under test can reject it.
    p = tmp_path / second_token
    p.parent.mkdir(parents=True, exist_ok=True)
    if second_token != "core/missing.py":
        p.write_text("# fixture\n", encoding="utf-8")

    agent = FakeAgent(gen_report=FakeReport(proposal=None))
    _handle_repair(f"core/foo.py {second_token}", agent, tmp_path)

    assert agent.gen_calls, f"one-click mode expected: {why}"
    assert agent.gen_calls[0]["test_paths"] == (second_token,)


def test_proposal_path_escaping_the_workspace_stops_before_the_controller(tmp_path, capsys):
    """A real file *outside* the workspace is rejected by the path resolver.

    The token has to name an existing `.py` file, otherwise the handler reads it
    as a test path and never enters two-step mode — so the file is created in
    the parent directory, which is exactly what makes `..` traversal a threat.
    """
    outside = tmp_path.parent / "outside_proposal.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    agent = FakeAgent(apply_report=FakeReport())

    assert _handle_repair(f"core/foo.py ../{outside.name}", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "repair failed before controller run" in err
    assert agent.apply_calls == [], "the lane must not run on a rejected proposal path"


# ── :propose-repair ──────────────────────────────────────────────────────────

def test_propose_repair_reports_a_parse_error_without_calling_the_agent(tmp_path, capsys):
    agent = FakeAgent()
    assert _handle_propose_repair("", agent, tmp_path) is True
    assert "Usage: :propose-repair" in capsys.readouterr().err
    assert agent.gen_calls == []


def test_propose_repair_forwards_every_parsed_argument(tmp_path, capsys):
    agent = FakeAgent(gen_report=FakeReport("GEN-ONLY"))
    assert _handle_propose_repair(
        "core/foo.py tests/test_foo.py --pattern test_x --trace run_42", agent, tmp_path
    ) is True

    call = agent.gen_calls[0]
    assert call == {
        "target_path": "core/foo.py",
        "workspace_root": tmp_path,
        "test_paths": ("tests/test_foo.py",),
        "test_pattern": "test_x",
        "trace_id": "run_42",
    }
    assert "GEN-ONLY" in capsys.readouterr().err
    assert agent.apply_calls == [], ":propose-repair must never apply anything"


# ── the fakes are held to the real contracts ─────────────────────────────────

def test_report_contracts_match_the_fakes():
    """If the real reports change shape, the fakes above stop being valid."""
    from core.repair_proposal import ProposalGenerationReport
    from core.self_repair_models import RepairReport

    assert callable(ProposalGenerationReport.user_summary)
    assert "proposal" in ProposalGenerationReport.__dataclass_fields__
    assert callable(RepairReport.user_summary)

    fields = RepairProposal.__dataclass_fields__
    for name in ("path", "proposed_content", "test_paths", "test_pattern", "reason"):
        assert name in fields, f"cli/commands_repair.py constructs RepairProposal({name}=…)"
