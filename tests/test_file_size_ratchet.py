"""The file-size ratchet is a build gate, not an orphaned script.

`scripts/check_ceo_file_baseline.py` existed, watched five files, and was wired
into nothing — the 2026-08 audit found three of them over their ceilings with
the guard reporting it to nobody (the repo's recurring anti-pattern: a decider
no call site reaches). This test is the call site.

The ceilings are a RATCHET: measured size at last review plus small slack, so
the only thing the gate forbids is growing back. When a decomposition lands,
the ceiling is lowered to bank the win — loop.py's entry already banks the
#217–#224 extraction (−686 lines).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_ceo_file_baseline.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("check_ceo_file_baseline", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_watched_file_has_grown_past_its_ratchet():
    assert _load_guard().main() == 0, (
        "a watched file grew past its ratchet ceiling — shrink it back or, if "
        "the growth is a deliberate reviewed decision, raise the ceiling in "
        "scripts/check_ceo_file_baseline.py WITH a comment saying why"
    )


def test_the_watchlist_covers_every_current_giant():
    """A file can only dodge the ratchet by staying small.

    Any production python file at or above the smallest watched ceiling's
    magnitude (1 300 lines) must be on the watchlist — otherwise the next
    giant grows in an unwatched file and the guard guards history instead of
    the repository.
    """
    guard = _load_guard()
    watched = set(guard.WATCH)
    threshold = 1_300
    offenders = []
    for scope in ("core", "cli", "app", "tools", "api"):
        for path in (REPO_ROOT / scope).glob("*.py"):
            lines = len(path.read_text(encoding="utf-8").splitlines())
            rel = path.relative_to(REPO_ROOT).as_posix()
            if lines >= threshold and rel not in watched:
                offenders.append(f"{rel} ({lines})")
    for name in ("main.py", "agent_tick.py"):
        path = REPO_ROOT / name
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines >= threshold and name not in watched:
            offenders.append(f"{name} ({lines})")
    assert not offenders, (
        f"files at giant size but not on the ratchet watchlist: {offenders}"
    )


def test_loop_pys_decomposition_win_is_banked():
    """The #217–#224 extraction took loop.py from 4733 to 4047. The ceiling
    must hold that win — a value at or above the OLD size would let the whole
    decomposition silently regrow."""
    assert _load_guard().WATCH["core/loop.py"] < 4_500
