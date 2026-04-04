# pylint: disable=protected-access,attribute-defined-outside-init

import types
import unittest
from unittest.mock import patch

from execution.task_router import RequiredArg, TaskIntent, TaskRoute, TaskRouter, TaskStatus, route
from llm.github_backend import GitHubBackend
from llm.llm_router import LLMRouter
from llm.search_backend import DuckDuckGoBackend
from llm.telegram_sink import TelegramSink
from loop.reliability import ReliabilitySystem, RetryStrategy


class _Monitor:
    def __init__(self):
        self.info_calls = []
        self.warning_calls = []

    def info(self, message, source=None):
        self.info_calls.append((message, source))

    def warning(self, message, source=None):
        self.warning_calls.append((message, source))


class _Client:
    def __init__(self, result="ok", exc=None, model="m", health_ok=True):
        self.result = result
        self.exc = exc
        self.model = model
        self.health_ok = health_ok
        self.calls = []
        self.total_tokens = 10
        self.total_cost_usd = 0.25
        self.budget_control = None
        self.unloaded = False

    def infer(self, prompt, context=None, system=None, history=None, max_tokens=None):
        self.calls.append((prompt, context, system, history, max_tokens))
        if self.exc:
            raise self.exc
        return self.result

    def set_model(self, model):
        self.model = model

    def health(self):
        return {"ok": self.health_ok}

    def unload(self):
        self.unloaded = True


class _FakeResponse:
    def __init__(self, payload=None, status_code=200, raise_exc=None):
        self.payload = payload if payload is not None else {"ok": True}
        self.status_code = status_code
        self.raise_exc = raise_exc

    def raise_for_status(self):
        if self.raise_exc:
            raise self.raise_exc

    def json(self):
        return self.payload


class _FakeRequestsModule:
    class RequestException(Exception):
        pass

    def __init__(self):
        self.get_calls = []
        self.post_calls = []
        self.get_result = _FakeResponse({"value": 1})
        self.post_result = _FakeResponse({"value": 2})

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_result

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.post_result


class _DDGSContext:
    def __init__(self, text_result=None, news_result=None, image_result=None, exc=None):
        self.text_result = text_result if text_result is not None else []
        self.news_result = news_result if news_result is not None else []
        self.image_result = image_result if image_result is not None else []
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def text(self, query, max_results=5):
        _ = (query, max_results)
        if self.exc:
            raise self.exc
        return self.text_result

    def news(self, query, max_results=5):
        _ = (query, max_results)
        if self.exc:
            raise self.exc
        return self.news_result

    def images(self, query, max_results=5):
        _ = (query, max_results)
        if self.exc:
            raise self.exc
        return self.image_result


class _FakeThread:
    def __init__(self, target=None, daemon=None):
        self.target = target
        self.daemon = daemon
        self.started = False
        self.joined = False

    def start(self):
        self.started = True

    def is_alive(self):
        return True

    def join(self, timeout=None):
        _ = timeout
        self.joined = True


class _OneLoopStopEvent:
    def __init__(self):
        self.calls = 0

    def is_set(self):
        self.calls += 1
        return self.calls > 1

    def set(self):
        self.calls = 999


class _FakeQueue:
    def __init__(self):
        self.items = []

    def put(self, value):
        self.items.append(value)

    def empty(self):
        return not self.items

    def get_nowait(self):
        return self.items.pop(0)


class _FakeProcess:
    def __init__(self, target=None, args=None, kwargs=None, daemon=None):
        self.target = target
        self.args = args or ()
        self.kwargs = kwargs or {}
        self.daemon = daemon
        self._alive = False
        self.terminated = False

    def start(self):
        self._alive = True
        if self.target:
            self.target(*self.args, **self.kwargs)
        self._alive = False

    def join(self, timeout=None):
        _ = timeout

    def is_alive(self):
        return self._alive

    def terminate(self):
        self.terminated = True
        self._alive = False


