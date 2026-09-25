"""«Здесь такого нет» от сервера — наблюдение и доказательство отсутствия.

Экзамен 2026-09-25, задача 20 (последняя версия несуществующего пакета PyPI):
API ответил 404 трижды, web_fetch поднимал ошибку, доказательство не ложилось в
улики, агент крутил 14 вызовов, проверка ставила «проверить нельзя» и срезала
верный ответ «пакета нет» — 2 из 5 прогонов провалены. RFC 9110 §15.5.5: 404 —
сервер-источник не нашёл текущего представления ресурса.
"""
from __future__ import annotations

import urllib.error

import pytest

from core.evidence import evidence_from_tool_result
from core.verifier_absence import absence_certified_by_not_found
from tools.web_fetch import WebFetchTool

_URL = "https://pypi.org/pypi/zqxv-nonexistent-2026/json"


class _Opener:
    def __init__(self, code: int) -> None:
        self.code = code

    def open(self, req, timeout=None):
        raise urllib.error.HTTPError(url=_URL, code=self.code, msg="Not Found", hdrs=None, fp=None)


def _fetch(code: int) -> dict:
    return WebFetchTool(opener=_Opener(code)).run(url=_URL)


def test_a_404_comes_back_as_an_observation() -> None:
    out = _fetch(404)
    assert out["not_found"] is True and out["status_code"] == 404
    assert out["text"].startswith(f"HTTP 404 Not Found: {_URL}")
    assert WebFetchTool().validate_output(out)[0]


@pytest.mark.parametrize("code", [401, 403, 429, 500, 503])
def test_other_codes_say_nothing_about_existence_and_stay_errors(code: int) -> None:
    with pytest.raises(ValueError, match=f"HTTP {code}"):
        _fetch(code)


def test_the_404_proves_that_the_named_thing_is_absent() -> None:
    ev = evidence_from_tool_result(tool_name="web_fetch", arguments={"url": _URL}, output=_fetch(404))
    assert ev is not None and ev.obtained_via == "web_fetch"
    assert absence_certified_by_not_found("Пакета zqxv-nonexistent-2026 на PyPI не существует.", [ev])


def test_it_proves_nothing_else() -> None:
    ev = evidence_from_tool_result(tool_name="web_fetch", arguments={"url": _URL}, output=_fetch(404))
    assert not absence_certified_by_not_found("Пакета requests на PyPI не существует.", [ev]), "другой предмет"
    assert not absence_certified_by_not_found("На PyPI нет JSON API.", [ev]), "устройство сайта — не предмет"
    assert not absence_certified_by_not_found("Пакет zqxv-nonexistent-2026 весит 3 МБ.", [ev]), "не отсутствие"
