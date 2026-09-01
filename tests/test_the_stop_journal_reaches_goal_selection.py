"""Прошлая остановка доходит до выбора цели — и это видно машине, не по фразе.

Замер 2026-09-01, из-за которого тест существует: агент запускался сам четыре
раза (03:31, 07:31, 11:31, 15:31) и не сделал ничего. В 11:31 цель прошла
ворота, но исполнение остановил межзапусковый бюджет; в 15:31 он предложил ТУ
ЖЕ цель дословно — потому что факт остановки не доходил ни до памяти, ни до
эпизодов, ни до потока наблюдений. Спрошенный, что произошло, он назвал
причиной чужую ошибку из соседнего прогона.

Требование оператора к приёмке (2026-09-01): «не полагаться на текст
reasoning; итог выбора цели обязан нести машинное поле, и доказывать
дифференциальным тестом — без записи выбирает X, с совпадающей подписью либо
Y, либо X с машинным обоснованием повтора». Здесь проверяется именно
управляющий эффект: журнал остановок ВИДЕН выбору (`stop_considered`), а
подпись в отчёте берётся только из журнала (`previous_stop_ref`), то есть
выдуманная моделью строка туда не проходит.
"""
from __future__ import annotations

import json

from core.charter_goal import CHARTER_RELPATH, propose_charter_goal

_CHARTER = (
    "# Corporate Model — FUTURE / TARGET\n"
    "The organisation exists only when roles, authority and evidence are "
    "explicit, and approval of escalated actions stays with a human.\n"
)

_STOP_SIGNATURE = "abc123def456789000"


class _LLM:
    """Ловит промпт и отвечает заранее заданной целью."""

    def __init__(self, reply: dict):
        self._reply = reply
        self.user = ""

    def complete(self, *, system: str, user: str, **_kw) -> str:
        self.user = user
        return json.dumps(self._reply, ensure_ascii=False)


def _reply(**overrides) -> dict:
    base = {
        "goal": (
            "Проследить, как записываются собственные материальные утверждения, "
            "и предложить падающий тест против самопометки verified"
        ),
        "anchor_id": 0,
        "why_now": "the charter demands explicit evidence for own claims",
        "success_check": "a reviewer sees a failing-test task with a trace",
    }
    base.update(overrides)
    return base


def _workspace(tmp_path, *, stops: tuple[dict, ...] = ()):
    charter = tmp_path / CHARTER_RELPATH
    charter.parent.mkdir(parents=True, exist_ok=True)
    charter.write_text(_CHARTER, encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "campaign_ledger.jsonl").write_text("", encoding="utf-8")
    if stops:
        (data / "self_stops.jsonl").write_text(
            "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in stops),
            encoding="utf-8",
        )
    return tmp_path


def _stop(**overrides) -> dict:
    base = {
        "kind": "budget_stop",
        "reason": "propose_engineering_task:cap",
        "source": "data/campaign_ledger.jsonl",
        "signature": _STOP_SIGNATURE,
        "ts": "2026-09-01T08:31:13+00:00",
    }
    base.update(overrides)
    return base


def test_without_a_stop_journal_the_choice_says_so(tmp_path):
    """Прогон A дифференциального теста: журнала нет — поле честно пустое."""
    llm = _LLM(_reply())

    report = propose_charter_goal(llm, _workspace(tmp_path))

    assert report.status == "proposed"
    assert report.stop_considered is False
    assert report.previous_stop_ref == ""
    assert "recent STOPS" not in llm.user


def test_a_recorded_stop_reaches_the_prompt_and_the_report(tmp_path):
    """Прогон B: запись есть — она видна и в промпте, и машинным полем."""
    llm = _LLM(_reply(previous_stop_ref=_STOP_SIGNATURE))

    report = propose_charter_goal(llm, _workspace(tmp_path, stops=(_stop(),)))

    assert report.status == "proposed"
    assert report.stop_considered is True
    assert report.previous_stop_ref == _STOP_SIGNATURE
    assert "recent STOPS" in llm.user
    assert "propose_engineering_task:cap" in llm.user
    assert _STOP_SIGNATURE[:12] in llm.user


def test_an_invented_signature_does_not_reach_the_report(tmp_path):
    """Поле доказывает эффект, а не фразу: подпись обязана быть из журнала."""
    llm = _LLM(_reply(previous_stop_ref="totally-made-up-signature"))

    report = propose_charter_goal(llm, _workspace(tmp_path, stops=(_stop(),)))

    assert report.status == "proposed"
    assert report.stop_considered is True
    assert report.previous_stop_ref == "", "выдуманная подпись не является учётом"


def test_a_refusal_also_carries_the_machine_field(tmp_path):
    """Отказ — тоже исход выбора: по нему должно быть видно, видел ли он журнал."""
    llm = _LLM(_reply(success_check=""))

    report = propose_charter_goal(llm, _workspace(tmp_path, stops=(_stop(),)))

    assert report.status == "declined"
    assert report.stop_considered is True


def test_an_unreadable_journal_does_not_block_the_choice(tmp_path):
    """Подсказка низкодоверенная: её потеря не имеет права остановить работу."""
    ws = _workspace(tmp_path)
    (ws / "data" / "self_stops.jsonl").write_text("{ не json\n", encoding="utf-8")
    llm = _LLM(_reply())

    report = propose_charter_goal(llm, ws)

    assert report.status == "proposed"
    assert report.stop_considered is False