class _SlowProcess(_FakeProcess):
    def start(self):
        self._alive = True


class _StartFailProcess(_FakeProcess):
    def start(self):
        raise OSError("no mp")


class _AliveThread:
    def __init__(self, target=None, daemon=None):
        self.target = target
        self.daemon = daemon

    def start(self):
        return None

    def join(self, timeout=None):
        _ = timeout

    def is_alive(self):
        return True


class TaskRouterCoverageTests(unittest.TestCase):
    def setUp(self):
        self.router = TaskRouter()

    def test_route_empty_headings_and_helper_results(self):
        empty = self.router.route("   ")
        self.assertEqual(empty.intent, TaskIntent.EMPTY)
        self.assertEqual(empty.status, TaskStatus.NON_ACTIONABLE)

        heading = self.router.route("[7/40] Работа с Gmail")
        self.assertEqual(heading.intent, TaskIntent.HEADING)
        self.assertFalse(heading.can_execute)
        self.assertIn("Заголовок", heading.reason)
        self.assertIn("SKIPPED", heading.to_non_actionable_result()["message"])

        blocked = TaskRoute(
            intent=TaskIntent.TIME,
            status=TaskStatus.BLOCKED,
            missing_args=[RequiredArg("timezone", "город или часовой пояс")],
        )
        payload = blocked.to_blocked_result()
        self.assertIn("timezone", payload["missing_args"][0]["name"])
        self.assertIn("BLOCKED", payload["message"])

        self.assertEqual(route("какое время в london").intent, TaskIntent.TIME)

    def test_route_intents_and_missing_args(self):
        weather_partial = self.router.route("какая погода в моём городе сегодня")
        self.assertEqual(weather_partial.intent, TaskIntent.WEATHER)
        self.assertEqual(weather_partial.status, TaskStatus.PARTIAL)
        self.assertTrue(weather_partial.missing_args[0].recoverable)

        weather_blocked = self.router.route("какая погода сегодня")
        self.assertEqual(weather_blocked.status, TaskStatus.BLOCKED)

        time_blocked = self.router.route("который час сейчас")
        self.assertEqual(time_blocked.intent, TaskIntent.TIME)
        self.assertEqual(time_blocked.status, TaskStatus.BLOCKED)

        currency_ok = self.router.route("покажи курс usd/ils")
        self.assertEqual(currency_ok.intent, TaskIntent.CURRENCY)
        self.assertEqual(currency_ok.status, TaskStatus.SUCCESS)

        sports_blocked = self.router.route("расписание матчей конкретной команды")
        self.assertEqual(sports_blocked.intent, TaskIntent.SPORTS)
        self.assertEqual(sports_blocked.status, TaskStatus.BLOCKED)

        email_blocked = self.router.route("найди письма от конкретного отправителя")
        self.assertEqual(email_blocked.intent, TaskIntent.EMAIL)
        self.assertEqual(email_blocked.status, TaskStatus.BLOCKED)
        self.assertTrue(email_blocked.block_web_search)

        contacts_blocked = self.router.route("найди контакт по имени имя_контакта")
        self.assertEqual(contacts_blocked.intent, TaskIntent.CONTACTS)
        self.assertEqual(contacts_blocked.status, TaskStatus.BLOCKED)

        company_blocked = self.router.route("найди контакт компании по specific company")
        self.assertEqual(company_blocked.intent, TaskIntent.CONTACTS)
        self.assertEqual(company_blocked.status, TaskStatus.BLOCKED)

        pdf_blocked = self.router.route("извлеки вывод из pdf")
        self.assertEqual(pdf_blocked.intent, TaskIntent.PDF_EXTRACT)
        self.assertEqual(pdf_blocked.status, TaskStatus.BLOCKED)

        calendar_blocked = self.router.route("найди событие по ключевому keyword_here")
        self.assertEqual(calendar_blocked.intent, TaskIntent.CALENDAR)
        self.assertEqual(calendar_blocked.status, TaskStatus.BLOCKED)

        web = self.router.route("найди официальный источник по погоде в Берлине")
        self.assertEqual(web.intent, TaskIntent.WEB_SEARCH)
        self.assertEqual(web.tool_name, "search")

        general = self.router.route("сделай что-нибудь полезное")
        self.assertEqual(general.intent, TaskIntent.GENERAL)
        self.assertEqual(general.status, TaskStatus.SUCCESS)

    def test_router_helper_methods(self):
        self.assertTrue(self.router._match("official source", (r"official source",)))
        self.assertTrue(self.router._has_location("weather in berlin"))
        self.assertTrue(self.router._has_location("погода в Москве"))
        self.assertFalse(self.router._has_location("погода без города"))
        self.assertTrue(self.router._has_city_for_time("время в tokyo"))
        self.assertFalse(self.router._has_city_for_time("время здесь"))
        self.assertTrue(self.router._has_currency_pair("convert usd/eur now"))
        self.assertFalse(self.router._has_currency_pair("какой курс валют"))
        self.assertTrue(self.router._team_is_vague("матчи конкретной команды"))
        self.assertTrue(self.router._is_vague("найди keyword_here"))
        self.assertTrue(self.router._has_file_reference(r"извлеки из c:\docs\report.pdf"))
        self.assertFalse(self.router._has_file_reference("извлеки из pdf файла"))

    def test_router_extra_positive_paths(self):
        self.assertEqual(self.router.route("работа с gmail").intent, TaskIntent.HEADING)
        self.assertEqual(self.router.route("раздел контакты").status, TaskStatus.NON_ACTIONABLE)
        self.assertEqual(
            self.router.route(r"извлеки вывод из C:\\docs\\report.pdf").status,
            TaskStatus.SUCCESS,
        )
        self.assertEqual(self.router.route("найди документ в моих файлах").intent, TaskIntent.USER_FILES)


