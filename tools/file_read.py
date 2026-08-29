"""File Read tool — sandboxed to the workspace root.

Refuses path traversal and oversized files. Returns plain text.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.base import Tool

MAX_BYTES = 1_000_000  # 1 MB hard cap for MVP
_MAX_HINT_ENTRIES = 40  # cap the "did you mean" listing so error stays compact

#: Credential-shaped paths this tool refuses to READ. The self-apply lane
#: already refused to WRITE these (`core/self_apply_lane._is_denied`); measured
#: 2026-08-22, nothing consulted a denylist on the read side, so
#: `file_read(".env")` returned the whole credential file. Same shapes, both
#: directions. Matching is on the resolved RELATIVE path, so a directory named
#: `secrets/` is covered wherever it sits inside the workspace.
_CRED_NAMES = frozenset({
    "credentials", "credentials.json", "id_rsa", "id_dsa", "id_ecdsa",
    "id_ed25519", ".netrc", ".npmrc", ".pgpass", ".htpasswd",
})
_CRED_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")
_CRED_DIRS = ("secrets/", ".ssh/", ".gnupg/")


def _is_credential_path(rel: str) -> bool:
    """True when the relative path names a credential store rather than work.

    Deliberately narrow: it must not become a second wall around the workspace.
    `.env` matches by name or as a prefix (`.env.local`), never as a substring —
    `environment.md` and `tests/test_env_probe.py` stay readable.
    """
    # NOT `lstrip("./")`: lstrip removes CHARACTERS, so it turns ".env" into
    # "env" and the name check silently misses the very file this gate exists
    # for. Measured while building this gate, 2026-08-22.
    lower = rel.replace("\\", "/").lower()
    while lower.startswith("./"):
        lower = lower[2:]
    name = lower.rsplit("/", 1)[-1]
    if name == ".env" or name.startswith(".env."):
        return True
    if name in _CRED_NAMES:
        return True
    if any(name.endswith(sfx) for sfx in _CRED_SUFFIXES):
        return True
    return any(lower.startswith(d) or f"/{d}" in f"/{lower}" for d in _CRED_DIRS)


class FileReadTool(Tool):
    name = "file_read"
    description = "Read a UTF-8 text file from inside the workspace and return its contents."
    risk = "read_only"

    def __init__(self, workspace_root: Path | str):
        self.workspace_root = Path(workspace_root).resolve()

    @staticmethod
    def _local_path(raw_path: str) -> Path:
        """Interpret CLI/user paths consistently on Windows and POSIX."""
        return Path(raw_path.strip().replace("\\", "/"))

    def _nearest_dir_hint(self, target: Path) -> str:
        """Best-effort listing of REAL entries near a missing path."""
        probe = target.parent
        while True:
            try:
                probe.relative_to(self.workspace_root)
            except ValueError:
                return ""  # walked above the sandbox; give no hint
            if probe.is_dir():
                break
            if probe == probe.parent:
                return ""  # reached filesystem root without a real dir
            probe = probe.parent

        try:
            entries = sorted(
                p.name + ("/" if p.is_dir() else "")
                for p in probe.iterdir()
                if not p.name.startswith(".")
            )
        except OSError:
            return ""
        if not entries:
            return ""

        shown = entries[:_MAX_HINT_ENTRIES]
        more = (
            ""
            if len(entries) <= _MAX_HINT_ENTRIES
            else f", … (+{len(entries) - _MAX_HINT_ENTRIES} more)"
        )
        rel = probe.relative_to(self.workspace_root)
        where = "workspace root" if str(rel) == "." else f"'{rel.as_posix()}'"
        return (
            f". Nearest existing directory {where} actually contains: "
            f"{', '.join(shown)}{more}. "
            f"Use one of these real paths instead of guessing."
        )

    def run(self, path: str) -> str:
        # Read-only file access may target user-supplied local documents
        # with non-ASCII names. We still keep the sandbox boundary strict:
        # path must be a non-empty string and must resolve inside workspace.
        if not isinstance(path, str):
            raise PermissionError(
                f"file_read path must be a string, got {type(path).__name__}"
            )
        if not path.strip():
            raise PermissionError("file_read path must be non-empty")
        # Заготовка вместо адреса — не «файл не найден», а недостроенный план:
        # см. core/placeholder_text и docs/CODE_NOTES.md.
        from core.placeholder_text import looks_like_unfilled_path

        # base_dir несёт факт существования: реальный файл — всегда адрес,
        # что бы ни значилось в его имени (контракт агента, груз №2).
        if looks_like_unfilled_path(path, base_dir=str(self.workspace_root)):
            raise ValueError(
                f"refusing to read an unfilled placeholder path: {path!r} — "
                "the plan carries a template where an address belongs"
            )
        local_path = self._local_path(path)
        target = (
            local_path.resolve()
            if local_path.is_absolute()
            else (self.workspace_root / local_path).resolve()
        )
        # Credential gate: checked on the RESOLVED path, so `docs/../.env` and an
        # absolute route to the same file are refused alike. The message names
        # the kind, never the contents — a refusal that quotes the secret it
        # protects would defeat itself.
        try:
            rel_for_gate = str(target.relative_to(self.workspace_root))
        except ValueError:
            rel_for_gate = target.name  # outside: the sandbox check below refuses it
        if _is_credential_path(rel_for_gate):
            raise PermissionError(
                f"refusing to read a credential file: {path!r} — this path names "
                "a credential store, and the agent's own tools do not fetch keys"
            )

        try:
            target.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError(f"Path escapes workspace: {target}") from exc

        if not target.exists():
            raise FileNotFoundError(
                f"File not found: {target}{self._nearest_dir_hint(target)}"
            )
        if not target.is_file():
            raise IsADirectoryError(f"Not a file: {target}")

        size = target.stat().st_size
        if size > MAX_BYTES:
            raise ValueError(f"File too large ({size} bytes > {MAX_BYTES})")

        # Strict UTF-8: a binary or wrong-encoding file must fail loudly so
        # the loop classifies it as a tool error, not as silently-garbled text.
        try:
            return target.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise UnicodeDecodeError(
                exc.encoding,
                exc.object,
                exc.start,
                exc.end,
                f"file is not valid UTF-8: {target.name} ({exc.reason})",
            ) from exc

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, str):
            return False, [f"expected str, got {type(output).__name__}"]
        if not output.strip():
            return False, ["file is empty or whitespace-only"]
        return True, []
