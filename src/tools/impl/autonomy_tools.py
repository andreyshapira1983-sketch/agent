"""
Autonomy extension tools: fetch_url (allowlist + LRU cache), parse_json, aggregate_simple,
suggest_priority, manage_queue, get_fetch_cache_stats, reset_fetch_cache,
get_system_metrics, generate_question, log_reading, get_reading_log.
Лимиты из config/agent.json (autonomy_limits); allowlist из config/allowed_sites.json.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from collections import OrderedDict
from pathlib import Path
from datetime import datetime, timezone

from src.tools.base import tool_schema
from src.tools.registry import register
from src.monitoring.system_metrics import get_system_metrics_snapshot
from src.communication.dialogue_generator import DialogueGenerator

_log = logging.getLogger(__name__)

_READING_LOG_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "reading_log.json"
_READING_LOG_MAX_ENTRIES = 500
_READING_SUMMARY_MAX_LEN = 2000

# Лимиты по умолчанию (переопределяются из config/agent.json)
MAX_JSON_INPUT_LEN = 100_000
MAX_PARSE_JSON_OUTPUT_LEN = 50_000
MAX_AGGREGATE_ARRAY_LEN = 10_000
MAX_SUGGEST_PRIORITY_TASKS = 1_000
ALLOWED_FETCH_HOSTS_FALLBACK = ("https://api.github.com", "https://raw.githubusercontent.com")

_fetch_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()
_fetch_cache_hits = 0
_fetch_cache_misses = 0
_dialogue_generator = DialogueGenerator()


def _get_limits() -> dict:
    """Лимиты из config/agent.json -> autonomy_limits."""
    try:
        from src.evolution.config_manager import load_config
        cfg = load_config()
        return dict(cfg.get("autonomy_limits") or {})
    except Exception:
        return {}


def _get_allowed_fetch_prefixes() -> tuple[str, ...]:
    """Разрешённые URL-префиксы для fetch_url: config/allowed_sites.json или fallback."""
    try:
        from src.policy.allowed_sites import get_allowed_sites
        prefixes = get_allowed_sites()
        if prefixes:
            return tuple(p.rstrip("/") for p in prefixes)
    except Exception:
        pass
    return ALLOWED_FETCH_HOSTS_FALLBACK


def _fetch_url(url: str) -> str:
    """Safe GET: только разрешённые хосты (config/allowed_sites.json). LRU-кэш с TTL; hit/miss считаются."""
    global _fetch_cache_hits, _fetch_cache_misses
    if not url or not url.strip():
        return "Error: empty URL."
    url_stripped = url.strip()
    url_clean = url_stripped.split("#")[0].split("?")[0]
    if not url_clean.startswith("https://"):
        return "Error: only HTTPS URLs are allowed."

    limits = _get_limits()
    cache_max = limits.get("fetch_cache_max_entries", 0)
    ttl_sec = limits.get("fetch_cache_ttl_sec", 0)
    now = time.monotonic()

    if cache_max > 0 and url_stripped in _fetch_cache:
        ts, body = _fetch_cache[url_stripped]
        if ttl_sec <= 0 or (now - ts) <= ttl_sec:
            _fetch_cache.move_to_end(url_stripped)
            _fetch_cache_hits += 1
            return body
        del _fetch_cache[url_stripped]

    allowed_prefixes = _get_allowed_fetch_prefixes()
    allowed = False
    for prefix in allowed_prefixes:
        p = prefix.rstrip("/")
        if url_clean.startswith(p + "/") or url_clean == p:
            allowed = True
            break
    if not allowed:
        return f"Error: URL not in allowlist (config/allowed_sites.json). Allowed prefixes: {list(allowed_prefixes)[:10]}."

    _fetch_cache_misses += 1
    try:
        from src.environment.api_world import request
        max_bytes = limits.get("fetch_max_response_bytes")
        body = request("GET", url_stripped, max_response_bytes=max_bytes)
        if cache_max > 0:
            while len(_fetch_cache) >= cache_max:
                _fetch_cache.popitem(last=False)
            _fetch_cache[url_stripped] = (now, body)
        return body
    except Exception as e:
        _log.debug("fetch_url failed: %s", e)
        return f"Fetch error: {e!s}"[:500]


def _parse_json(text: str) -> str:
    """Распарсить JSON; лимит на длину ввода и вывода."""
    limits = _get_limits()
    max_in = limits.get("max_json_input_len", MAX_JSON_INPUT_LEN)
    max_out = limits.get("max_parse_json_output_len", MAX_PARSE_JSON_OUTPUT_LEN)
    if not (text or "").strip():
        return "Error: empty input."
    if len(text) > max_in:
        return f"Error: input exceeds max length ({len(text)} > {max_in})."
    try:
        obj = json.loads(text)
        out = json.dumps(obj, ensure_ascii=False)
        if len(out) > max_out:
            return f"Error: parsed result length exceeds max ({len(out)} > {max_out})."
        return out
    except json.JSONDecodeError as e:
        return f"Error: invalid JSON: {e!s}"[:300]


def _aggregate_simple(values_json: str) -> str:
    """min/max/sum/avg/count по массиву чисел; лимит на размер массива."""
    limits = _get_limits()
    max_len = limits.get("max_aggregate_array_len", MAX_AGGREGATE_ARRAY_LEN)
    if not (values_json or "").strip():
        return "Error: empty input."
    try:
        arr = json.loads(values_json)
        if not isinstance(arr, list):
            return "Error: input must be a JSON array of numbers."
        nums = []
        for x in arr[: max_len + 1]:
            try:
                nums.append(float(x))
            except (TypeError, ValueError):
                pass
        if len(arr) > max_len:
            nums = nums[:max_len]
        if not nums:
            return "Error: no numbers in array."
        return json.dumps({
            "min": min(nums),
            "max": max(nums),
            "sum": sum(nums),
            "avg": sum(nums) / len(nums),
            "count": len(nums),
        }, ensure_ascii=False)
    except json.JSONDecodeError as e:
        return f"Error: invalid JSON: {e!s}"[:300]


def _suggest_priority(tasks_json: str) -> str:
    """Порядок индексов задач по длине описания (короткие первыми)."""
    limits = _get_limits()
    max_tasks = limits.get("max_suggest_priority_tasks", MAX_SUGGEST_PRIORITY_TASKS)
    if not (tasks_json or "").strip():
        return json.dumps({"order": []})
    try:
        tasks = json.loads(tasks_json)
        if not isinstance(tasks, list):
            return "Error: input must be a JSON array of task descriptions."
        tasks = tasks[: max_tasks + 1][:max_tasks]
        indexed = [(i, str(t)[:500]) for i, t in enumerate(tasks)]
        indexed.sort(key=lambda x: (len(x[1]), x[0]))
        return json.dumps({"order": [x[0] for x in indexed]}, ensure_ascii=False)
    except json.JSONDecodeError as e:
        return f"Error: invalid JSON: {e!s}"[:300]


def _manage_queue(action: str, task_id: str = "", tool: str = "", arguments: str = "{}") -> str:
    """status / enqueue / dequeue очереди задач."""
    from src.tasks.queue import size, peek, enqueue, dequeue
    from src.tasks.task_state import Task

    if action == "status":
        return json.dumps({"size": size(), "peek": peek(20)})
    if action == "enqueue":
        if not task_id or not tool:
            return "Error: enqueue requires task_id and tool."
        try:
            args = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            args = {}
        t = Task(id=task_id, payload={"tool": tool, "arguments": args})
        if not enqueue(t):
            return "Error: task queue full or max tasks per cycle reached. Complete or drop tasks first."
        return f"Enqueued task_id={task_id} tool={tool}."
    if action == "dequeue":
        task = dequeue()
        if task is None:
            return "Queue empty."
        return json.dumps({"task_id": task.id, "payload": task.payload}, ensure_ascii=False)
    return "Error: action must be status, enqueue, or dequeue."


def get_fetch_cache_stats() -> dict:
    """Метрики кэша fetch_url для мониторинга."""
    return {"fetch_cache_hits": _fetch_cache_hits, "fetch_cache_misses": _fetch_cache_misses}


def _reset_fetch_cache() -> str:
    """Очистить LRU-кэш fetch_url."""
    global _fetch_cache, _fetch_cache_hits, _fetch_cache_misses
    _fetch_cache.clear()
    _fetch_cache_hits = 0
    _fetch_cache_misses = 0
    return "Fetch cache cleared."


def _get_system_metrics(force_refresh: bool = False, top_n: int = 5) -> str:
    """Return real system metrics snapshot from monitoring.system_metrics."""
    try:
        n = int(top_n)
    except (TypeError, ValueError):
        n = 5
    n = max(1, min(20, n))
    snapshot = get_system_metrics_snapshot(force_refresh=bool(force_refresh), ttl_sec=5, top_n=n)
    return json.dumps(snapshot, ensure_ascii=False)


def _generate_question(context: str = "") -> str:
    """Generate a curiosity-driven follow-up question for dialogue continuity."""
    return _dialogue_generator.generate_question(context_text=(context or ""))


def _log_reading(url: str, title: str, summary: str) -> str:
    """Записать в лог прочитанного: URL, название, краткий вывод. Файл data/reading_log.json."""
    url = (url or "").strip()
    title = (title or "").strip()[:500]
    summary = (summary or "").strip()[:_READING_SUMMARY_MAX_LEN]
    if not url:
        return "Error: url is required."
    try:
        _READING_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"entries": []}
        if _READING_LOG_PATH.exists():
            try:
                data = json.loads(_READING_LOG_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        entries = data.get("entries") or []
        entries.append({
            "url": url,
            "title": title or url,
            "summary": summary,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        if len(entries) > _READING_LOG_MAX_ENTRIES:
            entries = entries[-_READING_LOG_MAX_ENTRIES:]
        data["entries"] = entries
        _READING_LOG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return f"OK: записано в лог прочитанного (всего записей: {len(entries)})."
    except Exception as e:
        _log.debug("log_reading failed: %s", e)
        return f"Error: {e!s}"


def _get_reading_log(limit: int = 20) -> str:
    """Вернуть последние записи из лога прочитанного (для контекста: что агент уже читал)."""
    try:
        n = max(1, min(int(limit), 100))
        if not _READING_LOG_PATH.exists():
            return "Лог прочитанного пуст."
        data = json.loads(_READING_LOG_PATH.read_text(encoding="utf-8"))
        entries = data.get("entries") or []
        last = entries[-n:]
        last.reverse()
        lines = []
        for i, e in enumerate(last, 1):
            lines.append(f"{i}. {e.get('title', e.get('url', '?'))}\n   URL: {e.get('url', '')}\n   Вывод: {e.get('summary', '')[:300]}")
        return "\n\n".join(lines) if lines else "Записей нет."
    except Exception as e:
        return f"Error: {e!s}"


def _search_openlibrary(query: str, limit: int = 20) -> str:
    """Поиск книг в Open Library. Возвращает JSON-список {title, url} для последующего fetch_url и log_reading."""
    try:
        from src.environment.api_world import request
        n = max(1, min(int(limit), 50))
        q = (query or "").strip() or "book"
        url = f"https://openlibrary.org/search.json?q={urllib.parse.quote(q)}&limit={n}"
        raw = request("GET", url, max_response_bytes=500_000)
        data = json.loads(raw)
        docs = data.get("docs") or []
        out = []
        for d in docs:
            key = d.get("key", "")
            title = d.get("title", "?")
            if key and key.startswith("/"):
                full_url = f"https://openlibrary.org{key}"
                out.append({"title": title, "url": full_url})
        return json.dumps(out, ensure_ascii=False)[:15000]
    except Exception as e:
        _log.debug("search_openlibrary failed: %s", e)
        return f"Error: {e!s}"


def _get_gutenberg_book_list(limit: int = 25, start_index: int = 1) -> str:
    """Список книг Gutenberg (по популярности). Возвращает JSON-список {title, url}. start_index: 1, 26, 51... (по 25 на страницу)."""
    try:
        from src.environment.api_world import request
        n = max(1, min(int(limit), 50))
        start = max(1, int(start_index))
        url = f"https://www.gutenberg.org/ebooks/search/?sort_order=downloads&start_index={start}"
        raw = request("GET", url, max_response_bytes=500_000)
        # Страница содержит /ebooks/123 — ищем ID, строим полный URL
        ids = list(dict.fromkeys(re.findall(r"/ebooks/(\d+)", raw)))[:n]
        out = [{"title": f"Gutenberg ID {i}", "url": f"https://www.gutenberg.org/ebooks/{i}"} for i in ids]
        return json.dumps(out, ensure_ascii=False)[:15000]
    except Exception as e:
        _log.debug("get_gutenberg_book_list failed: %s", e)
        return f"Error: {e!s}"


def register_autonomy_tools() -> None:
    """Регистрация инструментов расширения автономии."""
    register(
        "fetch_url",
        tool_schema(
            "fetch_url",
            "Safe GET request. Only URLs from config/allowed_sites.json. Response cached (LRU).",
            {"url": {"type": "string", "description": "Full HTTPS URL"}},
            required=["url"],
        ),
        _fetch_url,
    )
    register(
        "parse_json",
        tool_schema(
            "parse_json",
            "Parse JSON string. Returns pretty-printed JSON or error. Input/output length limited by config.",
            {"text": {"type": "string", "description": "JSON string to parse"}},
            required=["text"],
        ),
        _parse_json,
    )
    register(
        "aggregate_simple",
        tool_schema(
            "aggregate_simple",
            "Compute min, max, sum, avg, count for a JSON array of numbers.",
            {"values_json": {"type": "string", "description": "JSON array of numbers, e.g. [1,2,3]"}},
            required=["values_json"],
        ),
        _aggregate_simple,
    )
    register(
        "suggest_priority",
        tool_schema(
            "suggest_priority",
            "Suggest task order by description length (short first). Returns JSON with order (indices).",
            {"tasks_json": {"type": "string", "description": "JSON array of task description strings"}},
            required=["tasks_json"],
        ),
        _suggest_priority,
    )
    register(
        "manage_queue",
        tool_schema(
            "manage_queue",
            "Task queue: action=status (size, peek), enqueue (task_id, tool, arguments), dequeue.",
            {
                "action": {"type": "string", "description": "status | enqueue | dequeue"},
                "task_id": {"type": "string", "description": "For enqueue"},
                "tool": {"type": "string", "description": "For enqueue"},
                "arguments": {"type": "string", "description": "JSON object for enqueue (default {})"},
            },
            required=["action"],
        ),
        lambda action, task_id="", tool="", arguments="{}": _manage_queue(action, task_id=task_id, tool=tool, arguments=arguments),
    )
    register(
        "reset_fetch_cache",
        tool_schema(
            "reset_fetch_cache",
            "Clear fetch_url LRU cache.",
            {},
            required=[],
        ),
        _reset_fetch_cache,
    )
    register(
        "get_system_metrics",
        tool_schema(
            "get_system_metrics",
            "Get real CPU/RAM/process snapshot from monitoring source with source and timestamp fields.",
            {
                "force_refresh": {
                    "type": "boolean",
                    "description": "Bypass cache and collect fresh snapshot (default false)",
                },
                "top_n": {
                    "type": "integer",
                    "description": "How many top CPU processes to include (1..20, default 5)",
                },
            },
            required=[],
        ),
        _get_system_metrics,
    )
    register(
        "log_reading",
        tool_schema(
            "log_reading",
            "Record something you read (e.g. from a free library via fetch_url): url, title, short summary. Stored in data/reading_log.json. Call after reading a book page or article to remember what you learned.",
            {
                "url": {"type": "string", "description": "URL of the page/book read"},
                "title": {"type": "string", "description": "Title of the work or page (optional)"},
                "summary": {"type": "string", "description": "Short summary or key takeaway (what you learned)"},
            },
            required=["url"],
        ),
        lambda url, title="", summary="": _log_reading(url, title=title, summary=summary),
    )
    register(
        "get_reading_log",
        tool_schema(
            "get_reading_log",
            "Get last N entries from reading log (what you previously read). Use to recall books/articles and their summaries before answering.",
            {"limit": {"type": "integer", "description": "Number of recent entries (default 20, max 100)"}},
            required=[],
        ),
        lambda limit=20: _get_reading_log(limit=limit),
    )
    register(
        "search_openlibrary",
        tool_schema(
            "search_openlibrary",
            "Search Open Library for books. Returns JSON list of {title, url}. Then use fetch_url on each url to read, then log_reading to record. Use to scan many books.",
            {
                "query": {"type": "string", "description": "Search query (e.g. 'python', 'dostoevsky')"},
                "limit": {"type": "integer", "description": "Max number of results (default 20, max 50)"},
            },
            required=["query"],
        ),
        lambda query, limit=20: _search_openlibrary(query, limit=limit),
    )
    register(
        "get_gutenberg_book_list",
        tool_schema(
            "get_gutenberg_book_list",
            "Get list of Project Gutenberg books (by popularity). Returns JSON list of {title, url}. Then fetch_url each url, then log_reading. start_index 1, 26, 51... for next page (25 per page). Use to scan many books.",
            {
                "limit": {"type": "integer", "description": "Max books to return (default 25, max 50)"},
                "start_index": {"type": "integer", "description": "Page offset: 1, 26, 51... (default 1)"},
            },
            required=[],
        ),
        lambda limit=25, start_index=1: _get_gutenberg_book_list(limit=limit, start_index=start_index),
    )
    register(
        "generate_question",
        tool_schema(
            "generate_question",
            "Generate a context-aware follow-up question for the dialogue.",
            {
                "context": {
                    "type": "string",
                    "description": "Optional context text used to choose question theme",
                }
            },
            required=[],
        ),
        _generate_question,
    )
