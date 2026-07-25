"""Guard: cli/command_registry.py must agree with the live command surface.

Phase 1 introduced the registry as **pure data** that nothing consumes yet. Its
whole value depends on being an exact mirror of what the code actually does, so
every fact in it is re-derived here from the real sources and compared:

- the accepted tokens and their grouping come from the ``head`` dispatch chain in
  ``main.py``;
- ``usage`` comes from ``docs/COMMANDS_MAP.md``;
- ``in_help`` comes from the live ``:help`` page;
- ``in_startup_summary`` comes from the REPL banner literal;
- ``phase`` comes from the two pre-``load_dotenv`` fast paths in ``main()``.

A hand edit that drifts from any of those fails here. When Phase 2 makes ``:help``
and the banner render *from* the registry, the two visibility tests below become
the assertions that the rendering did not lose anything.
"""
from __future__ import annotations

import importlib.util
import io
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

import main as main_module
from cli import command_registry as reg

REPO_ROOT = Path(main_module.__file__).resolve().parent
MAIN_SOURCE = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
CMAP_SOURCE = (REPO_ROOT / "docs" / "COMMANDS_MAP.md").read_text(encoding="utf-8")

_CMD = r":[a-z0-9][a-z0-9-]*"
_STANDALONE = re.compile(r"(?<![\w-])(" + _CMD + r")(?![\w-])")


# ── independent derivations from the real sources ────────────────────────────

