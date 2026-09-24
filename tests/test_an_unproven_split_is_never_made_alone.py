"""Самостоятельный разрез только с доказательством: дубль или второй предмет.

24.09 для core/step_sanitizer.py доказательства не было, а раскольщик вынес
«самую крупную связную кучку» — три мелкие функции, которые зовёт сам файл;
нечитаемая функция на 765 строк осталась нетронутой. Правило оператора:
делить за чужие функции или нечитаемость, никогда за длину.
"""
from __future__ import annotations

from pathlib import Path

from core.self_build_producer import _deterministic_split_report

# Один предмет: большая функция и её собственные помощники.
_ONE_SUBJECT = '''
def _window(value):
    return max(1, min(int(value), 400))


def _coerce(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sanitize(args):
    n = _coerce(args.get("n"))
    if n is None:
        return None
''' + "".join(f"    if args.get('k{i}'):\n        n += {i}\n" for i in range(200)) + '''    return _window(n)
'''


class _Inbox:
    def __init__(self) -> None:
        self.published: list[object] = []

    def create(self, *a, **k):  # pragma: no cover — сработает только при поломке
        self.published.append((a, k))
        raise AssertionError("an unproven split reached the approval inbox")


def test_a_file_with_one_subject_is_not_split(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "one.py").write_text(_ONE_SUBJECT, encoding="utf-8")
    inbox = _Inbox()
    report = _deterministic_split_report(
        workspace=tmp_path, concrete_target="core/one.py", inbox=inbox,
        reader=lambda rel: (tmp_path / rel).read_text(encoding="utf-8"),
        gates=[], roles=[],
    )
    assert report is not None and report.status == "no_patch"
    assert "no proof" in report.reason and "Length alone" in report.reason
    assert not inbox.published
    assert "_window" in (tmp_path / "core" / "one.py").read_text(encoding="utf-8")
