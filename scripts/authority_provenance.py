"""Show which commit introduced each agent authority and whether a human decision is named.

Reads git history and docs/audit/MASTER_ISSUE_REGISTRY.md; run it from the repository root.
Options: --json prints the raw rows.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

#: Authority -> token whose first appearance in git history marks the authority itself.
AUTHORITIES: dict[str, str] = {
    "budget_ledger": "BudgetKillSwitch",
    "approval_inbox": "effects_approved",
    "standing_grant_usage": "standing_runs_today",
    "runtime_tasks": "_RUNNABLE_TASK_KINDS",
    "runtime_schedules": "SchedulerStore",
    "model_usage": "substitute_model_with_reason",
    "tool_receipts": "matched_evidence_lacks_receipt",
    "persistent_memory": "_INDEPENDENT_MEMORY_ORIGINS",
    "memory_writes": "MemoryWriteRegistry",
    "procedural_memory": "_procedure_status_for",
    "self_improvement_issues": "SelfImprovementIssue",
    "campaign_ledger": "run_paced_campaign",
    "episodic_memory": "measured_outcomes",
    "source_registry": "_apply_staleness",
}

#: The initial commit added everything at once, so it carries no provenance.
_LUMP_SUBJECT = "Initial commit"

#: How a named human decision is phrased in the registry.
_HUMAN_DECISION = re.compile(
    r"(operator ruling|решени\w* оператора|слово оператора|оператор реши|"
    r"выбор оператора|operator decision)",
    re.IGNORECASE,
)

_REGISTRY = pathlib.Path("docs/audit/MASTER_ISSUE_REGISTRY.md")

#: Machine matches rejected by hand review: citing an entry that names a human
#: decision does not mean the decision was about this authority.
_HAND_REJECTED: dict[str, str] = {
    "procedural_memory": (
        "вводящий коммит ссылается на MIR-044 и MIR-057, но решение оператора "
        "от 2026-07-19 там про отказ от «варианта A» для консолидации памяти, "
        "а не про права процедурной памяти (выборка 2026-08-25)"
    ),
}


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False,
        encoding="utf-8", errors="replace",
    )
    return out.stdout or ""


def introducing_commit(token: str) -> tuple[str, str, str] | None:
    """(hash, date, subject) of the commit that first added the token, or None."""
    lines = [
        line for line in
        _git("log", "-S", token, "--format=%h|%ad|%s", "--date=short").splitlines()
        if line.strip()
    ]
    if not lines:
        return None
    h, date, subject = lines[-1].split("|", 2)
    return h, date, subject


def _agent_co_authored(commit: str) -> bool:
    return "claude" in _git("log", "-1", "--format=%b", commit).lower()


def _entries_naming_a_human_decision() -> set[str]:
    """Registry entry ids whose text names a human decision."""
    if not _REGISTRY.is_file():
        return set()
    text = _REGISTRY.read_text(encoding="utf-8")
    found: set[str] = set()
    for part in re.split(r"\n(?=### MIR-)", text):
        m = re.match(r"### (MIR-\d+)", part)
        if m and _HUMAN_DECISION.search(part):
            found.add(m.group(1))
    return found


def survey() -> list[dict[str, object]]:
    human_entries = _entries_naming_a_human_decision()
    rows: list[dict[str, object]] = []
    for store, token in AUTHORITIES.items():
        found = introducing_commit(token)
        row: dict[str, object] = {"store": store, "token": token}
        if found is None:
            row.update(origin="токен не найден", commit="", date="", subject="")
        else:
            commit, date, subject = found
            # Search the whole commit message, not only the subject.
            full = _git("log", "-1", "--format=%s%n%b", commit)
            cited = sorted(set(re.findall(r"MIR-\d+", full)))
            row.update(
                commit=commit, date=date, subject=subject,
                origin="начальный ком" if subject.strip() == _LUMP_SUBJECT else "коммит",
                agent_co_authored=_agent_co_authored(commit),
                cites=cited,
                human_decision_named=(
                    any(c in human_entries for c in cited)
                    and store not in _HAND_REJECTED
                ),
                hand_rejected=_HAND_REJECTED.get(store, ""),
            )
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rows = survey()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    lump = sum(1 for r in rows if r.get("origin") == "начальный ком")
    named = sum(1 for r in rows if r.get("human_decision_named"))
    for r in rows:
        mark = "куском" if r.get("origin") == "начальный ком" else str(r.get("date"))
        who = "агент" if r.get("agent_co_authored") else "—"
        print(f"{r['store']:24} {mark:12} соавтор: {who:6} "
              f"решение человека названо: {'да' if r.get('human_decision_named') else 'нет'}")
    print()
    print(f"без прослеживаемого происхождения: {lump} из {len(rows)}")
    print(f"с НАЗВАННЫМ решением человека:     {named} из {len(rows)}")
    for r in rows:
        if r.get("hand_rejected"):
            print(f"  отвергнуто выборкой — {r['store']}: {r['hand_rejected']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
