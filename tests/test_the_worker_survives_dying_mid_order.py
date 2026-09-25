"""Исполнитель площадки умирает посреди заказа и после перезапуска доводит его ровно один раз.

План оператора 25.09: проверка на падение исполнителя — ДО первого настоящего
заказа. Смерть процесса изображает `_Died` (BaseException): исполнитель ловит
только ошибки площадки, так что она пролетает насквозь, как настоящее падение.
Второй проход — новый исполнитель с новым клиентом над той же рабочей папкой.

Правило (AWS Builders' Library, «Making retries safe with idempotent APIs»):
итог каждого побочного шага записан, и повтор берёт записанное, а не делает
шаг заново: агент не гоняется дважды за один раунд, файл не грузится дважды,
сдача — одна.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.market_worker import MarketWorker
from tests.test_agent_market_worker import (  # noqa: F401 — фикстура
    _client,
    _msg,
    _only_allowed,
    market,
)


class _Died(BaseException):
    """Процесс исполнителя умер здесь."""


def _worker(market, tmp_path: Path, asked: list[str], *, die_in_run: bool = False,  # noqa: F811
            reply: str = "# Итог\n\n4") -> MarketWorker:
    def run(text: str) -> str:
        asked.append(text)
        if die_in_run and len(asked) == 1:
            raise _Died
        return reply
    return MarketWorker(_client(market, tmp_path), run, allowed_jobs={"job-test"}, workdir=tmp_path / "market")


def _die_once(obj, name: str, when=lambda *a, **k: True) -> None:
    real, fired = getattr(obj, name), []

    def wrapped(*a, **k):
        if not fired and when(*a, **k):
            fired.append(1)
            raise _Died
        return real(*a, **k)
    setattr(obj, name, wrapped)


def _submits(m) -> int:
    return sum(1 for method, path in m.calls if method == "POST" and path.endswith("/submit"))


def _uploads(m) -> int:
    return sum(1 for f in m.files.values() if f.get("completed"))


def test_dying_while_the_agent_works_resumes_the_started_order(market, tmp_path: Path) -> None:  # noqa: F811
    _only_allowed(market)
    asked: list[str] = []
    with pytest.raises(_Died):
        _worker(market, tmp_path, asked, die_in_run=True).poll_once()
    a = market.assignments["as-1"]["assignment"]
    assert a["startedAt"] and a["status"] == "in_progress"  # начат и брошен
    report = _worker(market, tmp_path, asked).poll_once()[0]
    assert report.kind == "in_flight" and "submit" in report.steps and report.error is None
    assert a["status"] == "submitted" and _submits(market) == 1


def test_dying_during_upload_does_not_run_the_agent_again(market, tmp_path: Path) -> None:  # noqa: F811
    _only_allowed(market)
    asked: list[str] = []
    first = _worker(market, tmp_path, asked)
    _die_once(first.client, "upload_file")
    with pytest.raises(_Died):
        first.poll_once()
    report = _worker(market, tmp_path, asked).poll_once()[0]
    assert len(asked) == 1, "the saved answer is delivered, the model is not paid twice"
    assert any("из сохранённого" in s for s in report.steps)
    assert _submits(market) == 1 and _uploads(market) == 1
    assert next(f for f in market.files.values() if f.get("completed"))["bytes"] == "# Итог\n\n4".encode()


def test_dying_before_submit_neither_reruns_nor_reuploads(market, tmp_path: Path) -> None:  # noqa: F811
    _only_allowed(market)
    asked: list[str] = []
    first = _worker(market, tmp_path, asked)
    _die_once(first.client, "submit")
    with pytest.raises(_Died):
        first.poll_once()
    report = _worker(market, tmp_path, asked).poll_once()[0]
    assert len(asked) == 1 and len(market.files) == 1, "one run, one file"
    assert any("уже был загружен" in s for s in report.steps)
    assert _submits(market) == 1 and market.assignments["as-1"]["assignment"]["status"] == "submitted"


def test_dying_after_submit_leaves_a_submitted_order_alone(market, tmp_path: Path) -> None:  # noqa: F811
    _only_allowed(market)
    asked: list[str] = []
    first = _worker(market, tmp_path, asked)
    _die_once(first.client, "post_message", when=lambda aid, body: body.startswith("Сдал результат"))
    with pytest.raises(_Died):
        first.poll_once()
    report = _worker(market, tmp_path, asked).poll_once()[0]
    assert report.kind == "submitted" and len(asked) == 1 and _submits(market) == 1
    assert "нового нет" in report.steps[-1]


def test_dying_in_a_rework_neither_repeats_the_ack_nor_loses_the_comment(market, tmp_path: Path) -> None:  # noqa: F811
    _only_allowed(market)
    row = market.assignments["as-1"]
    row["assignment"].update(startedAt="t", submittedAt="t", deliverableUrl="https://x/old")
    row["latestMessage"] = _msg("Нужно подробнее", "2026-09-25T10:40:00Z")
    asked: list[str] = []
    with pytest.raises(_Died):
        _worker(market, tmp_path, asked, die_in_run=True).poll_once()
    report = _worker(market, tmp_path, asked).poll_once()[0]
    acks = [m for m in market.posted["as-1"] if m.startswith("Получил замечания")]
    assert len(acks) == 1, "the buyer is told once"
    assert "Нужно подробнее" in asked[-1], "after our ack the comment lives only in the thread"
    assert "submit" in report.steps and _submits(market) == 1


def test_dying_mid_state_write_keeps_the_old_state(market, tmp_path: Path, monkeypatch) -> None:  # noqa: F811
    worker = _worker(market, tmp_path, [])
    worker._remember("as-1", seen_message_at="2026-09-25T10:00:00Z")
    import core.market_worker as mw

    def die(*a, **k):
        raise _Died
    monkeypatch.setattr(mw.os, "replace", die)
    with pytest.raises(_Died):
        worker._remember("as-1", seen_message_at="2026-09-25T11:00:00Z")
    kept = json.loads((tmp_path / "market" / "state.json").read_text(encoding="utf-8"))
    assert kept["as-1"]["seen_message_at"] == "2026-09-25T10:00:00Z"
