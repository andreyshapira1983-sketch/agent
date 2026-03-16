import importlib

from src.communication.telegram_commands import get_agent_status, get_quality_status, reset_quality_status


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
                "recent_history": [{"event_type": "accepted_patch", "status": "ok"}],
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


def test_get_quality_status_includes_history(monkeypatch):
    metrics_mod = importlib.import_module("src.monitoring.metrics")

    def fake_get_metrics():
        return {
            "quality": {
                "tasks_solved": 2,
                "accepted_patches": 1,
                "successful_repairs": 1,
                "failed_repairs": 0,
                "test_runs_total": 2,
                "test_runs_passed": 2,
                "test_pass_ratio": 1.0,
                "recent_history": [
                    {
                        "event_type": "accepted_patch",
                        "status": "ok",
                        "target_path": "src/a.py",
                        "patch_id": "p1",
                    },
                    {
                        "event_type": "repair_attempt",
                        "status": "failed",
                        "target_path": "src/b.py",
                        "patch_id": "p2",
                        "note": "sandbox_validation_failed",
                    },
                ],
            }
        }

    monkeypatch.setattr(metrics_mod, "get_metrics", fake_get_metrics)

    text = get_quality_status()

    assert "Quality:" in text
    assert "Принято патчей: 1" in text
    assert "accepted_patch: ok; path=src/a.py; patch=p1" in text
    assert "repair_attempt: failed; path=src/b.py; patch=p2; note=sandbox_validation_failed" in text


def test_reset_quality_status_calls_metrics_reset(monkeypatch):
    metrics_mod = importlib.import_module("src.monitoring.metrics")
    called = {"reset": False}

    def fake_reset_quality():
        called["reset"] = True

    monkeypatch.setattr(metrics_mod.metrics, "reset_quality", fake_reset_quality)

    text = reset_quality_status()

    assert called["reset"] is True
    assert "сброшены" in text.lower()