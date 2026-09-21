"""Заголовок ответа сверяется с его же фактами; правдивый факт не клеймится.

2026-09-21, 05:52. Агент отдал текст файлов вместо изменённых и написал:

    Conclusion: «Признак `PYTHON_PROBE_WORKSPACE_IMPORT` присутствует в обоих
                 файлах: в коде инструмента и в тесте»
    Facts:      «В коде инструмента `tools/python_probe.py` имя
                 `PYTHON_PROBE_WORKSPACE_IMPORT` в явном виде отсутствует:
                 переключатель не реализован [file:tests/test_python_probe_workspace_import.py]»

Правда стояла в фактах и получила `[claim-refuted]`; ложный заголовок прошёл
чисто. Две поломки:

1. Ворота отсутствия сочли, что улика «содержит то, чьё отсутствие
   утверждали». Но отсутствие утверждалось про `tools/python_probe.py`, а
   цитировался ТЕСТ — он имя, конечно, содержит. Источник, который не
   является названным местом, опровергнуть «там нет» не может.
2. Детектор противоречий сверял утверждения только с разделом Unverified.
   Заголовок, утверждающий наличие того, чьё отсутствие называют факты того
   же ответа, не проверял никто. Новый сигнал — наблюдающий: он виден в
   журнале и в записи эпизода, но в карантин памяти не отправляет
   (обвинение в противоречии закрывает эпизоду вход в опыт, и прежний
   детектор пришлось настраивать на 420 живых ответах, чтобы не резать
   честные).
"""
from __future__ import annotations

from core.answer_contradiction import headline_contradicts_facts
from core.evidence import Evidence, make_evidence
from core.smart_memory import DISQUALIFYING_DEFECT_SIGNALS
from core.verifier_absence import absence_reason

_TEST_FILE = (
    '_SWITCH = "PYTHON_PROBE_WORKSPACE_IMPORT"\n'
    'monkeypatch.setenv("PYTHON_PROBE_WORKSPACE_IMPORT", "1")\n'
)
_TRUE_FACT = (
    "В коде инструмента tools/python_probe.py имя PYTHON_PROBE_WORKSPACE_IMPORT в "
    "явном виде отсутствует: переключатель не реализован, а тест на него ссылается"
)


def _ev(source: str, excerpt: str) -> Evidence:
    return make_evidence(kind="file", source_id=source, obtained_via="file_read",
                         claim="c", excerpt=excerpt)


def test_a_different_file_cannot_refute_an_absence_elsewhere() -> None:
    ev = _ev("file:tests/test_python_probe_workspace_import.py", _TEST_FILE)
    assert absence_reason(_TRUE_FACT, ev, "file") is None


def test_the_named_place_itself_still_refutes() -> None:
    ev = _ev("file:tools/python_probe.py", 'WORKSPACE_IMPORT_ENV = "PYTHON_PROBE_WORKSPACE_IMPORT"\n')
    reason = absence_reason(_TRUE_FACT, ev, "file")
    assert reason is not None and reason.code == "absence_refuted_by_evidence"


_ANSWER = f"""Conclusion:
Признак `PYTHON_PROBE_WORKSPACE_IMPORT` присутствует в обоих файлах: в коде инструмента и в тесте.

Facts:
- {_TRUE_FACT} [file:tests/test_python_probe_workspace_import.py]
- Тест выставляет `PYTHON_PROBE_WORKSPACE_IMPORT` через monkeypatch [file:tests/test_python_probe_workspace_import.py]

Sources:
1. [file:tests/test_python_probe_workspace_import.py]

Confidence: medium

Unverified:
nothing

Safety:
nothing
"""


def test_the_headline_that_denies_its_own_facts_is_named() -> None:
    found = headline_contradicts_facts(_ANSWER)
    assert found, "заголовок утверждает наличие того, чьё отсутствие называют факты"
    assert found[0].subject == "python_probe_workspace_import"


def test_agreeing_headline_and_facts_are_not_accused() -> None:
    agreeing = _ANSWER.replace(
        "присутствует в обоих файлах: в коде инструмента и в тесте",
        "есть только в тесте, в коде инструмента его нет")
    assert headline_contradicts_facts(agreeing) == ()


def test_a_denial_of_a_different_object_is_not_a_contradiction() -> None:
    other = _ANSWER.replace(
        f"- {_TRUE_FACT}", "- В коде инструмента нет функции `_load_switch`: её не писали")
    assert headline_contradicts_facts(other) == ()


def test_the_signal_observes_and_does_not_quarantine() -> None:
    assert "headline_contradicts_facts" not in DISQUALIFYING_DEFECT_SIGNALS


def test_the_operator_sees_it_under_the_answer() -> None:
    """Проводка в цикл: сигнал в запись, событие в журнал, строка под ответом."""
    from core.loop_response_deciders import AgentLoopResponseDeciders
    from core.response_draft import ResponseDraft

    class _Log:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict]] = []

        def log(self, event: str, payload: dict) -> None:
            self.events.append((event, payload))

    host = AgentLoopResponseDeciders.__new__(AgentLoopResponseDeciders)
    host._defect_signals = []
    host.log = _Log()
    draft = ResponseDraft(body=_ANSWER)
    host._headline_check(draft)
    assert host._defect_signals == ["headline_contradicts_facts"]
    assert host.log.events and host.log.events[0][0] == "headline_contradicts_facts"
    rendered = draft.render()
    assert "противоречит его же фактам" in rendered
    assert rendered.index("противоречит") > rendered.index("Conclusion"), "под ответом"
