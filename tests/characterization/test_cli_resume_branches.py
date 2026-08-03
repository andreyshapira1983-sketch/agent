"""C3/C4 — `--resume` trace-ID validation and all four checkpoint branches.

Observed branch order at 9daa9bf (`main.py:1938-1992`):

1. trace ID fails the allowlist            -> stderr message, return 2
2. loader returns None                     -> "Running fresh." then normal run
3. last phase is `paused` and paused data  -> restore question/file hint, run
4. a cached `answer` exists                -> replay to stdout, return 0
5. otherwise (synthesis incomplete)        -> notice, restore question, run

Branch 3 is checked *before* branch 4, so a paused last phase wins over a cached
answer. That ordering is recorded here so extraction cannot silently reverse it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.budget_guard as budget_guard_module
import cli.app as app_module
import cli.intent_bridge as bridge_module
import main as main_module
from core.checkpoint import CheckpointWriter
from core.loop import format_human_response

REPO_ROOT = Path(main_module.__file__).resolve().parent


def _fake_agent() -> SimpleNamespace:
    return SimpleNamespace(log=SimpleNamespace(log=lambda *a, **k: None, trace_id="trace-fake"))


def _patch(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch the collaborators a resume run would otherwise reach."""
    state: dict = {"build": [], "run": []}
    monkeypatch.setattr(app_module, "load_dotenv", lambda *a, **k: None)

    def fake_build_agent(workspace, **kwargs):
        state["build"].append({"workspace": workspace, **kwargs})
        return _fake_agent()

    def fake_run(agent, **kwargs):
        state["run"].append(kwargs)
        return "fresh answer"

    monkeypatch.setattr(app_module, "build_agent", fake_build_agent)
    monkeypatch.setattr(budget_guard_module, "_run_agent_with_budget_guard", fake_run)
    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", lambda *a, **k: False)
    monkeypatch.setattr(bridge_module, "handle_conversational_operator_input", lambda *a, **k: False)
    return state


def _argv(tmp_path: Path, trace: str, *extra: str) -> list[str]:
    return ["main.py", "--workspace", str(tmp_path), "--resume", trace, *extra]


# ── branch 1: invalid trace ID ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "bad",
    [
        "not/a/valid/id",
        "..",
        "../escape",
        "has.dot",
        "has space",
        "_leading-underscore",
        "",
    ],
)
def test_invalid_trace_id_returns_two_without_building_agent(bad, tmp_path, monkeypatch, capsys):
    state = _patch(monkeypatch)
    monkeypatch.setattr(sys, "argv", _argv(tmp_path, bad))

    if bad == "":
        # An empty --resume is falsy, so the resume block is skipped entirely and
        # the run proceeds as an ordinary REPL/one-shot invocation. Recorded so
        # the allowlist is not credited with behavior it does not have.
        pytest.skip("empty --resume is falsy: no validation branch is entered")

    assert main_module.main() == 2
    assert state["build"] == []
    err = capsys.readouterr().err
    assert f"[resume] Invalid trace_id {bad!r}:" in err
    assert "only letters, digits, hyphens and underscores are allowed." in err


def test_invalid_trace_id_exit_code_is_two_in_a_real_process(tmp_path):
    """Proves the launcher maps the return value to process exit code 2.

    Runs `main.py` with CWD inside the tmp workspace so `load_dotenv()` cannot
    pick up the repository's `.env`; validation fails before any provider or
    agent construction, so no network path is reachable.
    """
    env = {**os.environ, "AGENT_PROVIDER": "mock", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "main.py"),
            "--resume",
            "not/a/valid/id",
            "--workspace",
            str(tmp_path),
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "[resume] Invalid trace_id" in proc.stderr
    assert not (tmp_path / "logs").exists()


def test_valid_trace_id_shapes_are_accepted(tmp_path, monkeypatch, capsys):
    """The allowlist accepts alphanumerics plus hyphen/underscore after char 1."""
    state = _patch(monkeypatch)
    monkeypatch.setattr(sys, "argv", _argv(tmp_path, "run_ABC-123", "--ask", "q"))

    assert main_module.main() == 0

    err = capsys.readouterr().err
    assert "Invalid trace_id" not in err
    assert "[resume] No usable checkpoint found" in err
    assert len(state["build"]) == 1


# ── branch 2: no usable checkpoint ────────────────────────────────────────────

def test_missing_checkpoint_falls_through_to_a_fresh_run(tmp_path, monkeypatch, capsys):
    state = _patch(monkeypatch)
    monkeypatch.setattr(sys, "argv", _argv(tmp_path, "run_absent", "--ask", "original question"))

    assert main_module.main() == 0

    err = capsys.readouterr().err
    assert "[resume] No usable checkpoint found for trace_id='run_absent'. Running fresh." in err
    assert state["run"][0]["user_question"] == "original question"


def test_checkpoint_without_observe_record_is_unusable(tmp_path, monkeypatch, capsys):
    """The loader needs an OBSERVE record; without one it returns None."""
    state = _patch(monkeypatch)
    writer = CheckpointWriter("run_noobserve", tmp_path / "logs")
    writer.save_respond("an answer that must not be replayed")
    monkeypatch.setattr(sys, "argv", _argv(tmp_path, "run_noobserve", "--ask", "q"))

    assert main_module.main() == 0

    assert "[resume] No usable checkpoint found" in capsys.readouterr().err
    assert len(state["build"]) == 1