class ReliabilityCoverageTests(unittest.TestCase):
    def test_retry_circuit_and_helpers(self):
        monitor = _Monitor()
        reliability = ReliabilitySystem(default_retries=3, default_delay=0.01, monitoring=monitor, circuit_threshold=1)
        state = {"calls": 0}

        def flaky():
            state["calls"] += 1
            if state["calls"] == 1:
                raise ValueError("fail")
            return "ok"

        with patch("loop.reliability.time.sleep", return_value=None):
            self.assertEqual(reliability.retry(flaky, exceptions=(ValueError,)), "ok")

        self.assertEqual(reliability._calc_pause(2, 3, RetryStrategy.FIXED), 2)
        self.assertEqual(reliability._calc_pause(2, 3, RetryStrategy.LINEAR), 6)
        self.assertEqual(reliability._calc_pause(2, 3, RetryStrategy.EXPONENTIAL), 8)
        self.assertEqual(reliability._resolve_fallback(lambda: "fb"), "fb")
        self.assertTrue(monitor.warning_calls)

        calls = {"n": 0}

        def always_fail():
            calls["n"] += 1
            raise RuntimeError("boom")

        with patch("loop.reliability.time.sleep", return_value=None):
            self.assertEqual(
                reliability.retry(
                    always_fail,
                    retries=1,
                    exceptions=(RuntimeError,),
                    fallback="fallback",
                    circuit_name="net",
                ),
                "fallback",
            )

        self.assertTrue(reliability.is_circuit_open("net"))
        self.assertEqual(reliability.retry(always_fail, fallback="skip", circuit_name="net"), "skip")
        self.assertEqual(calls["n"], 1)
        reliability.reset_circuit("net")
        self.assertFalse(reliability.is_circuit_open("net"))
        self.assertIn("net", reliability.get_circuit_status())

    def test_timeout_recovery_and_decorator(self):
        reliability = ReliabilitySystem()

        with patch("loop.reliability.multiprocessing.Queue", return_value=_FakeQueue()), \
             patch("loop.reliability.multiprocessing.Process", side_effect=_FakeProcess):
            result = reliability.with_timeout(sum, 1.0, [1, 2, 3], fallback="x")
        self.assertEqual(result, 6)

        slow_process = _SlowProcess()
        with patch("loop.reliability.multiprocessing.Queue", return_value=_FakeQueue()), \
             patch("loop.reliability.multiprocessing.Process", return_value=slow_process):
            result = reliability.with_timeout(sum, 0.01, [1, 2], fallback="timeout")
        self.assertEqual(result, "timeout")
        self.assertTrue(slow_process.terminated)

        with patch("loop.reliability.multiprocessing.Process", return_value=_StartFailProcess()):
            self.assertEqual(reliability.with_timeout(lambda: "thread", 0.1, fallback="x"), "thread")
            with self.assertRaises(RuntimeError):
                reliability.with_timeout(lambda: (_ for _ in ()).throw(RuntimeError("bad")), 0.1)

        self.assertIsNone(reliability.recover("missing"))
        reliability.register_recovery("cleanup", lambda value: f"clean:{value}")
        self.assertEqual(reliability.recover("cleanup", "ok"), "clean:ok")

        state = {"n": 0}

        @reliability.reliable(retries=2, delay=0.01, fallback="decorator")
        def wrapped():
            state["n"] += 1
            raise ValueError("x")

        with patch("loop.reliability.time.sleep", return_value=None):
            self.assertEqual(wrapped(), "decorator")
        self.assertEqual(state["n"], 2)

    def test_timeout_worker_and_thread_timeout_path(self):
        queue = _FakeQueue()

        from loop.reliability import _timeout_worker

        _timeout_worker(queue, lambda: (_ for _ in ()).throw(ValueError("bad")), (), {})
        self.assertEqual(queue.items[0][0], "err")

        reliability = ReliabilitySystem()
        with patch("loop.reliability.multiprocessing.Process", return_value=_StartFailProcess()), \
             patch("loop.reliability.threading.Thread", side_effect=_AliveThread):
            self.assertEqual(reliability.with_timeout(lambda: "never", 0.01, fallback="thread-timeout"), "thread-timeout")


