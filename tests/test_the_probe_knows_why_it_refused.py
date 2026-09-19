"""A refusal must name the true reason, or it teaches the wrong lesson.

Found 2026-08-20 while auditing the suite with the probe itself. Fourteen
mutation runs were launched with test selections that contained paths which do
not exist. pytest answers a missing path with exit code 4 — "usage error",
nothing was run. `run_tests` read any non-zero code as "red", so the probe
printed:

    REFUSED: the selection is already red — a survivor would mean nothing

An accurate refusal with a false cause is worse than a crash: it sends the
reader to debug failing tests that never ran, and it hides a typo behind a
plausible story about the suite's health. The three states must stay apart:
the selection is green, the selection is red, or the selection could not be
run at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _probe_module():
    import mutation_probe

    return mutation_probe


def test_a_missing_path_is_not_reported_as_a_red_selection() -> None:
    probe = _probe_module()
    state = probe.selection_state(["tests/test_this_path_does_not_exist_qqzz.py"])
    assert state == "uncollectable", (
        f"a path that does not exist was classified {state!r} — the probe would "
        "refuse with the wrong reason and send the reader after phantom failures"
    )


def test_a_genuinely_failing_selection_is_still_red() -> None:
    """Boundary pin: the fix must not make every refusal 'uncollectable'."""
    probe = _probe_module()
    failing = _REPO / "tests" / "fixtures" / "probe_red_fixture.py"
    failing.parent.mkdir(parents=True, exist_ok=True)
    failing.write_text("def test_deliberately_red():\n    assert False\n", encoding="utf-8")
    try:
        state = probe.selection_state([str(failing.relative_to(_REPO))])
    finally:
        failing.unlink()
    assert state == "red"


def test_a_healthy_selection_is_green() -> None:
    probe = _probe_module()
    assert probe.selection_state(["tests/test_evidence.py"]) == "green"
