"""Одна стена, ударенная двадцать раз, не имеет права стереть остальные.

ЗАМЕР ПО ЖИВОМУ ЖУРНАЛУ ВЛАДЕЛЬЦА (`data/self_stops.jsonl`, 2026-09-18):

    строк всего                       35
    разных стен (подписей)             3
    окно `_RECENT_STOPS` = 20 строк -> 2 разные стены

Двадцать строк доносят до модели ДВА факта. Третья стена — `goal_parse`,
подпись `2ea80c747449`, три удара 2026-09-17 — не видна выбору цели вообще,
хотя во всём журнале стен всего три. Треть собственного знания об остановках
потеряна не из-за срока и не из-за объёма памяти, а потому, что одна стена
вытеснила соседей количеством копий.

ПОЧЕМУ КОПИЙ СТОЛЬКО. `agent_tick.py` зовёт `record_self_stop` ВНУТРИ цикла
трёх попыток: один прогон, остановившийся один раз, оставляет три одинаковых
строки с одной подписью. Шесть мёртвых прогонов за 112 секунд дали двадцать
строк — и вымыли из окна всё, что было записано днём раньше.

И это ещё и самоусиление. Блок остановок в запросе говорит модели «совпавшая
стена — проверь её». Модель послушно предлагает «Investigate the unexplained
goal_selection_failure at wall 'goal_repeat'». Эта цель повторяет недавнюю
цель кампании -> отказ -> записывается ЕЩЁ одна строка про `goal_repeat`,
которая в следующий раз зовёт ещё настойчивее. Выход отказа стал входом
следующего отказа.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Окно должно считать РАЗНЫЕ стены, а не строки.
Повторяемость не выбрасывается — она превращается в число рядом со стеной
(«ударено 20 раз, последний раз тогда-то»), потому что двадцать одинаковых
строк читаются как двадцать свидетельств, а это ложный вес: свидетельство
одно.

ЧЕГО ЗДЕСЬ НЕТ. Писателя не трогаем, и это названо вслух: разные попытки
одного прогона могут удариться о РАЗНЫЕ стены, и такую запись терять нельзя.
После сворачивания у читателя повтор перестаёт вытеснять — значит, чинить
надо там, где потеря, а не там, где источник.

СЛЕПОТА, ПОЙМАННАЯ РЕВИЗИЕЙ #350. Два свидетеля в первой редакции были
ЗЕЛЁНЫМИ до всякой правки и потому ничего не доказывали:

  * «число ударов дошло до промпта» искал подстроку "20" — а "20" стоит
    внутри "2026" в отметке времени;
  * «показан последний удар» брал журнал, где последний удар частой стены
    попадал в старый хвост из 20 строк, так что старому читателю ничего не
    стоило это утверждение выполнить.

Оба переписаны: первый ищет метку `hit 20x`, которую соседний текст
произвести не может; второй берёт журнал, где последний удар стены лежит ЗА
хвостом, и заодно проверяет, что удары вне хвоста не выпали из счёта. Против
старого читателя красных свидетелей стало восемь из двенадцати вместо шести.
"""
from __future__ import annotations

import json

from core.charter_goal import (
    _RECENT_STOPS,
    CHARTER_RELPATH,
    _recent_stops,
    propose_charter_goal,
)

_CHARTER = (
    "# Corporate Model — FUTURE / TARGET\n"
    "The organisation exists only when roles, authority and evidence are "
    "explicit, and approval of escalated actions stays with a human.\n"
)

#: Подписи взяты из живого журнала владельца, чтобы свидетель говорил о том
#: же, что случилось на самом деле.
_SIG_REPEAT = "8b7c22aa5ad1" + "0" * 52
_SIG_OTHER = "9fe0d2a1502c" + "0" * 52
_SIG_PARSE = "2ea80c747449" + "0" * 52


class _LLM:
    """Ловит промпт и отвечает заранее заданной целью."""

    def __init__(self, reply: dict) -> None:
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


def _stop(reason: str, signature: str, ts: str) -> dict:
    return {
        "kind": "goal_selection_failure",
        "reason": reason,
        "source": "data/charter_decisions.jsonl",
        "signature": signature,
        "ts": ts,
    }


def _live_shaped_journal() -> list[dict]:
    """Журнал той же формы, что живой: три стены, одна забита копиями."""
    rows: list[dict] = []
    for i in range(3):
        rows.append(_stop("goal_parse", _SIG_PARSE,
                          f"2026-09-17T21:33:{20 + i:02d}+00:00"))
    for i in range(20):
        rows.append(_stop("goal_repeat", _SIG_REPEAT,
                          f"2026-09-18T11:24:{20 + i:02d}+00:00"))
    for i in range(12):
        rows.append(_stop("other", _SIG_OTHER,
                          f"2026-09-18T11:26:{i:02d}+00:00"))
    return rows


