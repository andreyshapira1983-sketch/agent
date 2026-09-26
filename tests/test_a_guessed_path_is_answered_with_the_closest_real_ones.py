"""A read of a guessed path names the closest real files, even in a directory of hundreds (live run 26.09)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.file_read import FileReadTool


@pytest.fixture
def root(tmp_path: Path) -> Path:
    core = tmp_path / "core"
    core.mkdir()
    for i in range(60):
        (core / f"a_module_{i:02d}.py").write_text("x = 1\n", encoding="utf-8")
    (core / "goal_content_judge.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("guess", ["core/goal_judge.py", "core/goal_guard.py"])
def test_the_real_file_is_named_first(root: Path, guess: str) -> None:
    with pytest.raises(FileNotFoundError) as exc:
        FileReadTool(workspace_root=root).run(path=guess)

    assert "goal_content_judge.py" in str(exc.value)
