"""Paths that agent self-repair never touches: one list and one path comparison."""
from __future__ import annotations

from core.control_files import CONTROL_RELPATHS

__all__ = ["PROTECTED_FILES", "PROTECTED_TREES", "is_protected", "path_key"]

#: Files by exact path from the repo root.
PROTECTED_FILES: frozenset[str] = frozenset({
    # Main rules, sandboxes, the instruction-tamper guard and the benchmarks that measure the agent.
    "core/root_principles.py", "core/governance.py", "core/injection_guard.py",
    "tools/python_probe.py", "tools/convert_file.py",
    "tests/test_capability_baseline.py", "tests/capability_tasks.py",
    "scripts/capability_baseline.py", "scripts/check_function_length_baseline.py",
    "scripts/check_ceo_file_baseline.py", "tests/test_ruff_config.py",
    # Brakes and approval gates: spend, brake list, policy, approval inbox.
    "core/budget_kill_switch.py", "core/budget_governor.py", "core/budget_ledger.py",
    "core/usd_spend.py", "core/control_files.py", "core/policy.py", "core/approval.py",
    "core/approval_inbox.py", "core/actuation_gateway.py", "core/rule_approved_apply.py",
    # Self-repair paths, their judge, their git hands and this list.
    "core/self_apply_lane.py", "core/self_apply_bridge.py", "core/patch_route.py",
    "core/self_repair_utils.py", "core/self_build_producer.py", "core/safe_vcs.py",
    "core/protected_paths.py",
    # Organs without which the agent does not start, and its doctrine. `core/loop.py`, `core/model_usage.py` and `docs/COGNITIVE_CORE.md` are closed only to the self-build producer (CRITICAL_DENY); the diagnosis lane may touch them.
    "main.py", "core/autonomous_runtime.py",
    "AGENT_DOCTRINE.md", "docs/AGENT_DOCTRINE.md",
    # The adopting burn-in, its sandbox, launch and experience memory.
    "core/burn_in_supervisor.py", "core/burn_in_sandbox.py",
    "scripts/burn_in_supervisor.py", "scripts/autonomous_repair.py",
    "scripts/install_daemon.ps1",
    "state/burn_in_head.json", "state/burn_in_offers.jsonl",
    "state/burn_in_adoptions.jsonl", "config/burn_in_sandbox.json",
    # What git treats as junk: editing it would open `git add -A` to experience memory.
    ".gitignore",
    # Operator brake files.
    *CONTROL_RELPATHS,
})

#: Whole trees: compared by prefix.
PROTECTED_TREES: tuple[str, ...] = ("config/", ".github/", ".git/", "secrets/", ".venv/")


def path_key(path: object) -> str | None:
    """Root-relative path in one form for comparison; None if empty, absolute or escaping the root."""
    raw = str(path).strip().strip('"').replace("\\", "/")
    # A colon means a drive or an NTFS stream (`file.py::$DATA`): neither is a path inside the root.
    if not raw or raw.startswith("/") or ":" in raw:
        return None
    parts: list[str] = []
    for seg in raw.split("/"):
        if seg == "..":
            if not parts:
                return None
            parts.pop()
            continue
        # Windows drops trailing dots and spaces from names: `x.py.` is the same file.
        seg = seg.rstrip(" .")
        if seg:
            parts.append(seg)
    # Case is always folded: an extra refusal is cheaper than an edit behind the fence.
    return "/".join(parts).casefold() or None


_FILE_KEYS: frozenset[str] = frozenset(k for k in map(path_key, PROTECTED_FILES) if k)
_TREE_KEYS: tuple[str, ...] = tuple(t.casefold() for t in PROTECTED_TREES)


def is_protected(path: object) -> bool:
    """Whether the path is forbidden to self-repair; a path escaping the root is forbidden too."""
    if not str(path).strip():
        return False
    key = path_key(path)
    if key is None:
        return True
    return key in _FILE_KEYS or (key + "/").startswith(_TREE_KEYS)