def _dispatched_tokens() -> set[str]:
    """Reuse the existing guard's parser rather than re-inventing it."""
    script = REPO_ROOT / "scripts" / "commands_map_check.py"
    spec = importlib.util.spec_from_file_location("commands_map_check_reg", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return set(mod.dispatched_commands(MAIN_SOURCE))


def _dispatch_branches() -> list[tuple[str, ...]]:
    """Token tuples, one per ``if head == …`` / ``if head in {…}`` branch."""
    branches: list[tuple[str, ...]] = []
    for line in MAIN_SOURCE.splitlines():
        m_eq = re.search(r'if\s+head\s*==\s*"(' + _CMD + r')"\s*:', line)
        m_in = re.search(r"if\s+head\s+in\s*\{([^}]*)\}\s*:", line)
        if m_eq:
            branches.append((m_eq.group(1),))
        elif m_in:
            tokens = tuple(re.findall(r'"(' + _CMD + r')"', m_in.group(1)))
            if tokens:
                branches.append(tokens)
    return branches


def _pre_dotenv_tokens() -> set[str]:
    marker = "\n    load_dotenv()"
    assert marker in MAIN_SOURCE, "load_dotenv() call site moved"
    prefix = MAIN_SOURCE.split(marker, 1)[0]
    return set(re.findall(r'head\.lower\(\)\s*==\s*"(' + _CMD + r')"', prefix))


def _help_tokens() -> set[str]:
    buf = io.StringIO()
    with redirect_stderr(buf), redirect_stdout(io.StringIO()):
        assert main_module.handle_meta_command(":help", SimpleNamespace(), REPO_ROOT) is True
    return set(_STANDALONE.findall(buf.getvalue()))


def _banner_tokens() -> set[str]:
    """Tokens in the startup banner literal that `main()` prints to stderr."""
    m = re.search(r'"Commands: (.*?)",\n', MAIN_SOURCE, re.S)
    assert m, "startup banner literal not found in main.py"
    return set(_STANDALONE.findall(m.group(1)))


def _help_usage() -> dict[str, str]:
    """token -> the sketch printed between the token(s) and the description."""
    buf = io.StringIO()
    with redirect_stderr(buf), redirect_stdout(io.StringIO()):
        main_module.handle_meta_command(":help", SimpleNamespace(), REPO_ROOT)
    sketches: dict[str, str] = {}
    for line in buf.getvalue().splitlines():
        m = re.match(r"^  (" + _CMD + r")([^\n]*)$", line)
        if not m:
            continue
        tokens = [m.group(1)]
        rest = m.group(2)
        while True:
            m_alias = re.match(r"^\s*\|\s*(" + _CMD + r")", rest)
            if not m_alias:
                break
            tokens.append(m_alias.group(1))
            rest = rest[m_alias.end() :]
        split = re.match(r"^\s*(.*?)\s{2,}(\S.*)$", rest)
        sketch = (split.group(1) if split else rest).strip()
        for token in tokens:
            sketches.setdefault(token, sketch)
    return sketches


def _commands_map_usage() -> dict[str, str]:
    """token -> usage sketch, parsed from the COMMANDS_MAP signature cells.

    Aliases are separated by an escaped pipe -- and so are the alternatives
    inside a sketch like ``[on\\|off\\|status]``, so a part only starts a new
    alias when it begins with a real token.
    """
    usage: dict[str, str] = {}
    for raw in CMAP_SOURCE.splitlines():
        if not raw.startswith("| `:"):
            continue
        cell = raw.strip().strip("|").split(" | ")[0].strip().strip("`")
        tokens: list[str] = []
        bits: list[str] = []
        for part in (p.strip().strip("`").strip() for p in cell.split(r"\|")):
            if not part or part == "?":
                continue
            m = re.match(r"^(" + _CMD + r")(?:\s+(.*))?$", part)
            if m:
                tokens.append(m.group(1))
                rest = (m.group(2) or "").strip()
                if rest:
                    bits.append(rest)
            elif bits:
                bits[-1] = bits[-1] + "|" + part
            else:
                bits.append(part)
        for token in tokens:
            usage[token] = " ".join(bits).strip()
    return usage


# ── purity ───────────────────────────────────────────────────────────────────

def test_registry_is_pure_data():
    source = (REPO_ROOT / "cli" / "command_registry.py").read_text(encoding="utf-8")
    imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", source, re.M)
    forbidden = [
        name
        for name in imports
        if name.split(".")[0] in {"core", "app", "tools", "api", "main", "docker"}
        or name.startswith("cli.commands")
    ]
    assert not forbidden, f"registry must import no runtime module, found: {forbidden}"
    # stdlib only, and no behaviour smuggled in
    assert set(imports) <= {"__future__", "dataclasses"}, imports
    for banned in ("subprocess", "open(", "requests", "urllib"):
        assert banned not in source, banned


def test_registry_import_has_no_side_effects(tmp_path, monkeypatch):
    """Re-importing the module must not touch the filesystem."""
    opened: list[str] = []
    original_open = Path.open

    def guarded(self, *args, **kwargs):
        opened.append(str(self))
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    importlib.reload(reg)
    assert opened == []


# ── the token surface ────────────────────────────────────────────────────────

def test_registry_tokens_equal_the_dispatched_tokens():
    assert reg.all_tokens() == frozenset(_dispatched_tokens())


def test_every_dispatch_branch_is_exactly_one_registry_command():
    branches = _dispatch_branches()
    registry_sets = {frozenset(spec.tokens) for spec in reg.COMMANDS}
    assert len(reg.COMMANDS) == len(branches), (
        f"{len(branches)} dispatch branches but {len(reg.COMMANDS)} registry commands"
    )
    assert {frozenset(b) for b in branches} == registry_sets


def test_canonical_is_the_first_token_of_its_dispatch_branch():
    by_first = {b[0]: b for b in _dispatch_branches()}
    for spec in reg.COMMANDS:
        assert spec.canonical in by_first, spec.canonical
        assert tuple(spec.tokens) == by_first[spec.canonical]


def test_tokens_are_unique_across_the_registry():
    seen: dict[str, str] = {}
    for spec in reg.COMMANDS:
        for token in spec.tokens:
            assert token not in seen, f"{token} claimed by {seen.get(token)} and {spec.canonical}"
            seen[token] = spec.canonical
    assert len(seen) == len(reg.BY_TOKEN)


def test_canonicals_and_handler_keys_are_unique():
    canonicals = [spec.canonical for spec in reg.COMMANDS]
    handlers = [spec.handler_key for spec in reg.COMMANDS]
    assert len(set(canonicals)) == len(canonicals)
    assert len(set(handlers)) == len(handlers)


def test_no_canonical_is_also_an_alias():
    canonicals = {spec.canonical for spec in reg.COMMANDS}
    aliases = {alias for spec in reg.COMMANDS for alias in spec.aliases}
    assert canonicals.isdisjoint(aliases)


# ── field integrity ──────────────────────────────────────────────────────────

def test_every_command_has_description_category_and_handler_key():
    for spec in reg.COMMANDS:
        assert spec.description.strip(), spec.canonical
        assert spec.category.strip(), spec.canonical
        assert spec.handler_key.strip(), spec.canonical


def test_handler_key_is_derived_from_the_canonical_token():
    for spec in reg.COMMANDS:
        assert spec.handler_key == spec.canonical.lstrip(":").replace("-", "_")


def test_usage_matches_commands_map_where_it_documents_one():
    """COMMANDS_MAP is the primary source: its signature cell is backtick-delimited
    and parses deterministically, so where it declares a usage the registry must
    reproduce it exactly. This is what catches the escaped-pipe corruption class
    (``[on\\|off\\|status]`` collapsing to ``[on off status]``)."""
    expected = _commands_map_usage()
    mismatches = {
        spec.canonical: {"registry": spec.usage, "commands_map": expected[spec.canonical]}
        for spec in reg.COMMANDS
        if expected.get(spec.canonical) and spec.usage != expected[spec.canonical]
    }
    assert not mismatches, f"usage drifted from COMMANDS_MAP: {mismatches}"


def test_usage_falls_back_to_the_help_sketch_when_the_map_has_none():
    """Secondary source. Several help lines lack the two-space padding, so their
    description runs into the sketch; such a line contributes nothing rather than
    prose (see test_usage_never_contains_prose)."""
    from_map = _commands_map_usage()
    from_help = _help_usage()
    for spec in reg.COMMANDS:
        if from_map.get(spec.canonical):
            continue
        sketch = from_help.get(spec.canonical, "")
        parts = sketch.split()
        clean = bool(parts) and all(
            p.startswith(("<", "[", "-")) or p == "..." for p in parts
        )
        assert spec.usage == (sketch if clean else ""), (
            f"{spec.canonical}: usage={spec.usage!r} but the help sketch is "
            f"{sketch!r} (clean={clean})"
        )


def test_usage_never_contains_prose():
    """Guards the defect class where a help line's description leaked in."""
    for spec in reg.COMMANDS:
        for part in spec.usage.split():
            assert part.startswith(("<", "[", "-")) or part in {"...", "…"} or part.endswith(
                ("]", ">", "…")
            ), f"{spec.canonical}: suspicious usage part {part!r} in {spec.usage!r}"


def test_all_commands_work_in_both_modes():
    """Recorded at 72fc7a8: every `:token` is accepted one-shot and in the REPL."""
    for spec in reg.COMMANDS:
        assert spec.modes == reg.BOTH_MODES, spec.canonical


def test_phase_marks_exactly_the_pre_dotenv_fast_paths():
    expected = _pre_dotenv_tokens()
    assert expected == {":self-build-propose", ":schedule-disable"}
    marked = {spec.canonical for spec in reg.COMMANDS if spec.phase == reg.PHASE_PRE_DOTENV}
    assert marked == expected
    for spec in reg.COMMANDS:
        assert spec.phase in {reg.PHASE_PRE_DOTENV, reg.PHASE_POST_AGENT}


# ── visibility flags vs the live surfaces ────────────────────────────────────

def test_in_help_matches_the_live_help_page():
    help_tokens = _help_tokens()
    for spec in reg.COMMANDS:
        listed = any(token in help_tokens for token in spec.tokens)
        assert spec.in_help == listed, (
            f"{spec.canonical}: in_help={spec.in_help} but the :help page "
            f"{'lists' if listed else 'omits'} it"
        )


def test_in_startup_summary_matches_the_banner():
    banner = _banner_tokens()
    for spec in reg.COMMANDS:
        listed = any(token in banner for token in spec.tokens)
        assert spec.in_startup_summary == listed, (
            f"{spec.canonical}: in_startup_summary={spec.in_startup_summary} but the "
            f"banner {'lists' if listed else 'omits'} it"
        )


def test_recorded_divergences_between_help_and_banner():
    """The known gaps from CLI_BASELINE.md section 3.1, now expressed as data."""
    not_in_help = sorted(spec.canonical for spec in reg.COMMANDS if not spec.in_help)
    assert not_in_help == [":help", ":refresh-models"]
    in_banner = sum(1 for spec in reg.COMMANDS if spec.in_startup_summary)
    assert in_banner == 71
    assert len(reg.COMMANDS) == 91
    assert len(reg.all_tokens()) == 140


# ── deliberate exclusions ────────────────────────────────────────────────────

def test_question_mark_and_repl_block_tokens_are_not_in_the_registry():
    for excluded in ("?", ":task-begin", ":task-end", ":task-abort", ":end"):
        assert reg.lookup(excluded) is None, excluded
    # `?` really is dispatched as a :help alias -- it is excluded on purpose
    # because it lives outside the `:token` namespace, not by oversight.
    assert 'head in {":help", "?"}' in MAIN_SOURCE
    assert reg.lookup(":help") is not None


# ── lookup helpers ───────────────────────────────────────────────────────────

def test_lookup_resolves_canonical_and_alias():
    spec = reg.lookup(":mem")
    assert spec is not None and spec.canonical == ":mem"
    assert reg.lookup(":memory") is spec
    assert reg.lookup(":MEMORY") is spec
    assert reg.lookup("  :memory  ") is spec


def test_lookup_returns_none_for_unknown_tokens():
    assert reg.lookup(":no-such-command") is None
    assert reg.lookup("") is None


def test_canonical_tokens_and_in_category_are_consistent():
    canonicals = reg.canonical_tokens()
    assert len(canonicals) == len(reg.COMMANDS)
    assert set(canonicals) == {spec.canonical for spec in reg.COMMANDS}
    covered = [spec for category in {s.category for s in reg.COMMANDS}
               for spec in reg.in_category(category)]
    assert len(covered) == len(reg.COMMANDS)


def test_categories_match_the_commands_map_sections():
    sections = {line[3:].strip() for line in CMAP_SOURCE.splitlines() if line.startswith("## ")}
    for spec in reg.COMMANDS:
        assert spec.category in sections, f"{spec.canonical}: unknown category {spec.category!r}"


def test_command_spec_is_frozen():
    spec = reg.COMMANDS[0]
    with pytest.raises(Exception):
        spec.canonical = ":changed"  # type: ignore[misc]
