"""Прочитанное в работе становится источником, а не только конспектом.

Замер 2026-09-23. За ночь агент прочитал десятки первоисточников — IEEE 754,
препринт Шора, работы Белла, ISO 8601, Unicode TR15, PEP 8, главы Тонга,
Эриксона, Могенсена — и написал 85 конспектов. В реестре источников за ту же
ночь не прибавилось НИ ОДНОЙ записи; из 569 источников всего девять несли
http-адрес.

Причина оказалась не в одной дыре, а в целом слое. Приём знания живёт в
`core/ingestion.py` и имеет пять входов, из которых автомату доступен ровно
один (`ingest_files`): `ingest_source`, `ingest_project`, `ingest_web_topic` и
`ingest_rss_feed` вызываются только из команд `cli/`. Человек с клавиатурой
мог пополнить знание, работающий агент — нет.

Отсюда его собственный ответ оператору в то утро: причинно-следственной записи
«прочитал X → стал работать лучше» у него нет. Её и не могло быть: страница
прочитана, конспект написан, источник нигде не записан — назавтра искать
заново.

Здесь закрыт тот край, что касается САМОЙ РАБОТЫ: всё, что ход действительно
прочитал — страница через `web_fetch`, файл через `file_read`, — попадает в
реестр источников по окончании хода. Это не приём темы с поиском в интернете
(`ingest_web_topic` остаётся командой): это запись того, что уже прочитано,
с адресом, отпечатком содержимого и временем чтения.

Тихо и необязательно: сбой записи не валит ход, но и не молчит — он попадает
в журнал. Прочитанное дважды не удваивается: реестр сам отбрасывает повтор по
адресу.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

#: Улики, которые ЯВЛЯЮТСЯ прочитанным источником. Вывод инструмента, результат
#: теста и попадание поисковика — не источники: первое не документ, второе
#: измерение, третье лишь указатель на страницу, которую ещё не открыли.
#: Имена берутся из `SourceType` реестра дословно: выдуманное «web» реестр
#: отвергает, и запись молча не состоялась бы.
#: ТОЛЬКО веб-страница. Файлы рабочей папки уже регистрирует штатный приём
#: (`ingest_files` — единственный вход приёма, доступный автомату), и сначала
#: я писал их второй раз: тест test_knowledge_pipeline поймал `file:doc.txt`
#: дважды. Дыра была не в файлах, а в вебе: за ночь агент прочитал десятки
#: страниц, и ни одна не попала в реестр, потому что `ingest_web_topic`
#: вызывается только из команды cli/. Чиним ровно дыру, не шире.
_SOURCE_KINDS: dict[str, str] = {
    "web_page": "web_page",
}
_MAX_PER_TURN = 12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_read_sources(agent: Any, chain: Any) -> int:
    """Записать прочитанное этим ходом в реестр источников; вернуть их число."""
    store = getattr(agent, "source_registry_store", None)
    if store is None or chain is None:
        return 0
    try:
        from core.source_registry import SourceRegistry
    except Exception:  # noqa: BLE001 — без реестра ход продолжается как прежде
        return 0

    # Уже записанное не пишется второй раз. Сам склад дедуплицирует по id, но
    # в одном ходе источник может прийти и обычным путём приёма, и отсюда —
    # и тогда в реестре оказываются две строки про один файл (поймано тестом
    # test_knowledge_pipeline 23.09: `file:doc.txt` дважды). Мы только что
    # чистили реестры от вранья; засорять их дублями было бы тем же самым.
    known: set[str] = set()
    try:
        known = {str(getattr(rec, "id", "")) for rec in store.load_sources()}
    except Exception as exc:  # noqa: BLE001 — не прочитался склад: пишем как есть,
        # дедуп склада всё равно отсечёт совпадения по id, а молчать нельзя.
        log_early = getattr(agent, "log", None)
        if log_early is not None:
            log_early.log("read_sources_known_unread",
                          {"error": f"{type(exc).__name__}: {exc}"[:200]})

    registry = SourceRegistry()
    seen: set[str] = set()
    for evidence in list(getattr(chain, "evidences", ()) or ())[:60]:
        kind = str(getattr(evidence, "kind", "") or "")
        source_type = _SOURCE_KINDS.get(kind)
        # Поле улики называется `source_id`, а НЕ `locator`: locator — имя в
        # реестре, куда мы пишем. 23.09 я взял имя из места назначения и
        # приписал источнику, не посмотрев на саму улику; getattr отдавал
        # пустую строку, каждая улика пропускалась, функция возвращала ноль —
        # и молчала. Тесты этого не поймали, потому что проверяли двойник, где
        # поле `locator` объявил я сам: тест подтверждал выдумку, а не жизнь.
        locator = str(getattr(evidence, "source_id", "") or "").strip()
        if source_type is None or not locator or locator in seen:
            continue
        if f"{source_type}:{locator}" in known:
            continue
        seen.add(locator)
        registry.register_source(
            type=source_type,
            title=str(getattr(evidence, "title", "") or locator)[:200],
            locator=locator,
            last_read_at=str(getattr(evidence, "fetched_at", "") or _now()),
            metadata={
                # Отпечаток содержимого — то, чем прочитанное отличается от
                # названного: по нему видно, ту ли страницу читали потом.
                "content_hash": str(getattr(evidence, "content_hash", "") or ""),
                "registered_by": "turn",
            },
        )
        if len(seen) >= _MAX_PER_TURN:
            break
    log = getattr(agent, "log", None)
    if not seen:
        # Громко, а не молча. Улики в ходе были, а к записи не подошла ни одна —
        # это либо новый вид улики, либо снова выдуманное имя поля. Тихий ноль
        # здесь три часа выдавал себя за «нечего записывать».
        looked = len(list(getattr(chain, "evidences", ()) or ()))
        if looked and log is not None:
            log.log("read_sources_none_matched", {
                "evidences_seen": looked,
                "kinds": sorted({str(getattr(e, "kind", "")) for e in
                                 list(getattr(chain, "evidences", ()) or ())[:60]})[:8],
                "expected_kinds": sorted(_SOURCE_KINDS),
            })
        return 0
    try:
        written = store.save_registry(registry)
    except Exception as exc:  # noqa: BLE001 — запись знания не валит ход,
        # но и не пропадает молча: иначе это ровно тот тихий отказ, против
        # которого весь этот модуль и написан.
        if log is not None:
            log.log("read_sources_unregistered",
                    {"error": f"{type(exc).__name__}: {exc}"[:200], "named": len(seen)})
        return 0
    if log is not None:
        log.log("read_sources_registered", {"named": len(seen), "written": written})
    return len(seen)
