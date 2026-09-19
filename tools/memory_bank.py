from core.persistent_memory import ACCEPTED_KINDS_TEXT, memory_door_verdict
from tools.base import Tool


class MemoryBankTool(Tool):
    name = "memory_bank"
    description = (
        "Store a single piece of output into durable memory through the gated "
        "memory door. The door rejects code, duplicates, hypotheses, and unknown "
        "kinds. A None refusal is NOT an error - it is the gate working as intended."
    )
    # Контракт аргументов — для планировщика: до 2026-09-03 он видел только
    # имя и описание, угадывал text/kind/provenance и шесть раз подряд стучался
    # не тем ключом.
    arguments = (
        "text (str: the conclusion itself, plain prose - no code, no backticks), "
        "kind (str: " + ACCEPTED_KINDS_TEXT + "), "
        "provenance (str: where the evidence lives - a file path, journal id or run id). "
        "All three are required."
    )
    # Append-only store; record deletable via delete(record_id)
    risk = "reversible"

    #: Ceiling on banked records per process (authority change 2026-09-04):
    #: the unattended door is open, and a flood of «insights» is the failure
    #: the one-day reflection dump already showed (625 records in a day).
    max_writes_per_process = 12

    def __init__(self, *, store, policy, max_writes_per_process: int | None = None):
        self._store = store
        self._policy = policy
        if max_writes_per_process is not None:
            self.max_writes_per_process = int(max_writes_per_process)
        self._banked = 0

    def run(self, **kwargs):
        allowed = {"text", "kind", "provenance"}
        extra = set(kwargs) - allowed
        if extra:
            raise PermissionError(f"Unexpected arguments: {sorted(extra)}")
        missing = [k for k in ("text", "kind", "provenance") if k not in kwargs]
        if missing:
            raise ValueError(f"memory_bank requires {missing}; " + self.arguments)
        text = kwargs["text"]
        kind = kwargs["kind"]
        provenance = kwargs["provenance"]
        if self._banked >= self.max_writes_per_process:
            return {
                "banked": False, "mem_id": None, "kind": kind,
                "refused_by": (
                    f"ceiling: {self.max_writes_per_process} records already banked "
                    "in this process; the rest waits for the next run"
                ),
            }
        mem_id, refused_by = memory_door_verdict(
            self._store, self._policy, text, kind, provenance
        )
        if mem_id is not None:
            self._banked += 1
        return {
            "banked": mem_id is not None,
            "mem_id": mem_id,
            "kind": kind,
            # Причина отказа — для того, кто стучится; None, когда принято.
            "refused_by": refused_by or None,
        }

    def validate_output(self, output):
        reasons = []
        if not isinstance(output, dict):
            return False, [f"expected dict, got {type(output).__name__}"]
        if "banked" not in output or not isinstance(output["banked"], bool):
            reasons.append("banked must be a bool")
        if "mem_id" not in output or not (
            output["mem_id"] is None or isinstance(output["mem_id"], str)
        ):
            reasons.append("mem_id must be str or None")
        if output.get("banked") and not (
            isinstance(output.get("mem_id"), str)
            and output["mem_id"].startswith("mem_")
        ):
            reasons.append("banked=True requires mem_id starting with 'mem_'")
        if "kind" not in output:
            reasons.append("kind must be present")
        return (not reasons), reasons
