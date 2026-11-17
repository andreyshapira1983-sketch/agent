"""Which stored answers may be served again without a model call (operator, 2026-11-17)."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: Read-only tools whose inputs can be checked: each read file must hash the same as when read.
FILE_TOOLS = frozenset({"file_read"})
#: Tools whose answer ages with time, not with a file; python_probe runs on their output.
WEB_TOOLS = frozenset({"web_search", "web_fetch", "python_probe"})
WEB_TTL = timedelta(hours=24)
#: Reads that leave the world as it was; any other tool is an action that must be redone.
READ_TOOLS = FILE_TOOLS | WEB_TOOLS | {"list_dir", "find_in_files"}
_WINDOW = re.compile(r":\d+-\d+$")  # file:<path>:<a>-<b> labels a windowed read


def _created(episode: Any) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(getattr(episode, "created_at", "") or ""))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def read_files(episode: Any) -> list[str]:
    """Every file behind the answer, by its `file:` label — whatever tool (or the loop itself) read it."""
    out = []
    for lab in getattr(episode, "source_labels", ()) or ():
        rel = _WINDOW.sub("", str(lab)[5:].strip()) if str(lab).startswith("file:") else ""
        if rel and rel not in out:
            out.append(rel)
    return out


def tools_allow_replay(episode: Any, now: datetime | None = None) -> bool:
    """Tool half of the replay gate: any action or unchecked read means the work is redone."""
    tools = set(getattr(episode, "tools_used", ()) or ())
    if not tools <= FILE_TOOLS | WEB_TOOLS:
        return False
    if tools & WEB_TOOLS:
        created = _created(episode)
        if created is None or (now or datetime.now(timezone.utc)) - created > WEB_TTL:
            return False
    return not (tools & FILE_TOOLS) or bool(read_files(episode))


def _inside(root: Path, rel: str) -> Path | None:
    base = Path(root).resolve()
    path = (base / rel).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    return path


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def input_digests(episode: Any, root: Path | None, run_started: float) -> tuple[str, ...]:
    """`path<TAB>sha256` per read file, taken at banking; a file edited during the run gets none."""
    if root is None:
        return ()
    out = []
    for rel in read_files(episode):
        path = _inside(root, rel)
        try:
            # Changed after the run began: the answer may describe the old text (hunter, 17.11).
            if path is None or not run_started or path.stat().st_mtime > run_started:
                continue
        except OSError:
            continue
        digest = _sha(path)
        if digest:
            out.append(f"{rel}\t{digest}")
    return tuple(out)


def stale_inputs(episode: Any, root: Path | None) -> list[str]:
    """Read files whose content is not provably the same as when the answer was stored."""
    stored = dict(d.split("\t", 1) for d in getattr(episode, "input_digests", ()) or () if "\t" in d)
    stale = []
    for rel in read_files(episode):
        path = None if root is None else _inside(root, rel)
        if path is None or rel not in stored or _sha(path) != stored[rel]:
            stale.append(rel)
    return stale


def replay_note(episode: Any) -> str:
    """One line telling the reader the answer came from memory and why it is still valid."""
    created = _created(episode)
    when = created.strftime("%d.%m.%Y %H:%M UTC") if created else "без даты"
    why = []
    if read_files(episode):
        why.append("файлы с тех пор не менялись: " + ", ".join(read_files(episode)))
    if set(getattr(episode, "tools_used", ()) or ()) & WEB_TOOLS:
        why.append("сведения из интернета не старше суток")
    return f"(Ответ из памяти от {when}, модель не вызывалась" + ("; " + "; ".join(why) if why else "") + ".)"


_NEXT_SECTION = {"facts", "sources", "confidence", "unverified", "safety"}


def _header(line: str) -> str:
    return line.replace("**", "").lstrip("# ").split(":", 1)[0].strip().lower()


def with_replay_note(answer: str, note: str) -> str:
    """Put the note at the end of the Conclusion section, where the chat formatter keeps it."""
    lines = answer.splitlines()
    start = next((i for i, ln in enumerate(lines) if _header(ln) == "conclusion" and ":" in ln), None)
    if start is None:
        return f"{answer.rstrip()}\n{note}"
    end = next((i for i in range(start + 1, len(lines))
                if ":" in lines[i] and _header(lines[i]) in _NEXT_SECTION), len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return "\n".join([*lines[:end], note, *lines[end:]])
