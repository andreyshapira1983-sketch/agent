"""MIR-071 — a discarded builder reply is preserved, redacted, and named.

Measured live (2026-08-03, operator's tick): the builder spent 84 cost units
on a 15948-token reply (≈ the 16000-token builder cap), the truncated JSON
fragment-parsed into a dict WITHOUT `content`, the critic vetoed it as
«empty generated content», and the reply survived NOWHERE — the run journal
keeps only token counters. Retention-first was the operator-approved fix
order: preserve the evidence, then the next occurrence is diagnosable.

Pins: the raw lands (redacted) under logs/self_build_rejects/, the veto
names the real failure and the file, the report/journal payloads carry the
path but never the blob, and a genuinely empty reply stays «empty generated
content» with nothing to preserve.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.self_build_producer import produce_self_apply_proposal


class _Inbox:
    def __init__(self):
        self.items = []

    def list(self):
        return []

    def add(self, **kw):
        self.items.append(kw)
        return type("Item", (), {"id": "ain_test"})()


def _grounded(target: str):
    def select():
        return type(
            "Cand", (), {
                "target_path": target,
                "problem_quote": "file has drifted from its docstring",
                "evidence_ref": "TECH_DEBT.md#test",
            },
        )()
    return select


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _produce(tmp_path: Path, reply: str, llm: object | None = None):
    target = "core/release_hygiene.py"
    src = (_REPO_ROOT / target).read_text(encoding="utf-8")
    ws = tmp_path
    (ws / target).parent.mkdir(parents=True, exist_ok=True)
    (ws / target).write_text(src, encoding="utf-8")

    class _LLM:
        """Predates `allow_continuation` on purpose — older wrappers exist."""

        provider = "mock"
        model = "mock-1"

        def complete(self, system, user, max_tokens=2000, temperature=0.0):
            return reply

    return produce_self_apply_proposal(
        workspace=ws,
        inbox=_Inbox(),
        llm=llm if llm is not None else _LLM(),
        candidate_targets=(target,),
        grounded_selector=_grounded(target),
        max_builder_attempts=1,
    )


def test_a_fragment_parsed_reply_is_preserved_and_named(tmp_path):
    """The live shape: truncated JSON whose inner fragment wins extraction —
    content is missing, but the reply is anything but empty. The secret-shaped
    string inside must arrive REDACTED on disk."""
    fake_key = "AKIA" + "IOSFODNN" + "7EXAMPLE"   # assembled: no key-shaped literal
    reply = (
        'I will produce the split now. {"path": "core/x.py", "note": 1} '
        f"and the credentials I saw were {fake_key} — "
        '{"files": [{"path": "core/x.py", "content": "truncated'
    )
    report = _produce(tmp_path, reply)

    assert report.status == "critic_veto"
    joined = "; ".join(report.veto_reasons)
    assert "did not parse into usable content" in joined
    assert "empty generated content" not in joined
    assert "raw builder reply preserved:" in joined

    rejects = list((tmp_path / "logs" / "self_build_rejects").glob("reject_*.txt"))
    assert len(rejects) == 1
    saved = rejects[0].read_text(encoding="utf-8")
    assert "I will produce the split now." in saved
    assert fake_key not in saved, "the preserved raw must be redacted"

    # The FULL report payload (what reaches journals/episodes) carries the
    # pointer but never the blob.
    payload = json.dumps(report.to_dict(), ensure_ascii=False)
    assert "I will produce the split now." not in payload
    assert "raw builder reply preserved:" in payload
    assert "raw_chars" in payload


def test_an_unparseable_reply_is_preserved_too(tmp_path):
    report = _produce(tmp_path, "no json here at all, just prose " * 20)
    assert report.status == "critic_veto"
    assert any("raw builder reply preserved:" in v for v in report.veto_reasons)
    rejects = list((tmp_path / "logs" / "self_build_rejects").glob("reject_*.txt"))
    assert len(rejects) == 1


def test_a_genuinely_empty_reply_stays_empty_with_nothing_preserved(tmp_path):
    report = _produce(tmp_path, "")
    assert report.status == "critic_veto"
    joined = "; ".join(report.veto_reasons)
    assert "empty generated content" in joined
    assert "raw builder reply preserved:" not in joined
    assert not (tmp_path / "logs" / "self_build_rejects").exists()


# ── A truncated reply is a different failure from a nonsense reply ──────────
# Measured 2026-08-04: three live builder replies were stitched from two legs
# and came back with the escaping style flipping at the boundary — escaped
# `\n` inside the JSON string before it, real newlines after. Unparseable, each
# after paying for extra legs, and the veto that followed said only "did not
# parse", so the target took the blame (MIR-083).


class _TruncatingLLM:
    """Stops on the token cap and reports it, as `core.llm.LLM` now does."""

    provider = "mock"
    model = "mock-1"

    def __init__(self):
        self.last_answer_was_truncated = True
        self.continuation_allowed = None

    def complete(self, system, user, max_tokens=2000, temperature=0.0,
                 allow_continuation=True):
        self.continuation_allowed = allow_continuation
        # Leg one, cut off inside the JSON string — exactly what the live
        # replies looked like before anything was stitched onto them.
        return '{"files": [{"path": "core/x.py", "content": "def f():\\n'


def test_the_builder_declines_stitching(tmp_path):
    """The answer must parse as one object; a splice cannot be trusted."""
    llm = _TruncatingLLM()

    _produce(tmp_path, "", llm=llm)

    assert llm.continuation_allowed is False


def test_a_truncated_reply_is_named_as_too_big_not_as_nonsense(tmp_path):
    report = _produce(tmp_path, "", llm=_TruncatingLLM())

    joined = "; ".join(report.veto_reasons)
    assert report.status == "critic_veto"
    assert "too large for a single pass" in joined, joined
    assert "invalid JSON" not in joined, (
        "a reply that ran out of room was reported as a malformed one"
    )