class LLMRouterCoverageTests(unittest.TestCase):
    def test_routing_modes_priority_and_stats(self):
        monitor = _Monitor()
        light = _Client(result="light", model="light-model")
        heavy = _Client(result="heavy", model="heavy-model")
        local = _Client(result="local", model="local-model")

        with patch.dict("os.environ", {"LLM_ROUTER_MODE": "balanced"}):
            router = LLMRouter(light_client=light, heavy_client=heavy, local_client=local, monitoring=monitor)

        self.assertTrue(router._is_heavy("архитектурный аудит всей системы", None))
        self.assertFalse(router._is_heavy("короткий ответ", None))
        self.assertTrue(router._is_cheap("SEARCH: docs", None))
        self.assertTrue(router._is_cheap("hello", None))
        self.assertFalse(router._is_cheap("архитектурный аудит", "[HEAVY]"))

        self.assertEqual(router.infer("hello"), "local")
        heavy_prompt = (
            "архитектурный аудит всей системы с детальным планом рефакторинга, "
            "долгосрочной стратегией, анализом рисков и оптимизацией всех подсистем"
        )
        self.assertEqual(router.infer(heavy_prompt), "heavy")
        stats = router.routing_stats()
        self.assertEqual(stats["total_calls"], 1)
        self.assertEqual(stats["local_calls"], 1)
        self.assertEqual(stats["heavy_calls"], 1)
        self.assertEqual(stats["mode"], "balanced")
        self.assertTrue(monitor.info_calls)

        self.assertIn("light-model", router.model)
        self.assertEqual(router.total_tokens, 30)
        self.assertAlmostEqual(router.total_cost_usd, 0.75)

        budget = object()
        router.budget_control = budget
        self.assertIs(router.budget_control, budget)
        self.assertIs(light.budget_control, budget)
        self.assertIs(heavy.budget_control, budget)

        router.set_model("changed")
        self.assertEqual(light.model, "changed")

    def test_backend_availability_and_fallbacks(self):
        heavy = _Client(result="heavy")
        heavy._credit_exhausted = True
        light = _Client(result="light")
        local = _Client(result="local")

        with patch.dict("os.environ", {"LLM_ROUTER_MODE": "local-first"}):
            router = LLMRouter(light_client=light, heavy_client=heavy, local_client=local)
        self.assertFalse(router._heavy_available())

        with patch("llm.llm_router.importlib.import_module", return_value=types.SimpleNamespace(virtual_memory=lambda: types.SimpleNamespace(percent=90.0))):
            self.assertFalse(router._local_available())
            self.assertTrue(local.unloaded)

        local2 = _Client(result="local2")
        heavy2 = _Client(result="heavy2", exc=RuntimeError("bad heavy"))
        light2 = _Client(result="light2")
        monitor = _Monitor()
        with patch.dict("os.environ", {"LLM_ROUTER_MODE": "llm-first"}):
            router2 = LLMRouter(light_client=light2, heavy_client=heavy2, local_client=local2, monitoring=monitor)
        self.assertEqual(router2.infer("архитектурный анализ"), "light2")
        self.assertTrue(monitor.warning_calls)

        router3 = LLMRouter(light_client=_Client(exc=RuntimeError("l")), heavy_client=_Client(exc=RuntimeError("h")), local_client=_Client(exc=RuntimeError("q")))
        with self.assertRaises(RuntimeError):
            router3.infer("simple")

    def test_llm_router_edge_paths(self):
        self.assertTrue(LLMRouter(light_client=None, heavy_client=None, local_client=None)._mode == "balanced")

        weird_local = types.SimpleNamespace(health=lambda: "bad")
        router = LLMRouter(light_client=None, heavy_client=None, local_client=weird_local)
        self.assertFalse(router._light_available())
        with patch("llm.llm_router.importlib.import_module", side_effect=ImportError("no psutil")):
            self.assertFalse(router._local_available())

        broken_local = types.SimpleNamespace(health=lambda: (_ for _ in ()).throw(TypeError("x")))
        router2 = LLMRouter(light_client=None, heavy_client=None, local_client=broken_local)
        self.assertFalse(router2._local_available())

        with patch.dict("os.environ", {"LLM_ROUTER_MODE": "weird-mode"}):
            router3 = LLMRouter(light_client=_Client(), heavy_client=None, local_client=None)
        self.assertEqual(router3.routing_stats()["mode"], "balanced")
        self.assertTrue(router3._is_heavy("x" * 13000, None))
        self.assertFalse(router3._is_cheap("x" * 1300, None))
        self.assertEqual(router3.model, "LLMRouter(mode=balanced, light=m)")
        router3.budget_control = object()
        self.assertIsNotNone(router3.budget_control)


