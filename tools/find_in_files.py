"""Find in Files — поиск по рабочей папке: файлы по имени и строки по тексту.

Зачем существует: замер 2026-09-19, опыт с библиотекой книг (~100 текстовых
книг, 113 МБ). Искать агенту было нечем, кроме `findstr` через shell_exec, и
большинство провалов были одного вида: «такой книги в библиотеке нет», хотя
она была. `findstr /s /c "===== PAGE" knowledge_library` падал на синтаксисе,
`/s` без маски имени ничего не находил, пустой вывод читался как отсутствие.
Здесь поиск кросс-платформенный, а отрицательный ответ говорит, СКОЛЬКО файлов
просмотрено, — «0 совпадений в 97 файлах» и «поиск не состоялся» больше не
выглядят одинаково.

Только чтение, внутри рабочей папки; файлы ключей не просматриваются никогда
(тот же запрет, что у file_read).
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from tools.base import Tool
from tools.file_read import _is_credential_path

MAX_RESULTS = 200
DEFAULT_RESULTS = 50
MAX_FILE_BYTES = 50_000_000
_SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"})
_LINE_CHARS = 240
#: Собственные журнал и память агента в корне рабочей папки. Веб-экзамен
#: 2026-09-19, второй прогон, N01: поиск «release» и «версия» по «.» нашёл
#: 3 строки в 1 файле — `logs/trace_….jsonl`, журнал ЭТОГО хода, куда только
#: что записан сам запрос. Документ `docs/RELEASE.md` («Текущий выпуск: 5.7.8»)
#: этих слов не содержит; агент прочёл эхо своего вопроса и ответил «не
#: найдено». Ищутся они, только когда `path` указывает внутрь них.
_OWN_RUNTIME_DIRS = frozenset({"logs", "data"})


class FindInFilesTool(Tool):
    name = "find_in_files"
    description = (
        "Search the workspace. With `query`: every line containing it, as "
        "'path:line: text' — the line number feeds file_read(start_line=...). "
        "Without `query`: the files whose NAME matches `name` (a glob such as "
        "'*Lebl*' or '*.txt'). Recursive under `path`; case-insensitive; "
        "`regex=true` treats query as a regular expression. A negative answer "
        "states how many files were searched."
    )
    risk = "read_only"

    def __init__(self, workspace_root: Path | str):
        self.workspace_root = Path(workspace_root).resolve()

    def _files(self, root: Path, name: str) -> list[Path]:
        out: list[Path] = []
        pattern = (name or "*").lower()
        inside_own = root != self.workspace_root and root.relative_to(self.workspace_root).parts[0] in _OWN_RUNTIME_DIRS
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(self.workspace_root)
            if any(part in _SKIP_DIRS for part in rel.parts) or not path.is_file():
                continue
            if not inside_own and len(rel.parts) > 1 and rel.parts[0] in _OWN_RUNTIME_DIRS:
                continue
            if _is_credential_path(rel.as_posix()):
                continue
            if fnmatch.fnmatch(path.name.lower(), pattern):
                out.append(path)
        return out

    def run(
        self,
        query: str = "",
        path: str = ".",
        name: str = "*",
        regex: bool = False,
        max_results: int = DEFAULT_RESULTS,
    ) -> str:
        if not isinstance(path, str) or not isinstance(name, str) or not isinstance(query, str):
            raise TypeError("find_in_files: query, path and name must be strings")
        root = (self.workspace_root / (path.strip().replace("\\", "/") or ".")).resolve()
        try:
            root.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError(f"Path escapes workspace: {root}") from exc
        # Путь к ФАЙЛУ — поиск внутри этого файла. Замер 2026-09-19: агент искал
        # в Downey_ThinkPython2.txt, получил «Directory not found» и записал в
        # ответ, что файл прочитать не удалось.
        if root.is_file():
            if _is_credential_path(root.relative_to(self.workspace_root).as_posix()):
                raise PermissionError(f"refusing to search a credential file: {path!r}")
            files = [root]
        elif root.is_dir():
            files = self._files(root, name)
        else:
            raise FileNotFoundError(f"Not found: {path}")
        limit = max(1, min(int(max_results or DEFAULT_RESULTS), MAX_RESULTS))
        where = f"under {path or '.'} (name={name or '*'})"
        if not query:
            shown = [f.relative_to(self.workspace_root).as_posix() for f in files[:limit]]
            head = f"{len(files)} files {where}" + (f", showing {limit}" if len(files) > limit else "")
            return "\n".join([head, *shown])

        matcher = re.compile(query if regex else re.escape(query), re.IGNORECASE)
        hits: list[str] = []
        total = occurrences = matched_files = searched = 0
        for f in files:
            if f.stat().st_size > MAX_FILE_BYTES:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # не текст — не предмет поиска по строкам
            searched += 1
            rel = f.relative_to(self.workspace_root).as_posix()
            found_here = False
            for n, line in enumerate(text.splitlines(), 1):
                found = len(matcher.findall(line))
                if found:
                    total += 1
                    occurrences += found
                    found_here = True
                    if len(hits) < limit:
                        hits.append(f"{rel}:{n}: {line.strip()[:_LINE_CHARS]}")
            matched_files += found_here
        if not total:
            return f"no matches for {query!r} in {searched} text files {where}"
        # Строки и вхождения — разные числа: замер 2026-09-19, «entropy» стоит в
        # 125 строках Tong_StatisticalPhysics.txt, а вхождений 131, и агент
        # выдал число строк за число вхождений.
        head = (f"{total} matching lines ({occurrences} occurrences) in {matched_files} "
                f"of {searched} text files {where}")
        if total > limit:
            head += f", showing {limit}"
        return "\n".join([head, *hits])

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, str):
            return False, [f"expected str, got {type(output).__name__}"]
        return True, []
