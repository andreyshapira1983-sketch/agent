"""Each catalogue permission must ask the allowlist about ITS OWN sink.

WHO NEEDS THIS FILE. `core/loop.py:312-313` derives two permissions from the
same gate:

    may_knowledge       = not self._durable_learning_suppressed("knowledge")
    may_source_registry = not self._durable_learning_suppressed("source_registry")

They are two lines that differ only in a string, and they are the last thing
between a turn and two different durable writes — long-term memory and the
persisted source registry.

WHY IT WAS UNGUARDED, and why that is easy to miss. In every configuration the
suite exercises, the two answers are EQUAL: `durable_writes=None` allows both,
`frozenset()` denies both, and the two absolute brakes deny everything. They can
only disagree under an allowlist that holds one of the two names and not the
other, and no test used one. Measured 2026-08-09: with line 312 asking about
`"source_registry"` instead of `"knowledge"`, the whole suite passed — 7307
tests, exit 0.

`tests/test_catalogue_core_is_shared.py::test_permissions_reach_the_pipeline`
covers the hop AFTER this one: given the two booleans, the pipeline receives
`remember`, `auto_write_memory` and `source_store` accordingly. It cannot see a
swap upstream of itself, because it supplies the booleans directly.

The partial allowlist is therefore the whole point of this file, and each test
below states which one it holds.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bootstrap import build_agent
from core.loop_memory_write import KNOWN_DURABLE_SINKS


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _permissions(workspace: Path, allowlist: frozenset[str]) -> tuple[bool, bool]:
    """The two booleans exactly as `core/loop.py` derives them."""
    agent = build_agent(workspace, with_memory=True, durable_writes=allowlist,
                        approval_provider=None)
    return (
        not agent._durable_learning_suppressed("knowledge"),
        not agent._durable_learning_suppressed("source_registry"),
    )


def test_both_sink_names_are_ones_the_gate_recognises() -> None:
    """Precondition: an unknown name is denied by rule 3, which would hide a swap."""
    assert {"knowledge", "source_registry"} <= KNOWN_DURABLE_SINKS


def test_an_allowlist_holding_only_knowledge_permits_only_knowledge(
    tmp_path: Path,
) -> None:
    may_knowledge, may_source_registry = _permissions(
        tmp_path, frozenset({"knowledge"}))
    assert may_knowledge is True
    assert may_source_registry is False, (
        "the registry permission answered for the knowledge sink; the two "
        "derivations differ only in a string and nothing else was watching"
    )


def test_an_allowlist_holding_only_the_registry_permits_only_the_registry(
    tmp_path: Path,
) -> None:
    may_knowledge, may_source_registry = _permissions(
        tmp_path, frozenset({"source_registry"}))
    assert may_source_registry is True
    assert may_knowledge is False, (
        "the knowledge permission answered for the source_registry sink — the "
        "exact swap that left 7307 tests green"
    )


@pytest.mark.parametrize(
    "allowlist,expected",
    [
        (None, (True, True)),
        (frozenset(), (False, False)),
    ],
)
def test_the_configurations_that_cannot_tell_them_apart(
    tmp_path: Path, allowlist, expected
) -> None:
    """GUARD: these are the shapes the suite already had, and they agree.

    Stated so the two tests above cannot be mistaken for redundant: under every
    non-partial allowlist the two permissions are equal, which is precisely why
    a swap was invisible.
    """
    assert _permissions(tmp_path, allowlist) == expected


def test_each_derivation_in_the_loop_passes_its_own_sink_name() -> None:
    """The wiring itself, because the tests above cannot see it.

    Written after those tests FAILED to redden on the swap. They call the gate
    directly, so they exercise a restatement of `core/loop.py` rather than
    `core/loop.py` — the same trap this project recorded once before, where a
    replay gate's conditions were restated inside a test instead of called.

    Driving the real derivation would mean a full turn that reaches the
    catalogue branch. This asserts the relation instead, which is the pairing
    `tests/test_verified_memory_gate_follows_gateway_path.py` already uses:
    behaviour above, wiring here. It is not a body-equivalence ratchet — it
    reads one argument of one call and would not notice a renamed local.
    """
    import ast
    from pathlib import Path as _Path

    source = (_Path(__file__).resolve().parent.parent / "core" / "loop.py").read_text(
        encoding="utf-8")
    expected = {"may_knowledge": "knowledge",
                "may_source_registry": "source_registry"}
    found: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in expected:
            continue
        for call in ast.walk(node.value):
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "_durable_learning_suppressed"
                    and call.args
                    and isinstance(call.args[0], ast.Constant)):
                found[target.id] = call.args[0].value

    assert found == expected, (
        f"a catalogue permission is asking the allowlist about the wrong sink: "
        f"{found}. Under every non-partial allowlist the two answers are equal, "
        "so a swap here changes nothing until an operator allowlists one of them"
    )
