"""Hard rules learned from self-build rollbacks.

A rollback by itself does not make the agent smarter: the lesson text lands in
episodic memory and *may* steer the next Builder prompt, but nothing ENFORCES
it. This module turns specific, machine-readable failure causes into durable
HARD RULES that the Critic checks deterministically on every later attempt.

First rule kind — ``keep_importable``: a rollback whose reason contains

    ImportError: cannot import name 'X' from 'core.verifier'

becomes the rule "symbol X must remain importable from core/verifier.py".
On the next produce run for that target the Critic re-parses the proposed
content and vetoes BEFORE apply if the symbol is neither defined nor
re-exported — no LLM judgement involved, so the same rollback can never
happen twice for the same symbol.

Storage is one JSONL file (``data/self_build_rules.jsonl``) next to the other
agent state stores; loading and recording are best-effort and never raise
into the caller.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

RULES_FILENAME = "self_build_rules.jsonl"

# "cannot import name 'X' from 'core.verifier'" — the canonical CPython
# ImportError wording; the optional trailing "(path)" is ignored.
_IMPORT_ERROR_RE = re.compile(
    r"cannot import name '(?P<symbol>[^']+)' from '(?P<module>[^']+)'"
)


@dataclass
class Rule:
    """One enforceable lesson: symbol must stay importable from target."""

    target: str  # repo-relative path, forward slashes
    kind: str  # currently always "keep_importable"
    symbol: str
    source: str  # where the rule came from (proposal id / reason snippet)
    created_at: str = ""

    def key(self) -> tuple[str, str, str]:
        return (self.target, self.kind, self.symbol)


def _module_to_path(module: str) -> str:
    """core.verifier -> core/verifier.py (best-effort dotted-to-path)."""
    mod = module.strip()
    if not mod or "/" in mod or "\\" in mod:
        return mod.replace("\\", "/")
    return mod.replace(".", "/") + ".py"


def extract_rules_from_apply_result(result: dict) -> list[Rule]:
    """Parse a self-apply run result into zero or more hard rules.

    Only ``rolled_back`` outcomes produce rules; every distinct
    ``cannot import name 'X' from 'M'`` occurrence in the reason yields one
    ``keep_importable`` rule for the module's file path.
    """
    if str(result.get("status") or "") != "rolled_back":
        return []
    reason = str(result.get("reason") or "")
    proposal_id = str(result.get("proposal_id") or "")
    now = datetime.now(timezone.utc).isoformat()
    rules: list[Rule] = []
    seen: set[tuple[str, str, str]] = set()
    for match in _IMPORT_ERROR_RE.finditer(reason):
        symbol = match.group("symbol")
        target = _module_to_path(match.group("module"))
        rule = Rule(
            target=target,
            kind="keep_importable",
            symbol=symbol,
            source=f"rollback {proposal_id or '?'}: ImportError".strip(),
            created_at=now,
        )
        if rule.key() not in seen:
            seen.add(rule.key())
            rules.append(rule)
    return rules


class RuleStore:
    """Append-only JSONL store of hard rules, deduplicated on load."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[Rule]:
        rules: list[Rule] = []
        seen: set[tuple[str, str, str]] = set()
        try:
            if not self.path.exists():
                return []
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    rule = Rule(
                        target=str(raw.get("target") or ""),
                        kind=str(raw.get("kind") or ""),
                        symbol=str(raw.get("symbol") or ""),
                        source=str(raw.get("source") or ""),
                        created_at=str(raw.get("created_at") or ""),
                    )
                except (ValueError, TypeError):
                    continue
                if not rule.target or not rule.symbol or not rule.kind:
                    continue
                if rule.key() in seen:
                    continue
                seen.add(rule.key())
                rules.append(rule)
        except OSError:
            return []
        return rules

    def add(self, rule: Rule) -> bool:
        """Persist one rule; returns False for duplicates or write failures."""
        try:
            existing = {r.key() for r in self.load()}
            if rule.key() in existing:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(rule), ensure_ascii=False) + "\n")
            return True
        except OSError:
            return False

    def rules_for(self, target: str) -> list[Rule]:
        norm = target.replace("\\", "/").strip()
        return [r for r in self.load() if r.target == norm]


def default_rules_path(workspace: Path) -> Path:
    return workspace / "data" / RULES_FILENAME


def record_rules_from_result(workspace: Path, result: dict) -> int:
    """Extract and persist rules from one apply result; returns count added.

    Best-effort: any failure returns 0 and never raises into the caller.
    """
    try:
        rules = extract_rules_from_apply_result(result)
        if not rules:
            return 0
        store = RuleStore(default_rules_path(workspace))
        return sum(1 for rule in rules if store.add(rule))
    except Exception:  # noqa: BLE001 — rule recording must never break the caller
        return 0
