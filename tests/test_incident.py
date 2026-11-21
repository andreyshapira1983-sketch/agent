"""Tests for core/incident.py (§7 Incident Handling skeleton / B-04).

Coverage targets:
  - Incident record: field defaults, validation, critical/high force escalation
  - Serialization round-trip (to_dict/from_dict)
  - summarise_incidents digest (pure)
  - IncidentLog: open, update, persistence round-trip, resolve guards
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.incident import (
    Incident,
    IncidentLog,
    summarise_incidents,
)

# ---------------------------------------------------------------------------
# Incident record
# ---------------------------------------------------------------------------

class TestIncidentRecord:
    def test_invalid_severity_rejected(self) -> None:
        with pytest.raises(ValueError):
            Incident(severity="boom", trigger="x")  # type: ignore[arg-type]

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValueError):
            Incident(severity="low", trigger="x", status="weird")  # type: ignore[arg-type]

    def test_empty_trigger_rejected(self) -> None:
        with pytest.raises(ValueError):
            Incident(severity="low", trigger="   ")


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_from_dict_rejects_bad_severity(self) -> None:
        with pytest.raises(ValueError):
            Incident.from_dict({"severity": "nope", "trigger": "x"})


# ---------------------------------------------------------------------------
# summarise_incidents (pure)
# ---------------------------------------------------------------------------

class TestSummary:
    def test_empty(self) -> None:
        assert summarise_incidents([]) == "incidents: none recorded"

    def test_aggregate(self) -> None:
        incidents = [
            Incident(severity="critical", trigger="a", status="escalated"),
            Incident(severity="high", trigger="b", status="open"),
            Incident(severity="medium", trigger="c", status="resolved",
                     postmortem_note="done"),
            Incident(severity="low", trigger="d", status="resolved"),
        ]
        out = summarise_incidents(incidents)
        assert "4 total" in out
        assert "by_severity: critical=1 high=1 medium=1 low=1" in out
        # critical(escalated) + high(open) still owe a human
        assert "needs_human=2" in out


# ---------------------------------------------------------------------------
# IncidentLog store
# ---------------------------------------------------------------------------

class TestIncidentLog:
    def test_open_and_list(self, tmp_path: Path) -> None:
        log = IncidentLog(path=tmp_path / "incidents.jsonl")
        inc = log.open_incident(
            severity="high",
            trigger="circuit_breaker_open",
            affected_module="core.loop",
            containment_action="halted outbound tool calls",
        )
        assert inc.human_escalation is True
        assert len(log.open_incidents()) == 1
        assert len(log.needing_human()) == 1

    def test_persistence_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "incidents.jsonl"
        log = IncidentLog(path=path)
        log.open_incident(severity="medium", trigger="tool_error")
        # Reload from disk into a fresh store
        reloaded = IncidentLog(path=path)
        assert len(reloaded.incidents) == 1
        assert reloaded.incidents[0].trigger == "tool_error"


    def test_update_missing_returns_none(self, tmp_path: Path) -> None:
        log = IncidentLog(path=tmp_path / "incidents.jsonl")
        assert log.update("inc_does_not_exist", status="resolved") is None

    def test_cannot_resolve_critical_without_note(self, tmp_path: Path) -> None:
        log = IncidentLog(path=tmp_path / "incidents.jsonl")
        inc = log.open_incident(severity="critical", trigger="state_corruption")
        with pytest.raises(ValueError):
            log.update(inc.id, status="resolved")
        # With a note it resolves fine.
        resolved = log.update(inc.id, status="resolved", postmortem_note="restored backup")
        assert resolved is not None
        assert resolved.status == "resolved"
        assert resolved.needs_human is False

    def test_corrupt_lines_skipped_on_load(self, tmp_path: Path) -> None:
        path = tmp_path / "incidents.jsonl"
        log = IncidentLog(path=path)
        log.open_incident(severity="low", trigger="ok")
        # Append a malformed row directly.
        with path.open("a", encoding="utf-8") as fh:
            fh.write("not json\n")
            fh.write('{"severity": "bogus", "trigger": "x"}\n')
        reloaded = IncidentLog(path=path)
        # Only the one valid incident survives.
        assert len(reloaded.incidents) == 1

    def test_summary_via_store(self, tmp_path: Path) -> None:
        log = IncidentLog(path=tmp_path / "incidents.jsonl")
        log.open_incident(severity="low", trigger="a")
        assert "1 total" in log.summary()

    def test_no_path_is_in_memory_only(self) -> None:
        log = IncidentLog()
        log.open_incident(severity="low", trigger="ephemeral")
        assert len(log.incidents) == 1
