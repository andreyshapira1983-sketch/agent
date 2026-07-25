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
    """Every name loaded anywhere in main.py's own body."""
    tree = ast.parse((REPO_ROOT / "main.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            names.add(node.value.id)
    return names


def _patch_sites() -> list[tuple[str, int, str]]:
    """(relative path, line, patched name) for every setattr on the main module."""
    sites: list[tuple[str, int, str]] = []
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        aliases = set(re.findall(r"^\s*import main as (\w+)", text, re.M))
        if re.search(r"^\s*import main\s*$", text, re.M):
            aliases.add("main")
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
    sites = _patch_sites()
    assert len(sites) > 50, "patch-site scan collapsed — the regex or layout changed"
    patched_names = {name for _, _, name in sites}
    for expected in ("build_agent", "load_dotenv", "_StdinLineReader"):
        assert expected in patched_names, f"scan lost the {expected} sites"


def test_every_patch_on_main_is_still_observed():
    resolved = _names_main_resolves()
    inert = [
        (rel, line, name)
        for rel, line, name in _patch_sites()
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


@pytest.mark.parametrize("name", ["build_agent", "handle_meta_command", "load_dotenv"])
def test_load_bearing_names_are_still_resolved_by_main(name):
    """The three the rest of the repo leans on hardest, spelled out."""
    assert name in _names_main_resolves()
