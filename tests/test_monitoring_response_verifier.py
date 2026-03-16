from src.monitoring import response_verifier


def test_verifier_passes_non_system_text():
    text = "План готов, давайте продолжим улучшения."
    out = response_verifier.enforce_verified_system_metrics(text)
    assert out == text


def test_verifier_blocks_when_no_confirmed_source(monkeypatch):
    monkeypatch.setattr(
        response_verifier,
        "get_system_metrics_snapshot",
        lambda **kwargs: {
            "ok": False,
            "source": "unavailable",
            "timestamp_utc": "2026-03-15T00:00:00+00:00",
        },
    )
    out = response_verifier.enforce_verified_system_metrics("CPU 82% RAM 65%")
    assert "заблокирован" in out
    assert "source=unavailable" in out


def test_verifier_replaces_with_confirmed_metrics(monkeypatch):
    monkeypatch.setattr(
        response_verifier,
        "get_system_metrics_snapshot",
        lambda **kwargs: {
            "ok": True,
            "source": "psutil",
            "timestamp_utc": "2026-03-15T00:00:00+00:00",
            "cpu_percent": 11.2,
            "ram_percent": 44.3,
            "top_processes": [
                {"pid": 10, "name": "agent", "cpu_percent": 8.8, "rss_mb": 120.0},
            ],
        },
    )
    out = response_verifier.enforce_verified_system_metrics("CPU 82% по системе")
    assert "был заменён" in out
    assert "source=psutil" in out
    assert "CPU=11.2%" in out


def test_verifier_blocks_percent_near_system_context(monkeypatch):
    monkeypatch.setattr(
        response_verifier,
        "get_system_metrics_snapshot",
        lambda **kwargs: {
            "ok": False,
            "source": "unavailable",
            "timestamp_utc": "2026-03-15T00:00:00+00:00",
        },
    )
    out = response_verifier.enforce_verified_system_metrics("Текущая нагрузка системы 82% и растет")
    assert "заблокирован" in out


def test_verifier_does_not_block_non_system_percent():
    out = response_verifier.enforce_verified_system_metrics("Скидка по тарифу 15% до конца месяца")
    assert out == "Скидка по тарифу 15% до конца месяца"