class GitHubBackendCoverageTests(unittest.TestCase):
    def test_backend_import_and_public_methods(self):
        fake_requests = _FakeRequestsModule()
        with patch("llm.github_backend.importlib.import_module", return_value=fake_requests):
            backend = GitHubBackend("secret")

        self.assertIn("***", repr(backend))
        self.assertIn("Authorization", backend._headers())

        self.assertEqual(backend.get_repo("o", "r"), {"value": 1})
        self.assertEqual(backend.list_repos("user"), {"value": 1})
        self.assertEqual(backend.get_content("o", "r", "p", ref="main"), {"value": 1})
        self.assertEqual(backend.get_file("o", "r", "p"), {"value": 1})
        self.assertEqual(backend.list_issues("o", "r"), {"value": 1})
        self.assertEqual(backend.create_issue("o", "r", "t", labels=["bug"]), {"value": 2})
        self.assertEqual(backend.get_issue("o", "r", 1), {"value": 1})
        self.assertEqual(backend.comment_issue("o", "r", 1, "b"), {"value": 2})
        self.assertEqual(backend.create_pr("o", "r", "t", "h"), {"value": 2})
        self.assertEqual(backend.list_prs("o", "r"), {"value": 1})
        self.assertEqual(backend.search_code("q"), {"value": 1})
        self.assertEqual(backend.search_repos("q"), {"value": 1})
        self.assertEqual(backend.list_branches("o", "r"), {"value": 1})
        self.assertEqual(backend.get_commit("o", "r", "sha"), {"value": 1})
        self.assertEqual(backend.list_commits("o", "r", branch="main"), {"value": 1})
        self.assertEqual(backend.get_me(), {"value": 1})
        self.assertTrue(fake_requests.get_calls)
        self.assertTrue(fake_requests.post_calls)

    def test_backend_import_error_and_http_error(self):
        with patch("llm.github_backend.importlib.import_module", side_effect=ImportError("no requests")):
            with self.assertRaises(ImportError):
                GitHubBackend("token")

        fake_requests = _FakeRequestsModule()
        fake_requests.get_result = _FakeResponse(raise_exc=RuntimeError("bad get"))
        fake_requests.post_result = _FakeResponse(raise_exc=RuntimeError("bad post"))
        with patch("llm.github_backend.importlib.import_module", return_value=fake_requests):
            backend = GitHubBackend("secret")
        with self.assertRaises(RuntimeError):
            backend.get_repo("o", "r")
        with self.assertRaises(RuntimeError):
            backend.create_issue("o", "r", "t")


