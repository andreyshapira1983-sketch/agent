from pathlib import Path

from core.state_integrity import append_state_jsonl_unlocked, state_file_lock
from tools.base import Tool


class JournalAppendTool(Tool):
    name = "journal_append"
    description = (
        "Append a single JSON record to a data/*.jsonl journal file through the "
        "state-file lock. The path must live under data/ and end in .jsonl; the "
        "record must be a dict. Boundary violations raise ValueError WITHOUT writing."
    )
    # Append-only store; reversibility is guaranteed by the append-only journal semantics (no delete exists)
    risk = "reversible"
    arguments = (
        "path: str — data/<name>.jsonl, relative to the workspace root; "
        "record: dict — one JSON object to append"
    )

    def __init__(self, *, workspace_root):
        self._workspace_root = workspace_root

    def _resolve(self, path: str) -> Path:
        """The file under `<workspace>/data/` this path names — or ValueError.

        Block 7 (audit W2, 2026-09-03): `workspace_root` was stored and never
        read; the boundary was a string prefix, so `data/../../x.jsonl` passed
        and the file landed relative to the CURRENT DIRECTORY, not the
        workspace. The path is now resolved against the workspace and must
        stay inside its `data/` after resolution.
        """
        if not path.startswith("data/") or not path.endswith(".jsonl"):
            raise ValueError(f"path must live under data/ and end in .jsonl: {path}")
        root = Path(self._workspace_root).resolve()
        target = (root / path).resolve()
        data_dir = (root / "data").resolve()
        if target.parent != data_dir and data_dir not in target.parents:
            raise ValueError(f"path escapes the workspace data/ directory: {path}")
        return target

    def run(self, **kwargs):
        allowed = {"path", "record"}
        extra = set(kwargs) - allowed
        if extra:
            raise PermissionError(f"Unexpected arguments: {sorted(extra)}")
        path = kwargs["path"]
        record = kwargs["record"]
        if not isinstance(record, dict):
            raise ValueError(f"record must be a dict, got {type(record).__name__}")
        target = self._resolve(str(path))
        with state_file_lock(target):
            append_state_jsonl_unlocked(target, [record])
        return {"path": path, "appended": True}

    def validate_output(self, output):
        reasons = []
        if not isinstance(output, dict):
            return False, [f"expected dict, got {type(output).__name__}"]
        if "path" not in output or not isinstance(output["path"], str):
            reasons.append("path must be a str")
        if "appended" not in output or output["appended"] is not True:
            reasons.append("appended must be True")
        return (not reasons), reasons
