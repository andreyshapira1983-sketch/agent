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

    def __init__(self, *, workspace_root):
        self._workspace_root = workspace_root

    def run(self, **kwargs):
        allowed = {"path", "record"}
        extra = set(kwargs) - allowed
        if extra:
            raise PermissionError(f"Unexpected arguments: {sorted(extra)}")
        path = kwargs["path"]
        record = kwargs["record"]
        if not isinstance(record, dict):
            raise ValueError(f"record must be a dict, got {type(record).__name__}")
        if not path.startswith("data/") or not path.endswith(".jsonl"):
            raise ValueError(f"path must live under data/ and end in .jsonl: {path}")
        with state_file_lock(path):
            append_state_jsonl_unlocked(path, [record])
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