class SearchBackendCoverageTests(unittest.TestCase):
    def test_search_news_images_and_fallbacks(self):
        backend = DuckDuckGoBackend()
        ddgs_cls = lambda: _DDGSContext(
            text_result=[{"title": "T", "href": "U", "body": "S"}],
            news_result=[{"title": "N", "url": "NU", "body": "NS", "date": "D", "source": "SRC"}],
            image_result=[{"title": "I", "image": "IU", "url": "SRC"}],
        )
        with patch.object(DuckDuckGoBackend, "_import_ddgs", return_value=ddgs_cls):
            self.assertEqual(backend.search("q")[0]["url"], "U")
            self.assertEqual(backend.news("q")[0]["source"], "SRC")
            self.assertEqual(backend.images("q")[0]["url"], "IU")

        with patch.object(DuckDuckGoBackend, "_import_ddgs", return_value=None):
            self.assertIn("ddgs не установлен", backend.search("q")[0]["error"])
            self.assertIn("ddgs не установлен", backend.news("q")[0]["error"])
            self.assertIn("ddgs не установлен", backend.images("q")[0]["error"])

        ddgs_err = lambda: _DDGSContext(exc=RuntimeError("network"))
        with patch.object(DuckDuckGoBackend, "_import_ddgs", return_value=ddgs_err):
            self.assertEqual(backend.search("q")[0]["error"], "network")
            self.assertEqual(backend.news("q")[0]["error"], "network")
            self.assertEqual(backend.images("q")[0]["error"], "network")

    def test_import_ddgs_fallbacks(self):
        with patch("llm.search_backend.importlib.import_module", return_value=types.SimpleNamespace(DDGS="X")):
            self.assertEqual(DuckDuckGoBackend._import_ddgs(), "X")

        with patch("llm.search_backend.importlib.import_module", side_effect=[ImportError("x"), types.SimpleNamespace(DDGS="Y")]):
            self.assertEqual(DuckDuckGoBackend._import_ddgs(), "Y")

        with patch("llm.search_backend.importlib.import_module", side_effect=ImportError("x")):
            self.assertIsNone(DuckDuckGoBackend._import_ddgs())


