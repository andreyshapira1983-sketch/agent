"""MIR-077 — the silent-except ratchet: the invisible-failure class only shrinks.

Review rounds #283 and #286 each found the same defect shape in `core/`: a
broad ``except Exception`` swallowing a failure with no journal event — the
subsystem breaks and nothing anywhere says so. The audit mapped every broad
handler (`scripts/except_audit.py`); this ratchet pins the baseline of the
TARGET class — silent handlers that state no reason — so it can only go down.

Fixing a site means either journaling the failure (the #283/#286 pattern) or
writing the comment that names WHY silence is correct there; both moves take
the site out of the target class. Adding a NEW unjustified silent handler
fails this test.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

# Measured 2026-08-03 (map in MIR-077): 157 broad handlers total — 31
# journaled, 6 log-guards, 2 re-raise, 118 silent, of which 52 name no
# reason. Lower this number as classes get fixed; never raise it.
_BASELINE_UNJUSTIFIED_SILENT = 52


def _load_audit():
    spec = importlib.util.spec_from_file_location(
        "except_audit", _REPO / "scripts" / "except_audit.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_new_unjustified_silent_broad_excepts():
    audit = _load_audit()
    rows = audit.audit()
    bad = audit.unjustified_silent(rows)
    assert len(bad) <= _BASELINE_UNJUSTIFIED_SILENT, (
        f"новых немотивированных молчащих except: {len(bad)} > "
        f"{_BASELINE_UNJUSTIFIED_SILENT}. Либо журналируй сбой "
        "(образец: verification_explained_failed), либо напиши комментарий, "
        "называющий причину молчания, либо сузь тип исключения.\n"
        + "\n".join(f"  {r['file']}:{r['line']} ({r['kind']})" for r in bad[-10:])
    )


def test_the_scanner_sees_the_known_landscape():
    """Sanity pin: the scanner keeps distinguishing the classes it promised."""
    audit = _load_audit()
    rows = audit.audit()
    kinds = {r["kind"] for r in rows}
    assert "journaled" in kinds
    assert "log_guard" in kinds, "страховка вокруг журналирования — легитимный класс"
    assert len(rows) >= 100, "карта внезапно опустела — сканер сломан, а не код чист"
