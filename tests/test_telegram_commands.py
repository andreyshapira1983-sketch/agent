import importlib

from src.communication.telegram_commands import get_agent_status


def test_get_agent_status_includes_quality_metrics(monkeypatch):
    metrics_mod = importlib.import_module("src.monitoring.metrics")

    def fake_get_metrics():
        return {
            "calls": 10,
            "errors": 2,
            "successes": 8,
            "last_duration_sec": 1.23,
            "quality": {
                "tasks_solved": 5,
                "accepted_patches": 2,
                "successful_repairs": 1,
                "failed_repairs": 3,
                "test_runs_total": 4,
                "test_runs_passed": 3,
                "test_pass_ratio": 0.75,
            },
        }

    monkeypatch.setattr(metrics_mod, "get_metrics", fake_get_metrics)

    text = get_agent_status()

    assert "Качество:" in text
    assert "решено задач=5" in text
    assert "принято патчей=2" in text
    assert "успешных ремонтов=1" in text
    assert "проваленных ремонтов=3" in text
    assert "pass_ratio=0.75" in text