"""Раскол переносит ОДИН предмет в модуль с именем, а не свалку в _helpers2.

2026-09-21. Три дефекта раскольщика, найденные на живом разговоре:

1. `_movable_function_group` брал МАКСИМАЛЬНЫЙ набор всего переносимого — план
   для core/step_sanitizer.py вёз в один файл подставные URL, приведение int,
   окно строк, метку shell и четыре санитайзера разных инструментов. Связаны
   они были только через `sanitize_step`, который оставался дома.
2. Имя нового модуля выбирал счётчик: `_helpers`, занято → `_helpers2`. 20.09
   `episode_tools` ушла в новый smart_memory_helpers2.py, когда
   smart_memory_helpers.py уже держал её родню (отменено 24fb2d4).
3. Доказательство «дубль» требует свести повтор, а раскольщик умел только
   резать.
"""
from __future__ import annotations

from pathlib import Path

from core.incremental_splitter import plan_incremental_split
from core.split_proof import index_workspace, proof_for


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


_TWO_SUBJECTS = '''"""Two subjects."""
from __future__ import annotations

import re

_HOST_RE = re.compile(r"[a-z]+")


def url_host(url):
    return _HOST_RE.match(url)


def url_is_local(url):
    host = url_host(url)
    return host == "localhost"


def url_scheme(url):
    return url.split(":", 1)[0] if url_is_local(url) is not None else ""


def line_window(start, end):
    return max(0, end - start)


def line_clip(start, end):
    return line_window(start, end) or 1
'''


def test_only_one_connected_subject_moves(tmp_path) -> None:
    _write(tmp_path, "core/mixed.py", _TWO_SUBJECTS)
    plan = plan_incremental_split(tmp_path, "core/mixed.py")
    assert plan.status == "planned", plan.reason
    moved = set(plan.step.moved_names)
    url = {"_HOST_RE", "url_host", "url_is_local", "url_scheme"}
    line = {"line_window", "line_clip"}
    assert moved in (url, line), moved


def test_the_new_module_is_named_by_its_subject(tmp_path) -> None:
    _write(tmp_path, "core/mixed.py", _TWO_SUBJECTS)
    plan = plan_incremental_split(tmp_path, "core/mixed.py")
    assert plan.step.new_module in ("core/mixed_url.py", "core/mixed_line.py"), \
        plan.step.new_module


def test_an_occupied_name_is_refused_not_numbered(tmp_path) -> None:
    _write(tmp_path, "core/mixed.py", _TWO_SUBJECTS)
    first = plan_incremental_split(tmp_path, "core/mixed.py").step
    _write(tmp_path, first.new_module, "# already here\n")
    again = plan_incremental_split(tmp_path, "core/mixed.py")
    assert again.status == "no_split"
    assert "already exists" in again.reason
    assert not any((tmp_path / "core").glob("mixed_*2.py"))


def _chain(prefix: str, n: int, lines_each: int) -> str:
    out = []
    for i in range(n):
        call = f"    {prefix}_{i + 1}(x)\n" if i + 1 < n else ""
        body = "".join(f"    a{j} = x\n" for j in range(lines_each - 2))
        out.append(f"def {prefix}_{i}(x):\n{call}{body}")
    return "\n\n".join(out) + "\n"


def test_a_proven_group_is_what_moves(tmp_path) -> None:
    """С доказательством «два предмета» уезжает ровно доказанная группа —
    не та, что крупнее, и не всё переносимое."""
    _write(tmp_path, "core/twofold.py",
           _chain("alpha", 6, 30) + "\n\n" + _chain("omega", 5, 30))
    proof = proof_for("core/twofold.py", index_workspace(tmp_path))
    assert proof is not None and proof.kind == "multi_subject", proof
    plan = plan_incremental_split(tmp_path, "core/twofold.py", proof=proof)
    assert plan.status == "planned", plan.reason
    assert set(plan.step.moved_names) == set(proof.names)


def test_a_duplicate_is_merged_by_import_not_split(tmp_path) -> None:
    body = "".join(f"    v{i} = x + {i}\n" for i in range(8)) + "    return v0\n"
    _write(tmp_path, "core/keep.py", f"def parse_when(x):\n{body}\n\ndef clip(x):\n{body}")
    _write(tmp_path, "core/copy.py",
           f"import os\n\n\ndef parse_when(x):\n{body}\n\ndef clip(x):\n{body}\n\n"
           "def user():\n    return parse_when(1), clip(2), os.sep\n")
    proof = proof_for("core/copy.py", index_workspace(tmp_path))
    assert proof is not None and proof.kind == "dup"
    plan = plan_incremental_split(tmp_path, "core/copy.py", proof=proof)
    assert plan.status == "planned", plan.reason
    step = plan.step
    assert step.mode == "dedup" and step.new_module == "core/keep.py"
    assert step.new_content == "", "второй модуль не меняется"
    assert "def parse_when" not in step.target_content
    assert "from core.keep import" in step.target_content
    assert "def user()" in step.target_content
