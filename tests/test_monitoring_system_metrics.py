from src.monitoring import system_metrics


def test_snapshot_returns_unavailable_when_collector_absent(monkeypatch):
    monkeypatch.setattr(system_metrics, "_collect_with_psutil", lambda top_n: None)
    snap = system_metrics.get_system_metrics_snapshot(force_refresh=True, ttl_sec=0, top_n=3)
    assert snap["ok"] is False
    assert snap["source"] == "unavailable"
    assert "timestamp_utc" in snap
    assert "cpu_percent" in snap
    assert "ram_percent" in snap


def test_snapshot_cache_is_used(monkeypatch):
    calls = {"n": 0}

    def _fake_collect(top_n):
        assert top_n == 1
        calls["n"] += 1
        return {
            "ok": True,
            "source": "psutil",
            "timestamp_utc": "2026-03-15T00:00:00+00:00",
            "cpu_percent": 12.5,
            "ram_percent": 34.5,
            "top_processes": [{"pid": 1, "name": "x", "cpu_percent": 1.0, "rss_mb": 10.0}],
        }

    monkeypatch.setattr(system_metrics, "_collect_with_psutil", _fake_collect)
    a = system_metrics.get_system_metrics_snapshot(force_refresh=True, ttl_sec=10, top_n=1)
    b = system_metrics.get_system_metrics_snapshot(force_refresh=False, ttl_sec=10, top_n=1)

    assert a["source"] == "psutil"
    assert b["source"] == "psutil"
    assert calls["n"] == 1
