"""The stop event carries the approval inbox as an inventory, not the proposals' bodies.

Goal-first run 2026-09-19: every pass on a goal logged the whole inbox with each
proposal's file contents (text and base64) — ~2 MB per event, 8.4 of 9.5 MB of
the trace in six minutes; the live panel reading the trace tail went blind."""
from __future__ import annotations

import json

from core.autonomous_runtime_types import AutonomousRunReport


def test_the_log_form_drops_proposal_bodies_but_keeps_the_report() -> None:
    body = {"files": [{"path": "core/x.py", "content": "x" * 200_000, "content_b64": "eA==" * 50_000}]}
    item = {"id": "ain_1", "operation": "self_apply_lane.run", "status": "pending", "summary": "split x",
            "risk": "medium", "created_at": "2026-09-19T11:04:38+00:00", "payload": body}
    report = AutonomousRunReport(status="completed", dry_run=False, goal="g", tasks=[], budget={},
                                 circuit={}, approvals={"total": 1, "pending": 1, "items": [item]})
    logged = report.for_log()
    assert logged["approvals"]["items"] == [{k: item[k] for k in
                                            ("id", "operation", "status", "summary", "risk", "created_at")}]
    assert logged["approvals"]["total"] == 1 and len(json.dumps(logged)) < 2_000
    assert report.to_dict()["approvals"]["items"][0]["payload"] is body
