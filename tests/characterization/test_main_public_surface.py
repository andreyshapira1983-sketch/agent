"""C12 — what `main` is allowed to be: a launcher, and nothing else.

This module used to pin a compatibility *inventory*. At `9daa9bf`, `main` was an
import surface as much as a script: `agent_tick.py` and `api/server.py` did
`from main import build_agent` at call time, and 24 test modules imported 27
more names from it, so every extraction step had to keep re-exporting them or
silently break a caller.

Phase 7 retired that surface. The runtime imports `build_agent` from
`app.bootstrap`, the suites import each helper from the module that defines it,
and `main.py` is down to a docstring, `return run_cli()` and the `SystemExit`
tail. What this module pins now is that it stays that way — the inventory is
gone because there is nothing left to inventory.

The companion guard is `test_main_patch_seams.py`, which fails if any test tries
to fake something on `main` (or on `cli.app`) where the call site would not see
it.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import app.bootstrap as bootstrap_module
import cli.app as app_module
import cli.command_dispatch as dispatch_module
import main as main_module

REPO_ROOT = Path(main_module.__file__).resolve().parent


def test_main_imports_nothing_but_the_cli_entry_point():
    """No re-export block may grow back."""
    tree = ast.parse((REPO_ROOT / "main.py").read_text(encoding="utf-8"))
    imports = {
        f"{node.module}.{alias.name}"
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert imports == {"__future__.annotations", "cli.app.run_cli"}, sorted(imports)
    assert not [node for node in tree.body if isinstance(node, ast.Import)]


def test_main_defines_only_the_launcher():
    tree = ast.parse((REPO_ROOT / "main.py").read_text(encoding="utf-8"))
    defined = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert defined == ["main"]


def test_launcher_raises_system_exit_with_the_return_value():
    tail = (REPO_ROOT / "main.py").read_text(encoding="utf-8").rstrip().splitlines()[-2:]
    assert tail[0].strip() == 'if __name__ == "__main__":'
    assert tail[1].strip() == "raise SystemExit(main())"


def test_no_production_module_imports_from_main():
    """The runtime does not go through `main` at all.

    `agent_tick.py` and `api/server.py` used to do `from main import build_agent`
    lazily; they import it from its home now. Nothing outside `tests/` may reach
    into `main` again — that is what made the re-export block removable, and this
    is what keeps it removed.
    """
    for rel in ("agent_tick.py", "api/server.py"):
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "from main import" not in source, rel
        assert "from app.bootstrap import build_agent" in source, rel


def test_lazy_callers_are_faked_on_app_bootstrap(monkeypatch):
    """Where a `build_agent` fake belongs.

    `agent_tick.run_tick` and `api.server._build_server_agent` import it inside a
    function, so the binding is read off `app.bootstrap` at call time — the
    target `tests/test_autonomous_runtime.py` and `tests/test_budget_kill_switch.py`
    patch. A fake on `main` would sit there unused.
    """
    sentinel = SimpleNamespace(log=SimpleNamespace(log=lambda *a, **k: None))
    monkeypatch.setattr(bootstrap_module, "build_agent", lambda *a, **k: sentinel)

    def lazy_caller():  # mirrors agent_tick.py:739
        from app.bootstrap import build_agent

        return build_agent(Path("."))

    assert lazy_caller() is sentinel


def test_dotenv_is_loaded_through_cli_app(tmp_path, monkeypatch):
    """`cli.app` performs the startup sequence, so the fakes go there."""
    calls: list[int] = []
    monkeypatch.setattr(app_module, "load_dotenv", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(
        app_module,
        "build_agent",
        lambda *a, **k: SimpleNamespace(log=SimpleNamespace(log=lambda *x, **y: None)),
    )
    monkeypatch.setattr(dispatch_module, "handle_meta_command", lambda *a, **k: True)
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", ":models"])

    assert main_module.main() == 0
    assert calls == [1]


def test_main_is_callable_and_returns_an_int(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(
        app_module,
        "build_agent",
        lambda *a, **k: SimpleNamespace(log=SimpleNamespace(log=lambda *x, **y: None)),
    )
    monkeypatch.setattr(dispatch_module, "handle_meta_command", lambda *a, **k: True)
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", ":models"])

    result = main_module.main()
    assert isinstance(result, int)
    assert result == 0
