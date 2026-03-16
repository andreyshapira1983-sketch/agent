from src.monitoring.metrics import Metrics, get_metrics, metrics


def test_metrics_class(tmp_path):
    m = Metrics(storage_path=tmp_path / "quality_metrics.json")
    m.record_call()
    m.record_call()
    m.record_success()
    m.record_error()
    m.log_time(0.5)
    m.record_task_solved()
    m.record_patch_accepted(patch_id="p1", target_path="src/a.py")
    m.record_repair_attempt(success=True, patch_id="p2", target_path="src/a.py", note="ok")
    m.record_repair_attempt(success=False, patch_id="p3", target_path="src/b.py", note="failed")
    m.record_test_run(passed=True)
    m.record_test_run(passed=False)

    summary = m.get_metrics_summary()
    assert summary["calls"] == 2  # nosec B101
    assert summary["errors"] == 1  # nosec B101
    assert summary["successes"] == 1  # nosec B101
    assert summary["last_duration_sec"] == 0.5  # nosec B101
    assert summary["quality"]["tasks_solved"] == 1  # nosec B101
    assert summary["quality"]["accepted_patches"] == 1  # nosec B101
    assert summary["quality"]["successful_repairs"] == 1  # nosec B101
    assert summary["quality"]["failed_repairs"] == 1  # nosec B101
    assert summary["quality"]["test_runs_total"] == 2  # nosec B101
    assert summary["quality"]["test_runs_passed"] == 1  # nosec B101
    assert summary["quality"]["test_pass_ratio"] == 0.5  # nosec B101
    assert len(summary["quality"]["recent_history"]) == 3  # nosec B101


def test_get_metrics():
    summary = get_metrics()
    assert "calls" in summary  # nosec B101
    assert "errors" in summary  # nosec B101
    assert "successes" in summary  # nosec B101
    assert "quality" in summary  # nosec B101
    assert summary == metrics.get_metrics_summary()  # nosec B101


def test_tool_times_recorded():
    import src.tools  # noqa: F401
    from src.tools.orchestrator import run_tool
    run_tool("get_current_time")
    summary = get_metrics()
    assert "tool_times" in summary  # nosec B101
    assert "get_current_time" in summary["tool_times"]  # nosec B101
    t = summary["tool_times"]["get_current_time"]
    assert "last_sec" in t and "avg_sec" in t and "count" in t  # nosec B101


def test_analyze_tool_performance():
    from src.monitoring.metrics import analyze_tool_performance
    out = analyze_tool_performance(top_n=5)
    assert isinstance(out, str)
    assert "performance" in out.lower() or "no tool" in out.lower()


def test_check_performance_alerts():
    from src.monitoring.metrics import check_performance_alerts
    out = check_performance_alerts()
    assert isinstance(out, str)
    assert "threshold" in out.lower() or "tool" in out.lower() or "no " in out.lower()


def test_export_performance_summary():
    import tempfile
    from pathlib import Path
    from src.monitoring.metrics import export_performance_summary
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "perf.json"
        out = export_performance_summary(file_path=str(path))
        assert "Exported" in out or path.name in out
        assert path.exists()
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "timestamp_iso" in data and "tool_times" in data


def test_quality_metrics_persist_between_instances(tmp_path):
    storage = tmp_path / "quality_metrics.json"

    m1 = Metrics(storage_path=storage)
    m1.record_task_solved()
    m1.record_patch_accepted(patch_id="p1", target_path="src/x.py")
    m1.record_repair_attempt(success=True, patch_id="p2", target_path="src/y.py", note="accepted")
    m1.record_repair_attempt(success=False, patch_id="p3", target_path="src/z.py", note="rejected")
    m1.record_test_run(passed=True)
    m1.record_test_run(passed=False)

    m2 = Metrics(storage_path=storage)
    summary = m2.get_metrics_summary()

    assert summary["quality"]["tasks_solved"] == 1  # nosec B101
    assert summary["quality"]["accepted_patches"] == 1  # nosec B101
    assert summary["quality"]["successful_repairs"] == 1  # nosec B101
    assert summary["quality"]["failed_repairs"] == 1  # nosec B101
    assert summary["quality"]["test_runs_total"] == 2  # nosec B101
    assert summary["quality"]["test_runs_passed"] == 1  # nosec B101
    assert summary["quality"]["test_pass_ratio"] == 0.5  # nosec B101
    assert len(summary["quality"]["recent_history"]) == 3  # nosec B101


def test_reset_quality_clears_counters_and_history(tmp_path):
    storage = tmp_path / "quality_metrics.json"
    m = Metrics(storage_path=storage)
    m.record_patch_accepted(patch_id="p1", target_path="src/x.py")
    m.record_repair_attempt(success=False, patch_id="p2", target_path="src/y.py")

    m.reset_quality()

    summary = m.get_metrics_summary()
    assert summary["quality"]["accepted_patches"] == 0  # nosec B101
    assert summary["quality"]["failed_repairs"] == 0  # nosec B101
    assert summary["quality"]["recent_history"] == []  # nosec B101


