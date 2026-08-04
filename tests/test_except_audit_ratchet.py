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

# Measured 2026-08-03, RE-measured after the #292 review round fixed the
# scanner (substring 'log' matched login/logic — 10 handlers were falsely
# journaled): 157 broad handlers = 21 journaled + 5 log-guards + 2 re-raise
# + 129 silent, of which 61 named no reason. Класс `core/loop.py` закрыт
# целиком (15 мест: 11 сенсоров журналируют сбой через
# `core/sensor_journal`, 4 значения по умолчанию назвали причину) —
# база опущена 61 → 46. Опускать дальше по мере починки; не поднимать.
#: 2026-08-05: closed to ZERO. 162 broad handlers remain; 50 journal the
#: failure (was 23) and every silent one now carries a written reason. The
#: scanner was fixed first — it matched call names by exact token, so a private
#: helper like `self._log` read as silence and 5 of the 46 were never broken.
#: From here the class cannot grow: a new broad handler must journal, re-raise,
#: narrow its type, or say in words why silence is right.
_BASELINE_UNJUSTIFIED_SILENT = 0


def _load_audit():
    path = _REPO / "scripts" / "except_audit.py"
    spec = importlib.util.spec_from_file_location("except_audit", path)
    assert spec is not None and spec.loader is not None, (
        f"сканер не найден или не загружаем: {path}"
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


# ── the scanner's own spec (review round #292: the ratchet's foundation
#    must not be an untested script) ─────────────────────────────────────────

def test_login_is_not_journaling():
    audit = _load_audit()
    rows = audit.classify_source(
        "try:\n    x = 1\nexcept Exception:\n    login()\n", "f.py"
    )
    assert rows[0]["kind"] == "silent_other", "подстрочное 'log' в login — не журнал"


def test_error_level_counts_as_journaling():
    audit = _load_audit()
    rows = audit.classify_source(
        "try:\n    x = 1\nexcept Exception as exc:\n    logger.error(exc)\n", "f.py"
    )
    assert rows[0]["kind"] == "journaled"


def test_comment_above_the_except_counts_as_a_reason():
    audit = _load_audit()
    rows = audit.classify_source(
        "try:\n    x = 1\n# намеренно глушим: изоляция по-записно\nexcept Exception:\n    pass\n",
        "f.py",
    )
    assert rows[0]["commented"] is True


def test_log_guard_is_recognised():
    audit = _load_audit()
    rows = audit.classify_source(
        "try:\n    self.log.log('e', {})\nexcept Exception:\n    pass\n", "f.py"
    )
    assert rows[0]["kind"] == "log_guard"


def test_narrow_exception_is_not_counted():
    audit = _load_audit()
    rows = audit.classify_source(
        "try:\n    x = 1\nexcept ValueError:\n    pass\n", "f.py"
    )
    assert rows == []


def test_tuple_containing_exception_is_broad():
    audit = _load_audit()
    rows = audit.classify_source(
        "try:\n    x = 1\nexcept (ValueError, Exception):\n    pass\n", "f.py"
    )
    assert len(rows) == 1 and rows[0]["kind"] == "silent_noop"


def test_except_star_is_scanned():
    audit = _load_audit()
    rows = audit.classify_source(
        "try:\n    x = 1\nexcept* Exception:\n    pass\n", "f.py"
    )
    assert len(rows) == 1 and rows[0]["kind"] == "silent_noop"


def test_hash_inside_a_string_is_not_a_justification():
    audit = _load_audit()
    rows = audit.classify_source(
        'try:\n    x = 1\nexcept Exception:\n    y = "#not a comment"\n', "f.py"
    )
    assert rows[0]["commented"] is False


def test_a_private_journaling_helper_counts_as_journaling():
    """`self._log(...)` reports the failure; the audit must not call it silence.

    Measured 2026-08-05: the token match was exact, so the leading underscore
    made `_log` a different word from `log` and five handlers in
    `core/autonomous_runtime.py` that DO journal were counted in the target
    class. An audit that sends a reader to fix what is not broken is worse
    than no audit — the reader learns to distrust it.
    """
    audit = _load_audit()
    rows = audit.classify_source(
        "try:\n    x = 1\nexcept Exception as e:\n"
        '    self._log("failed", {"error": type(e).__name__})\n',
        "f.py",
    )
    assert rows[0]["kind"] == "journaled"


def test_log_prefixed_helpers_count_but_login_does_not():
    """The widening is `log_` WITH the separator, so #292's rule still holds.

    `core/` defines `_log_error` and `_log_summary`; both are journaling. It
    also has to keep refusing `login` and `logic`, which is why the check is a
    prefix with the underscore and not a substring.
    """
    audit = _load_audit()
    for call, expected in (
        ("self._log_error(e)", "journaled"),
        ("self._log_summary(r)", "journaled"),
        ("self._login(u)", "silent_other"),
        ("compute_logic()", "silent_other"),
    ):
        rows = audit.classify_source(
            f"try:\n    x = 1\nexcept Exception as e:\n    {call}\n", "f.py"
        )
        assert rows[0]["kind"] == expected, f"{call} -> {rows[0]['kind']}"
