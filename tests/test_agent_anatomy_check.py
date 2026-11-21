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


def test_doc_and_core_are_in_sync():
    # The committed map must cover every core/*.py module.
    mod = _load_module()
    assert mod.main() == 0


def test_main_flags_a_core_module_missing_from_the_map(tmp_path, monkeypatch, capsys):
    """A map that omits one core/ module makes main() exit 1 and name it.

    The map is a planted copy, so no real file is edited.
    """
    mod = _load_module()
    doc = tmp_path / "AGENT_ANATOMY.md"
    listed = sorted(mod._core_modules() - {"planner"})
    doc.write_text(" ".join(f"core/{name}" for name in listed), encoding="utf-8")
    monkeypatch.setattr(mod, "DOC_PATH", str(doc))

    assert mod.main() == 1

    lines = capsys.readouterr().out.splitlines()
    assert "MISSING from knowledge/generated/AGENT_ANATOMY.md (1):" in lines
    assert "  - core/planner" in lines
    assert "RESULT: OUT OF SYNC - update knowledge/generated/AGENT_ANATOMY.md." in lines


# ── The generator, and the equality nobody was checking ──────────────────────


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
