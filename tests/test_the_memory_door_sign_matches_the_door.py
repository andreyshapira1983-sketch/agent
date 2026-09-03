"""Вывеска на двери памяти говорит то же, что дверь.

Замер 2026-09-03, разговор с агентом через его REPL (слово оператора «начинай с
Д5»): в присутственной сессии `durable_writes=None`, что по контракту
`app/bootstrap.build_agent` значит «все приёмники разрешены». Самоописание
(`core/runtime_self.runtime_self_block`) получало `None or ()` и печатало
«durable_writes: ничего не разрешено». Агент поверил вывеске: планировщик
выпустил шаг memory_bank без обязательного `text`, санитайзер его отбросил, и
в ответе агент честно написал «запись в память запрещена [runtime:
durable_writes]». Дверь была открыта, вывеска — закрыта; разговор не мог
осесть в его памяти — ровно та оторванность, что измерена 2026-08-29.

Три состояния, три разных строки: `None` — все приёмники; пустой набор —
ничего; явный набор — его имена. Безнадзорный путь передаёт явный набор
({episode, hygiene}) и его строка не меняется.
"""
from __future__ import annotations

from core.runtime_self import runtime_self_block


def _block(durable_writes):
    return runtime_self_block(
        trace_id="trace_x", run_id="run_x", session_id=None,
        stores={"persistent_store": object()}, durable_writes=durable_writes,
    )


def test_an_attended_session_shows_every_sink_open():
    text = _block(None)

    assert "ничего не разрешено" not in text
    assert "все приёмники" in text


def test_an_empty_allowlist_still_says_nothing_is_allowed():
    assert "durable_writes: ничего не разрешено" in _block(frozenset())


def test_an_explicit_allowlist_names_its_sinks_unchanged():
    assert "durable_writes: episode, hygiene" in _block(frozenset({"hygiene", "episode"}))
