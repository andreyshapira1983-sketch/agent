"""
Metrics: calls, errors, successes, timing, recent requests, per-tool execution times.
Единый класс Metrics и глобальный экземпляр; get_metrics() для совместимости.
"""
from __future__ import annotations

from collections import deque
from typing import Any


class Metrics:
    def __init__(self) -> None:
        self.calls = 0
        self.execution_times: list[float] = []
        self.errors: list[str] = []
        self.successes: list[bool] = []
        self._recent_requests: deque = deque(maxlen=20)
        self._tool_times: dict[str, deque[float]] = {}
        self._tool_times_maxlen = 100

    def record_call(self) -> None:
        self.calls += 1

    def log_time(self, duration: float) -> None:
        self.execution_times.append(duration)

    def log_tool_time(self, tool_name: str, duration_sec: float) -> None:
        """Record execution time for a tool (for performance analysis)."""
        if tool_name not in self._tool_times:
            self._tool_times[tool_name] = deque(maxlen=self._tool_times_maxlen)
        self._tool_times[tool_name].append(duration_sec)

    def get_tool_times_summary(self) -> dict[str, dict[str, Any]]:
        """Per-tool last duration, avg, count (from recent samples)."""
        out: dict[str, dict[str, Any]] = {}
        for name, times in self._tool_times.items():
            if not times:
                continue
            arr = list(times)
            out[name] = {
                "last_sec": round(arr[-1], 3),
                "avg_sec": round(sum(arr) / len(arr), 3),
                "count": len(arr),
            }
        return out

    def record_error(self, error_type: str = "error") -> None:
        self.errors.append(error_type)

    def record_success(self) -> None:
        self.successes.append(True)

    def record_request_preview(self, text: str) -> None:
        self._recent_requests.append((text[:80] + "..." if len(text) > 80 else text))

    def get_average_time(self) -> float:
        if not self.execution_times:
            return 0.0
        return sum(self.execution_times) / len(self.execution_times)

    def get_metrics_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "calls": self.calls,
            "errors": len(self.errors),
            "successes": len(self.successes),
            "last_duration_sec": round(self.execution_times[-1], 2) if self.execution_times else None,
            "recent_requests": list(self._recent_requests)[-10:],
            "average_time": self.get_average_time(),
        }
        tool_summary = self.get_tool_times_summary()
        if tool_summary:
            summary["tool_times"] = tool_summary
        return summary


# Глобальный экземпляр для использования в main и инструментах
metrics = Metrics()


def get_metrics() -> dict[str, Any]:
    """Совместимость: возвращает то же, что metrics.get_metrics_summary()."""
    return metrics.get_metrics_summary()


def analyze_tool_performance(top_n: int = 10) -> str:
    """
    Текстовая сводка по производительности инструментов: самые медленные по среднему времени,
    среднее и число вызовов. Для анализа узких мест.
    """
    summary = metrics.get_tool_times_summary()
    if not summary:
        return "No tool execution data yet."
    rows = []
    for name, data in summary.items():
        rows.append((name, data["avg_sec"], data["count"], data["last_sec"]))
    rows.sort(key=lambda x: -x[1])
    lines = ["Tool performance (slowest first by avg_sec):"]
    for name, avg, count, last in rows[:top_n]:
        lines.append(f"  {name}: avg={avg}s last={last}s count={count}")
    return "\n".join(lines)


def export_performance_summary(file_path: str | None = None) -> str:
    """
    Выгрузить сводку производительности (tool_times + fetch_cache stats) в JSON-файл для исторического анализа.
    Если file_path не задан — пишет в config/performance_logs/ с именем по текущей дате/времени.
    Возвращает путь к записанному файлу или сообщение об ошибке.
    """
    import json
    from pathlib import Path
    from datetime import datetime, timezone

    try:
        tool_times = metrics.get_tool_times_summary()
        fetch_stats: dict[str, Any] = {}
        try:
            from src.tools.impl.autonomy_tools import get_fetch_cache_stats
            fetch_stats = get_fetch_cache_stats()
        except Exception:
            pass
        payload = {
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            "tool_times": tool_times,
            "fetch_cache": fetch_stats,
        }
        if file_path is None:
            root = Path(__file__).resolve().parent.parent.parent
            log_dir = root / "config" / "performance_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            file_path = str(log_dir / f"perf_{ts}.json")
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"Exported to {path}"
    except Exception as e:
        return f"Export failed: {e!s}"


def check_performance_alerts() -> str:
    """
    Проверить, есть ли инструменты, превысившие порог tool_performance.warn_threshold_sec.
    Возвращает текстовый отчёт; при обнаружении пишет в audit запись performance_alert.
    """
    try:
        from src.evolution.config_manager import load_config
        threshold = float((load_config().get("tool_performance") or {}).get("warn_threshold_sec", 0))
    except Exception:
        threshold = 0.0
    if threshold <= 0:
        return "Performance alerts disabled (warn_threshold_sec <= 0)."
    summary = metrics.get_tool_times_summary()
    exceeded = []
    for name, data in summary.items():
        if data["last_sec"] > threshold or data["avg_sec"] > threshold:
            exceeded.append((name, data["last_sec"], data["avg_sec"]))
    if not exceeded:
        return "No tools exceeded the threshold."
    lines = [f"Tools exceeding threshold ({threshold}s):"]
    for name, last, avg in exceeded:
        lines.append(f"  {name}: last={last}s avg={avg}s")
    report = "\n".join(lines)
    try:
        from src.hitl.audit_log import audit
        audit("performance_alert", {"threshold_sec": threshold, "tools": [x[0] for x in exceeded]})
    except Exception:
        pass
    try:
        from src.communication.telegram_alerts import send_alert
        send_alert("⚠️ Performance alert\n" + report)
    except Exception:
        pass
    try:
        from src.personality.triggers import fire_trigger
        fire_trigger("performance_alert", with_random=False)
    except Exception:
        pass
    return report
