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


def _produce(tmp_path: Path, reply: str):
    target = "core/release_hygiene.py"
    src = Path(target).read_text(encoding="utf-8")
    ws = tmp_path
    (ws / target).parent.mkdir(parents=True, exist_ok=True)
    (ws / target).write_text(src, encoding="utf-8")

    class _LLM:
        provider = "mock"
        model = "mock-1"

        def complete(self, system, user, max_tokens=2000, temperature=0.0):
            return reply

    return produce_self_apply_proposal(
        workspace=ws,
        inbox=_Inbox(),
        llm=_LLM(),
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

    # The blob never rides in the report/journal payload — only the pointer.
    payload = json.dumps(report.to_log_payload() if hasattr(report, "to_log_payload") else {
        "roles": [r.to_dict() for r in report.role_outputs]
    }, ensure_ascii=False)
    assert "I will produce the split now." not in payload
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
