"""A `.qm` assertion must fail when the thing it names is gone.

The R-A property once asserted module-wide membership of `knowledge_auto_write` in
`getattr_names`. R-C reads the same field from the same module, so removing BOTH
R-A sites left the property green: it was watching the module, not the sites.

This test reproduces that exact masking condition on a COPY of the production
module -- both R-A reads replaced, R-C untouched, the copy still parsing -- and
requires the assertions carried by `app.qm` to fail on it. Nothing in the
repository is mutated.

The first attempt at the original mutation was itself vacuous: the replacement left
a comment inside parentheses, the copy stopped parsing, and the red came from
syntax rather than from the missing reads. The parse check below is that lesson.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_BRIDGE = _ROOT / "scripts" / "qm_py_anchor.py"
_TARGET = _ROOT / "core" / "ingestion.py"
_READ = 'bool(getattr(agent, "knowledge_auto_write", False))'


def _facts(module_path: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(_BRIDGE), str(module_path), "__module__"],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    return json.loads(proc.stdout)


def _ra_assertions() -> list[dict]:
    specimen = json.loads((_ROOT / "app.qm").read_text(encoding="utf-8"))
    for binding in specimen["producer_bindings"]:
        for check in binding.get("consumer_checks", []):
            if check.get("regime") == "R-A":
                return [a for prop in check["properties"] for a in prop["assertions"]]
    raise AssertionError("app.qm declares no R-A consumer check")


def _evaluate(assertions: list[dict], facts: dict) -> list[bool]:
    spec = importlib.util.spec_from_file_location(
        "qm_link_check_under_test", _ROOT / "scripts" / "qm_link_check.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return [module._check_assertion(a, facts)[0] for a in assertions]


def _mutated_copy(tmp_path: Path, removals: int) -> Path:
    source = _TARGET.read_text(encoding="utf-8")
    assert source.count(_READ) == 3, (
        "this test is written against three reads of the field -- two R-A and one "
        f"R-C. Found {source.count(_READ)}. Re-derive the sites before trusting it."
    )
    for _ in range(removals):
        source = source.replace(_READ, "bool(False)", 1)
    copy = tmp_path / "ingestion_mutated.py"
    copy.write_text(source, encoding="utf-8")
    ast.parse(source)          # the red must not come from a syntax error
    return copy


def test_the_assertions_pass_on_the_real_module() -> None:
    assert all(_evaluate(_ra_assertions(), _facts(_TARGET)))


def test_removing_both_ra_reads_fails_even_though_rc_still_reads_the_field(
    tmp_path: Path,
) -> None:
    copy = _mutated_copy(tmp_path, removals=2)
    facts = _facts(copy)
    assert facts["parse_ok"] and facts["anchor_found"] is False
    # the masking condition itself: module-wide membership SURVIVES, because R-C
    # still reads the field. An assertion at that scope would stay green here.
    assert "knowledge_auto_write" in facts["getattr_names"]
    assert not any(_evaluate(_ra_assertions(), facts)), (
        "app.qm's R-A assertions stayed green with both R-A reads gone -- they are "
        "watching the module rather than the sites"
    )


def test_removing_only_the_first_ra_read_already_fails(tmp_path: Path) -> None:
    facts = _facts(_mutated_copy(tmp_path, removals=1))
    assert not all(_evaluate(_ra_assertions(), facts))
