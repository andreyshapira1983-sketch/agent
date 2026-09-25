"""patch_check называет новый текст, положенный ПОСЛЕ закрытого блока LINES.

loop_journal 24.09: агент клал в LINES старые строки, закрывал блок и писал
новые после «>>>>>>> REPLACE»; «text outside blocks» молчал почему — подсказка
видела выброшенный кусок, но не блок перед ним.
"""
from __future__ import annotations

from pathlib import Path

from tools.patch_check import PatchCheckTool, after_lines_hint

_PATCH = ("FILE: core/x.py\n<<<<<<< LINES 3-3\nold_value = 1\n>>>>>>> REPLACE\n"
          "new_value = 2\n")


def test_the_new_text_after_the_block_is_named(tmp_path: Path) -> None:
    (tmp_path / "p.txt").write_text(_PATCH, encoding="utf-8")
    result = PatchCheckTool(workspace_root=tmp_path).run("p.txt")
    assert result["verdict"] == "red" and result["why"] == "text outside blocks"
    assert "ПОСЛЕ '>>>>>>> REPLACE'" in result["errors"][0]


def test_text_after_a_search_block_is_not_called_a_lines_mistake() -> None:
    raw = "FILE: a.py\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\nstray words\n"
    assert after_lines_hint(raw, "stray words\n") == ""
    assert after_lines_hint(_PATCH, "new_value = 2\n").startswith("новый текст стоит ПОСЛЕ")
