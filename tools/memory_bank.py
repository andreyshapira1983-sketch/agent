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

    def __init__(self, *, store, policy):
        self._store = store
        self._policy = policy

    def run(self, **kwargs):
        allowed = {"text", "kind", "provenance"}
        extra = set(kwargs) - allowed
        if extra:
            raise PermissionError(f"Unexpected arguments: {sorted(extra)}")
        text = kwargs["text"]
        kind = kwargs["kind"]
        provenance = kwargs["provenance"]
        mem_id, refused_by = memory_door_verdict(
            self._store, self._policy, text, kind, provenance
        )
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
