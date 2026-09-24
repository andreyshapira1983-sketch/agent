"""Does this text name something that exists in the workspace?"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

#: A token that could be a repository path. Deliberately loose — it only
#: proposes candidates, and existence on disk is what decides.
#: Отрезки ограничены по длине НАРОЧНО (F-8 в docs/audit/FIELD_CHECK_QUEUE.md).
#: Без предела выражение растёт квадратично на одном непрерывном прогоне
#: словесных знаков: замер 2026-08-25 — 4,7 мс на 801 знаке, 18,3 на 1601,
#: 73,3 на 3201, 293,9 на 6401, то есть учетверение на каждое удвоение. Взрыв
#: даёт не длина текста (92 КБ обычного английского — 5,8 мс) и не base64 (в ней
#: есть `/`), а именно длинный прогон без разделителя: каждая стартовая позиция
#: набирает хвост и откатывается, не найдя ни `/`, ни расширения.
#:
#: Опереться на чужой предел здесь нельзя. Класс проходили как H-10, и там
#: защитой служит `MAX_EXCERPT_CHARS`, названный в той же записи ПОБОЧНЫМ —
#: заведён ради памяти, а не ради стоимости разбора. К тому же он сюда не
#: достаёт: `names_workspace_path(focus)` в `reflection` получает текст
#: целиком, без усечения.
#:
#: 120 знаков на отрезок и 40 отрезков — с большим запасом над любым рабочим
#: адресом (весь путь в Windows ограничен 260 знаками).
_PATH_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_.\-]{1,120}(?:/[A-Za-z0-9_.\-]{1,120}){1,40}"
    r"|[A-Za-z0-9_\-]{1,120}\.[A-Za-z0-9]{1,5}"
)

#: Directories that are not the agent's own source, so naming a file inside one
#: says nothing about the turn being about this repository.
_IGNORED_ROOTS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv"})


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@lru_cache(maxsize=4096)
def _exists(candidate: str, root: str) -> bool:
    if not candidate or candidate.startswith(("http://", "https://")):
        return False
    parts = candidate.replace("\\", "/").split("/")
    if parts and parts[0] in _IGNORED_ROOTS:
        return False
    target = Path(root) / candidate
    try:
        # `resolve` so `../` cannot walk out and report an unrelated file as ours.
        resolved = target.resolve()
        resolved.relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return resolved.exists()


def workspace_paths_named(text: str, *, root: Path | None = None) -> list[str]:
    """Paths in *text* that actually exist in the workspace."""
    base = str(root or _repo_root())
    seen: list[str] = []
    for match in _PATH_TOKEN_RE.finditer(text or ""):
        candidate = match.group(0).strip(".,;:!?»«\"'()[]")
        if candidate and candidate not in seen and _exists(candidate, base):
            seen.append(candidate)
    return seen


def names_workspace_path(text: str, *, root: Path | None = None) -> bool:
    """True when the text names at least one file or directory we own."""
    return bool(workspace_paths_named(text, root=root))
