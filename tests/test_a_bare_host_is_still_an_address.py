"""Адрес без схемы — это адрес, а жалоба обязана назвать отвергнутое.

Замер 2026-09-20 по трассам за четверо суток: из 47 снятых шагов 11 сняты с
причиной «web_fetch url must start with http:// or https://». Что именно
присылалось, установить нельзя: жалоба не сохраняла значение — и это второй
дефект в той же строке. Мера, которая не записывает отвергнутое, не даёт
себя перемерить.

Голое имя узла — адрес с умолчательной схемой: так его понимает и браузер,
и человек, диктующий «rfc-editor.org». Достраивается только то, что похоже
на узел; всё остальное по-прежнему снимается, и все прежние замки —
локальная сеть, узлы-заглушки, длина, ASCII — стоят после достройки, а не
до неё.
"""
from __future__ import annotations

from core.step_sanitizer import sanitize_step


def _fetch(url):
    warnings: list[str] = []
    spec = sanitize_step("web_fetch", {"url": url}, None, 0, warnings)
    return spec, warnings


def test_a_bare_host_gets_the_default_scheme() -> None:
    spec, warnings = _fetch("rfc-editor.org/rfc/rfc9111.html")
    assert spec is not None, "адрес без схемы — всё ещё адрес"
    assert spec["arguments"]["url"] == "https://rfc-editor.org/rfc/rfc9111.html"
    assert not warnings


def test_an_explicit_scheme_is_untouched() -> None:
    spec, _warnings = _fetch("http://example.org.ru/page")
    assert spec is not None and spec["arguments"]["url"] == "http://example.org.ru/page"


def test_a_phrase_is_not_an_address_and_the_complaint_names_it() -> None:
    spec, warnings = _fetch("see section 4.2 of the spec")
    assert spec is None
    assert warnings and "section 4.2" in warnings[0], \
        "жалоба обязана назвать отвергнутое"


def test_another_scheme_is_not_smuggled_in() -> None:
    for url in ("file:///etc/passwd", "javascript:alert(1)", "ftp://host/x"):
        spec, _warnings = _fetch(url)
        assert spec is None, url


def test_the_old_locks_still_stand_after_the_default_scheme() -> None:
    assert _fetch("localhost/admin")[0] is None, "локальная сеть"
    assert _fetch("example.com/page")[0] is None, "узел-заглушка"
