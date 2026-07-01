"""Read-only tests for scripts/agent_anatomy_check.py (TD-029).

These tests only load the script's pure helpers and read existing repo files.
They do not run agent code, hit the network, or write files.
"""
from __future__ import annotations

import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "agent_anatomy_check.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("agent_anatomy_check", _SCRIPT)
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


def test_script_does_not_import_core_or_git():
    # Guard the read-only contract at the source level.
    with open(_SCRIPT, "r", encoding="utf-8") as handle:
        src = handle.read()
    assert "import core" not in src
    assert "from core" not in src
    assert "subprocess" not in src
    assert "urllib" not in src
    assert "requests" not in src
