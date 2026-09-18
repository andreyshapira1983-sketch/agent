"""A real child process on pass two must execute accepted code, not just a pointer."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import autonomous_repair as repair


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603 — local fixture repository, literal git argv
        [shutil.which("git") or "git", *args],
        cwd=root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "--quiet", "--initial-branch=main")
    _git(root, "config", "user.name", "Repair Test")
    _git(root, "config", "user.email", "repair-test@localhost")
    (root / "core").mkdir()
    (root / "core" / "widget.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "budget_limits.json").write_text(
        '{"windows": {"hour": {"llm_calls": 20}, "day": {"llm_calls": 100}}}',
        encoding="utf-8",
    )
    (root / ".gitignore").write_text(
        ".autonomous-repair/\ndata/\nlogs/\nstate/\n__pycache__/\n"
        "config/burn_in_sandbox.json\nconfig/budget_limits.json\n",
        encoding="utf-8",
    )
    # The fixture stands in for proposal production, not for the supervisor.
    # Adoption, a fresh pytest process, the accepted head and the next child
    # invocation all run through the real implementation.
    (root / "agent_tick.py").write_text(
        """
import json
import os
import subprocess
from pathlib import Path
from core.widget import VALUE

def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()

root = Path.cwd()
assert os.environ["AGENT_BURN_IN_SANDBOX"] == "on"
assert "AGENT_BUDGET_CONFIG_PATH" not in os.environ
assert json.loads((root / "config/burn_in_sandbox.json").read_text())["workspace"] == str(root)
data = root / "data"
data.mkdir(exist_ok=True)
seen = data / "values.json"
values = json.loads(seen.read_text()) if seen.exists() else []
values.append(VALUE)
seen.write_text(json.dumps(values))
if len(values) == 1:
    original = git("rev-parse", "--abbrev-ref", "HEAD")
    git("checkout", "-b", "candidate")
    Path("core/widget.py").write_text("VALUE = 2\\n")
    git("add", "core/widget.py")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@localhost",
        "commit", "-m", "candidate")
    sha = git("rev-parse", "HEAD")
    git("checkout", original)
    Path("state").mkdir(exist_ok=True)
    Path("state/burn_in_offers.jsonl").write_text(json.dumps({"sha": sha}) + "\\n")
""",
        encoding="utf-8",
    )
    (root / "test_widget.py").write_text(
        "import os\nfrom core.widget import VALUE\n\ndef test_widget():\n"
        "    assert VALUE in (1, 2)\n"
        "    assert 'AGENT_BUDGET_CONFIG_PATH' not in os.environ\n",
        encoding="utf-8",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "fixture")
    return root


def test_the_second_process_uses_accepted_code_and_keeps_memory(source: Path) -> None:
    original_head = _git(source, "rev-parse", "HEAD")
    result = repair.run_session(source, repair.RepairLimits(passes=2, seconds=90))

    assert result["status"] == "completed", result
    assert result["accepted"] == 1
    first, second = result["passes"]
    assert second["started_from"] == first["next_head"]
    assert second["started_from"] != first["started_from"]
    assert json.loads((Path(second["workspace"]) / "data" / "values.json").read_text()) == [1, 2]
    # Unchanged HEAD and clean tracked files cover the entire source, not just widget.py.
    assert _git(source, "rev-parse", "HEAD") == original_head
    assert _git(source, "status", "--porcelain") == ""
    assert not (source / "data").exists()
    assert not _git(Path(result["session"]) / "repository", "remote")


def test_red_candidate_does_not_become_the_next_agent(source: Path) -> None:
    (source / "test_widget.py").write_text(
        "from core.widget import VALUE\n\ndef test_widget():\n    assert VALUE == 1\n",
        encoding="utf-8",
    )
    result = repair.run_session(source, repair.RepairLimits(passes=2, seconds=90))

    assert result["accepted"] == 0
    first, second = result["passes"]
    assert not first["adoptions"][0]["accepted"]
    assert second["started_from"] == first["started_from"]
    assert json.loads((Path(second["workspace"]) / "data" / "values.json").read_text()) == [1, 1]


def test_a_failed_child_stops_instead_of_claiming_completion(
    source: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repair, "_campaign", lambda *_args: 3)
    result = repair.run_session(source, repair.RepairLimits(passes=2, seconds=90))

    assert result["status"] == "stopped"
    assert result["accepted"] == 0
    assert len(result["passes"]) == 1
    assert "code 3" in result["reason"]


def test_inherited_budget_override_does_not_escape_into_tests(
    source: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A runtime-wide override otherwise replaces each test's own budget file."""
    monkeypatch.setenv("AGENT_BUDGET_CONFIG_PATH", str(source / "operator-budget.json"))
    result = repair.run_session(source, repair.RepairLimits(passes=1, seconds=90))
    assert result["status"] == "completed", result
    assert result["accepted"] == 1


