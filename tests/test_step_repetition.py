"""Tests for core.step_repetition (MAST FM-1.3)."""

from core.step_repetition import StepRepetitionTracker, normalize_args


def test_normalize_args_handles_none_and_unjsonable():
    assert normalize_args(None) == ""

    class Weird:
        def __repr__(self) -> str:
            return "Weird()"

    out = normalize_args(Weird())
    assert "Weird" in out


def test_tracker_isolates_per_args():
    t = StepRepetitionTracker(threshold=2)
    assert t.observe("file_read", {"path": "a"}) is None
    assert t.observe("file_read", {"path": "b"}) is None
    rep_a = t.observe("file_read", {"path": "a"})
    assert rep_a is not None
    # path=b still under threshold
    assert t.count("file_read", {"path": "b"}) == 1


def test_tracker_ignores_empty_tool():
    t = StepRepetitionTracker(threshold=2)
    assert t.observe("", {"x": 1}) is None
    assert t.observe("", {"x": 1}) is None


def test_tracker_summary_lists_pairs():
    t = StepRepetitionTracker()
    t.observe("file_read", {"path": "a"})
    t.observe("web_search", {"q": "x"})
    s = t.summary()
    assert any(k.startswith("file_read|") for k in s)
    assert any(k.startswith("web_search|") for k in s)
