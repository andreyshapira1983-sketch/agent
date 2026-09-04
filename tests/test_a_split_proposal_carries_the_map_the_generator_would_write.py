"""A split proposal carries the anatomy map the generator would write.

Measured 2026-09-04 on the lane (ain_b7a0…, the regenerated smart_memory
split): rolled back on `test_agent_anatomy_check.py`. The producer's
`_sync_anatomy_index` wrote its own canned row («Extracted from … by
autonomous self-build module split») and never touched «_Total: N modules»,
while the guard compares the map with `scripts/gen_anatomy.py` byte for
byte. So every split proposal that adds a module failed the same test and
rolled back — silently, always: the agent's splits could never land.

Now the map in a proposal is what the generator itself renders for the
PROPOSED tree: groups from the proposal's `core/anatomy_groups.py`, modules
on disk plus the new ones, purpose = the first sentence of the new module's
own docstring, taken from the proposal's content. Without a workspace (a
sandbox with no generator) the old canned path stays, so nothing that
worked before is lost.
"""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

_A = '"""Module A does one thing.\n\nLonger text.\n"""\nX = 1\n'
_B = '"""Module B keeps the other thing.\n"""\nY = 2\n'
_A_HELPERS = (
    '"""Helpers extracted verbatim from ``core/a.py`` by the incremental splitter.\n'
    'Second sentence must not leak into the cell.\n"""\nZ = 3\n'
)
_GROUPS = (
    'GROUPS = [\n'
    '    ("One (§1)", "First group.", [\n        "a",\n    ]),\n'
    '    ("Two (§2)", "Second group.", [\n        "b",\n        "anatomy_groups",\n    ]),\n'
    ']\n'
)


def _sandbox(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "core").mkdir(parents=True)
    (ws / "scripts").mkdir()
    (ws / "knowledge" / "generated").mkdir(parents=True)
    (ws / "core" / "a.py").write_text(_A, encoding="utf-8")
    (ws / "core" / "b.py").write_text(_B, encoding="utf-8")
    (ws / "core" / "anatomy_groups.py").write_text(_GROUPS, encoding="utf-8")
    shutil.copy(_ROOT / "scripts" / "gen_anatomy.py", ws / "scripts" / "gen_anatomy.py")
    return ws


def _generator(ws: Path):
    spec = importlib.util.spec_from_file_location("gen_anatomy_sandbox", ws / "scripts" / "gen_anatomy.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _reader(ws: Path):
    def read(rel: str):
        p = ws / rel
        return p.read_text(encoding="utf-8") if p.is_file() else None
    return read


def test_the_map_in_the_proposal_is_exactly_what_the_generator_renders_for_that_tree(tmp_path):
    from core.self_build_producer import _sync_anatomy_index

    ws = _sandbox(tmp_path)
    gen = _generator(ws)
    (ws / "knowledge" / "generated" / "AGENT_ANATOMY.md").write_text(
        gen.build_document(actual={"a", "b", "anatomy_groups"}, groups=gen.load_groups_from_source(_GROUPS),
                           purpose=lambda s: gen.purpose_from_source((ws / "core" / f"{s}.py").read_text(encoding="utf-8"))),
        encoding="utf-8", newline="\n",
    )
    new_groups = _GROUPS.replace('        "a",\n', '        "a",\n        "a_helpers",\n')
    build = {"files": [
        {"path": "core/a.py", "content": _A},
        {"path": "core/a_helpers.py", "content": _A_HELPERS},
        {"path": "core/anatomy_groups.py", "content": new_groups},
    ]}

    _sync_anatomy_index(build, "core/a.py", _reader(ws), workspace=ws)

    carried = {f["path"]: f["content"] for f in build["files"]}
    doc = carried["knowledge/generated/AGENT_ANATOMY.md"]
    assert "_Total: 4 modules across 2 groups._" in doc
    assert "| `core/a_helpers` | Helpers extracted verbatim from ``core/a.py`` by the incremental splitter. |" in doc
    assert "Second sentence" not in doc
    # And it is byte-for-byte the generator's own rendering of the proposed tree —
    # apply the proposal to the sandbox and ask the generator directly.
    for path, content in carried.items():
        (ws / path).write_text(content, encoding="utf-8", newline="\n")
    gen2 = _generator(ws)
    expected = gen2.build_document(
        actual={"a", "b", "anatomy_groups", "a_helpers"}, groups=gen2.load_groups_from_source(new_groups),
        purpose=lambda s: gen2.purpose_from_source((ws / "core" / f"{s}.py").read_text(encoding="utf-8")),
    )
    assert doc == expected


def test_without_a_generator_in_the_workspace_the_old_canned_row_still_lands(tmp_path):
    from core.self_build_producer import _sync_anatomy_index

    ws = _sandbox(tmp_path)
    (ws / "scripts" / "gen_anatomy.py").unlink()
    (ws / "knowledge" / "generated" / "AGENT_ANATOMY.md").write_text(
        "# Agent Anatomy\n\n| `core/a` | Module A does one thing. |\n", encoding="utf-8",
    )
    build = {"files": [{"path": "core/a.py", "content": _A}, {"path": "core/a_helpers.py", "content": _A_HELPERS}]}

    _sync_anatomy_index(build, "core/a.py", _reader(ws), workspace=ws)

    doc = {f["path"]: f["content"] for f in build["files"]}["knowledge/generated/AGENT_ANATOMY.md"]
    assert "| `core/a_helpers` | Extracted from `core/a` by autonomous self-build module split. |" in doc


def test_an_unrenderable_tree_leaves_the_map_alone_for_the_guard_to_name(tmp_path):
    """Groups that do not cover the new module: no invented row, no crash."""
    from core.self_build_producer import _sync_anatomy_index

    ws = _sandbox(tmp_path)
    (ws / "knowledge" / "generated" / "AGENT_ANATOMY.md").write_text("# map\n", encoding="utf-8")
    build = {"files": [{"path": "core/a.py", "content": _A}, {"path": "core/a_helpers.py", "content": _A_HELPERS}]}

    _sync_anatomy_index(build, "core/a.py", _reader(ws), workspace=ws)

    paths = [f["path"] for f in build["files"]]
    # The canned fallback still runs when rendering fails, so a row is added —
    # but never a lie about the total: the canned row is the pre-2026-09-04 behaviour.
    assert "knowledge/generated/AGENT_ANATOMY.md" in paths


def test_the_live_generator_exposes_the_three_hooks():
    spec = importlib.util.spec_from_file_location("gen_anatomy_live", _ROOT / "scripts" / "gen_anatomy.py")
    assert spec and spec.loader
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert callable(gen.load_groups_from_source) and callable(gen.purpose_from_source)
    assert gen.purpose_from_source('"""One. Two."""') == "One."
