"""patch_check называет, что не так с выброшенной строкой, и не путает пустую строку с куском.

Замер 2026-09-23 по data/self_repair_log.jsonl: 11 красных «text outside
blocks». В семи сломана сама строка-маркер (SEARCH без <<<<<<<, блок не закрыт
>>>>>>> REPLACE, путь и маркер на одной строке), в одном — формат unified
diff, в трёх — проза. Сообщение печатало выброшенное и молчало, что с ним не
так. После правки подсказку получают 11 выброшенных кусков из 12, проза — нет.
Так подсказывает Python с 3.10 («Did you mean …?»): ближайшее правильное имя.

И отдельно: 23.09 18:36 агент оставил между <<<<<<< SEARCH и ======= пустую
строку, кусок стал '\\n', нашёлся 69 раз — новый файл не создался.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.patch_check import apply_blocks, marker_hint, parse_blocks


@pytest.mark.parametrize(("stray", "needle"), [
    ("FILE:tests/test_x.py\nSEARCH\n=======\n>>>>>>> REPLACE", "<<<<<<< SEARCH"),
    ("SEARCH:\n    str(getattr(t, 'code', ''))", "<<<<<<< SEARCH"),
    ("FILE:core/verifier.py\n<<<<<<< LINES 1-1\n=======\nFILE:tests/t.py", ">>>>>>> REPLACE"),
    ("--- tools/python_probe.py\n+++ tools/python_probe.py\n@@ -107,6 +107,9 @@", "unified diff"),
    ("FILE:/<<<<<<< LINES 229-229\n    self._defect_signals.append(x)", "отдельной строкой"),
    ("FILE:t.py\n<<<<<< SEARCH\nx\n", "<<<<<<< SEARCH"),
])
def test_a_broken_marker_gets_a_named_fix(stray: str, needle: str) -> None:
    assert needle in marker_hint(stray)


def test_prose_gets_no_hint() -> None:
    assert marker_hint("Step 1 did not return the exact text of lines 150-172") == ""


def test_a_blank_line_in_search_still_creates_a_new_file(tmp_path: Path) -> None:
    blocks = parse_blocks("FILE: tests/test_new.py\n<<<<<<< SEARCH\n\n=======\nx = 1\n>>>>>>> REPLACE\n")
    assert blocks and blocks[0]["old"].strip() == ""

    errors = apply_blocks(tmp_path, blocks)

    assert errors == []
    assert (tmp_path / "tests" / "test_new.py").read_text(encoding="utf-8") == "x = 1\n"
