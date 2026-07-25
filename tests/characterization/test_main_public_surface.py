"""C12 — the `main` module's importable/monkeypatchable surface.

`main` is not only a script: production code and tests import names *from it*.
`agent_tick.py` and `api/server.py` do `from main import build_agent` at call
time, and several suites patch attributes by name on the module object
(`monkeypatch.setattr(main, "build_agent", …)`). Those bindings resolve against
`main`'s namespace, so an extraction that moves `main()` elsewhere silently turns
such patches into no-ops unless the re-exports stay.

`main.py` already documents this compatibility contract for the `cli.parsers`
helpers (main.py:68-80, cli/__init__.py:4-5). This module pins the whole surface
so Phase 7's "remove compatibility re-exports" step has an inventory to audit
against.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.bootstrap as bootstrap_module
import cli.parsers as parsers_module
import main as main_module

REPO_ROOT = Path(main_module.__file__).resolve().parent

# Re-exported from cli/parsers.py for backwards compatibility (main.py:70-80).
PARSER_REEXPORTS = (
    "_compact_one_line",
    "_env_bool",
    "_parse_ingest_options",
    "_parse_remember",
    "_parse_repair_generation_args",
    "_parse_source_planning_args",
    "_resolve_workspace_text_file",
    "_split_meta_args",
    "_truncate_text",
)

# Names other modules or test suites reach for on `main` today.
IMPORTED_ELSEWHERE = (
    "main",
    "build_agent",
    "load_dotenv",
    "handle_meta_command",
    "handle_conversational_operator_input",
    "_dispatch_operator_intent",
    "_handle_local_operator_reply",
    "_local_operator_reply",
    "_run_agent_with_budget_guard",
    "_resume_question_from_checkpoint",
    "_preflight_file_hint",
    "_collect_instruction_buffer",
    "_coalesce_burst",
    "_StdinLineReader",
    "_force_utf8_io",
    "PASTE_COALESCE_GAP_SECONDS",
    "CLIApprovalProvider",
    "_print_daemon_inbox_notice",
    "_handle_operator_task",
    "_handle_self_build_propose",
    "_schedule_disable_message",
)


@pytest.mark.parametrize("name", PARSER_REEXPORTS)
def test_parser_reexport_is_the_same_object_as_in_cli_parsers(name):
    assert hasattr(main_module, name), f"main lost the {name} re-export"
    assert getattr(main_module, name) is getattr(parsers_module, name)


@pytest.mark.parametrize("name", IMPORTED_ELSEWHERE)
def test_name_is_present_on_the_main_module(name):
    assert hasattr(main_module, name), f"main.{name} is imported elsewhere and must exist"


def test_build_agent_is_a_reexport_of_app_bootstrap():
    """`main` does not define agent construction — it re-exports it."""
    assert main_module.build_agent is bootstrap_module.build_agent


def test_runtime_callers_import_build_agent_from_main(monkeypatch):
    """agent_tick.py and api/server.py bind `build_agent` through `main`."""
    for rel in ("agent_tick.py", "api/server.py"):
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "from main import build_agent" in source, rel


def test_monkeypatching_build_agent_by_name_is_observed_by_main(tmp_path, monkeypatch):
    """The patch-by-name contract several suites rely on."""
    seen: list[dict] = []

    def fake_build_agent(workspace, **kwargs):
        seen.append({"workspace": workspace, **kwargs})
        return SimpleNamespace(log=SimpleNamespace(log=lambda *a, **k: None))

    monkeypatch.setattr(main_module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(main_module, "build_agent", fake_build_agent)
    monkeypatch.setattr(main_module, "handle_meta_command", lambda *a, **k: True)
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", ":models"])

    assert main_module.main() == 0
    assert len(seen) == 1


def test_monkeypatching_load_dotenv_by_name_is_observed_by_main(tmp_path, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(main_module, "load_dotenv", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(
        main_module,
        "build_agent",
        lambda *a, **k: SimpleNamespace(log=SimpleNamespace(log=lambda *x, **y: None)),
    )
    monkeypatch.setattr(main_module, "handle_meta_command", lambda *a, **k: True)
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", ":models"])

    assert main_module.main() == 0
    assert calls == [1]


def test_monkeypatching_dispatch_operator_intent_by_name_is_observed(tmp_path, monkeypatch):
    """`main` re-exports the symbol and the re-export stays rebindable.

    This asserts the *surface*, not interception: since the bridge moved to
    `cli/intent_bridge.py`, `main` no longer calls it, and
    tests/test_intent_bridge.py:42 correctly patches `cli.intent_bridge`
    instead. test_main_patch_seams.py exempts this module for that reason.
    """
    monkeypatch.setattr(main_module, "_dispatch_operator_intent", lambda *a, **k: True)
    assert main_module._dispatch_operator_intent(None, None, tmp_path) is True


def test_main_is_callable_and_returns_an_int(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(
        main_module,
        "build_agent",
        lambda *a, **k: SimpleNamespace(log=SimpleNamespace(log=lambda *x, **y: None)),
    )
    monkeypatch.setattr(main_module, "handle_meta_command", lambda *a, **k: True)
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", ":models"])

    result = main_module.main()
    assert isinstance(result, int)
    assert result == 0


def test_launcher_raises_system_exit_with_the_return_value():
    """The module tail is already the thin-launcher shape."""
    tail = (REPO_ROOT / "main.py").read_text(encoding="utf-8").rstrip().splitlines()[-2:]
    assert tail[0].strip() == 'if __name__ == "__main__":'
    assert tail[1].strip() == "raise SystemExit(main())"


def test_force_utf8_io_is_reexported_from_app_io():
    import app.io as io_module

    assert main_module._force_utf8_io is io_module._force_utf8_io
