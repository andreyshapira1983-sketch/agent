"""Прочитанное в работе попадает в реестр источников, а не только в конспект.

Замер 2026-09-23: за ночь агент прочитал десятки первоисточников — IEEE 754,
препринт Шора, работы Белла, Unicode TR15, PEP 8, главы Тонга и Эриксона — и
написал 85 конспектов. В реестре источников за ту же ночь НЕ ПРИБАВИЛОСЬ НИ
ОДНОЙ записи; из 569 источников девять несли http-адрес.

Причина оказалась слоем, а не дырой: приём знания имеет пять входов, и
автомату доступен ровно один. `ingest_source`, `ingest_project`,
`ingest_web_topic`, `ingest_rss_feed` зовутся только из `cli/`. Человек с
клавиатурой знание пополнить мог, работающий агент — нет. Отсюда его же ответ
оператору: записи «прочитал X → стал работать лучше» у него нет.
"""
from __future__ import annotations

from typing import Any

# НАСТОЯЩИЕ Evidence и ProvenanceChain, а не двойники. 23.09 эти тесты были
# зелёными на выдуманном поле `locator`, которого у улики нет (там `source_id`),
# и три часа подтверждали мою ошибку вместо жизни. Двойник проверяет того, кто
# его написал; настоящий объект проверяет код.
from core.evidence import Evidence, ProvenanceChain
from core.read_sources_registry import register_read_sources


def _Evidence(kind: str, locator: str, title: str = "", content_hash: str = "") -> Evidence:
    del title
    return Evidence(id="ev_" + str(abs(hash(locator)))[:8], kind=kind,
                    source_id=locator, obtained_via="test",
                    content_hash=content_hash or "h" * 8,
                    fetched_at="2026-09-23T04:00:00+00:00", confidence=0.7,
                    claim="", excerpt="")


def _Chain(evidences: list[Evidence]) -> ProvenanceChain:
    return ProvenanceChain(evidences=list(evidences))


class _Store:
    def __init__(self, broken: bool = False) -> None:
        self.saved: list[Any] = []
        self._broken = broken

    def save_registry(self, registry: Any) -> int:
        if self._broken:
            raise OSError("реестр недоступен")
        self.saved.append(registry)
        # `sources` — свойство-кортеж, а не метод: двойник, зовущий его как
        # функцию, падал внутри регистрации, и та честно возвращала ноль.
        return len(registry.sources)


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))


class _Agent:
    def __init__(self, store: Any) -> None:
        self.source_registry_store = store
        self.log = _Log()


def test_a_fetched_page_becomes_a_source() -> None:
    """Ровно тот случай: страница прочитана, а источника не было."""
    agent = _Agent(_Store())
    chain = _Chain([
        _Evidence("web_page", "https://www.unicode.org/reports/tr15/", "UAX #15", "abc123"),
        _Evidence("web_page", "https://peps.python.org/pep-0008/", "PEP 8"),
    ])

    named = register_read_sources(agent, chain)

    assert named == 2, "прочитанное не стало источником"
    assert any(e == "read_sources_registered" for e, _ in agent.log.events)


def test_a_tool_output_is_not_a_source() -> None:
    """Граница: вывод инструмента и попадание поисковика — не документы."""
    agent = _Agent(_Store())
    chain = _Chain([
        _Evidence("tool_output", "python_probe"),
        _Evidence("web_search_hit", "https://example.org/hit"),
        _Evidence("test_result", "pytest"),
    ])

    assert register_read_sources(agent, chain) == 0
    # Тишины здесь БОЛЬШЕ НЕТ. Улики в ходе были, а к записи не подошла ни
    # одна — журнал обязан это сказать: три часа 23.09 тихий ноль выдавал
    # себя за «нечего записывать», пока я не полез в живой след.
    events = dict(agent.log.events)
    assert "read_sources_none_matched" in events, agent.log.events
    assert events["read_sources_none_matched"]["evidences_seen"] == 3
    assert "tool_output" in events["read_sources_none_matched"]["kinds"]


def test_a_workspace_file_is_left_to_the_ordinary_ingest() -> None:
    """Файлы рабочей папки пишет штатный приём — дублировать его незачем."""
    agent = _Agent(_Store())

    assert register_read_sources(agent, _Chain([_Evidence("file", "core/loop.py")])) == 0


def test_the_same_page_read_twice_is_named_once() -> None:
    agent = _Agent(_Store())
    url = "https://peps.python.org/pep-0008/"
    chain = _Chain([_Evidence("web_page", url), _Evidence("web_page", url)])

    assert register_read_sources(agent, chain) == 1


def test_a_broken_registry_does_not_break_the_turn_and_does_not_stay_silent() -> None:
    """Знание не валит ход — но и не пропадает молча: это был бы тихий отказ."""
    agent = _Agent(_Store(broken=True))
    chain = _Chain([_Evidence("web_page", "https://example.org/a")])

    assert register_read_sources(agent, chain) == 0
    assert any(e == "read_sources_unregistered" for e, _ in agent.log.events)


def test_no_store_no_crash() -> None:
    class _Bare:
        log = _Log()

    assert register_read_sources(_Bare(), _Chain([_Evidence("web_page", "https://x.org/")])) == 0
    assert register_read_sources(_Agent(_Store()), None) == 0


def test_the_run_tail_calls_it() -> None:
    """Связь с механизмом: хвост хода зовёт регистрацию, иначе это снова шов."""
    import inspect

    from core import loop_run_tail

    assert "register_read_sources" in inspect.getsource(loop_run_tail)
