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


def test_the_old_locks_still_stand_after_the_default_scheme() -> None:
    assert _fetch("localhost/admin")[0] is None, "локальная сеть"
    assert _fetch("example.com/page")[0] is None, "узел-заглушка"