def _straddling_journal() -> list[dict]:
    """Стена, чей ПОСЛЕДНИЙ удар лежит за пределами старого хвоста из 20 строк.

    Нужна ровно для того, чего не умел `_live_shaped_journal`: там последний
    удар частой стены попадал в хвост, и «показан последний удар» выполнялось
    само собой, без всякого сворачивания.
    """
    rows = [_stop("goal_repeat", _SIG_REPEAT, f"2026-09-18T11:20:0{i}+00:00")
            for i in range(3)]
    rows.append(_stop("goal_repeat", _SIG_REPEAT, "2026-09-18T11:24:39+00:00"))
    rows += [_stop("other", _SIG_OTHER, f"2026-09-18T11:26:{i:02d}+00:00")
             for i in range(20)]
    return rows


def _workspace(tmp_path, *, stops: list[dict] | tuple[dict, ...] = ()):
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


# ---------------------------------------------------------------------------
# 1. Вытеснение: стена, ударенная чаще, не имеет права стереть остальные
# ---------------------------------------------------------------------------

def test_a_wall_hit_often_does_not_evict_the_walls_hit_rarely(tmp_path) -> None:
    """Свидетель замера: `goal_parse` пропадал из памяти целиком.

    Тридцать пять строк, три стены. Хвост из двадцати строк на живом журнале
    нёс только две. Здесь форма та же, и `goal_parse` — самая старая и самая
    редкая — обязана дойти.
    """
    ws = _workspace(tmp_path, stops=_live_shaped_journal())

    stops = _recent_stops(ws)
    walls = {st["reason"] for st in stops}

    assert walls == {"goal_parse", "goal_repeat", "other"}, (
        f"журнал знает три стены, до выбора дошли {sorted(walls)}: "
        "частая стена вытеснила редкую"
    )


def test_the_rare_wall_reaches_the_prompt_itself(tmp_path) -> None:
    """Дойти до функции мало: решение принимает модель, читая промпт."""
    llm = _LLM(_reply())
    ws = _workspace(tmp_path, stops=_live_shaped_journal())

    propose_charter_goal(llm, ws)

    assert "goal_parse" in llm.user, (
        "выбор цели не увидел третью стену: она осталась только в файле"
    )


# ---------------------------------------------------------------------------
# 2. Ложный вес: одно свидетельство не должно выглядеть как двадцать
# ---------------------------------------------------------------------------

def test_one_wall_takes_one_line_not_twenty(tmp_path) -> None:
    """Двадцать копий одной подписи — одно свидетельство, а не двадцать."""
    ws = _workspace(tmp_path, stops=_live_shaped_journal())

    stops = _recent_stops(ws)
    signatures = [st["signature"] for st in stops]

    assert len(signatures) == len(set(signatures)), (
        f"подписи повторяются: {len(signatures)} строк на "
        f"{len(set(signatures))} стен — модель читает копии как улики"
    )


def test_the_repetition_survives_as_a_number(tmp_path) -> None:
    """Повторяемость не выбрасывается: она обязана остаться числом.

    Стена, ударенная двадцать раз, и стена, ударенная однажды, — разные
    новости. Свернуть их в одну строку без счёта значило бы обменять одну
    потерю на другую.
    """
    ws = _workspace(tmp_path, stops=_live_shaped_journal())

    stops = _recent_stops(ws)
    by_wall = {st["reason"]: st for st in stops}

    assert by_wall["goal_repeat"]["hits"] == 20
    assert by_wall["other"]["hits"] == 12
    assert by_wall["goal_parse"]["hits"] == 3


def test_the_prompt_says_out_loud_how_often_the_wall_was_hit(tmp_path) -> None:
    """Число обязано дойти до модели, иначе счёт остался внутри кода.

    Признанная слепота этого свидетеля в первой редакции: он искал в строке
    подстроку "20" и был ЗЕЛЁНЫМ до всякой правки — потому что "20" стоит
    внутри "2026" в отметке времени. Проверка обязана искать метку, которую
    ничто соседнее произвести не может, иначе она проверяет календарь.
    """
    llm = _LLM(_reply())
    ws = _workspace(tmp_path, stops=_live_shaped_journal())

    propose_charter_goal(llm, ws)

    line = next(
        (ln for ln in llm.user.splitlines()
         if "goal_repeat" in ln and ln.lstrip().startswith("-")),
        "",
    )
    assert "hit 20x" in line, (
        f"строка про частую стену не назвала число ударов: {line!r}"
    )
    rare = next(
        (ln for ln in llm.user.splitlines()
         if "goal_parse" in ln and ln.lstrip().startswith("-")),
        "",
    )
    assert "hit 3x" in rare, (
        f"редкая стена не назвала своё число ударов: {rare!r}"
    )


