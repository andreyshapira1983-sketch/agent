"""
Tests for autonomy extension tools: fetch_url, parse_json, aggregate_simple, suggest_priority.
"""
from __future__ import annotations

import json

import src.tools  # noqa: F401 — register all tools
from src.tools.registry import call


def test_parse_json_ok():
    out = call("parse_json", text='{"a":1,"b":2}')
    parsed = json.loads(out)
    assert parsed == {"a": 1, "b": 2}


def test_parse_json_invalid():
    out = call("parse_json", text="not json")
    assert "error" in out.lower() or "JSON" in out


def test_parse_json_empty():
    out = call("parse_json", text="")
    assert "empty" in out.lower() or "error" in out.lower()


def test_aggregate_simple_ok():
    out = call("aggregate_simple", values_json="[1, 2, 3, 4, 5]")
    data = json.loads(out)
    assert data["count"] == 5
    assert data["min"] == 1
    assert data["max"] == 5
    assert data["sum"] == 15
    assert data["avg"] == 3.0


def test_aggregate_simple_empty_array():
    out = call("aggregate_simple", values_json="[]")
    assert "error" in out.lower() or "no numbers" in out.lower()


def test_aggregate_simple_not_array():
    out = call("aggregate_simple", values_json='{"x":1}')
    assert "error" in out.lower() or "array" in out.lower()


def test_suggest_priority_ok():
    out = call("suggest_priority", tasks_json='["Long task", "Short", "Medium"]')
    data = json.loads(out)
    assert "order" in data
    assert len(data["order"]) == 3
    assert set(data["order"]) == {0, 1, 2}
    # Short (len 5) before Medium (6) before Long task (9)
    assert data["order"].index(1) < data["order"].index(2)
    assert data["order"].index(2) < data["order"].index(0)


def test_suggest_priority_empty():
    out = call("suggest_priority", tasks_json="[]")
    data = json.loads(out)
    assert data["order"] == []


def test_suggest_priority_single():
    out = call("suggest_priority", tasks_json='["Only"]')
    data = json.loads(out)
    assert data["order"] == [0]


def test_fetch_url_disallowed():
    out = call("fetch_url", url="https://example.com/")
    assert "error" in out.lower() or "allowlist" in out.lower()


def test_fetch_url_http_rejected():
    out = call("fetch_url", url="http://api.github.com")
    assert "error" in out.lower() or "https" in out.lower()


def test_fetch_url_empty():
    out = call("fetch_url", url="")
    assert "empty" in out.lower() or "error" in out.lower()


def test_parse_json_input_too_long():
    from src.tools.impl.autonomy_tools import MAX_JSON_INPUT_LEN
    out = call("parse_json", text="{}" + " " * (MAX_JSON_INPUT_LEN - 1))  # over limit, not empty
    assert "exceeds" in out.lower() or "max length" in out.lower()


def test_aggregate_simple_single_pass_result():
    out = call("aggregate_simple", values_json="[3, 1, 2]")
    data = json.loads(out)
    assert data["min"] == 1 and data["max"] == 3 and data["sum"] == 6 and data["avg"] == 2.0


def test_aggregate_simple_array_capped():
    from src.tools.impl.autonomy_tools import MAX_AGGREGATE_ARRAY_LEN
    big = "[" + ",".join("0" for _ in range(MAX_AGGREGATE_ARRAY_LEN + 100)) + "]"
    out = call("aggregate_simple", values_json=big)
    data = json.loads(out)
    assert data["count"] == MAX_AGGREGATE_ARRAY_LEN


def test_suggest_priority_tasks_capped():
    from src.tools.impl.autonomy_tools import MAX_SUGGEST_PRIORITY_TASKS
    big = json.dumps([f"task_{i}" for i in range(MAX_SUGGEST_PRIORITY_TASKS + 50)])
    out = call("suggest_priority", tasks_json=big)
    data = json.loads(out)
    assert len(data["order"]) == MAX_SUGGEST_PRIORITY_TASKS


def test_manage_queue_status():
    import src.tools  # noqa: F401
    from src.tools.registry import call
    out = call("manage_queue", action="status")
    data = json.loads(out)
    assert "size" in data


def test_manage_queue_enqueue_dequeue():
    import src.tools  # noqa: F401
    from src.tools.registry import call
    call("manage_queue", action="enqueue", task_id="t1", tool="get_current_time", arguments="{}")
    out = call("manage_queue", action="status")
    assert json.loads(out)["size"] >= 1
    out2 = call("manage_queue", action="dequeue")
    assert "t1" in out2 or "get_current_time" in out2 or "payload" in out2
    out3 = call("manage_queue", action="dequeue")
    assert "empty" in out3.lower() or out3 == "Queue empty."


def test_get_fetch_cache_stats():
    from src.tools.impl.autonomy_tools import get_fetch_cache_stats
    st = get_fetch_cache_stats()
    assert "fetch_cache_hits" in st and "fetch_cache_misses" in st


def test_reset_fetch_cache():
    import src.tools  # noqa: F401
    from src.tools.registry import call
    out = call("reset_fetch_cache")
    assert "cleared" in out.lower()


def test_describe_workspace_lists_real_tree():
    out = call("describe_workspace", path="src", max_depth=1, max_entries=40)
    assert "src/" in out
    assert "tools/" in out or "core/" in out


def test_fetch_url_cache_hit():
    """При включённом кэше (cache_max > 0) возвращается закэшированное тело без запроса."""
    import time
    from unittest.mock import patch
    from src.tools.impl import autonomy_tools
    url = "https://api.github.com"
    autonomy_tools._fetch_cache[url] = (time.monotonic(), "cached_response")
    autonomy_tools._fetch_cache.move_to_end(url)
    with patch.object(autonomy_tools, "_get_limits", return_value={"fetch_cache_max_entries": 50, "fetch_cache_ttl_sec": 0}):
        try:
            out = call("fetch_url", url=url)
            assert out == "cached_response"
        finally:
            autonomy_tools._fetch_cache.pop(url, None)


def test_get_system_metrics_tool_shape():
    out = call("get_system_metrics", top_n=2)
    data = json.loads(out)
    assert "source" in data
    assert "timestamp_utc" in data
    assert "top_processes" in data
    assert isinstance(data["top_processes"], list)
    assert len(data["top_processes"]) <= 2


def test_generate_question_tool_returns_question():
    out = call("generate_question", context="python модуль тесты")
    assert isinstance(out, str)
    assert out.strip().endswith("?")