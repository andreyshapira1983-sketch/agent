"""Tests for the operator scripts/ — they were at 0% coverage.

These are deterministic, no-LLM, mostly no-network helper CLIs. We pin:
  - log_finding_baseline.py: the hard R1/R2/R3 rule engine + rendering,
  - first_live_probe.py: read-only signal gathering + report rendering +
    the no-clobber boundary,
  - generate_sbom.py: write + --check (in sync / out of sync / missing),
  - audit_release.py: a smoke run on the real repo (read-only, deterministic).
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


# ============================================================
# scripts/log_finding_baseline.py
# ============================================================

import scripts.log_finding_baseline as lfb


class TestLogFindingRules:
    def test_r1_broken_repair_safety_net_wins(self):
        records = [
            {"event": "tick_complete", "result_status": "failed"},  # would be R3
            {"event": "repair_attempt", "repair_proposed": False,
             "reason": "exception: boom", "ts": "t1"},
            {"event": "other", "error": "x"},  # would be R2
        ]
        finding = lfb.find_problem(records)
        assert finding["rule"] == "R1"
        assert finding["severity"] == "high"
        assert finding["log_line"] == 2
        assert "boom" in finding["evidence"]

    def test_repair_attempt_that_proposed_a_fix_is_not_r1(self):
        records = [
            {"event": "repair_attempt", "repair_proposed": True, "reason": "exception: x"},
            {"event": "node", "error": "real error"},
        ]
        finding = lfb.find_problem(records)
        assert finding["rule"] == "R2"

    def test_repair_attempt_reason_without_exception_prefix_is_not_r1(self):
        records = [
            {"event": "repair_attempt", "repair_proposed": False, "reason": "budget exhausted"},
        ]
        finding = lfb.find_problem(records)
        assert finding["rule"] == "none"

    def test_r2_explicit_error(self):
        records = [{"event": "tool_result", "error": "disk full", "ts": "t9"}]
        finding = lfb.find_problem(records)
        assert finding["rule"] == "R2"
        assert finding["severity"] == "high"
        assert finding["evidence"] == "disk full"

    def test_r3_failed_tick(self):
        records = [{"event": "tick_complete", "tests_health": "fail",
                    "tests_result": {"passed": 1, "failed": 2}}]
        finding = lfb.find_problem(records)
        assert finding["rule"] == "R3"
        assert finding["severity"] == "medium"
        assert "failed" in finding["evidence"]

    def test_no_problem(self):
        records = [{"event": "tick_complete", "result_status": "done", "tests_health": "pass"}]
        finding = lfb.find_problem(records)
        assert finding["rule"] == "none"
        assert finding["severity"] == "none"
        assert finding["log_line"] is None


class TestLogFindingIO:
    def test_load_records_missing_file_returns_empty(self, tmp_path: Path):
        assert lfb._load_records(tmp_path / "nope.jsonl") == []

    def test_load_records_skips_blank_and_malformed(self, tmp_path: Path):
        p = tmp_path / "log.jsonl"
        p.write_text(
            '{"event":"a"}\n\n   \nnot-json\n{"event":"b"}\n', encoding="utf-8"
        )
        records = lfb._load_records(p)
        assert [r["event"] for r in records] == ["a", "b"]

    def test_summarise_counts_events(self):
        s = lfb._summarise([{"event": "x"}, {"event": "x"}, {"event": "y"}, {}])
        assert s["total_records"] == 4
        assert s["event_counts"] == {"x": 2, "y": 1, "?": 1}

    def test_build_finding_uses_workspace_log(self, tmp_path: Path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "daemon_tick.jsonl").write_text(
            '{"event":"node","error":"kaboom"}\n', encoding="utf-8"
        )
        monkeypatch.setattr(lfb, "WORKSPACE", tmp_path)
        result = lfb.build_finding()
        assert result["finding"]["rule"] == "R2"
        assert result["summary"]["total_records"] == 1

    def test_render_contains_finding_details(self):
        result = {
            "log": "logs/daemon_tick.jsonl",
            "summary": {"total_records": 2, "event_counts": {"a": 2}},
            "finding": {
                "rule": "R2", "severity": "high", "title": "Tick recorded an error",
                "log_line": 1, "log_ts": "t1", "evidence": "disk full",
                "impact": "non-null error",
            },
        }
        text = lfb._render(result)
        assert "rule R2" in text
        assert "disk full" in text
        assert "log line: 1" in text

    def test_main_json_mode(self, tmp_path: Path, monkeypatch, capsys):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "daemon_tick.jsonl").write_text(
            '{"event":"tick_complete","result_status":"done","tests_health":"pass"}\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(lfb, "WORKSPACE", tmp_path)
        rc = lfb.main(["--json"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["finding"]["rule"] == "none"

    def test_main_text_mode(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.setattr(lfb, "WORKSPACE", tmp_path)  # no logs dir → empty
        rc = lfb.main([])
        assert rc == 0
        assert "DETERMINISTIC LOG-FINDING BASELINE" in capsys.readouterr().out


# ============================================================
# scripts/first_live_probe.py
# ============================================================

import scripts.first_live_probe as flp


class TestFormatAge:
    def test_none_age(self):
        assert "unknown" in flp._format_age(None)

    def test_seconds(self):
        assert flp._format_age(30) == "30s"

    def test_minutes(self):
        assert flp._format_age(600).endswith("min")

    def test_hours(self):
        assert flp._format_age(7200).endswith("h")


class TestFirstLiveProbe:
    def test_gather_signals_on_empty_workspace_reports_missing_daemon(self, tmp_path: Path):
        signals = flp._gather_signals(tmp_path)
        assert signals["daemon_status"] == "missing"
        assert signals["age"] is None
        assert signals["action"] is not None

    def test_render_contains_core_sections(self, tmp_path: Path):
        signals = flp._gather_signals(tmp_path)
        text = flp._render(signals)
        assert "# First live probe" in text
        assert "Best next action" in text
        assert "Approval inbox triage summary" in text

    def test_main_print_only_writes_nothing(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.setattr(flp, "WORKSPACE", tmp_path)
        rc = flp.main(["--print"])
        assert rc == 0
        assert "# First live probe" in capsys.readouterr().out
        assert not (tmp_path / "reports" / "first_live_probe.md").exists()

    def test_main_writes_report(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.setattr(flp, "WORKSPACE", tmp_path)
        rc = flp.main([])
        assert rc == 0
        report = tmp_path / "reports" / "first_live_probe.md"
        assert report.exists()
        assert "# First live probe" in report.read_text(encoding="utf-8")

    def test_main_refuses_to_clobber_without_force(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(flp, "WORKSPACE", tmp_path)
        assert flp.main([]) == 0
        # Second run without --force must refuse.
        assert flp.main([]) == 2

    def test_main_force_overwrites(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(flp, "WORKSPACE", tmp_path)
        assert flp.main([]) == 0
        assert flp.main(["--force"]) == 0


# ============================================================
# scripts/generate_sbom.py
# ============================================================

import scripts.generate_sbom as gs


_LOCK = (
    "# locked deps\n"
    "certifi==2024.2.2 \\\n"
    "    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
    "idna==3.6 \\\n"
    "    --hash=sha256:1111111111111111111111111111111111111111111111111111111111111111\n"
)


class TestGenerateSbom:
    def _setup(self, tmp_path: Path, monkeypatch) -> Path:
        (tmp_path / "requirements.lock").write_text(_LOCK, encoding="utf-8")
        monkeypatch.setattr(gs, "ROOT", tmp_path)
        return tmp_path

    def test_write_then_check_in_sync(self, tmp_path: Path, monkeypatch, capsys):
        self._setup(tmp_path, monkeypatch)
        assert gs.main([]) == 0
        assert (tmp_path / "sbom.cdx.json").exists()
        capsys.readouterr()
        # A check immediately after a write must report in-sync.
        assert gs.main(["--check"]) == 0
        assert "in sync" in capsys.readouterr().out

    def test_check_out_of_sync(self, tmp_path: Path, monkeypatch, capsys):
        self._setup(tmp_path, monkeypatch)
        assert gs.main([]) == 0
        (tmp_path / "sbom.cdx.json").write_text("{}", encoding="utf-8")
        assert gs.main(["--check"]) == 1
        assert "out of sync" in capsys.readouterr().err

    def test_check_missing_output(self, tmp_path: Path, monkeypatch, capsys):
        self._setup(tmp_path, monkeypatch)
        # No prior write → output missing.
        assert gs.main(["--check"]) == 1
        assert "missing" in capsys.readouterr().err

    def test_written_sbom_is_valid_cyclonedx(self, tmp_path: Path, monkeypatch, capsys):
        self._setup(tmp_path, monkeypatch)
        gs.main([])
        capsys.readouterr()
        payload = json.loads((tmp_path / "sbom.cdx.json").read_text(encoding="utf-8"))
        assert payload["bomFormat"] == "CycloneDX"
        names = {c["name"] for c in payload["components"]}
        assert {"certifi", "idna"} <= names


# ============================================================
# scripts/audit_release.py — smoke run on the real repo (read-only)
# ============================================================

class TestAuditRelease:
    def test_main_runs_and_returns_pass_or_fail(self, capsys):
        import scripts.audit_release as ar
        importlib.reload(ar)
        rc = ar.main()
        assert rc in (0, 1)
        payload = json.loads(capsys.readouterr().out)
        assert "release_hygiene" in payload
        assert "supply_chain" in payload
