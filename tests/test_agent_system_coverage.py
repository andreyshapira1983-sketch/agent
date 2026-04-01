import unittest
from unittest.mock import patch

from agents.agent_system import (
    AgentRole,
    BaseAgent,
    ResearchAgent,
    CodingAgent,
    DebuggingAgent,
    AnalysisAgent,
    PlanningAgent,
    LearningAgent,
    IntrospectionAgent,
    CommunicationAgent,
    ManagerAgent,
    build_agent_system,
)


class _Core:
    def __init__(self):
        self.contexts = []
        self.last_reasoning_prompt = ""
        self._decision = ""
        self._code = "``````"

    def set_code(self, code: str):
        self._code = code

    def set_decision(self, decision: str):
        self._decision = decision

    def build_context(self, goal):
        self.contexts.append(goal)

    def reasoning(self, prompt):
        self.last_reasoning_prompt = str(prompt)
        return f"reason:{prompt[:40]}"

    def code_generation(self, goal):
        _ = goal
        return self._code

    def problem_solving(self, goal):
        return f"debug:{goal}"

    def generate_hypothesis(self, goal):
        return f"analysis:{goal}"

    def plan(self, goal):
        return f"plan:{goal}"

    def converse(self, message):
        return f"talk:{message}"

    def decision_making(self, options, context_note=""):
        _ = (options, context_note)
        return self._decision


class _BaseAgentImpl(BaseAgent):
    def __init__(self):
        super().__init__(AgentRole.RESEARCH, cognitive_core=None, tools={}, name="base_impl")

    def handle(self, task: dict) -> dict:
        out = {"result": str(task.get("goal", "")), "status": "success", "agent": self.name}
        self._record(task, out)
        return out


class _ToolObj:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def use(self, name, **kwargs):
        _ = (name, kwargs)
        if self.error:
            raise RuntimeError(self.error)
        return self.result


class _Identity:
    def __init__(self, result="identity", fail=False):
        self.result = result
        self.fail = fail

    def introspect(self, message):
        _ = message
        if self.fail:
            raise RuntimeError("identity fail")
        return self.result


class _Knowledge:
    def __init__(self, result=None, fail=False):
        self.result = result
        self.fail = fail
        self.saved = []

    def get_relevant_knowledge(self, goal):
        _ = goal
        if self.fail:
            raise RuntimeError("kb fail")
        return self.result

    def store_long_term(self, key, value, **_kwargs):
        self.saved.append((key, value))