# ── branch 4: cached answer replay ────────────────────────────────────────────

def test_cached_answer_replays_to_stdout_without_building_an_agent(
    tmp_path, monkeypatch, capsys
):
    state = _patch(monkeypatch)
    writer = CheckpointWriter("run_done", tmp_path / "logs")
    writer.save_observe("what was asked", file_hint=None)
    writer.save_act(label="a1", tool="file_read", chars=12, status="done")
    writer.save_respond("the cached answer")
    monkeypatch.setattr(sys, "argv", _argv(tmp_path, "run_done"))

    assert main_module.main() == 0

    out = capsys.readouterr()
    # No LLM, no agent: the replay short-circuits before construction.
    assert state["build"] == []
    assert state["run"] == []
    # Answer goes to stdout with the standard blank-line wrapper.
    assert out.out == "\n" + format_human_response("the cached answer") + "\n\n"
    assert "[resume] Replaying cached answer for trace_id='run_done'" in out.err
    assert "artifacts=['a1']" in out.err


# ── branch 3: budget-paused restore ───────────────────────────────────────────

def test_paused_checkpoint_restores_question_and_file_hint(tmp_path, monkeypatch, capsys):
    state = _patch(monkeypatch)
    hint = tmp_path / "hint.md"
    hint.write_text("hint body\n", encoding="utf-8")
    writer = CheckpointWriter("run_paused", tmp_path / "logs")
    writer.save_observe("interrupted question", file_hint=str(hint))
    writer.save_paused(
        {
            "original_user_question": "interrupted question",
            "active_goal": "finish the interrupted goal",
            "current_phase": "act",
            "stop_reason": "budget_exhausted",
            "blocked_model": {"counter": "llm_calls"},
            "completed_steps": ["s1"],
            "remaining_steps": ["s2"],
            "planned_steps": ["s1", "s2"],
        }
    )
    monkeypatch.setattr(sys, "argv", _argv(tmp_path, "run_paused"))

    assert main_module.main() == 0

    err = capsys.readouterr().err
    assert "[resume] Resuming budget-paused checkpoint trace_id='run_paused' phase='act'." in err
    assert len(state["run"]) == 1
    question = state["run"][0]["user_question"]
    assert question.startswith("Resume the interrupted task from this saved budget checkpoint.")
    assert "Original user question: interrupted question" in question
    assert "Remaining steps: [\"s2\"]" in question
    # The checkpoint's file hint is restored when --file was not given.
    assert state["run"][0]["file_hint"] == str(hint)


def test_paused_branch_wins_over_a_cached_answer(tmp_path, monkeypatch, capsys):
    """Observed precedence: `paused` is tested before `answer is not None`."""
    state = _patch(monkeypatch)
    writer = CheckpointWriter("run_both", tmp_path / "logs")
    writer.save_observe("q", file_hint=None)
    writer.save_respond("cached answer that is NOT replayed")
    writer.save_paused({"current_phase": "act", "original_user_question": "q"})
    monkeypatch.setattr(sys, "argv", _argv(tmp_path, "run_both"))

    assert main_module.main() == 0

    out = capsys.readouterr()
    assert "Resuming budget-paused checkpoint" in out.err
    assert "Replaying cached answer" not in out.err
    assert "cached answer that is NOT replayed" not in out.out
    assert len(state["run"]) == 1


def test_explicit_ask_and_file_win_over_paused_checkpoint_values(tmp_path, monkeypatch):
    state = _patch(monkeypatch)
    override = tmp_path / "override.md"
    override.write_text("x\n", encoding="utf-8")
    writer = CheckpointWriter("run_paused2", tmp_path / "logs")
    writer.save_observe("checkpoint question", file_hint="checkpoint-hint.md")
    writer.save_paused({"current_phase": "plan", "original_user_question": "checkpoint question"})
    monkeypatch.setattr(
        sys,
        "argv",
        _argv(tmp_path, "run_paused2", "--ask", "explicit question", "--file", str(override)),
    )

    assert main_module.main() == 0

    assert state["run"][0]["user_question"] == "explicit question"
    assert state["run"][0]["file_hint"] == str(override)


# ── branch 5: synthesis incomplete ────────────────────────────────────────────

def test_incomplete_checkpoint_reruns_from_scratch_with_restored_question(
    tmp_path, monkeypatch, capsys
):
    state = _patch(monkeypatch)
    writer = CheckpointWriter("run_partial", tmp_path / "logs")
    writer.save_observe("half finished question", file_hint=None)
    writer.save_plan(attempt=1, step_ids=["s1"])
    monkeypatch.setattr(sys, "argv", _argv(tmp_path, "run_partial"))

    assert main_module.main() == 0

    err = capsys.readouterr().err
    assert (
        "[resume] Checkpoint found but synthesis incomplete (last_phase='plan'). "
        "Re-running from scratch." in err
    )
    assert state["run"][0]["user_question"] == "half finished question"
