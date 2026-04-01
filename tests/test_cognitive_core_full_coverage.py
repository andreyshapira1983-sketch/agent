# pylint: disable=protected-access,unused-argument
import sys
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.cognitive_core import CognitiveCore, LocalBrain, SolverMode, TaskType


class _LLMStub:
    def __init__(self, result=None, exc=None):
        self.result = result if result is not None else "Ответ с фактами: 1) A 2) B"
        self.exc = exc
        self.calls = []
        self._local = None
        self._light = self
        self._client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content="vision ok"))]
                    )
                )
            )
        )

    def infer(self, prompt, context=None, system=None, history=None):
        self.calls.append((prompt, context, system, history))
        if self.exc:
            raise self.exc
        return self.result


class _ReasoningResult:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return self._payload


class _ReasoningEngine:
    def check_paradox(self, statement):
        if "boom" in statement:
            raise RuntimeError("x")
        return {"paradox": True, "paradox_type": "liar", "resolution": "grounding"}

    def solve(self, variables, domains=None, constraints=None, find_all=None):
        if variables == ["err"]:
            raise RuntimeError("fail")
        return _ReasoningResult({"solutions": [{"x": 1}], "nodes_explored": 2, "is_complete": True})

    def solve_graph_search(self, graph=None, start="A", goal="B", heuristics=None):
        return {
            "verified": True,
            "a_star": {"path": [start, goal], "total_cost": 3.0, "visited_nodes": 2},
            "dijkstra": {"path": [start, goal], "total_cost": 3.0},
        }


class _OptimizationEngine:
    @staticmethod
    def knapsack_analysis(items=None, capacity=0):
        if capacity < 0:
            raise RuntimeError("bad")
        return {
            "dp": {"items_selected": ["A"], "total_cost": 2, "total_value": 5, "is_optimal": True},
            "greedy": {"items_selected": ["B"], "total_value": 3},
            "optimal_value": 5,
            "greedy_gap": 40.0,
            "exhaustive": {"total_value": 5},
        }


class _KnowledgeStub:
    def __init__(self):
        self.saved = []

    def get_relevant_knowledge(self, task=None):
        return "k"

    def get_episodic_memory(self, task=None):
        return "e"

    def store_semantic(self, key, value):
        self.saved.append((key, value))


class _IdentityStub:
    def describe(self):
        return {
            "name": "Agent",
            "role": "Autonomous",
            "mission": "Help",
            "values": ["truth", "safety"],
            "limitations": ["none"],
            "capabilities": [{"name": "x", "available": True}],
        }

    def get_real_capability_inventory(self):
        return {"proven": [{"action": "search"}], "never_tried": ["deploy"]}

    def modules_status_report(self, root):
        return f"report:{root}"


class _PersistentBrainStub:
    def __init__(self):
        self._conversations = []
        self.saved = []
        self.journal = []

    def get_recent_solver_cases(self, limit=None):
        return [{"verification": True, "task": "сравни варианты и выбери лучший"}]

    def record_solver_case(self, **kwargs):
        self.saved.append(kwargs)

    def record_exact_solver_journal(self, **kwargs):
        self.journal.append(kwargs)


def _make_core(llm=None):
    core = object.__new__(CognitiveCore)
    core.llm = llm or _LLMStub()
    core.brain = LocalBrain()
    core.perception = SimpleNamespace(get_data=lambda: {"p": 1})
    core.knowledge = _KnowledgeStub()
    core.human_approval_callback = None
    core.self_improvement = SimpleNamespace(_strategy_store={"general": "do"})
    core.proactive_mind = None
    core.monitoring = None
    core.reflection = SimpleNamespace(get_insights=lambda: ["i1", "i2", "i3", "i4", "i5"])
    core.identity = _IdentityStub()
    core.tool_layer = None
    core.context = None
    core.last_plan = None
    core.last_strategy = None
    core.last_reasoning = None
    core.last_decision = None
    core.last_hypothesis = None
    core.last_dialog = None
    core.last_problem_solution = None
    core.last_code = None
    core.persistent_brain = _PersistentBrainStub()  # type: ignore[assignment]
    core._recent_user_requests = []
    core._duplicate_window_sec = 1800
    core._max_recent_requests = 50
    core._semantic_duplicate_threshold = 0.72
    core._last_portfolio_idx = -1
    core.last_solver_metadata = None
    core.reasoning_engine = _ReasoningEngine()  # type: ignore[assignment]
    core.optimization_engine = _OptimizationEngine  # type: ignore[assignment]
    return core


