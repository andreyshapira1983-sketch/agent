"""One-time migration: collapse detector-echo rows in data/self_improvement_issues.jsonl.

Replays the current rules: drops rows minted only by unreliable detector signals and
merges rows that now share a failure fingerprint (the merged row stays open if any was).
Dry run by default; --apply writes after a timestamped backup; --workspace sets the root.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core.self_build_memory import _UNRELIABLE_DETECTOR_SIGNALS  # noqa: E402
from core.self_improvement_issues import (  # noqa: E402
    SelfImprovementIssue,
    SelfImprovementIssueRegistry,
    _detector_signals,
    failure_fingerprint,
    issue_from_failure,
)
from core.state_integrity import rewrite_state_jsonl  # noqa: E402


def _stamp(value: str) -> str:
    return value or "1970-01-01T00:00:00+00:00"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--workspace", type=Path, default=REPO)
    args = parser.parse_args(argv)

    path = args.workspace / "data" / "self_improvement_issues.jsonl"
    registry = SelfImprovementIssueRegistry(path)
    issues = registry.list()
    print("Detector-echo collapse (dry-run)" if not args.apply
          else "Detector-echo collapse (APPLY)")
    print(f"  registry: {path}")
    print(f"  issues before: {len(issues)}")

    dropped_unreliable = 0
    merged: dict[str, SelfImprovementIssue] = {}
    for issue in issues:
        text = issue.related_error_text or (issue.evidence[0] if issue.evidence else "")
        signals = _detector_signals(text)
        if signals:
            reliable = [s for s in signals if s not in _UNRELIABLE_DETECTOR_SIGNALS]
            if not reliable:
                dropped_unreliable += 1
                continue
            # Rewrite to the current writer's text (reliable signals only) so an old
            # mixed-signal row merges with a new row of the same class.
            rest = text.split(":", 1)[1] if ":" in text else ""
            text = f"detectors {', '.join(reliable)}:{rest}"
        fp = failure_fingerprint(text) if text else issue.fingerprint
        if fp not in merged:
            fresh = issue_from_failure(text, issue.first_seen) if text else issue
            merged[fp] = SelfImprovementIssue(
                fingerprint=fp,
                title=fresh.title,
                action=fresh.action,
                status=issue.status,
                first_seen=issue.first_seen,
                last_seen=issue.last_seen,
                evidence=issue.evidence[-8:],
                related_files=fresh.related_files or issue.related_files,
                related_error_text=issue.related_error_text,
                suggested_next_action=fresh.suggested_next_action,
            )
            continue
        cur = merged[fp]
        status = "open" if "open" in (cur.status, issue.status) else cur.status
        merged[fp] = SelfImprovementIssue(
            fingerprint=fp,
            title=cur.title,
            action=cur.action,
            status=status,  # type: ignore[arg-type]
            first_seen=min(cur.first_seen, issue.first_seen, key=_stamp),
            last_seen=max(cur.last_seen, issue.last_seen, key=_stamp),
            evidence=tuple(dict.fromkeys((*cur.evidence, *issue.evidence)))[-8:],
            related_files=tuple(dict.fromkeys((*cur.related_files, *issue.related_files)))[:6],
            related_error_text=cur.related_error_text or issue.related_error_text,
            suggested_next_action=cur.suggested_next_action,
        )

    out = sorted(merged.values(), key=lambda i: i.last_seen, reverse=True)
    print(f"  dropped (unreliable-sensor-only rows): {dropped_unreliable}")
    print(f"  issues after merge: {len(out)}")
    from collections import Counter
    print(f"  actions after: {dict(Counter(i.action for i in out))}")

    if not args.apply:
        print("\n  dry-run: nothing written. Re-run with --apply.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_suffix(f".jsonl.pre-echo-collapse-{stamp}.bak")
    shutil.copy2(path, backup)
    print(f"\n  backup: {backup.name}")
    rewrite_state_jsonl(path, [issue.to_dict() for issue in out])
    print(f"  written: {len(out)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
