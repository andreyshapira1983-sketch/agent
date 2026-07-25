"""C13 — every `monkeypatch.setattr(main, "…")` in the suite must still bite.

The extraction's one silent failure mode, documented in
`docs/refactor/CLI_BASELINE.md` section 2.5: a suite fakes a collaborator by
patching it **on `main`**, the call site later moves into `cli/…`, and the patch
becomes a no-op. Nothing turns red — the test keeps passing while quietly
running the real thing. That is how a `:models` dispatch and a real agent build
slipped into a "green" characterization run during the one-shot move, and how
the `_handle_self_apply_run` guard in tests/test_cli.py sat inert.

A patch on `main.NAME` is observed only where the *call site* resolves `NAME` in
`main`'s namespace. So: every name the suite patches on `main` must appear as a
load in `main.py` itself — either called there, or passed into `cli/one_shot.py`
/ `cli/repl.py` through the documented parameter seam.

When this test fails, do not add the name to the allowlist. Point the patch at
the module that owns the call site (`cli/command_dispatch.py`,
`cli/intent_bridge.py`, `app/budget_guard.py`, …).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import main as main_module

REPO_ROOT = Path(main_module.__file__).resolve().parent
TESTS_ROOT = REPO_ROOT / "tests"

# tests/characterization/test_main_public_surface.py patches names purely to
# assert that `main` *is* patchable (it is the re-export inventory Phase 7 will
# audit against), so its sites are about the surface, not about interception.
SURFACE_INVENTORY_MODULE = "characterization/test_main_public_surface.py"


def _names_main_resolves() -> set[str]:
    """Names for which a patch on `main` is still observed by something.

    Two ways that can be true:

    * `main.py` loads the name in its own body — the launcher itself calls it;
    * a non-test module does `from main import NAME` **inside a function**, so
      the binding is looked up on `main` at call time. `agent_tick.py` and
      `api/server.py` do exactly that with `build_agent`, which is why faking it
      on `main` still works for their paths (CLI_BASELINE.md §2.5).
    """
    names: set[str] = set()
    tree = ast.parse((REPO_ROOT / "main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            names.add(node.value.id)
    return names | _names_imported_from_main_at_call_time()


def _names_imported_from_main_at_call_time() -> set[str]:
    """`from main import NAME` inside a function, anywhere outside tests."""
    names: set[str] = set()
    skip = {"tests", ".git", "__pycache__", ".venv", "venv", "node_modules"}
    for path in REPO_ROOT.rglob("*.py"):
        parts = set(path.relative_to(REPO_ROOT).parts)
        if parts & skip or path.name == "main.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "from main import" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover - repo is expected to parse
            continue
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if isinstance(node, ast.ImportFrom) and node.module == "main":
                    names.update(alias.name for alias in node.names)
    return names


def _names_cli_app_resolves() -> set[str]:
    """Every name loaded in cli/app.py's body — where the startup wiring runs."""
    tree = ast.parse((REPO_ROOT / "cli" / "app.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            names.add(node.value.id)
    return names


def _patch_sites(module: str = "main") -> list[tuple[str, int, str]]:
    """(relative path, line, patched name) for every setattr on that module."""
    sites: list[tuple[str, int, str]] = []
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        aliases = set(re.findall(rf"^\s*import {re.escape(module)} as (\w+)", text, re.M))
        if re.search(rf"^\s*import {re.escape(module)}\s*$", text, re.M):
            aliases.add(module)
        rel = path.relative_to(TESTS_ROOT).as_posix()
        for alias in aliases:
            for match in re.finditer(
                rf"setattr\(\s*{alias}\s*,\s*[\"'](\w+)[\"']", text
            ):
                line = text[: match.start()].count("\n") + 1
                sites.append((rel, line, match.group(1)))
    return sites


def test_the_scanner_itself_finds_the_known_sites():
    """Guard the guard: a broken regex must not turn this file green."""
    app_sites = _patch_sites("cli.app")
    assert len(app_sites) > 50, "patch-site scan collapsed — the regex or layout changed"
    app_names = {name for _, _, name in app_sites}
    for expected in ("build_agent", "load_dotenv", "_StdinLineReader"):
        assert expected in app_names, f"scan lost the cli.app {expected} sites"
    # `build_agent` is still faked on `main` too, for the agent_tick / api paths.
    assert "build_agent" in {name for _, _, name in _patch_sites("main")}


def test_every_patch_on_cli_app_is_still_observed():
    """The same rule for `cli.app`, the module the wiring fakes moved to."""
    resolved = _names_cli_app_resolves()
    inert = [
        (rel, line, name)
        for rel, line, name in _patch_sites("cli.app")
        if name not in resolved
    ]
    assert not inert, "\n".join(
        [
            "These fakes are inert — cli/app.py does not resolve the name:",
            *(f"  tests/{rel}:{line} patches cli.app.{name}" for rel, line, name in inert),
        ]
    )


def test_every_patch_on_main_is_still_observed():
    resolved = _names_main_resolves()
    inert = [
        (rel, line, name)
        for rel, line, name in _patch_sites("main")
        if name not in resolved and rel != SURFACE_INVENTORY_MODULE
    ]
    assert not inert, "\n".join(
        [
            "These fakes are inert — main.py no longer resolves the name, so the",
            "real code runs while the test still passes. Patch the module that",
            "owns the call site instead:",
            *(f"  tests/{rel}:{line} patches main.{name}" for rel, line, name in inert),
        ]
    )


def test_build_agent_stays_patchable_through_main():
    """`agent_tick.py` and `api/server.py` bind it lazily, so faking it works.

    This is the one name for which a patch on `main` is still the right target,
    and the reason is not that `main` calls it — the launcher no longer calls
    anything — but that those two modules do `from main import build_agent`
    inside a function.
    """
    assert "build_agent" in _names_imported_from_main_at_call_time()


def test_the_launcher_performs_no_wiring_of_its_own():
    """`main.py` is a launcher: it hands off to `cli/app.py` and nothing else.

    `load_dotenv`, `_StdinLineReader`, `CLIApprovalProvider` and friends are
    still *re-exported* (pinned by `test_main_public_surface.py`) but no longer
    *called* here, so their fakes belong on `cli.app`.
    """
    tree = ast.parse((REPO_ROOT / "main.py").read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called == {"run_cli", "main", "SystemExit"}, (
        "main.py should only contain `return run_cli()` and the "
        "`raise SystemExit(main())` tail; calls found: " + str(sorted(called))
    )
