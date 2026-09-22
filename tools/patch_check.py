"""`patch_check` — проверить свою правку в отдельной копии, не трогая живой код.

Разговор 2026-09-22 (задача «банк уроков»): агент дважды писал правку, где
старый кусок был набран по памяти, а не скопирован из файла, — и узнавал об
этом только от Claude, ходом позже. Современные агенты замыкают этот круг
сами: инструмент правки сверяет кусок с файлом и возвращает ошибку со строками
файла (str_replace у редактора Anthropic, str_replace_editor в OpenHands,
SEARCH/REPLACE в Aider). Здесь то же, но без записи в живой код: правка
кладётся в чистую копию репозитория вне рабочей папки, там гоняются ruff и
тесты, и весь вывод возвращается агенту в том же ходе.

Формат правки — текстовые блоки (без JSON внутри JSON — на нём 14:09 сломался
план):

    FILE: core/x.py
    <<<<<<< SEARCH
    дословный кусок, встречается в файле ровно один раз
    =======
    новый текст
    >>>>>>> REPLACE

Пустой SEARCH — новый файл. Второй вид блока — по номерам строк, которые
показывает file_read (копировать старый текст не нужно; так же устроены
`insert`/`view` у редактора Anthropic и OpenHands):

    FILE: core/x.py
    <<<<<<< LINES 284-292
    новый текст на место строк 284–292
    >>>>>>> REPLACE

2026-09-22 14:37: модель трижды не смогла переписать сигнатуру символ в
символ, хотя видела её, — номера строк она видит без ошибок. Номера — по
файлу ДО правки. В ответе — итоговая разница, чтобы увидеть, что легло. Риск — `reversible`, как у `run_tests`: код
тестов исполняется, но рабочая папка не меняется.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from core.redaction import redact_text
from tools.base import Risk, Tool, require_ascii_identifier

_BLOCK_RE = re.compile(
    r"^FILE:[ \t]*(?P<path>\S+)[ \t]*\n<<<<<<< SEARCH\n(?P<old>.*?)^=======\n"
    r"(?P<new>.*?)^>>>>>>> REPLACE[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_LINES_RE = re.compile(
    r"^FILE:[ \t]*(?P<path>\S+)[ \t]*\n<<<<<<< LINES[ \t]+(?P<a>\d+)-(?P<b>\d+)[ \t]*\n"
    r"(?P<new>.*?)^>>>>>>> REPLACE[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_TAIL_LINES = 40
_DIFF_LINES = 150
_CONTEXT_LINES = 12
_TIMEOUT_SECONDS = 900


def parse_blocks(text: str) -> list[dict[str, Any]]:
    """Блоки правки в порядке текста: ПОИСК/ЗАМЕНА и замена по номерам строк."""
    found = [(m.start(), {"path": m["path"], "old": m["old"], "new": m["new"]})
             for m in _BLOCK_RE.finditer(text or "")]
    found += [(m.start(), {"path": m["path"], "lines": (int(m["a"]), int(m["b"])), "new": m["new"]})
              for m in _LINES_RE.finditer(text or "")]
    return [b for _, b in sorted(found, key=lambda x: x[0])]


def _apply_line_blocks(root: Path, blocks: list[dict[str, Any]]) -> list[str]:
    """Замены по номерам строк — по файлу ДО правки, снизу вверх."""
    errors: list[str] = []
    by_file: dict[str, list[dict[str, Any]]] = {}
    for b in blocks:
        by_file.setdefault(b["path"], []).append(b)
    for rel, items in by_file.items():
        target = (root / rel).resolve()
        if root.resolve() not in target.parents or not target.is_file():
            errors.append(f"LINES {rel}: файла нет в копии")
            continue
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
        spans = sorted((b["lines"] for b in items), reverse=True)
        bad = [f"{a}-{z}" for a, z in spans if not 1 <= a <= z <= len(lines)]
        overlap = any(spans[i][0] <= spans[i + 1][1] for i in range(len(spans) - 1))
        if bad or overlap:
            errors.append(f"LINES {rel}: диапазоны {bad or 'пересекаются'} вне 1-{len(lines)} или пересекаются")
            continue
        for b in sorted(items, key=lambda b: b["lines"][0], reverse=True):
            a, z = b["lines"]
            new = b["new"] if not b["new"] or b["new"].endswith("\n") else b["new"] + "\n"
            lines[a - 1:z] = new.splitlines(keepends=True)
        target.write_text("".join(lines), encoding="utf-8")
    return errors


def _diff(root: Path, originals: dict[str, str]) -> str:
    import difflib

    out: list[str] = []
    for rel, before in originals.items():
        target = root / rel
        after = target.read_text(encoding="utf-8") if target.is_file() else ""
        out += difflib.unified_diff(before.splitlines(), after.splitlines(), f"a/{rel}", f"b/{rel}", lineterm="", n=2)
    return "\n".join(out[:_DIFF_LINES]) + ("\n… (diff truncated)" if len(out) > _DIFF_LINES else "")


def _nearest(text: str, old: str) -> str:
    """Где в файле лежит первая строка куска — дословно, с номерами, для копирования."""
    first = next((ln.strip() for ln in old.splitlines() if ln.strip()), "")
    lines = text.splitlines()
    hits = [i for i, ln in enumerate(lines) if first and first in ln]
    if not hits:
        return f"первой строки куска {first!r} в файле нет"
    i = hits[0]
    shown = lines[i:i + _CONTEXT_LINES]
    return "в файле с строки {}:\n{}".format(
        i + 1, "\n".join(f"{i + 1 + k:5d}| {ln}" for k, ln in enumerate(shown)))


def _tail(text: str, n: int = _TAIL_LINES) -> str:
    return redact_text("\n".join((text or "").strip().splitlines()[-n:]))[0]


def apply_blocks(root: Path, blocks: list[dict[str, Any]]) -> list[str]:
    """Положить блоки в копию; вернуть ошибки (пусто — всё легло).

    Сначала замены по номерам строк (номера — по файлу до правки), затем ПОИСК/ЗАМЕНА.
    """
    errors = _apply_line_blocks(root, [b for b in blocks if "lines" in b])
    for n, b in enumerate(blocks, 1):
        if "lines" in b:
            continue
        target = (root / b["path"]).resolve()
        if root.resolve() not in target.parents:
            errors.append(f"блок {n}: путь {b['path']} выходит за копию")
            continue
        if not b["old"]:
            if target.exists():
                errors.append(f"блок {n}: SEARCH пуст (новый файл), но {b['path']} уже есть")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(b["new"], encoding="utf-8")
            continue
        if not target.is_file():
            errors.append(f"блок {n}: файла {b['path']} нет")
            continue
        text = target.read_text(encoding="utf-8")
        count = text.count(b["old"])
        if count != 1:
            errors.append(f"блок {n} ({b['path']}): кусок SEARCH найден {count} раз, нужно ровно 1; "
                          f"{_nearest(text, b['old'])}")
            continue
        target.write_text(text.replace(b["old"], b["new"]), encoding="utf-8")
    return errors


def _verdict(result: dict[str, Any]) -> dict[str, str]:
    """Зелёное — только правка, которая что-то меняет, несёт тест, и тест проходит.

    2026-09-22 14:43: агент заменил строки 284–292 теми же строками, без теста;
    проверка ответила applied=True, и ход закрылся как сделанный. Правка без
    изменения и без теста не доказывает ничего — это не зелёное.
    """
    if not result.get("diff"):
        return {"verdict": "red", "why": "the patch changes nothing (empty diff)"}
    if result.get("tests_exit_code") is None:
        return {"verdict": "red", "why": "no test in the patch — a change without a test proves nothing"}
    if result.get("tests_exit_code") != 0 or result.get("full_exit_code") not in (0, None):
        return {"verdict": "red", "why": "tests are not green"}
    return {"verdict": "green", "why": "the change applies, carries a test, and the tests pass"}


class PatchCheckTool(Tool):
    name = "patch_check"
    description = (
        "Check YOUR OWN code change before anyone applies it: reads a patch file of "
        "blocks: FILE: path / <<<<<<< SEARCH / exact old text / ======= / new text / "
        ">>>>>>> REPLACE (empty SEARCH = new file), OR - easier, no copying of old text - "
        "FILE: path / <<<<<<< LINES 284-292 / new text for those lines / >>>>>>> REPLACE "
        "(line numbers exactly as file_read shows them, before the change). It applies it to a clean copy "
        "of the repository OUTSIDE the workspace, runs ruff and pytest there and returns "
        "the output and the resulting diff. The workspace is never changed. If a SEARCH block is not found the "
        "result shows the real file lines to copy from. Arguments: path (the patch file, "
        "e.g. proposals/selffix/<name>/edits.txt), tests (optional list of test files), "
        "full (true = also run the whole suite)."
    )
    risk: Risk = "reversible"

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()

    def risk_for(self, arguments: dict[str, Any]) -> Risk:
        return "reversible"

    def _clone(self, dest: Path) -> None:
        subprocess.run(["git", "clone", "-q", str(self.workspace_root), str(dest)],  # noqa: S603, S607 — свои пути, без оболочки
                       check=True, capture_output=True, timeout=300)
        example = self.workspace_root / ".env.example"
        if example.exists():
            shutil.copy(example, dest / ".env.example")

    def _run(self, cmd: list[str], cwd: Path) -> tuple[int, str]:
        try:
            # argv из своих частей, пути проверены require_ascii_identifier.
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",  # noqa: S603
                               errors="replace", timeout=_TIMEOUT_SECONDS, check=False)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired:
            return -1, f"timeout {_TIMEOUT_SECONDS}s"

    def run(self, path: str, tests: list[str] | None = None, full: bool = False) -> dict[str, Any]:
        require_ascii_identifier(path, role="patch_check path")
        patch = (self.workspace_root / path).resolve()
        if self.workspace_root not in patch.parents or not patch.is_file():
            raise FileNotFoundError(f"patch file not found inside the workspace: {path}")
        blocks = parse_blocks(patch.read_text(encoding="utf-8"))
        if not blocks:
            return {"applied": False, "verdict": "red", "why": "no blocks",
                    "errors": ["в файле нет блоков FILE:/<<<<<<< SEARCH/=======/>>>>>>> REPLACE или <<<<<<< LINES a-b"]}
        with tempfile.TemporaryDirectory(prefix="patch_check_") as tmp:
            copy = Path(tmp) / "repo"
            self._clone(copy)
            files = list(dict.fromkeys(b["path"] for b in blocks))
            originals = {f: (copy / f).read_text(encoding="utf-8") if (copy / f).is_file() else "" for f in files}
            errors = apply_blocks(copy, blocks)
            result: dict[str, Any] = {"applied": not errors, "errors": errors, "files": files}
            if errors:
                return {**result, "verdict": "red", "why": "the patch did not apply"}
            result["diff"] = _diff(copy, originals)
            py = [b["path"] for b in blocks if b["path"].endswith(".py")]
            code, out = self._run([sys.executable, "-m", "ruff", "check", *py], copy)
            result["ruff"] = "not installed" if "No module named ruff" in out else (
                "clean" if code == 0 else _tail(out, 20))
            wanted = list(dict.fromkeys([*(tests or []), *(p for p in py if p.startswith("tests/"))]))
            for t in wanted:
                require_ascii_identifier(t, role="patch_check test path")
            if wanted:
                code, out = self._run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *wanted], copy)
                result.update(tests_exit_code=code, tests_output=_tail(out))
            else:
                result["tests_exit_code"] = None
                result["tests_output"] = "no test file in the patch and none named — a change without a test proves nothing"
            if full:
                code, out = self._run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], copy)
                result.update(full_exit_code=code, full_output=_tail(out, 25))
            result.update(_verdict(result))
            return result