class TelegramSinkCoverageTests(unittest.TestCase):
    def setUp(self):
        self.thread_patch = patch("llm.telegram_sink.threading.Thread", side_effect=_FakeThread)
        self.thread_patch.start()

    def tearDown(self):
        self.thread_patch.stop()

    def test_write_format_send_and_stop(self):
        sink = TelegramSink("tok", "1", min_level="WARNING")
        sink.write({"level": "INFO", "message": "skip"})
        self.assertEqual(len(sink._queue), 0)

        sink.write({"level": "ERROR", "message": "boom", "source": "test", "time_str": "now"})
        self.assertEqual(len(sink._queue), 1)
        self.assertIn("[ERROR]", sink._format(sink._queue[0]["entry"]))
        self.assertTrue(sink._should_send("CRITICAL"))
        self.assertFalse(sink._should_send("UNKNOWN"))

        fake_requests = types.SimpleNamespace(post=lambda *args, **kwargs: types.SimpleNamespace(status_code=200, json=lambda: {'ok': True}), RequestException=RuntimeError)
        with patch("llm.telegram_sink.requests", fake_requests), \
             patch("llm.telegram_sink._REQUEST_EXCEPTIONS", (RuntimeError, OSError)):
            self.assertTrue(sink.send_message("hello"))
            self.assertTrue(sink.send_alert("title", "body"))

        with patch("llm.telegram_sink.requests", None), \
             patch("llm.telegram_sink._REQUEST_EXCEPTIONS", (ImportError, OSError)):
            self.assertFalse(sink.send_message("hello"))

        sink.stop()
        self.assertTrue(getattr(sink._worker, "joined", False))

    def test_drain_loop_retry_and_drop(self):
        sink = TelegramSink("tok", "1", max_retries=1, rate_limit_seconds=0.0)
        sink._queue.append({"entry": {"level": "ERROR", "message": "x"}, "attempt": 0, "next_try_at": 0.0})
        sink._stop_event = _OneLoopStopEvent()

        with patch("llm.telegram_sink.time.sleep", return_value=None), \
             patch("llm.telegram_sink.random.uniform", return_value=0.0), \
             patch.object(sink, "_send", return_value=False):
            sink._drain_loop()
        self.assertEqual(len(sink._queue), 1)
        self.assertEqual(sink._queue[0]["attempt"], 1)
        sink._queue[0]["next_try_at"] = 0.0

        sink._stop_event = _OneLoopStopEvent()
        with patch("llm.telegram_sink.time.sleep", return_value=None), \
             patch("llm.telegram_sink.random.uniform", return_value=0.0), \
             patch.object(sink, "_send", return_value=False):
            sink._drain_loop()
        self.assertEqual(sink._dropped_count, 1)

        sink2 = TelegramSink("tok", "1", rate_limit_seconds=0.0)
        sink2._queue.append({"entry": {"level": "ERROR", "message": "ok"}, "attempt": 0, "next_try_at": 0.0})
        sink2._stop_event = _OneLoopStopEvent()
        with patch("llm.telegram_sink.time.sleep", return_value=None), \
             patch.object(sink2, "_send", return_value=True):
            sink2._drain_loop()
        self.assertEqual(len(sink2._queue), 0)

    def test_drain_loop_skip_branches(self):
        sink = TelegramSink("tok", "1", rate_limit_seconds=10.0)
        sink._stop_event = _OneLoopStopEvent()
        with patch("llm.telegram_sink.time.sleep", return_value=None):
            sink._drain_loop()

        sink._queue.append({"entry": {"level": "ERROR", "message": "future"}, "attempt": 0, "next_try_at": 9999999999.0})
        sink._stop_event = _OneLoopStopEvent()
        with patch("llm.telegram_sink.time.sleep", return_value=None):
            sink._drain_loop()
        self.assertEqual(len(sink._queue), 1)

        sink._queue[0]["next_try_at"] = 0.0
        sink._last_send = 9999999999.0
        sink._stop_event = _OneLoopStopEvent()
        with patch("llm.telegram_sink.time.sleep", return_value=None):
            sink._drain_loop()
        self.assertEqual(len(sink._queue), 1)


if __name__ == "__main__":
    unittest.main()