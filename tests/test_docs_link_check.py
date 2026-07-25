"""Read-only tests for scripts/docs_link_check.py (doc relative-link guard).

These tests load the script's pure helpers and read existing repo files only.
They do not run agent code, hit the network, or write outside a tmp dir.
"""
from __future__ import annotations

import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "docs_link_check.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("docs_link_check", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_file_exists():
    assert os.path.isfile(_SCRIPT)


def test_all_doc_links_resolve():
    # Every relative Markdown link in README + docs/ must point at a real file.
    mod = _load_module()
    broken = mod.find_broken_links()
    assert broken == [], f"broken relative links in docs: {broken}"


def test_main_returns_zero():
    mod = _load_module()
    assert mod.main() == 0


def test_line_anchor_and_fragment_are_stripped():
    mod = _load_module()
    assert mod._normalize_target("main.py:1069") == "main.py"
    assert mod._normalize_target("core/loop.py:41-52") == "core/loop.py"
    assert mod._normalize_target("docs/ROADMAP.md#track-h") == "docs/ROADMAP.md"


def test_external_links_skipped():
    mod = _load_module()
    assert mod._is_external("https://example.com")
    assert mod._is_external("#anchor")
    assert not mod._is_external("../AGENT_DOCTRINE.md")


def test_broken_link_is_detectable(tmp_path):
    # Pure proof the guard flags a missing target, without touching real docs.
    mod = _load_module()
    sample = tmp_path / "sample.md"
    ok = tmp_path / "real.md"
    ok.write_text("x", encoding="utf-8")
    sample.write_text(
        "see [missing](./nope_missing.md) and [ok](real.md)", encoding="utf-8"
    )
    broken = mod.find_broken_links([str(sample)])
    assert any("nope_missing.md" in target for _src, target in broken)
    assert all("real.md" not in target for _src, target in broken)


def test_script_is_read_only():
    with open(_SCRIPT, "r", encoding="utf-8") as handle:
        src = handle.read()
    assert "import core" not in src
    assert "from core" not in src
    assert "subprocess" not in src
    assert "urllib" not in src
    assert "requests" not in src
