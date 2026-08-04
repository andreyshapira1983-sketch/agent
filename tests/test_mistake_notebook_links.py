"""Адреса в блокноте ошибок должны вести в существующие места.

`docs/MISTAKE_NOTEBOOK.md` — общий канал между ассистентом и автономным
агентом: один пишет находку с адресом `файл:строка`, другой идёт и смотрит.
Битая ссылка обесценивает запись — искать снова придётся руками, а ради
избавления от этого поиска журнал и заведён.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_NOTEBOOK = _REPO / "docs" / "MISTAKE_NOTEBOOK.md"

#: Ссылка вида `[core/foo.py:123]` в тексте журнала.
_LINK_RE = re.compile(r"\[([\w/.\-]+\.py):(\d+)\]")


def _links() -> list[tuple[str, int]]:
    text = _NOTEBOOK.read_text(encoding="utf-8")
    return [(m.group(1), int(m.group(2))) for m in _LINK_RE.finditer(text)]


def test_the_notebook_exists_and_carries_addresses():
    assert _NOTEBOOK.is_file(), "блокнот ошибок пропал"
    assert _links(), "в журнале нет ни одного адреса — искать снова придётся руками"


def _inside_repo(rel: str) -> bool:
    """Ведёт ли адрес внутрь репозитория.

    Регулярка адресов допускает точки и слэши, поэтому `../../secrets.py`
    проходит её без возражений. Ссылка наружу в блокноте бессмысленна, а
    журнал пополняет в том числе агент — пусть сторож это ловит.
    """
    try:
        (_REPO / rel).resolve().relative_to(_REPO.resolve())
    except (ValueError, OSError):
        return False
    return True


def test_no_address_escapes_the_repository():
    outside = sorted({rel for rel, _ in _links() if not _inside_repo(rel)})

    assert not outside, f"адреса ведут за пределы репозитория: {outside}"


def test_the_escape_check_actually_catches_a_way_out():
    """Доказательство сторожа: путь наружу обязан быть отвергнут."""
    assert not _inside_repo("../../../etc/passwd.py")
    assert not _inside_repo("a/../../b.py")
    assert _inside_repo("core/loop.py")


def test_every_address_points_at_a_real_line():
    broken: list[str] = []
    cache: dict[str, list[str] | None] = {}   # один файл читаем один раз
    for rel, lineno in _links():
        if not _inside_repo(rel):
            broken.append(f"{rel}:{lineno} — путь ведёт наружу")
            continue
        if rel not in cache:
            path = _REPO / rel
            cache[rel] = (
                path.read_text(encoding="utf-8", errors="replace").splitlines()
                if path.is_file() else None
            )
        lines = cache[rel]
        if lines is None:
            broken.append(f"{rel}:{lineno} — файла нет")
        elif not 1 <= lineno <= len(lines):
            broken.append(f"{rel}:{lineno} — строки нет (всего {len(lines)})")
        elif not lines[lineno - 1].strip():
            broken.append(f"{rel}:{lineno} — строка пустая, адрес уехал")

    assert not broken, "адреса в блокноте протухли:\n  " + "\n  ".join(broken)


def test_each_finding_row_names_a_place():
    """Строка журнала без адреса — это жалоба, а не находка."""
    text = _NOTEBOOK.read_text(encoding="utf-8")
    header = "| № | Файл:строка |"
    assert header in text, (
        f"в блокноте нет журнала находок с заголовком {header!r} — "
        "либо таблицу переименовали, либо потеряли"
    )
    table = text[text.index(header):].split("\n\n", 1)[0]
    rows = [r for r in table.splitlines() if r.startswith("| ") and "---" not in r]
    body = rows[1:]  # без заголовка

    assert body, "журнал находок пуст"
    for row in body:
        assert _LINK_RE.search(row), f"строка журнала без адреса: {row[:80]}"
