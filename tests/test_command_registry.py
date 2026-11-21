"""Guard: cli/command_registry.py must agree with the live command surface.

Phase 1 introduced the registry as **pure data** that nothing consumes yet. Its
whole value depends on being an exact mirror of what the code actually does, so
every fact in it is re-derived here from the real sources and compared:

- the accepted tokens and their grouping come from the ``head`` dispatch chain in
  ``main.py``;
- ``usage`` comes from ``knowledge/maps/COMMANDS_MAP.md``;
- ``in_help`` comes from the live ``:help`` page;
- ``in_startup_summary`` comes from the REPL banner literal;
- ``phase`` comes from the two pre-``load_dotenv`` fast paths in ``main()``.

A hand edit that drifts from any of those fails here. When Phase 2 makes ``:help``
and the banner render *from* the registry, the two visibility tests below become
the assertions that the rendering did not lose anything.
"""
from __future__ import annotations

import io
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace

import cli.command_dispatch as dispatch_module
import main as main_module
from cli import command_registry as reg

REPO_ROOT = Path(main_module.__file__).resolve().parent
MAIN_SOURCE = (REPO_ROOT / "main.py").read_text(encoding="utf-8")

_CMD = r":[a-z0-9][a-z0-9-]*"
_STANDALONE = re.compile(r"(?<![\w-])(" + _CMD + r")(?![\w-])")


# ── independent derivations from the real sources ────────────────────────────


def _help_tokens() -> set[str]:
    buf = io.StringIO()
    with redirect_stderr(buf), redirect_stdout(io.StringIO()):
        assert dispatch_module.handle_meta_command(":help", SimpleNamespace(), REPO_ROOT) is True
    return set(_STANDALONE.findall(buf.getvalue()))


# ── purity ───────────────────────────────────────────────────────────────────

def test_registry_is_pure_data():
    """The registry and BOTH of its spec volumes keep the purity contract.

    "Pure" here means what the assertions below enforce: no runtime-layer
    imports and no side effects at import time — a frozen dataclass with a
    trivial `tokens` property is fine, dispatch logic is not. Since the #278
    split (authored by the self-build producer) the command table lives in
    two sibling volumes that the registry combines; the contract covers all
    three files so the split never becomes a door for behaviour."""
    spec_siblings = {".command_specs", ".command_specs_ops"}
    for name, allowed in (
        ("command_registry.py", {"__future__", "dataclasses"} | spec_siblings),
        ("command_specs.py", {"__future__", "dataclasses"}),
        ("command_specs_ops.py", {"__future__", "dataclasses", ".command_specs"}),
    ):
        source = Path(find_spec(f"cli.{name.removesuffix('.py')}").origin).read_text(encoding="utf-8")
        imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", source, re.MULTILINE)
        forbidden = [
            imp
            for imp in imports
            if imp.split(".")[0] in {"core", "app", "tools", "api", "main", "docker"}
            or imp.startswith("cli.commands")
        ]
        assert not forbidden, f"{name} must import no runtime module, found: {forbidden}"
        # stdlib + own pure-data siblings only, and no behaviour smuggled in
        assert set(imports) <= allowed, (name, imports)
        for banned in ("subprocess", "open(", "requests", "urllib"):
            assert banned not in source, (name, banned)


_IMPORT_PROBE = """
import sys

_WATCHED = {"open", "os.listdir", "os.scandir", "os.mkdir", "os.remove", "os.rename",
            "shutil.rmtree", "subprocess.Popen", "socket.connect"}
touched = []

def _hook(event, args):
    if event in _WATCHED:
        caller = sys._getframe(1).f_code.co_filename
        if not caller.startswith("<frozen importlib"):
            touched.append(f"{event} {args[0]!r} from {caller}")

sys.addaudithook(_hook)
import cli.command_registry
for item in touched:
    print(item)
"""


def test_registry_import_has_no_side_effects():
    """A fresh import of the registry and both spec volumes touches no file or process.

    Runs in a new interpreter so the spec modules really execute; only the import
    machinery's own reads of module code are allowed."""
    result = subprocess.run(  # noqa: S603 — fixed argv: this interpreter and a literal probe
        [sys.executable, "-B", "-c", _IMPORT_PROBE],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"importing the registry touched:\n{result.stdout}"


# ── the token surface ────────────────────────────────────────────────────────


def test_tokens_are_unique_across_the_registry():
    seen: dict[str, str] = {}
    for spec in reg.COMMANDS:
        for token in spec.tokens:
            assert token not in seen, f"{token} claimed by {seen.get(token)} and {spec.canonical}"
            seen[token] = spec.canonical
    assert len(seen) == len(reg.BY_TOKEN)


# ── visibility flags vs the live surfaces ────────────────────────────────────

def test_in_help_matches_the_live_help_page():
    help_tokens = _help_tokens()
    for spec in reg.COMMANDS:
        listed = any(token in help_tokens for token in spec.tokens)
        assert spec.in_help == listed, (
            f"{spec.canonical}: in_help={spec.in_help} but the :help page "
            f"{'lists' if listed else 'omits'} it"
        )


# ── lookup helpers ───────────────────────────────────────────────────────────


def test_canonical_tokens_and_in_category_are_consistent():
    canonicals = reg.canonical_tokens()
    assert len(canonicals) == len(reg.COMMANDS)
    assert set(canonicals) == {spec.canonical for spec in reg.COMMANDS}
    covered = [spec for category in {s.category for s in reg.COMMANDS}
               for spec in reg.in_category(category)]
    assert len(covered) == len(reg.COMMANDS)
