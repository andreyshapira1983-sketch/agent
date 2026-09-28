"""Заменённый вывод помечается, а не прячется: Jev называет, какой прежний вывод сменил новый.

Экзамен памяти 28.09: пометка с id заменившей записи убрала все устаревшие ответы,
голое «устарело» стоило модели лишних шагов. Jev колеблется (p 0.44–0.47) на записях,
которые сами — свежие поправки, поэтому запись не прячется и не уходит в архив:
модель видит историю и знает, какая запись действующая. См. `core/jev_judge.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.knowledge_use_policy import KnowledgeUsePolicy
from core.role_router import RoleContext
from tests.conftest import FakeLLM
from tests.test_a_known_fact_is_not_stored_twice import _QUESTION, FIRST, _episode, _loop, _say

CORRECTED = "Теорема Нётер у Тонга сформулирована в разделе 3.1, а не 2.4.1."
_FAKE_KEY = "fake-typesafe-key-for-tests"


class FakeJev:
    """Транспорт вместо сети: одна и та же вероятность на каждый вопрос, либо сбой."""

    def __init__(self, p: float = 0.9, error: Exception | None = None) -> None:
        self.p, self.error, self.calls = p, error, []

    def __call__(self, body: dict, key: str) -> tuple[int, dict]:
        self.calls.append((body, key))
        if self.error is not None:
            raise self.error
        return 200, {"model": "jev-test", "answers": {
            q: {"type": "noul", "noul": self.p} for q in body["questions"]}}


@pytest.fixture
def jev(monkeypatch: pytest.MonkeyPatch):
    def install(p: float = 0.9, *, error: Exception | None = None,
                flag: str | None = "1", key: str | None = _FAKE_KEY) -> FakeJev:
        fake = FakeJev(p, error)
        monkeypatch.setattr("core.jev_judge.http_transport", fake)
        for name, value in (("AGENT_JEV_MEMORY", flag), ("TYPESAFE_API_KEY", key)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        return fake
    return install


def _two_conclusions(workspace: Path) -> tuple[object, str, str]:
    """Прежний вывод, затем поправка к нему; модель сверки (Mem0) говорит ADD — обе записи живы."""
    fake = FakeLLM()
    loop = _loop(workspace, fake)
    loop._remember_conclusion(_episode(FIRST))
    first_id = loop.persistent_store.load()[0].id
    _say(fake, operation="ADD")
    loop._remember_conclusion(_episode(CORRECTED))
    new_id = _events(loop, "conclusion_memory_write")[-1]["record_id"]
    assert new_id and new_id != first_id, "поправка не записалась"
    return loop, first_id, new_id


def _allowed(record) -> bool:
    report = KnowledgeUsePolicy().filter(
        [record],
        role_context=RoleContext(
            role="assistant", tone="neutral", output_style="prose", knowledge_scopes=("*",),
            allowed_memory_types=("semantic",), allowed_memory_tags=("*",),
        ),
        question=_QUESTION,
    )
    return report.allowed == [record]


def _events(loop, name: str) -> list[dict]:
    rows = [json.loads(line) for line in loop.log.path.read_text(encoding="utf-8").splitlines()]
    return [row["payload"] for row in rows if row["event"] == name]


def test_a_replaced_conclusion_is_marked_with_the_new_id_and_stays_visible(workspace: Path, jev) -> None:
    fake_jev = jev(p=0.93)

    loop, first_id, new_id = _two_conclusions(workspace)

    records = {r.id: r for r in loop.persistent_store.load()}
    assert first_id not in {r.id for r in loop.persistent_store.load_archive()}, "пометка ≠ архив"
    assert first_id in records, "прежний вывод пропал из активной памяти"
    assert records[first_id].superseded_by == new_id, "прежний вывод не помечен id нового"
    assert records[new_id].superseded_by is None
    assert _allowed(records[first_id]), "помеченная запись выпала из выборки"
    line = "\n".join(loop.memory_record_lines([records[first_id]]))
    assert f"[superseded by {new_id}; the newer entry is current]" in line
    body, key = fake_jev.calls[0]
    assert key == _FAKE_KEY and body["model"] == "jev-latest"
    assert [q["type"] for q in body["questions"].values()] == ["noul"]
    assert "3.1" in json.dumps(body["state"], ensure_ascii=False), "Jev не показали новый вывод"


def test_a_wavering_no_leaves_the_old_conclusion_unmarked(workspace: Path, jev) -> None:
    """p 0.47 — так Jev колебался на действующих поправках; ниже порога 0.5 пометки нет."""
    fake_jev = jev(p=0.47)

    loop, _first, _new = _two_conclusions(workspace)

    assert fake_jev.calls, "Jev не спросили"
    assert all(r.superseded_by is None for r in loop.persistent_store.load())


@pytest.mark.parametrize("flag, key", [(None, _FAKE_KEY), ("0", _FAKE_KEY), ("1", None)])
def test_without_the_flag_or_the_key_jev_is_never_called(workspace: Path, jev, flag, key) -> None:
    fake_jev = jev(p=0.93, flag=flag, key=key)

    loop, _first, _new = _two_conclusions(workspace)

    assert fake_jev.calls == [], "Jev позвали без флага или без ключа"
    assert all(r.superseded_by is None for r in loop.persistent_store.load())
    assert _events(loop, "jev_memory_marks") == []


def test_a_failing_jev_marks_nothing_and_says_so(workspace: Path, jev) -> None:
    fake_jev = jev(error=ConnectionError("api.typesafe.ai unreachable"))

    loop, _first, new_id = _two_conclusions(workspace)

    assert fake_jev.calls, "Jev не спросили"
    assert all(r.superseded_by is None for r in loop.persistent_store.load())
    writes = _events(loop, "conclusion_memory_write")
    assert writes[-1]["record_id"] == new_id, "сбой Jev сорвал запись вывода"
    assert "jev_memory" in {p["sensor"] for p in _events(loop, "sensor_failed")}, "сбой не в журнале"


def test_texts_go_to_jev_redacted() -> None:
    from core import jev_judge
    from core.models import MemoryRecord

    sent: list[dict] = []
    old = MemoryRecord(content="Вывод: писать ivan.petrov@example.com", type="semantic")

    def transport(body: dict, _key: str) -> tuple[int, dict]:
        sent.append(body)
        return 200, {"answers": {"q0": {"type": "noul", "noul": 0.8}}}

    got = jev_judge.replacement_probabilities(
        _FAKE_KEY, "Вывод: писать не ivan.petrov@example.com, а в чат", [old], transport=transport)

    assert got == {old.id: 0.8}
    assert "ivan.petrov@example.com" not in json.dumps(sent, ensure_ascii=False)


def test_rate_limits_are_retried_and_other_errors_are_not() -> None:
    from core import jev_judge
    from core.models import MemoryRecord

    old = MemoryRecord(content="Вывод: раздел 2.4.1", type="semantic")
    replies = [(429, {}), (529, {}), (200, {"answers": {"q0": {"type": "noul", "noul": 0.6}}})]
    slept: list[float] = []

    got = jev_judge.replacement_probabilities(
        _FAKE_KEY, "Вывод: раздел 3.1", [old], transport=lambda _b, _k: replies.pop(0), sleep=slept.append)

    assert got == {old.id: 0.6} and len(slept) == 2
    with pytest.raises(jev_judge.JevError):
        jev_judge.replacement_probabilities(
            _FAKE_KEY, "Вывод: раздел 3.1", [old], transport=lambda _b, _k: (401, {}), sleep=slept.append)
    assert len(slept) == 2, "401 повторять бессмысленно"