class LocalBrainCoverageTests(unittest.TestCase):
    def test_local_brain_core_paths(self):
        b = LocalBrain()
        self.assertEqual(b._min_response_length("привет", TaskType.CONVERSATION), 1)
        self.assertEqual(b.classify_task("почему не работает код"), TaskType.DIAGNOSIS)
        self.assertEqual(b.classify_task("привет"), TaskType.TRIVIAL)
        self.assertEqual(b.classify_task("архитектура агента"), TaskType.RESEARCH)
        self.assertEqual(b.classify_task("сделай план"), TaskType.PLANNING)
        self.assertEqual(b.classify_task("сравни варианты"), TaskType.DECISION)
        self.assertEqual(b.classify_task("исследуй тему"), TaskType.RESEARCH)
        self.assertEqual(b.classify_task("что-то сложное"), TaskType.COMPLEX)

        self.assertFalse(b.needs_llm("", TaskType.CODE))
        self.assertFalse(b.needs_llm("привет", TaskType.TRIVIAL))
        self.assertFalse(b.needs_llm("как дела сегодня", TaskType.CONVERSATION))
        self.assertTrue(b.needs_llm("сделай подробный анализ архитектуры проекта", TaskType.RESEARCH))

        b.persistent_brain = _PersistentBrainStub()  # type: ignore[assignment]
        self.assertFalse(b.needs_llm("сравни варианты и выбери лучший путь", TaskType.DECISION))

        out = b.local_answer("status", TaskType.DECISION)
        self.assertIn("Статус", out)
        self.assertIn("локальных решений", out)
        self.assertIn("pong", b.local_answer("ping", TaskType.CODE))
        self.assertIn("Доступные команды", b.local_answer("help", TaskType.CODE))
        self.assertIn("решено локально", b.local_answer("другое", TaskType.CODE))

    def test_local_brain_dialog_memory_and_loop_and_templates(self):
        b = LocalBrain()
        self.assertEqual(b._local_memory_hint(), "")
        b.persistent_brain = SimpleNamespace(_conversations=[{"message": "Вчера обсуждали цели"}], stats={})  # type: ignore[assignment]
        self.assertIn("последний разговор", b._local_memory_hint())
        b.persistent_brain = SimpleNamespace(_conversations=[], stats={"boot_count": 2, "total_conversations": 3})  # type: ignore[assignment]
        self.assertIn("запусков", b._local_memory_hint())

        self.assertIn("не подключена", b._local_conversation_answer("привет"))
        b.local_llm = SimpleNamespace(infer=lambda task, system=None, history=None: "ok")
        self.assertEqual(b._local_conversation_answer("hi"), "ok")
        b.local_llm = SimpleNamespace(infer=lambda task, system=None, history=None: "")
        self.assertIn("не вернула", b._local_conversation_answer("hi"))
        b.local_llm = SimpleNamespace(infer=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertIn("упала", b._local_conversation_answer("hi"))

        for _ in range(5):
            b.detect_loop("повтори это сообщение")
        self.assertTrue(b.detect_loop("повтори это сообщение"))
        self.assertIn("Петля обнаружена", b.suggest_loop_break("x"))

        self.assertTrue(b.validate_response("12345678901234567890", task_type=TaskType.CONVERSATION)[0])
        self.assertFalse(b.validate_response("коротко", task_type=TaskType.CODE)[0])
        self.assertFalse(b.validate_response("it depends", task_type=TaskType.RESEARCH)[0])
        self.assertFalse(b.validate_response("просто слова без структуры", task_type=TaskType.RESEARCH)[0])
        self.assertFalse(b.validate_response("план без команд", task_type=TaskType.PLANNING)[0])
        self.assertFalse(b.validate_response("не код", task_type=TaskType.CODE)[0])
        self.assertFalse(b.validate_response("причина только", task_type=TaskType.DIAGNOSIS)[0])
        self.assertFalse(b.validate_response("рекомендую", task_type=TaskType.DECISION)[0])
        self.assertFalse(b.validate_response("I don't have access to real-time data", task_type=TaskType.RESEARCH)[0])
        self.assertFalse(b.validate_response("вам нужно установить пакет", task_type=TaskType.CODE)[0])
        self.assertFalse(b.validate_response("I cannot", task_type=TaskType.RESEARCH)[0])
        self.assertTrue(b.validate_response("SEARCH: x\n```bash\necho 1\n```", task_type=TaskType.PLANNING)[0])

        b.set_llm_unavailable("x")
        self.assertFalse(b.llm_is_available)
        b.set_quota_exhausted()
        self.assertTrue(b.quota_is_exhausted)
        self.assertFalse(b.should_retry_llm())
        b.set_llm_available()
        self.assertTrue(b.llm_is_available)

        self.assertIn("LLM недоступен", b.offline_plan("сделай бота", TaskType.CODE))
        self.assertTrue(len(b.offline_plan("поговорим", TaskType.CONVERSATION)) > 0)
        self.assertIn("SEARCH", b.offline_plan("исследуй тему python", TaskType.RESEARCH))
        self.assertIn("READ", b.offline_plan("исправь ошибку в логе", TaskType.DIAGNOSIS))

        self.assertIn("Telethon", b.offline_code("telegram bot"))
        self.assertIn("BeautifulSoup", b.offline_code("web crawler"))
        self.assertIn("requests", b.offline_code("api client"))
        self.assertIn("csv", b.offline_code("обработай файл csv"))
        self.assertIn("psutil", b.offline_code("системный монитор"))
        self.assertIn("outputs", b.offline_code("unknown"))

        self.assertTrue(LocalBrain._extract_search_keywords("the and or") == ["the and or"])
        self.assertEqual(LocalBrain._guess_filename("telegram video downloader"), "telegram_downloader.py")
        self.assertEqual(LocalBrain._guess_filename("telegram bot"), "telegram_bot.py")
        self.assertEqual(LocalBrain._guess_filename("scrape site"), "scraper.py")
        self.assertEqual(LocalBrain._guess_filename("api request"), "api_client.py")
        self.assertEqual(LocalBrain._guess_filename("system monitor"), "monitor.py")
        self.assertEqual(LocalBrain._guess_filename("new bot"), "bot.py")
        self.assertEqual(LocalBrain._guess_filename("other"), "script.py")

        self.assertIn("Telethon", LocalBrain._template_telegram_downloader())
        self.assertIn("TelegramClient", LocalBrain._template_telegram_bot())
        self.assertIn("BeautifulSoup", LocalBrain._template_web_scraper())
        self.assertIn("HTTP API", LocalBrain._template_api_client())
        self.assertIn("Обработчик файлов", LocalBrain._template_file_processor())
        self.assertIn("Монитор системных ресурсов", LocalBrain._template_system_monitor())

    def test_build_prompt_choose_and_stats(self):
        b = LocalBrain()
        ctx = {"knowledge": "k", "episodic_memory": "e", "recent_insights": ["a", "b"]}
        strategies = {"s1": "do1", "s2": "do2"}
        self.assertIn("ПОЛНЫЙ рабочий код", b.build_focused_prompt("task", TaskType.CODE, ctx, strategies))
        self.assertIn("ТОЛЬКО исполняемые действия", b.build_focused_prompt("task", TaskType.PLANNING, ctx, strategies))
        exec_prompt = b.build_focused_prompt("SEARCH: x", TaskType.PLANNING, ctx, strategies)
        self.assertTrue(exec_prompt.startswith("SEARCH: x"))
        self.assertIn("Контекст знаний", exec_prompt)
        self.assertIn("Назови причину", b.build_focused_prompt("task", TaskType.DIAGNOSIS, ctx, strategies))
        self.assertIn("Структурированный ответ", b.build_focused_prompt("task", TaskType.RESEARCH, ctx, strategies))
        self.assertIn("Сравни варианты", b.build_focused_prompt("task", TaskType.DECISION, ctx, strategies))
        self.assertEqual("hello", b.build_focused_prompt("hello", TaskType.CONVERSATION, ctx, strategies))
        self.assertIn("Проанализируй", b.build_focused_prompt("task", TaskType.COMPLEX, ctx, strategies))

        self.assertEqual("", b.choose_best_option([]))
        best = b.choose_best_option(["возможно попробовать", "исправь 2 ошибки и запусти проверку"])
        self.assertIn("исправь", best)
        b.record_llm_call()
        st = b.stats()
        self.assertIn("llm_calls", st)


class CognitiveCoreSolverAndRoutingTests(unittest.TestCase):
    def test_solver_parsers_and_pipeline(self):
        core = _make_core()
        task = (
            "ресурс=10\n"
            "A: reward=100 cost=6 p=0.9\n"
            "B: reward=60 cost=4 p=0.8\n"
            "выбери лучший вариант"
        )
        parsed = core._parse_resource_allocation(task)
        assert parsed is not None
        self.assertEqual(parsed["capacity"], 10)
        self.assertEqual(len(parsed["items"]), 2)

        pprob = core._parse_probabilistic_choice("Opt A:\nreward=10\nprobability=0.5\nOpt B:\nreward=8\nprobability=0.7")
        self.assertEqual(len((pprob or {}).get("options", [])), 2)

        pproj = core._parse_project_selection(
            "only 2 tasks\nProject X\nincome=100\nrequires: task1 + task2\nProject Y\nincome=50\nrequires: task1"
        )
        assert pproj is not None
        self.assertEqual(pproj["budget"], 2)

        pgraph = core._parse_graph_search("start=A goal=D\nA->B:2\nB->D:1\nheuristic A=2")
        assert pgraph is not None
        self.assertEqual(pgraph["start"], "A")

        route = core._route_solver(task)
        self.assertEqual(route["solver"], "DP_knapsack")
        self.assertEqual(core._classify_solver_task("без csp и sat, просто текст")["mode"], SolverMode.REASONING.value)
        self.assertTrue(core._has_graph_solver_markers("найди кратчайший путь из A в B"))
        self.assertTrue(core._has_solver_goal_markers("maximize reward"))
        self.assertFalse(core._has_formal_constraint_markers("без csp и sat"))

        self.assertIsNone(core._run_exact_solver_pipeline("Составь список ИСПОЛНЯЕМЫХ действий"))
        solved = core._run_exact_solver_pipeline(task)
        self.assertIsNotNone(solved)
        assert solved is not None
        self.assertIn("metadata", solved)
        self.assertIn("PROVEN", core._format_solver_solution(solved))

        core.reasoning_engine = None
        self.assertIsNone(core._solve_graph_search("start=A goal=B\nA->B:1"))

    def test_verification_helpers_and_calibration(self):
        core = _make_core()
        vr = core._verify_resource_solution(
            "resource=10\nA: reward=5 cost=2",
            {"optimal_value": 5, "dp": {"is_optimal": True}, "exhaustive": {"total_value": 5}},
        )
        self.assertTrue(vr["verification"])

        vp = core._verify_probabilistic_solution(
            "A:\nreward=10\nprobability=0.5\nB:\nreward=1\nprobability=0.2",
            {"result": {"best_option": "A"}},
        )
        self.assertTrue(vp["verification"])

        vc = core._verify_constraint_solution("x", {"result": {"is_optimal": False, "alternatives_checked": 2}})
        self.assertFalse(vc["verification"])

        vg = core._verify_graph_solution(
            {"result": {"verified": True, "a_star": {"total_cost": 2, "visited_nodes": 3}, "dijkstra": {"total_cost": 2}}}
        )
        self.assertTrue(vg["verification"])

        self.assertGreaterEqual(core._calibrate_confidence(True, 1.0, "DP_knapsack"), 0.9)
        self.assertLess(core._calibrate_confidence(False, 0.0, "heuristic"), 0.2)


class CognitiveCoreFlowTests(unittest.TestCase):
    def test_context_llm_call_and_top_level_flows(self):
        core = _make_core()
        ctx = core.build_context("задача")
        self.assertIn("recent_insights", ctx)
        self.assertIn("self_identity", ctx)

        self.assertIn("general", core._get_relevant_strategies("anything"))

        core.brain.detect_loop = lambda task: True
        core.brain.suggest_loop_break = lambda task: "loop"
        self.assertEqual(core._llm_call("x"), "loop")

        core = _make_core()
        core.brain.needs_llm = lambda task, task_type: False
        core.brain.local_answer = lambda task, task_type=None, history=None: "local"
        self.assertEqual(core._llm_call("x"), "local")

        core = _make_core()
        core.brain.local_coder_llm = SimpleNamespace(infer=lambda *args, **kwargs: "coder")
        core.brain.needs_llm = lambda task, task_type: True
        self.assertEqual(core._llm_call("напиши рабочий код для теста", task_type=TaskType.CODE), "coder")

        core = _make_core(_LLMStub(exc=RuntimeError("connection timeout")))
        core.brain.needs_llm = lambda task, task_type: True
        out = core._llm_call("сделай подробный исследовательский отчёт", task_type=TaskType.RESEARCH)
        self.assertIn("[LocalBrain:offline]", out)

        core = _make_core(_LLMStub(exc=RuntimeError("insufficient_quota")))
        core.brain.needs_llm = lambda task, task_type: True
        with self.assertRaises(RuntimeError):
            core._llm_call("сделай полный исследовательский отчёт по теме", task_type=TaskType.RESEARCH)

        core = _make_core(_LLMStub(result="коротко"))
        rejected = core._llm_call("задача кода", task_type=TaskType.CODE)
        self.assertIn("Ответ LLM отклонён", rejected)

        core = _make_core()
        core._run_exact_solver_pipeline = lambda task: {"metadata": {"optimality": "proven"}, "result": {}}
        core._format_solver_solution = lambda solution: "fmt"
        self.assertEqual(core.reasoning("q"), "fmt")
        self.assertEqual(core.plan("q"), "fmt")
        self.assertEqual(core.problem_solving("q"), "fmt")

        core = _make_core()
        core._llm_call = lambda *args, **kwargs: "ans"
        self.assertEqual(core.strategy_generator("g"), "ans")
        self.assertEqual(core.generate_hypothesis("h"), "ans")
        self.assertEqual(core.code_generation("c"), "ans")

    def test_reasoning_and_optimization_methods(self):
        core = _make_core()
        self.assertTrue(core.detect_logical_paradox("stmt")["paradox"])
        self.assertIn("error", core.detect_logical_paradox("boom"))

        self.assertIn("solutions", core.solve_csp(["x"], {"x": [1]}, []))
        self.assertIn("error", core.solve_csp(["err"], {}, []))

        self.assertIn("optimal_value", core.optimize_knapsack([], 10))
        self.assertIn("error", core.optimize_knapsack([], -1))

        core.optimization_engine = None
        self.assertIn("error", core.optimize_scheduling([]))

        core = _make_core()
        with patch("builtins.__import__", side_effect=ImportError("bad")):
            self.assertIn("error", core.optimize_scheduling([]))

    def test_decision_and_plan_callbacks(self):
        core = _make_core()
        core._run_exact_solver_pipeline = lambda task: None
        core.brain.choose_best_option = lambda options: "best"
        self.assertIn("best", core.decision_making(["a", "b"]))

        core = _make_core()
        core._run_exact_solver_pipeline = lambda task: None
        core._llm_call = lambda *args, **kwargs: "plan"
        core.human_approval_callback = lambda kind, payload: False
        self.assertIn("отклонён", core.plan("goal"))
        self.assertIn("отклонено", core.decision_making(["x" * 200]))


class CognitiveCoreIntentAndHandlersTests(unittest.TestCase):
    def test_duplicate_helpers(self):
        core = _make_core()
        self.assertEqual(CognitiveCore._normalize_message(" Привет!!! "), "привет")
        self.assertTrue("hello" in CognitiveCore._tokenize_for_similarity("hello the world"))
        self.assertEqual(CognitiveCore._extract_first_number("x 12,5 y"), "12.5")
        self.assertIsNone(CognitiveCore._extract_first_number("no"))
        self.assertTrue(CognitiveCore._is_result_number_confusion("ты перепутал, 153 это результат, не номер"))
        self.assertFalse(CognitiveCore._is_result_number_confusion("очень длинный " + "word " * 50 + " 10"))
        self.assertGreater(core._semantic_similarity("abc", "abc"), 0.99)
        self.assertEqual(core._semantic_similarity("", "x"), 0.0)
        self.assertTrue(CognitiveCore._has_meaningful_numeric_mismatch("20 источников", "30 источников"))

        core._remember_request("дай 20 источников", "ok")
        self.assertIsNotNone(core._find_duplicate("дай 20 источников"))
        self.assertIsNone(core._find_duplicate("дай 30 источников"))

        core.persistent_brain._conversations = [  # type: ignore[union-attr]
            {"role": "user", "time": time.time(), "message": "похожая просьба", "response": "resp"}
        ]
        self.assertIsNotNone(core._find_duplicate("похожая просьба"))

    def test_detect_intent_and_converse_branches(self):
        core = _make_core()
        core._llm_call = lambda *args, **kwargs: "research"
        self.assertEqual(core._detect_query_intent("длинный запрос без ключей но с семью словами точно"), "research")
        self.assertEqual(core._detect_query_intent("привет"), "conversation")
        self.assertEqual(core._detect_query_intent("переведи этот текст на английский пожалуйста сейчас"), "translate")
        self.assertEqual(core._detect_query_intent("создай модуль x для обработки данных сейчас"), "build_module")
        self.assertEqual(core._detect_query_intent("создай pdf файл с отчётом по проекту"), "create_output")
        self.assertEqual(core._detect_query_intent("какие у тебя инструменты и чем ты владеешь сейчас"), "self_profile")
        self.assertEqual(core._detect_query_intent("прочитай экран и скажи что на нём написано сейчас"), "screenshot")
        self.assertEqual(core._detect_query_intent("зайди на сайт и прочитай страницу с вакансиями"), "browser")
        self.assertEqual(core._detect_query_intent("портфолио мои проекты перечисли полностью прямо сейчас пожалуйста"), "portfolio")
        self.assertEqual(core._detect_query_intent("найди вакансии на upwork и проверь свежие предложения"), "job_hunt")
        self.assertEqual(core._detect_query_intent("сохраняешь ли сайты и хранишь ли тексты после задачи сейчас"), "knowledge_memory")
        self.assertEqual(core._detect_query_intent("cover letter for job posting please respond quickly now"), "cover_letter")
        self.assertEqual(core._detect_query_intent("какие модули у меня активны и какие подсистемы доступны"), "my_modules")
        self.assertEqual(core._detect_query_intent("исследуй рынок и собери данные"), "research")
        self.assertEqual(core._detect_query_intent("архитектура системы"), "architecture")
        self.assertEqual(core._detect_query_intent("написать код"), "code")
        self.assertEqual(core._detect_query_intent("ошибка traceback"), "debugging")
        self.assertEqual(core._detect_query_intent("сделай план"), "planning")
        self.assertEqual(core._detect_query_intent("написать unit test"), "test")
        self.assertEqual(core._detect_query_intent("напиши документ"), "document")
        self.assertEqual(core._detect_query_intent("выбери лучший вариант"), "decision")

        core = _make_core()
        core._detect_query_intent = lambda query: "research"
        core._llm_call = lambda *args, **kwargs: "ok"
        self.assertEqual(core.converse("очень длинный уникальный запрос для проверки антидубля"), "ok")

        core = _make_core()
        core._llm_call = lambda *args, **kwargs: "перевод"
        core._detect_query_intent = lambda query: "translate"
        self.assertEqual(core.converse("x"), "перевод")

        core._detect_query_intent = lambda query: "conversation"
        self.assertEqual(core.converse("x"), "перевод")

    def test_handlers(self):
        core = _make_core()
        self.assertIsNone(CognitiveCore._extract_portfolio_project_index("", 0))
        self.assertEqual(CognitiveCore._extract_portfolio_project_index("project #2", 3), 1)

        mod = types.ModuleType("skills.portfolio_builder")
        setattr(mod, 'ANDREY_PORTFOLIO_PROJECTS', [{"title": "A"}, {"title": "B"}])

        class PB:
            def __init__(self, llm=None, telegram_bot=None, telegram_chat_id=None):
                pass

            def get_project(self, idx):
                projects = getattr(mod, 'ANDREY_PORTFOLIO_PROJECTS', [])
                return projects[idx] if idx < len(projects) else None

            def format_for_chat(self, p, idx):
                return f"fmt:{idx}:{p.get('title')}"

        setattr(mod, 'PortfolioBuilder', PB)
        sys.modules["skills.portfolio_builder"] = mod

        self.assertIn("fmt", core._handle_portfolio("все проекты"))

        loop = SimpleNamespace(
            job_hunter=SimpleNamespace(_last_run=0.0, _seen_uids={1, 2}, _run_interval=300.0, hunt=lambda: 2)
        )
        core.proactive_mind = SimpleNamespace(loop=loop)
        self.assertIn("Найдено", core._handle_job_hunt("x"))

        ks = SimpleNamespace(_long_term={"acquired:a": 1, "acquired_hash:b": 2}, _episodic=[1], _short_term=[1, 2])
        ap = SimpleNamespace(queue_size=lambda: 3, _processed=[1], quality_threshold=0.5)
        core.proactive_mind = SimpleNamespace(loop=SimpleNamespace(knowledge_system=ks, acquisition_pipeline=ap))
        self.assertIn("long_term", core._handle_knowledge_memory(""))

        loop = SimpleNamespace(
            _cycle_count=7,
            _running=True,
            _goal="goal",
            _consecutive_failures=1,
            _subgoal_queue=["sg"],
            persistent_brain=SimpleNamespace(_lessons=[1], _conversations=[1]),
            skill_library=SimpleNamespace(_skills={"a": 1}),
            tool_layer=SimpleNamespace(_tools={"t": 1}),
            identity=_IdentityStub(),
        )
        core.proactive_mind = SimpleNamespace(loop=loop)
        self.assertIn("Сейчас работаю", core._handle_self_profile("статус"))
        self.assertIn("Работаю в штатном режиме", core._handle_self_profile("как дела"))
        self.assertIn("Роль", core._handle_self_profile("кто ты"))

        self.assertIn("ModuleBuilder не подключён", core._handle_build_module("создай модуль"))
        setattr(core, 'module_builder', SimpleNamespace(build_module=lambda **kwargs: SimpleNamespace(ok=True, file_path="x.py", class_name="X")))
        self.assertIn("Укажи имя", core._handle_build_module("создай модуль | "))
        self.assertIn("создан успешно", core._handle_build_module("создай модуль test | desc"))
        setattr(core, 'module_builder', SimpleNamespace(build_module=lambda **kwargs: SimpleNamespace(ok=False, error="bad")))
        self.assertIn("Не удалось", core._handle_build_module("создай модуль test | desc"))

        core._llm_call = lambda *args, **kwargs: "нет блока"
        self.assertIn("не вернул блок", core._handle_create_output("создай pdf"))

        core._llm_call = lambda *args, **kwargs: "```python\nprint('x')\n```"
        core.proactive_mind = SimpleNamespace(loop=SimpleNamespace(action_dispatcher=None))
        self.assertIn("недоступен", core._handle_create_output("создай pdf"))

        core.identity = _IdentityStub()
        core.proactive_mind = SimpleNamespace(loop=SimpleNamespace(identity=_IdentityStub()))
        self.assertIn("report:", core._handle_my_modules(""))

        bmod = types.ModuleType("skills.browser_agent")

        class BA:
            def __init__(self, llm=None):
                pass

            def browse(self, task):
                return None

        setattr(bmod, 'BrowserAgent', BA)
        sys.modules["skills.browser_agent"] = bmod
        self.assertIn("Открываю браузер", core._handle_browser("задача"))

        with patch("builtins.__import__", side_effect=ImportError("no pillow")):
            self.assertIn("Pillow", core._handle_screenshot("x"))

    def test_converse_switches_all_intents(self):
        core = _make_core()
        core._analyze_agent_architecture = lambda query: "arch"  # type: ignore[assignment]
        core._llm_call = lambda *args, **kwargs: "llm"
        core._handle_build_module = lambda message: "bm"  # type: ignore[assignment]
        core._handle_create_output = lambda message: "co"  # type: ignore[assignment]
        core._handle_screenshot = lambda question="": "ss"  # type: ignore[assignment]
        core._handle_self_profile = lambda _message="": "sp"  # type: ignore[assignment]
        core._handle_knowledge_memory = lambda _message="": "km"  # type: ignore[assignment]
        core._handle_my_modules = lambda _message="": "mm"  # type: ignore[assignment]
        core._handle_job_hunt = lambda m: "jh"  # type: ignore[assignment]
        core._handle_browser = lambda m: "br"  # type: ignore[assignment]
        core._handle_portfolio = lambda m: "pf"  # type: ignore[assignment]
        intents = [
            "architecture", "code", "debugging", "planning", "test", "document", "decision", "research",
            "build_module", "create_output", "screenshot", "self_profile", "knowledge_memory", "my_modules",
            "job_hunt", "browser", "portfolio", "translate", "cover_letter", "conversation",
        ]
        for intent in intents:
            core._detect_query_intent = lambda query, intent=intent: intent  # type: ignore[assignment]
            out = core.converse("длинный текст запроса чтобы пройти проверки и ветки")
            self.assertTrue(isinstance(out, str) and len(out) > 0)

    def test_plan_create_output_and_architecture_analysis(self):
        core = _make_core()
        core._run_exact_solver_pipeline = lambda task: None  # type: ignore[assignment]
        core._llm_call = lambda *args, **kwargs: "SEARCH: x"
        core.tool_layer = SimpleNamespace(_tools={"python_runtime": SimpleNamespace(description="x")})
        out_plan = core.plan("построй план")
        self.assertIn("SEARCH", out_plan)

        outputs_dir = Path(__file__).resolve().parents[1] / "outputs"
        outputs_dir.mkdir(exist_ok=True)
        target = outputs_dir / "created_by_test.txt"
        if target.exists():
            target.unlink()

        def _use(tool_name, code=None):
            target.write_text("ok", encoding="utf-8")
            return {"output": str(target), "error": ""}

        loop = SimpleNamespace(
            action_dispatcher=SimpleNamespace(dispatch=lambda cmd, goal="": {"results": [{"output": "", "error": ""}]}),
            tool_layer=SimpleNamespace(use=_use),
        )
        core.proactive_mind = SimpleNamespace(loop=loop)
        core._llm_call = lambda *args, **kwargs: "```python\nprint('x')\n```"
        create_out = core._handle_create_output("создай файл")
        self.assertIn("Файл создан", create_out)

        cap = SimpleNamespace(find_gaps=lambda: ["missing_package:requests", "weak_action:test"])
        gm = SimpleNamespace(add_goal=lambda *args, **kwargs: None, get_active_goals=lambda: ["g1", "g2"])
        sr = SimpleNamespace(get_metrics=lambda: {"total_incidents": 2, "fixed": 1, "failed": 1})
        replay_buf = [SimpleNamespace(success=True), SimpleNamespace(success=False)]
        replay = SimpleNamespace(size=2, _buffer=replay_buf)
        proposal = SimpleNamespace(status="proposed", area="planning")
        si = SimpleNamespace(_proposals=[proposal], analyse_and_propose=lambda max_proposals=3: [proposal])
        cycle = SimpleNamespace(success=True)
        loop = SimpleNamespace(
            capability_discovery=cap,
            goal_manager=gm,
            self_repair=sr,
            experience_replay=replay,
            self_improvement=si,
            _cycle_count=5,
            _consecutive_failures=0,
            _history=[cycle, cycle],
        )
        core.proactive_mind = SimpleNamespace(loop=loop)
        report = core._analyze_agent_architecture("проанализируй архитектуру")
        self.assertIn("Проанализировал себя", report)


class ValidateResponseExtendedTests(unittest.TestCase):
    """Покрытие всех ветвей validate_response."""

    def setUp(self):
        self.b = LocalBrain()

    def test_weak_phrases(self):
        ok, _ = self.b.validate_response("это зависит от контекста", task_type=TaskType.RESEARCH)
        self.assertFalse(ok)

    def test_low_uniqueness(self):
        # >40 слов но все одинаковые
        text = " ".join(["слово"] * 50)
        ok, msg = self.b.validate_response(text, task_type=TaskType.COMPLEX)
        self.assertFalse(ok)
        self.assertIn("информативность", msg)

    def test_research_structured_pass(self):
        ok, _ = self.b.validate_response(
            "Результаты:\n- Первый пункт: факт\n- Второй: ещё факт",
            task_type=TaskType.RESEARCH,
        )
        self.assertTrue(ok)

    def test_research_unstructured_short_fail(self):
        ok, _ = self.b.validate_response("просто текст без структуры", task_type=TaskType.RESEARCH)
        self.assertFalse(ok)

    def test_realtime_refusal(self):
        ok, _ = self.b.validate_response(
            "не могу предоставить актуальные данные сейчас", task_type=TaskType.COMPLEX,
        )
        self.assertFalse(ok)

    def test_prose_action_marker(self):
        ok, _ = self.b.validate_response(
            "для работы с этим вам нужно установить пакет", task_type=TaskType.CODE,
        )
        self.assertFalse(ok)

    def test_short_refusal(self):
        ok, _ = self.b.validate_response("я не могу это сделать", task_type=TaskType.COMPLEX)
        self.assertFalse(ok)

    def test_diagnosis_markers(self):
        ok, _ = self.b.validate_response(
            "причина в том что X, исправление: Y, проверь Z",
            task_type=TaskType.DIAGNOSIS,
        )
        self.assertTrue(ok)

    def test_decision_markers(self):
        ok, _ = self.b.validate_response(
            "рекомендую вариант A, риски минимальны, ресурсы достаточны",
            task_type=TaskType.DECISION,
        )
        self.assertTrue(ok)

    def test_code_looks_like_code(self):
        ok, _ = self.b.validate_response(
            "import os\ndef main():\n    pass\nif __name__ == '__main__':\n    main()",
            task_type=TaskType.CODE,
        )
        self.assertTrue(ok)

    def test_long_response_not_refused(self):
        ok, _ = self.b.validate_response("я не могу " + "x " * 200, task_type=TaskType.COMPLEX)
        self.assertTrue(ok)


class OfflinePlanExtendedTests(unittest.TestCase):
    """Покрытие offline_plan ветвей для разных task_type и variant."""

    def setUp(self):
        self.b = LocalBrain()

    def test_planning_creation_variants(self):
        for _ in range(4):
            out = self.b.offline_plan("создай бота для обработки данных", TaskType.PLANNING)
            self.assertIn("LLM недоступен", out)

    def test_planning_research(self):
        out = self.b.offline_plan("изучи какие API есть для данных", TaskType.PLANNING)
        self.assertIn("SEARCH", out)

    def test_research_variants(self):
        self.b._offline_variant = 0
        self.b._last_offline_goal = None  # type: ignore[assignment]
        for i in range(4):
            out = self.b.offline_plan(f"исследуй тему номер {i}", TaskType.RESEARCH)
            self.assertIn("SEARCH", out)

    def test_diagnosis_variants(self):
        out = self.b.offline_plan("ошибка в программе нужна диагностика", TaskType.DIAGNOSIS)
        self.assertIn("LLM недоступен", out)

    def test_other_types(self):
        out = self.b.offline_plan("что-то про задачу", TaskType.DECISION)
        self.assertIn("SEARCH", out)

    def test_empty_actions_fallback(self):
        out = self.b.offline_plan("", TaskType.TRIVIAL)
        self.assertIn("SEARCH", out)


class CognitiveCoreInitTests(unittest.TestCase):
    """Покрытие __init__ через настоящий конструктор."""

    def test_init_normal(self):
        llm = _LLMStub()
        core = CognitiveCore(llm)
        self.assertIsNotNone(core.brain)
        self.assertIsNone(core.tool_layer)
        self.assertEqual(core._last_portfolio_idx, -1)

    def test_init_with_local_backend(self):
        llm = _LLMStub()
        llm._local = SimpleNamespace(infer=lambda *a, **k: "local")  # type: ignore[assignment]
        core = CognitiveCore(llm)
        self.assertEqual(core.brain.local_llm, llm._local)

    def test_init_with_all_components(self):
        core = CognitiveCore(
            _LLMStub(),
            perception=SimpleNamespace(get_data=lambda: {}),
            knowledge=_KnowledgeStub(),
            identity=_IdentityStub(),
            reflection=SimpleNamespace(get_insights=lambda: []),
        )
        self.assertIsNotNone(core.knowledge)


class SolverEdgeCaseTests(unittest.TestCase):
    """Покрытие _solve_expected_value, _solve_project_selection, _solve_graph_search."""

    def test_solve_expected_value(self):
        core = _make_core()
        result = core._solve_probabilistic_choice(
            "Opt A:\nreward=10\nprobability=0.5\nOpt B:\nreward=8\nprobability=0.7"
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["result"]["best_option"], "Opt B")

    def test_solve_project_selection(self):
        core = _make_core()
        result = core._solve_project_selection(
            "only 2 tasks\nProject X\nincome=100\nrequires: task1 + task2\nProject Y\nincome=50\nrequires: task1"
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("result", result)

    def test_solve_project_selection_none(self):
        core = _make_core()
        result = core._solve_project_selection("no parseable data here")
        self.assertIsNone(result)

    def test_solve_graph_search(self):
        core = _make_core()
        result = core._solve_graph_search("start=A goal=D\nA->B:2\nB->D:1\nheuristic A=2")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("metadata", result)

    def test_solve_graph_no_parsed(self):
        core = _make_core()
        result = core._solve_graph_search("no graph here")
        self.assertIsNone(result)

    def test_solve_graph_no_engine(self):
        core = _make_core()
        core.reasoning_engine = None
        result = core._solve_graph_search("start=A goal=D\nA->B:2\nB->D:1\nheuristic A=2")
        self.assertIsNone(result)


class ConverseFullFlowTests(unittest.TestCase):
    """Покрытие converse() — дубликаты, number confusion, cover letter, offline."""

    def test_number_confusion(self):
        core = _make_core()
        out = core.converse("ты перепутал, 153 это результат, не номер")
        self.assertIn("153", out)
        self.assertIn("значение результата", out)

    def test_duplicate_exact(self):
        core = _make_core()
        core._detect_query_intent = lambda query: "research"  # type: ignore[assignment]
        core._llm_call = lambda *a, **kw: "first answer"
        core.converse("уникальный длинный запрос для проверки дубликатов который не короткий")
        # Повтор
        out = core.converse("уникальный длинный запрос для проверки дубликатов который не короткий")
        self.assertIn("уже был", out)

    def test_duplicate_semantic(self):
        core = _make_core()
        core._detect_query_intent = lambda query: "research"  # type: ignore[assignment]
        core._llm_call = lambda *a, **kw: "ответ"
        core._remember_request("исследуй тему анализа данных подробно", "ответ")
        dup = core._find_duplicate("исследуй тему анализа данных подробно")
        self.assertIsNotNone(dup)

    def test_cover_letter_no_fit(self):
        core = _make_core()
        core._detect_query_intent = lambda query: "cover_letter"  # type: ignore[assignment]
        core._llm_call = lambda *a, **kw: "FIT: no — требуется японский язык"
        out = core.converse("Senior Japanese Developer needed for onsite work in Tokyo")
        self.assertIn("не подходит", out)

    def test_cover_letter_fit(self):
        core = _make_core()
        call_count = [0]

        def _mock_llm(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return "FIT: yes"
            return "Dear hiring manager, I am Andrey..."

        core._detect_query_intent = lambda query: "cover_letter"  # type: ignore[assignment]
        core._llm_call = _mock_llm
        out = core.converse("Python developer needed for remote AI automation work")
        self.assertIn("Andrey", out)

    def test_offline_plan_hidden(self):
        core = _make_core()
        core._detect_query_intent = lambda query: "code"  # type: ignore[assignment]
        core._llm_call = lambda *a, **kw: "[LocalBrain:offline] LLM недоступен — план по шаблону."
        out = core.converse("напиши длинный запрос который точно не короткий")
        self.assertIn("недоступен", out)

    def test_conversation_default(self):
        core = _make_core()
        core._detect_query_intent = lambda query: "conversation"  # type: ignore[assignment]
        core._llm_call = lambda *a, **kw: "Привет!"
        out = core.converse("как дела")
        self.assertEqual(out, "Привет!")

    def test_translate_intent(self):
        core = _make_core()
        core._detect_query_intent = lambda query: "translate"  # type: ignore[assignment]
        core._llm_call = lambda *a, **kw: "Hello world"
        out = core.converse("переведи привет мир на английский пожалуйста сейчас")
        self.assertEqual(out, "Hello world")

    def test_brain_stats(self):
        core = _make_core()
        st = core.brain_stats()
        self.assertIn("llm_calls", st)


class ArchitectureAnalysisExtendedTests(unittest.TestCase):
    """Покрытие _analyze_agent_architecture — все 7 секций + edge cases."""

    def _make_loop(self, **overrides):
        defaults = dict(
            capability_discovery=SimpleNamespace(find_gaps=lambda: []),
            goal_manager=SimpleNamespace(
                add_goal=lambda *a, **k: None,
                get_active_goals=lambda: ["goal1"],
            ),
            self_repair=SimpleNamespace(
                get_metrics=lambda: {"total_incidents": 3, "fixed": 2, "failed": 1},
            ),
            experience_replay=SimpleNamespace(
                size=5,
                _buffer=[
                    SimpleNamespace(success=True),
                    SimpleNamespace(success=False),
                    SimpleNamespace(success=True),
                    SimpleNamespace(success=True),
                    SimpleNamespace(success=False),
                ],
            ),
            self_improvement=SimpleNamespace(
                _proposals=[
                    SimpleNamespace(status="proposed", area="logging"),
                    SimpleNamespace(status="applied", area="caching"),
                ],
                analyse_and_propose=lambda max_proposals=3: [
                    SimpleNamespace(area="testing"),
                ],
            ),
            _cycle_count=100,
            _consecutive_failures=2,
            _history=[
                SimpleNamespace(success=True),
                SimpleNamespace(success=True),
                SimpleNamespace(success=False),
            ],
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_full_report(self):
        core = _make_core()
        loop = self._make_loop()
        core.proactive_mind = SimpleNamespace(loop=loop)
        report = core._analyze_agent_architecture("анализ")
        self.assertIn("Проанализировал себя", report)
        self.assertIn("Self-repair", report)
        self.assertIn("ExperienceReplay", report)
        self.assertIn("Self-improvement", report)
        self.assertIn("Циклов выполнено", report)
        self.assertIn("Активных целей", report)

    def test_with_gaps(self):
        core = _make_core()
        loop = self._make_loop(
            capability_discovery=SimpleNamespace(
                find_gaps=lambda: ["missing_package:requests", "weak_action:test"],
            ),
        )
        core.proactive_mind = SimpleNamespace(loop=loop)
        report = core._analyze_agent_architecture("анализ")
        self.assertIn("Отсутствующие пакеты", report)
        self.assertIn("Слабые действия", report)
        self.assertIn("Автоматически создал цели", report)

    def test_no_loop(self):
        core = _make_core()
        core.proactive_mind = None
        report = core._analyze_agent_architecture("анализ")
        self.assertIn("Проанализировал себя", report)
        self.assertIn("критических проблем не обнаружено", report)

    def test_find_gaps_error(self):
        core = _make_core()

        def _bad_gaps():
            raise RuntimeError("fail")

        loop = self._make_loop(
            capability_discovery=SimpleNamespace(find_gaps=_bad_gaps),
        )
        core.proactive_mind = SimpleNamespace(loop=loop)
        report = core._analyze_agent_architecture("анализ")
        self.assertIn("find_gaps() ошибка", report)


class HandleSelfProfileExtendedTests(unittest.TestCase):
    """Покрытие _handle_self_profile с tool_layer."""

    def test_with_tool_reference(self):
        core = _make_core()
        core.tool_layer = SimpleNamespace(
            _tools={"search": SimpleNamespace(description="Поиск в интернете")},
        )
        loop = SimpleNamespace(
            _cycle_count=10,
            _running=True,
            _goal="goal",
            _consecutive_failures=0,
            _subgoal_queue=[],
            persistent_brain=SimpleNamespace(_lessons=[1], _conversations=[1, 2]),
            skill_library=None,
            tool_layer=None,
            identity=_IdentityStub(),
        )
        core.proactive_mind = SimpleNamespace(loop=loop)
        out = core._handle_self_profile("что умеешь")
        self.assertIn("штатном режиме", out)


class HandlePortfolioExtendedTests(unittest.TestCase):
    """Покрытие _handle_portfolio: ImportError, next project, index wrap."""

    def test_portfolio_import_error(self):
        core = _make_core()
        # Remove cached module
        sys.modules.pop("skills.portfolio_builder", None)
        with patch("builtins.__import__", side_effect=ImportError("no module")):
            out = core._handle_portfolio("проекты")
            self.assertTrue(len(out) > 0)

    def test_portfolio_next(self):
        core = _make_core()
        mod = types.ModuleType("skills.portfolio_builder")
        setattr(mod, 'ANDREY_PORTFOLIO_PROJECTS', [{"title": "A"}, {"title": "B"}, {"title": "C"}])

        class PB:
            def __init__(self, **kw):
                pass

            def get_project(self, idx):
                projects = getattr(mod, 'ANDREY_PORTFOLIO_PROJECTS', [])
                return projects[idx] if idx < len(projects) else None

            def format_for_chat(self, p, idx):
                return f"project:{idx}:{p['title']}"

        setattr(mod, 'PortfolioBuilder', PB)
        sys.modules["skills.portfolio_builder"] = mod

        core._last_portfolio_idx = 0
        out = core._handle_portfolio("следующий проект")
        self.assertIn("project:1", out)


class HandleJobHuntExtendedTests(unittest.TestCase):
    """Покрытие _handle_job_hunt: no hunter, timeout, results."""

    def test_no_hunter(self):
        core = _make_core()
        core.proactive_mind = SimpleNamespace(loop=SimpleNamespace(job_hunter=None))
        out = core._handle_job_hunt("ищи вакансии")
        self.assertTrue(len(out) > 0)


class SolverClassifierAndRouterTests(unittest.TestCase):
    """Покрытие _classify_solver_task, _route_solver, _run_exact_solver_pipeline deep paths."""

    def test_classify_probabilistic(self):
        core = _make_core()
        result = core._classify_solver_task(
            "maximize reward\nA:\nreward=10\nprobability=0.5\nB:\nreward=8\nprobability=0.7"
        )
        self.assertEqual(result["mode"], SolverMode.SOLVER.value)

    def test_classify_graph_with_markers(self):
        core = _make_core()
        result = core._classify_solver_task("найди кратчайший путь из A в B\nA->B:2\nB->C:1")
        self.assertEqual(result["mode"], SolverMode.SOLVER.value)

    def test_classify_constraint_project(self):
        core = _make_core()
        result = core._classify_solver_task(
            "maximize income\nonly 2 tasks\nProject X\nincome=100\nrequires: task1 + task2\n"
            "Project Y\nincome=50\nrequires: task1"
        )
        self.assertEqual(result["mode"], SolverMode.SOLVER.value)

    def test_classify_formal_constraint(self):
        core = _make_core()
        result = core._classify_solver_task(
            "constraint satisfaction: domain x=[1,2,3], variable y, assign"
        )
        self.assertEqual(result["mode"], SolverMode.SOLVER.value)

    def test_classify_heuristic_fallback(self):
        core = _make_core()
        result = core._classify_solver_task("просто текст без задачи")
        self.assertEqual(result["mode"], SolverMode.REASONING.value)

    def test_run_pipeline_with_persistent_brain(self):
        core = _make_core()
        task = "ресурс=10\nA: reward=100 cost=6 p=0.9\nB: reward=60 cost=4 p=0.8\nmaximize reward"
        result = core._run_exact_solver_pipeline(task)
        self.assertIsNotNone(result)
        self.assertTrue(len(core.persistent_brain.saved) > 0)  # type: ignore[union-attr]

    def test_has_graph_markers_from_to(self):
        core = _make_core()
        self.assertTrue(core._has_graph_solver_markers("start=A goal=D"))
        self.assertTrue(core._has_graph_solver_markers("путь из A в B"))
        self.assertFalse(core._has_graph_solver_markers("просто текст"))

    def test_has_formal_constraint_negated(self):
        core = _make_core()
        self.assertFalse(core._has_formal_constraint_markers("без csp и sat"))
        self.assertTrue(core._has_formal_constraint_markers("constraint satisfaction problem"))

    def test_route_solver_all_types(self):
        core = _make_core()
        route = core._route_solver("ресурс=10\nA: reward=5 cost=2\nmaximize")
        self.assertEqual(route["solver"], "DP_knapsack")

    def test_normalize_solver_text(self):
        core = _make_core()
        normalized = core._normalize_solver_text("  hello   world  ")
        self.assertIn("hello", normalized)


class HandleJobHuntDeepTests(unittest.TestCase):
    """Покрытие _handle_job_hunt: found > 0, found == 0, error, timeout."""

    def test_hunt_found(self):
        core = _make_core()
        hunter = SimpleNamespace(
            _last_run=time.time() - 60,
            _seen_uids={1, 2},
            _run_interval=300.0,
            hunt=lambda: 3,
        )
        loop = SimpleNamespace(job_hunter=hunter)
        core.proactive_mind = SimpleNamespace(loop=loop)
        out = core._handle_job_hunt("ищи")
        self.assertIn("Найдено", out)

    def test_hunt_zero(self):
        core = _make_core()
        hunter = SimpleNamespace(
            _last_run=time.time() - 600,
            _seen_uids=set(),
            _run_interval=300.0,
            hunt=lambda: 0,
        )
        loop = SimpleNamespace(job_hunter=hunter)
        core.proactive_mind = SimpleNamespace(loop=loop)
        out = core._handle_job_hunt("ищи")
        self.assertIn("не нашёл", out)

    def test_hunt_error(self):
        core = _make_core()
        hunter = SimpleNamespace(
            _last_run=0.0,
            _seen_uids=set(),
            _run_interval=300.0,
            hunt=lambda: (_ for _ in ()).throw(RuntimeError("network")),
        )
        loop = SimpleNamespace(job_hunter=hunter)
        core.proactive_mind = SimpleNamespace(loop=loop)
        out = core._handle_job_hunt("ищи")
        self.assertTrue(len(out) > 0)

    def test_no_proactive_mind(self):
        core = _make_core()
        core.proactive_mind = None
        out = core._handle_job_hunt("ищи")
        self.assertTrue(len(out) > 0)


class HandleScreenshotTests(unittest.TestCase):
    """Покрытие _handle_screenshot ветвей."""

    def test_no_pillow(self):
        core = _make_core()
        with patch("builtins.__import__", side_effect=ImportError("no pillow")):
            out = core._handle_screenshot("что на экране")
            self.assertIn("Pillow", out)

    def test_grab_fails(self):
        core = _make_core()
        mock_mod = types.ModuleType("PIL")

        class FakeImageGrab:
            @staticmethod
            def grab():
                raise RuntimeError("no display")

        setattr(mock_mod, 'ImageGrab', FakeImageGrab)
        sys.modules["PIL"] = mock_mod
        sys.modules["PIL.ImageGrab"] = mock_mod
        try:
            out = core._handle_screenshot("x")
            self.assertTrue("скриншот" in out or "Pillow" in out or len(out) > 0)
        finally:
            sys.modules.pop("PIL", None)
            sys.modules.pop("PIL.ImageGrab", None)


class ConverseEdgeCaseTests(unittest.TestCase):
    """Дополнительные edge cases для converse."""

    def test_duplicate_no_preview(self):
        core = _make_core()
        core._detect_query_intent = lambda query: "research"  # type: ignore[assignment]
        core._llm_call = lambda *a, **kw: "first"
        core.converse("длинный уникальный запрос без короткости вообще")
        # Wipe response to simulate empty preview
        core._recent_user_requests[-1]["response"] = ""
        out = core.converse("длинный уникальный запрос без короткости вообще")
        self.assertIn("заново", out)

    def test_short_message_skips_duplicate(self):
        core = _make_core()
        core._detect_query_intent = lambda query: "research"  # type: ignore[assignment]
        core._llm_call = lambda *a, **kw: "ответ"
        core._remember_request("тест", "prev")
        out = core.converse("тест")
        self.assertEqual(out, "ответ")

    def test_cover_letter_skip_duplicate(self):
        core = _make_core()
        call_count = [0]

        def _mock_llm(*a, **kw):
            call_count[0] += 1
            return "FIT: no — не подходит"

        core._detect_query_intent = lambda query: "cover_letter"  # type: ignore[assignment]
        core._llm_call = _mock_llm
        core._remember_request("длинная вакансия для проверки антидубля", "prev")
        out = core.converse("длинная вакансия для проверки антидубля")
        self.assertIn("не подходит", out)

    def test_semantic_duplicate_with_preview(self):
        core = _make_core()
        core._detect_query_intent = lambda query: "research"  # type: ignore[assignment]
        core._llm_call = lambda *a, **kw: "Вот результат исследования о ML подробно"
        core.converse("исследуй тему машинного обучения подробно детально")
        # Second time should be detected as duplicate
        out = core.converse("исследуй тему машинного обучения подробно детально")
        self.assertIn("уже был", out)


class ToolDescriptionBuildingTests(unittest.TestCase):
    """Покрытие _handle_self_profile tool description paths."""

    def test_tool_descriptions_truncated(self):
        core = _make_core()
        core.tool_layer = SimpleNamespace(
            _tools={
                "search": SimpleNamespace(description="A" * 200),
                "calc": SimpleNamespace(description="Calculator tool"),
            },
        )
        loop = SimpleNamespace(
            _cycle_count=5,
            _running=True,
            _goal="work",
            _consecutive_failures=0,
            _subgoal_queue=[],
            persistent_brain=SimpleNamespace(_lessons=[], _conversations=[]),
            skill_library=None,
            tool_layer=None,
            identity=None,
        )
        core.proactive_mind = SimpleNamespace(loop=loop)
        out = core._handle_self_profile("статус")
        self.assertIn("Сейчас работаю", out)


class ChatMarkersFixTest(unittest.TestCase):
    """Проверка что chat markers не матчатся как подстроки."""

    def test_ok_not_in_word(self):
        core = _make_core()
        # "рынок" содержит "ок" но не должен матчить
        intent = core._detect_query_intent("исследуй рынок и собери данные")
        self.assertEqual(intent, "research")

    def test_poka_standalone(self):
        core = _make_core()
        intent = core._detect_query_intent("пока")
        self.assertEqual(intent, "conversation")

    def test_ok_standalone(self):
        core = _make_core()
        intent = core._detect_query_intent("ок")
        self.assertEqual(intent, "conversation")


if __name__ == "__main__":
    unittest.main()