def test_the_newest_hit_is_the_one_shown(tmp_path) -> None:
    """Свёрнутая стена показывает ПОСЛЕДНИЙ удар, а не первый.

    Иначе после сворачивания стена, по которой бьются прямо сейчас, выглядела
    бы вчерашней — и подсказка молча состарилась бы.

    ПРИЗНАННАЯ СЛЕПОТА ПЕРВОЙ РЕДАКЦИИ (ревизия #350, замечание верное).
    Свидетель брал `_live_shaped_journal`, где последний удар частой стены
    попадал в старый хвост из 20 строк. Старый читатель возвращал строки как
    есть, словарь `by_wall` оставлял последнюю — и утверждение было ЗЕЛЁНЫМ
    до всякой правки. Это ровно та же болезнь, что уже ловили на реестре:
    контроль обязан нести данные, на которых охрана вообще может сработать.

    Здесь журнал устроен так, что старому читателю эту стену не достать: её
    последний удар стоит ЗА хвостом. Заодно проверяется, что удары, лежащие
    вне хвоста, не потеряны из счёта.
    """
    ws = _workspace(tmp_path, stops=_straddling_journal())

    stops = _recent_stops(ws)
    by_wall = {st["reason"]: st for st in stops}

    assert "goal_repeat" in by_wall, (
        "стена, чей последний удар лежит за хвостом, пропала целиком"
    )
    assert by_wall["goal_repeat"]["ts"] == "2026-09-18T11:24:39+00:00"
    assert by_wall["goal_repeat"]["hits"] == 4, (
        "удары за пределами хвоста выпали из счёта"
    )


def test_the_walls_are_ordered_by_their_latest_hit(tmp_path) -> None:
    """Порядок — по последнему удару: свежая стена стоит последней."""
    ws = _workspace(tmp_path, stops=_live_shaped_journal())

    assert [st["reason"] for st in _recent_stops(ws)] == [
        "goal_parse", "goal_repeat", "other",
    ]


# ---------------------------------------------------------------------------
# 3. Контроли: то, что ломать нельзя
# ---------------------------------------------------------------------------

def test_a_single_stop_still_looks_exactly_as_before(tmp_path) -> None:
    """Контроль: журнал без повторов ведёт себя ровно как до правки."""
    row = _stop("goal_parse", _SIG_PARSE, "2026-09-17T21:33:24+00:00")
    ws = _workspace(tmp_path, stops=[row])

    stops = _recent_stops(ws)

    assert len(stops) == 1
    assert stops[0]["kind"] == "goal_selection_failure"
    assert stops[0]["reason"] == "goal_parse"
    assert stops[0]["signature"] == _SIG_PARSE
    assert stops[0]["ts"] == "2026-09-17T21:33:24+00:00"
    assert stops[0]["hits"] == 1


def test_the_window_still_has_a_ceiling(tmp_path) -> None:
    """Контроль: сворачивание не превращает окно в безразмерное.

    Память об остановках остаётся ограниченной — меняется единица счёта, а
    не наличие потолка.
    """
    rows = [
        _stop(f"wall_{i}", f"{i:064d}", f"2026-09-1{i % 9}T0{i % 9}:00:00+00:00")
        for i in range(_RECENT_STOPS + 7)
    ]
    ws = _workspace(tmp_path, stops=rows)

    assert len(_recent_stops(ws)) == _RECENT_STOPS


def test_the_newest_walls_are_the_ones_kept(tmp_path) -> None:
    """Контроль: при переполнении выбывает САМАЯ СТАРАЯ стена, не новая."""
    rows = [
        _stop(f"wall_{i}", f"{i:064d}", f"2026-09-18T{i:02d}:00:00+00:00")
        for i in range(_RECENT_STOPS + 3)
    ]
    ws = _workspace(tmp_path, stops=rows)

    kept = {st["reason"] for st in _recent_stops(ws)}

    assert "wall_0" not in kept and "wall_2" not in kept
    assert f"wall_{_RECENT_STOPS + 2}" in kept


def test_a_row_without_a_signature_is_still_carried(tmp_path) -> None:
    """Контроль: строка без подписи не имеет права исчезнуть при сворачивании.

    Старые записи могут не нести подпись. Свернуть их все в одну значило бы
    выдумать, что это одна и та же стена.
    """
    rows = [
        {"kind": "other", "reason": "alpha", "ts": "2026-09-18T01:00:00+00:00"},
        {"kind": "other", "reason": "beta", "ts": "2026-09-18T02:00:00+00:00"},
    ]
    ws = _workspace(tmp_path, stops=rows)

    assert {st["reason"] for st in _recent_stops(ws)} == {"alpha", "beta"}


def test_an_unreadable_journal_still_does_not_block_the_choice(tmp_path) -> None:
    """Контроль: подсказка низкодоверенная, её потеря не останавливает работу."""
    ws = _workspace(tmp_path)
    (ws / "data" / "self_stops.jsonl").write_text("{ не json\n", encoding="utf-8")

    report = propose_charter_goal(_LLM(_reply()), ws)

    assert report.status == "proposed"
    assert report.stop_considered is False
