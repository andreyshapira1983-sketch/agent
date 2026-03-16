from .metrics import Metrics

class MetricsAnalyzer:
    def __init__(self, metrics: Metrics):
        self.metrics = metrics

    def analyze_performance(self):
        avg_time = self.metrics.get_average_time()
        if avg_time > 1.0:  # Условие оповещения, порог в 1 секунду
            self.trigger_alert("Average execution time exceeded threshold.")

    def suggest_optimization(self):
        avg_time = self.metrics.get_average_time()
        if avg_time > 1.0:
            return "Consider increasing cache size or optimizing algorithms."
        return "Performance is within acceptable limits."

    def trigger_alert(self, message):
        print(f"ALERT: {message}")

    def get_summary(self):
        return self.metrics.get_metrics_summary()