class AgentSystemCoverageTests(unittest.TestCase):
    def test_base_agent_handle_not_implemented(self):
        base = BaseAgent(AgentRole.RESEARCH, cognitive_core=None, tools={}, name="base")
        with self.assertRaises(NotImplementedError):
            base.handle({"goal": "x"})

    def test_base_agent_record_and_history(self):
        ag = _BaseAgentImpl()
        r = ag.handle({"goal": "task"})
        self.assertEqual(r["status"], "success")
        h = ag.get_history()
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["task"]["goal"], "task")

    def test_research_agent_with_kb_and_tool_object(self):
        core = _Core()
        kb = _Knowledge(result={"k": 1})
        tools = _ToolObj(result="S")
        ag = ResearchAgent(cognitive_core=core, tools=tools, knowledge=kb)
        out = ag.handle({"goal": "ai"})
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["knowledge_results"], {"k": 1})
        self.assertEqual(out["search_results"], "S")
        self.assertIn("Knowledge base", core.last_reasoning_prompt)
        self.assertIn("Search results", core.last_reasoning_prompt)

    def test_research_agent_kb_and_tool_failures(self):
        core = _Core()
        kb = _Knowledge(result=None, fail=True)
        tools = _ToolObj(result=None, error="x")
        ag = ResearchAgent(cognitive_core=core, tools=tools, knowledge=kb)
        out = ag.handle({"goal": "ai"})
        self.assertIsNone(out["knowledge_results"])
        self.assertIsNone(out["search_results"])

    def test_research_agent_with_dict_tool(self):
        core = _Core()
        tools = {"search": lambda query: f"R:{query}"}
        ag = ResearchAgent(cognitive_core=core, tools=tools, knowledge=None)
        out = ag.handle({"goal": "topic"})
        self.assertEqual(out["search_results"], "R:topic")

    def test_research_agent_with_dict_tool_exception(self):
        core = _Core()

        def _boom(**kwargs):
            _ = kwargs
            raise RuntimeError("search fail")

        tools = {"search": _boom}
        ag = ResearchAgent(cognitive_core=core, tools=tools, knowledge=None)
        out = ag.handle({"goal": "topic"})
        self.assertIsNone(out["search_results"])

    def test_research_agent_without_core(self):
        ag = ResearchAgent(cognitive_core=None, tools=None, knowledge=None)
        out = ag.handle({"goal": "topic"})
        self.assertIn("cognitive_core не подключён", out["result"])

    def test_coding_agent_validates_python_runtime_via_tool_object(self):
        core = _Core()
        core.set_code("```python\nprint('ok')\n```")
        ag = CodingAgent(cognitive_core=core, tools=_ToolObj(result={"ok": True}))
        out = ag.handle({"goal": "gen"})
        self.assertTrue(out["validated"])

    def test_coding_agent_validates_python_runtime_via_dict_tool(self):
        core = _Core()
        core.set_code("```python\nprint('ok')\n```")
        tools = {"python_runtime": lambda code: {"ran": bool(code)}}
        ag = CodingAgent(cognitive_core=core, tools=tools)
        out = ag.handle({"goal": "gen"})
        self.assertTrue(out["validated"])

    def test_coding_agent_validation_error(self):
        core = _Core()
        core.set_code("```python\nprint('ok')\n```")
        ag = CodingAgent(cognitive_core=core, tools=_ToolObj(result=None, error="runtime fail"))
        out = ag.handle({"goal": "gen"})
        self.assertFalse(out["validated"])
        self.assertIn("validation_error", out)

    def test_coding_agent_without_code_block_or_core(self):
        core = _Core()
        core.set_code("no python block")
        ag1 = CodingAgent(cognitive_core=core, tools=None)
        out1 = ag1.handle({"goal": "gen"})
        self.assertFalse(out1["validated"])

        # Есть python-блок, но python_runtime инструмента нет → run_result=None ветка
        core2 = _Core()
        core2.set_code("```python\nprint('ok')\n```")
        ag_mid = CodingAgent(cognitive_core=core2, tools={"other": lambda **k: k})
        out_mid = ag_mid.handle({"goal": "gen"})
        self.assertFalse(out_mid["validated"])

        ag2 = CodingAgent(cognitive_core=None, tools=None)
        out2 = ag2.handle({"goal": "gen"})
        self.assertIn("cognitive_core не подключён", out2["result"])

    def test_debugging_agent_branches(self):
        core = _Core()
        ag = DebuggingAgent(cognitive_core=core)
        out = ag.handle({"goal": "err"})
        self.assertEqual(out["result"], "debug:err")

        ag2 = DebuggingAgent(cognitive_core=None)
        out2 = ag2.handle({"goal": "err"})
        self.assertIn("cognitive_core не подключён", out2["result"])

    def test_analysis_agent_branches(self):
        core = _Core()
        ag = AnalysisAgent(cognitive_core=core)
        out = ag.handle({"goal": "data"})
        self.assertEqual(out["result"], "analysis:data")

        ag2 = AnalysisAgent(cognitive_core=None)
        out2 = ag2.handle({"goal": "data"})
        self.assertIn("cognitive_core не подключён", out2["result"])

    def test_planning_agent_windows_prompt_and_no_core(self):
        core = _Core()
        ag = PlanningAgent(cognitive_core=core, tools=None, working_dir=".")
        with patch("platform.system", return_value="Windows"):
            out = ag.handle({"goal": "disk", "plan": "p"})
        self.assertEqual(out["status"], "success")
        self.assertIn("PowerShell", core.last_reasoning_prompt)
        self.assertIn("ЗАПРЕЩЕНЫ: df, free", core.last_reasoning_prompt)

        ag2 = PlanningAgent(cognitive_core=None)
        out2 = ag2.handle({"goal": "disk"})
        self.assertIn("cognitive_core не подключён", out2["result"])

    def test_planning_agent_linux_prompt_uses_bash_hint(self):
        core = _Core()
        ag = PlanningAgent(cognitive_core=core, tools=None, working_dir=".")
        with patch("platform.system", return_value="Linux"):
            ag.handle({"goal": "disk"})
        self.assertIn("используй bash", core.last_reasoning_prompt)
        self.assertIn("ЗАПРЕЩЕНЫ: dir, tasklist", core.last_reasoning_prompt)

    def test_learning_agent_all_branches(self):
        kb = _Knowledge()
        ag1 = LearningAgent(cognitive_core=None, knowledge=kb)
        out1 = ag1.handle({"goal": "x", "source": "s"})
        self.assertIn("сохранено", out1["result"])
        self.assertEqual(kb.saved, [("x", "s")])

        core = _Core()
        ag2 = LearningAgent(cognitive_core=core, knowledge=None)
        out2 = ag2.handle({"goal": "x"})
        self.assertIn("reason:", out2["result"])

        ag3 = LearningAgent(cognitive_core=None, knowledge=None)
        out3 = ag3.handle({"goal": "x"})
        self.assertIn("нет подключённых систем", out3["result"])

    def test_introspection_agent_branches_and_detector(self):
        identity = _Identity(result="I")
        core = _Core()
        ag = IntrospectionAgent(cognitive_core=core, identity=identity)

        out1 = ag.handle({"goal": "кто ты"})
        self.assertEqual(out1["result"], "I")

        ag_fail = IntrospectionAgent(cognitive_core=core, identity=_Identity(fail=True))
        out2 = ag_fail.handle({"goal": "кто ты"})
        self.assertIn("Ошибка", out2["result"])

        out3 = ag.handle({"goal": "обычный вопрос"})
        self.assertEqual(out3["result"], "talk:обычный вопрос")

        ag_none = IntrospectionAgent(cognitive_core=None, identity=None)
        out4 = ag_none.handle({"goal": "обычный"})
        self.assertIn("нет ни identity ни cognitive_core", out4["result"])

        self.assertTrue(ag.detect_introspective_question("Расскажи о себе"))
        self.assertFalse(ag.detect_introspective_question("погода"))

    def test_communication_agent_branches(self):
        core = _Core()
        identity = _Identity(result="SELF")
        ag = CommunicationAgent(cognitive_core=core, identity=identity)

        # Интроспективный маршрут
        out1 = ag.handle({"goal": "кто ты"})
        self.assertEqual(out1["result"], "SELF")

        # Обычный маршрут
        out2 = ag.handle({"message": "привет"})
        self.assertEqual(out2["result"], "talk:привет")

        # Без core
        ag2 = CommunicationAgent(cognitive_core=None, identity=None)
        out3 = ag2.handle({"message": "привет"})
        self.assertIn("cognitive_core не подключён", out3["result"])

    def test_manager_agent_register_unregister_list_get(self):
        m = ManagerAgent()
        a = CommunicationAgent(cognitive_core=None)
        m.register(a)
        self.assertIsNotNone(m.get_agent("communication"))
        listed = m.list_agents()
        self.assertEqual(listed[0]["name"], "communication")
        m.unregister("communication")
        self.assertIsNone(m.get_agent("communication"))

    def test_manager_agent_handle_with_explicit_and_missing_role(self):
        m = ManagerAgent()
        comm = CommunicationAgent(cognitive_core=None)
        m.register(comm)

        out1 = m.handle({"role": "communication", "message": "hi"})
        self.assertEqual(out1["status"], "success")

        out2 = m.handle({"message": "hi"})
        self.assertEqual(out2["status"], "success")

        out3 = m.handle({"role": AgentRole.RESEARCH, "goal": "x"})
        self.assertEqual(out3["status"], "failed")
        self.assertIn("не найден", out3["result"])

    def test_manager_agent_decision_making_path_and_queue(self):
        core = _Core()
        core.set_decision("лучший агент: analysis")
        m = ManagerAgent(cognitive_core=core)
        an = AnalysisAgent(cognitive_core=core)
        m.register(an)

        out = m.handle({"goal": "данные"})
        self.assertEqual(out["agent"], "analysis")

        m.enqueue({"role": "analysis", "goal": "g1"})
        m.enqueue({"role": "analysis", "goal": "g2"})
        self.assertEqual(m.queue_size(), 2)
        results = m.run_queue()
        self.assertEqual(len(results), 2)
        self.assertEqual(m.queue_size(), 0)

    def test_build_agent_system_factory(self):
        core = _Core()
        kb = _Knowledge()
        identity = _Identity()
        manager = build_agent_system(cognitive_core=core, tools={}, knowledge=kb, identity=identity, working_dir=".")
        names = {a["name"] for a in manager.list_agents()}
        self.assertEqual(
            names,
            {"research", "coding", "debugging", "analysis", "planning", "learning", "communication"},
        )


if __name__ == "__main__":
    unittest.main()