def test_a_timed_out_child_is_not_adopted(
    source: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*_args):
        raise repair.SupervisorError("repair pass timed out")

    monkeypatch.setattr(repair, "_campaign", timeout)
    result = repair.run_session(source, repair.RepairLimits(passes=2, seconds=90))

    assert result["status"] == "stopped"
    assert result["accepted"] == 0
    assert "timed out" in result["reason"]
    assert json.loads((Path(result["session"]) / "report.json").read_text()) == result


@pytest.mark.parametrize("name", vars(repair.RepairLimits()))
def test_zero_never_means_unlimited(name: str) -> None:
    with pytest.raises(ValueError, match="positive"):
        repair.RepairLimits(**{name: 0})


def test_missing_budget_fails_before_any_copy_is_created(source: Path) -> None:
    (source / "config" / "budget_limits.json").unlink()
    with pytest.raises(repair.SupervisorError, match="spend limits"):
        repair.run_session(source, repair.RepairLimits())
    assert not (source / ".autonomous-repair").exists()


def test_the_controller_is_outside_the_agents_edit_authority() -> None:
    from core.burn_in_sandbox import _FENCE
    from core.burn_in_supervisor import SUPERVISOR_FENCE

    assert "scripts/autonomous_repair.py" in _FENCE & SUPERVISOR_FENCE


def test_interrupt_kills_the_child_before_returning_control(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from core import bounded_subprocess

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    process = SimpleNamespace(communicate=interrupt)
    killed = []
    monkeypatch.setattr(bounded_subprocess.subprocess, "Popen", lambda *_a, **_kw: process)
    monkeypatch.setattr(bounded_subprocess, "kill_process_tree", killed.append)

    with pytest.raises(KeyboardInterrupt):
        bounded_subprocess.run_with_tree_kill(["fixture"], cwd=".", env={}, timeout=1)

    assert killed == [process]


def test_preparation_failure_leaves_a_stopped_report(
    source: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refused(*_args, **_kwargs):
        raise repair.SupervisorError("cannot prepare next copy")

    monkeypatch.setattr(repair, "_prepare_tree", refused)
    result = repair.run_session(source, repair.RepairLimits(passes=1, seconds=90))

    assert result["status"] == "stopped"
    assert "prepare" in result["reason"]
    assert json.loads((Path(result["session"]) / "report.json").read_text()) == result


def test_function_census_counts_source_but_not_isolated_copies(tmp_path: Path) -> None:
    from scripts import check_function_length_baseline as checker

    source = tmp_path / "core"
    copy = tmp_path / ".autonomous-repair" / "session" / "core"
    source.mkdir()
    copy.mkdir(parents=True)
    body = "def long_function():\n" + "    pass\n" * 160
    (source / "widget.py").write_text(body, encoding="utf-8")
    (copy / "widget.py").write_text(body, encoding="utf-8")

    assert checker.measure(tmp_path) == {"core/widget.py:long_function": 161}
