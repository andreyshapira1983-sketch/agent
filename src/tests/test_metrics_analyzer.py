from src.monitoring.metrics import metrics
from src.monitoring.metrics_analyzer import MetricsAnalyzer


def test_metrics_analyzer():
    metrics.log_time(1.2)
    metrics.log_time(1.0)
    analyzer = MetricsAnalyzer(metrics)

    analyzer.analyze_performance()

    suggestion = analyzer.suggest_optimization()
    assert "Consider" in suggestion or "acceptable" in suggestion  # nosec B101

    summary = analyzer.get_summary()
    assert "calls" in summary  # nosec B101
    assert "errors" in summary  # nosec B101
    assert "successes" in summary  # nosec B101
