"""Средние дефекты: `:hygiene --apply` под тормозом аудита и вытеснение свежей карточки (18.11)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.bootstrap import build_agent
from cli.commands_memory import _handle_hygiene
from core import failure_cards as fc
from core.failure_cards import MAX_CARDS, Card, CardStore
from core.models import MemoryRecord


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


# ── A. `:hygiene … --apply` не удаляет под :audit ──────────────────────────

_TXT = "Сервис отвечает медленнее вечером по трём замерам на стенде номер один."


def _agent_with_garbage(tmp_path: Path):
    agent = build_agent(tmp_path, with_memory=True, durable_writes=frozenset({"hygiene"}),
                        approval_provider=None)
    st = agent.persistent_store
    old = datetime.now(timezone.utc) - timedelta(days=5)
    st.save(MemoryRecord(content=_TXT, type="semantic", tags=["a"], source="user-explicit"))
    st.save(MemoryRecord(content=_TXT, type="semantic", tags=["a"], source="user-explicit"))
    st.save(MemoryRecord(content="Истёкшая запись про погоду в понедельник утром.",
                         type="semantic", ttl_seconds=1, created_at=old))
    return agent


def _rows(agent) -> int:
    with agent.persistent_store.path.open(encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


@pytest.mark.parametrize("cmd", ["expire --apply", "dedupe --apply", "archive --apply",
                                 "all --apply", "--apply", "episodic --yes"])
def test_hygiene_apply_is_refused_under_audit(tmp_path: Path, cmd: str,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    agent = _agent_with_garbage(tmp_path)
    agent.set_audit_read_only(True)
    before = _rows(agent)
    assert _handle_hygiene(cmd, agent, tmp_path) is True
    assert _rows(agent) == before
    assert "audit_read_only" in capsys.readouterr().err


def test_hygiene_facades_do_not_delete_under_audit(tmp_path: Path) -> None:
    agent = _agent_with_garbage(tmp_path)
    agent.set_audit_read_only(True)
    agent.expire_persistent(dry_run=False)
    agent.dedupe_persistent(dry_run=False)
    agent.archive_persistent(dry_run=False, min_age_days=0, threshold=1.0)
    # Счёт по живым дублям: истёкшую запись хранилище само снимает при
    # чтении, это не фасад гигиены.
    text = agent.persistent_store.path.read_text(encoding="utf-8")
    assert text.count("стенде номер один") == 2
    assert '"archived": true' not in text


def test_hygiene_dry_run_report_still_allowed_under_audit(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    agent = _agent_with_garbage(tmp_path)
    agent.set_audit_read_only(True)
    before = _rows(agent)
    _handle_hygiene("expire", agent, tmp_path)
    err = capsys.readouterr().err
    assert "expire (dry_run=True): 1 record(s)" in err
    assert _rows(agent) == before


def test_hygiene_apply_works_without_audit(tmp_path: Path) -> None:
    agent = _agent_with_garbage(tmp_path)
    before = _rows(agent)
    _handle_hygiene("expire --apply", agent, tmp_path)
    assert _rows(agent) < before


# ── B. свежая карточка переживает вытеснение ───────────────────────────────

def _events(n: int) -> list[dict]:
    ts = f"2026-11-17T10:0{n}"
    return [{"event": "tool_call", "ts": ts, "trace_id": f"tr{n}",
             "payload": {"id": "c1", "tool_name": "file_read", "arguments": {"path": "x.py"}}},
            {"event": "tool_result", "ts": ts, "trace_id": f"tr{n}",
             "payload": {"tool_call_id": "c1", "status": "error",
                         "error": "FileNotFoundError: no such file zzz_recurring"}}]


@pytest.mark.parametrize(("status", "seen"), [("unlearned", 2), ("guard", 1)])
def test_recurring_new_error_builds_a_count_in_a_full_store(
        tmp_path: Path, status: str, seen: int) -> None:
    st = CardStore(tmp_path)
    for i in range(MAX_CARDS):
        st.cards[f"old{i}"] = Card(sig=f"old{i}", tool=f"other{i}", error="e", seen=seen,
                                   last_seen="2026-01-01", status=status)
    st.save()
    counts = []
    for n in range(3):
        fc.learn_from_events(tmp_path, _events(n), llm=None, trace_id=f"tr{n}")
        cards = CardStore(tmp_path).cards
        assert len(cards) == MAX_CARDS
        mine = [c for c in cards.values() if c.tool == "file_read"]
        counts.append(mine[0].seen if mine else None)
    assert counts[0] is not None
    assert counts[-1] is not None and counts[-1] > counts[0]
