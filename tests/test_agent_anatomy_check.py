"""Read-only tests for the anatomy map: its drift check AND its generator (TD-029).

These tests only load the scripts' pure helpers and read existing repo files.
They do not run agent code, hit the network, or write files.

## Why the generator is tested here too

`agent_anatomy_check.py` compares *module names* between `core/` and the map, and
that is all it compares. So the map could be hand-edited into name-sync while
`gen_anatomy.py` — the thing that is supposed to produce it — drifted 37 modules
behind `core/` and could not run at all. Nothing failed, because nothing checked
that the committed document is what the generator actually emits.

`test_the_committed_map_is_exactly_what_the_generator_emits` is that missing
check. It fails if someone hand-edits the map, if a new `core/` module is not
grouped, or if a module docstring changes without regenerating.
"""
from __future__ import annotations

import importlib.util
import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "agent_anatomy_check.py")
_GENERATOR = os.path.join(_ROOT, "scripts", "gen_anatomy.py")
_DOC = os.path.join(_ROOT, "docs", "AGENT_ANATOMY.md")


def _load_module():
    spec = importlib.util.spec_from_file_location("agent_anatomy_check", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_anatomy", _GENERATOR)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_file_exists():
    assert os.path.isfile(_SCRIPT)


def test_core_modules_excludes_init():
    mod = _load_module()
    names = mod._core_modules()
    assert "__init__" not in names
    assert "loop" in names
    assert all(not n.endswith(".py") for n in names)


def test_documented_modules_parses_core_tokens():
    mod = _load_module()
    found = mod._documented_modules("see `core/loop` and core/planner here")
    assert "loop" in found
    assert "planner" in found


def test_doc_and_core_are_in_sync():
    # The committed map must cover every core/*.py module.
    mod = _load_module()
    assert mod.main() == 0


def test_drift_is_detectable_via_set_math():
    # Pure-logic proof that a missing module would be flagged, without editing
    # any real file: the check is set difference between core/ and documented.
    mod = _load_module()
    actual = mod._core_modules()
    documented = mod._documented_modules("core/loop only")
    missing = actual - documented
    assert "planner" in missing  # present in core/, absent from this fake doc


# ── The generator, and the equality nobody was checking ──────────────────────

def test_the_committed_map_is_exactly_what_the_generator_emits():
    """The map is a build artefact; a hand-edit must show up as a red test.

    `build_document()` writes nothing, so this compares without side effects.
    """
    gen = _load_generator()
    with open(_DOC, encoding="utf-8") as handle:
        committed = handle.read()
    assert gen.build_document() == committed, (
        "docs/AGENT_ANATOMY.md differs from `python scripts/gen_anatomy.py`. "
        "Either regenerate it, or — if a new core/ module needs a home — add it "
        "to GROUPS in scripts/gen_anatomy.py first."
    )


def test_groups_cover_core_exactly():
    """Neither an ungrouped module nor a group entry with no module behind it."""
    gen = _load_generator()
    grouped = [m for _title, _desc, mods in gen.GROUPS for m in mods]
    assert len(grouped) == len(set(grouped)), "a module is listed in two groups"
    assert set(grouped) == gen._actual_modules()


def test_every_purpose_cell_is_a_finished_sentence():
    """The summary is unwrapped before it is cut.

    Reading `splitlines()[0]` truncated wherever the author happened to wrap the
    docstring, so rows ended mid-clause ("... and the"). Also catches an empty
    cell, which is what a module with no docstring used to produce.
    """
    gen = _load_generator()
    row = re.compile(r"^\| `core/([a-zA-Z0-9_]+)` \| (.*?) \|$")
    bad: list[str] = []
    for line in gen.build_document().splitlines():
        match = row.match(line)
        if not match:
            continue
        name, purpose = match.group(1), match.group(2).strip()
        if not purpose or not purpose.endswith((".", "?", "!", ")")):
            bad.append(f"core/{name}: {purpose!r}")
    assert not bad, "purpose cells that are empty or cut mid-sentence:\n" + "\n".join(bad)


def test_first_doc_line_rejoins_a_wrapped_summary(tmp_path, monkeypatch):
    """The extraction rule itself, on a planted docstring — no real module needed."""
    gen = _load_generator()
    fake_core = tmp_path / "core"
    fake_core.mkdir()
    (fake_core / "wrapped.py").write_text(
        '"""Intent understanding — the translator between plain human language\n'
        'and the agent\'s actions.\n\n'
        'A second paragraph that must not appear in the summary.\n"""\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(gen, "CORE", str(fake_core))
    assert gen._first_doc_line("wrapped") == (
        "Intent understanding — the translator between plain human language "
        "and the agent's actions."
    )


def test_build_document_raises_rather_than_killing_the_caller(monkeypatch):
    """A library function must not exit the process on bad input.

    `SystemExit` derives from `BaseException`, so an ordinary
    `except Exception` around a call would not catch it — and this function is
    now called from tests and could be called from elsewhere.
    """
    gen = _load_generator()
    monkeypatch.setattr(gen, "GROUPS", [("Only group", "desc", ["loop"])])
    with pytest.raises(ValueError, match="not grouped"):
        gen.build_document()

    monkeypatch.setattr(
        gen, "GROUPS", [("A", "d", ["loop"]), ("B", "d", ["loop"])]
    )
    with pytest.raises(ValueError, match="listed twice"):
        gen.build_document()


def test_main_reports_a_bad_group_table_as_exit_1(monkeypatch, capsys):
    """The operator-visible contract is unchanged: message on stderr, exit 1."""
    gen = _load_generator()
    monkeypatch.setattr(gen, "GROUPS", [("Only group", "desc", ["loop"])])
    assert gen.main() == 1
    assert "not grouped" in capsys.readouterr().err


def test_no_map_row_leads_with_split_provenance():
    """The map says what a module does, not where it was carved from.

    Twelve modules used to open with "Extracted from ... by autonomous
    self-build module split", which told a reader nothing. The provenance is
    kept as a second paragraph, which `_first_doc_line` does not read.
    """
    gen = _load_generator()
    offenders = [
        line for line in gen.build_document().splitlines()
        if line.startswith("| `core/") and "| Extracted from" in line
    ]
    assert not offenders, "rows leading with provenance boilerplate:\n" + "\n".join(offenders)


def test_first_doc_line_is_empty_for_a_module_without_one(tmp_path, monkeypatch):
    gen = _load_generator()
    fake_core = tmp_path / "core"
    fake_core.mkdir()
    (fake_core / "bare.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(gen, "CORE", str(fake_core))
    assert gen._first_doc_line("bare") == ""


def test_script_does_not_import_core_or_git():
    # Guard the read-only contract at the source level.
    with open(_SCRIPT, encoding="utf-8") as handle:
        src = handle.read()
    assert "import core" not in src
    assert "from core" not in src
    assert "subprocess" not in src
    assert "urllib" not in src
    assert "requests" not in src
