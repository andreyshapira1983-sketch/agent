"""No watched function may grow back, and none may appear unwatched.

The file guard answers "is this module too big"; this one answers "can a human
hold this in their head". Measured 2026-08-04 over 9 797 functions: 99.3% are
under 100 lines and the pain sits in a handful — `core/loop.py:_run_inner` is
2 213 lines, 3.4x the next-longest. A 3 000-line file of 100-line functions
reads fine; a 2 000-line file that is one function does not.

Wired into the suite on purpose: the file guard sat orphaned for months while
three files were over their ceilings and it reported that to nobody.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "check_function_length_baseline.py"


def _guard():
    spec = importlib.util.spec_from_file_location("check_function_length_baseline", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_ratchet_holds():
    """Growth past a ceiling, or a new long function, fails here."""
    assert _guard().main([]) == 0, (
        "a function grew past its ceiling, or a new one crossed the threshold "
        "unwatched — shrink it, or bank the decision by editing WATCH in "
        "scripts/check_function_length_baseline.py"
    )


def test_every_watched_function_still_exists():
    """A stale entry watches nothing and hides that the ratchet went slack."""
    module = _guard()
    found = module.measure(_REPO)
    missing = sorted(set(module.WATCH) - set(found))

    assert not missing, (
        f"WATCH names functions that no longer exist: {missing}. Split or "
        "renamed — drop the entry instead of leaving a ceiling nobody meets."
    )


def test_ceilings_stay_close_to_the_measurement():
    """A ceiling far above reality is a ratchet in name only."""
    module = _guard()
    found = module.measure(_REPO)
    slack = {
        key: (ceiling, found[key])
        for key, ceiling in module.WATCH.items()
        if key in found and ceiling - found[key] > 40
    }

    assert not slack, (
        "these ceilings drifted far above the measured length — lower them to "
        f"bank the win: {slack}"
    )


def test_the_threshold_and_the_watch_list_agree():
    """Anything above the threshold is watched; nothing below clutters the list."""
    module = _guard()
    found = module.measure(_REPO)
    unwatched = sorted(
        k for k, n in found.items()
        if n > module.REPORT_THRESHOLD and k not in module.WATCH
    )
    trivial = sorted(
        k for k in module.WATCH
        if k in found and found[k] <= module.REPORT_THRESHOLD
    )

    assert not unwatched, f"long functions outside the watch list: {unwatched}"
    assert not trivial, (
        f"these are under {module.REPORT_THRESHOLD} lines now — drop them from "
        f"WATCH: {trivial}"
    )
