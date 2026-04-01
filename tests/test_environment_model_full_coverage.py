import unittest
from unittest.mock import patch

from environment.environment_model import EnvironmentModel, EnvironmentState, StateTransition


class _Mon:
    def __init__(self):
        self.calls = []

    def info(self, message, source=None, data=None):
        self.calls.append((message, source, data))


class _Core:
    def __init__(self):
        self.calls = []

    def reasoning(self, prompt):
        self.calls.append(prompt)
        return "LLM analysis"


class EnvironmentModelTests(unittest.TestCase):
    def test_environment_state_and_transition_matches(self):
        s = EnvironmentState("id1", {"a": [1]})
        src = {"a": [1]}
        src["a"].append(2)
        self.assertEqual(s.data, {"a": [1]})
        d = s.to_dict()
        self.assertEqual(d["state_id"], "id1")
        self.assertIn("timestamp", d)

        t = StateTransition("idle", "read", "x", ["ok"], reversible=True, risk_level="low")
        self.assertTrue(t.matches("idle", "READ file"))
        self.assertFalse(t.matches("executing", "read file"))
        self.assertFalse(t.matches("idle", "write file"))

    def test_init_defaults_and_summary(self):
        m = EnvironmentModel()
        summary = m.summary()
        self.assertEqual(summary["sm_state"], "idle")
        self.assertGreaterEqual(summary["sm_states_count"], 1)
        self.assertGreaterEqual(summary["transitions_count"], 1)

    def test_update_set_get_get_full_state_clear(self):
        mon = _Mon()
        m = EnvironmentModel(monitoring=mon)
        m.update({"a": 1}, snapshot=True)
        m.update({"b": 2}, snapshot=True)
        m.set("c", 3)
        self.assertEqual(m.get("a"), 1)
        self.assertIsNone(m.get("missing"))

        full = m.get_full_state()
        full["a"] = 999
        self.assertEqual(m.get("a"), 1)

        m.clear()
        self.assertEqual(m.get_full_state(), {})
        self.assertTrue(mon.calls)

    def test_entities_relations_constraints(self):
        m = EnvironmentModel()
        m.register_entity("svc", {"v": 1})
        m.update_entity("svc", {"x": 2})
        m.update_entity("user", {"role": "admin"})
        self.assertEqual(m.get_entity("svc")["properties"]["x"], 2)
        self.assertIsNone(m.get_entity("none"))

        m.add_relation("svc", "depends_on", "db")
        m.add_relation("user", "uses", "svc")
        self.assertEqual(len(m.get_relations()), 2)
        self.assertEqual(len(m.get_relations("svc")), 2)

        m.add_constraint("нет доступа к интернету")
        self.assertEqual(len(m.get_constraints()), 1)

    def test_state_machine_register_apply_get_set(self):
        m = EnvironmentModel()
        m.register_state("custom", "desc")
        m.register_transition("idle", "hello", "custom", ["changed"], reversible=False, risk_level="high")

        self.assertEqual(m.get_sm_state(), "idle")
        out = m.apply_transition("say HELLO now")
        self.assertIsNotNone(out)
        self.assertEqual(out["from_state"], "idle")
        self.assertEqual(out["to_state"], "custom")
        self.assertEqual(m.get_sm_state(), "custom")

        m.set_sm_state("idle")
        self.assertEqual(m.get_sm_state(), "idle")
        self.assertIsNone(m.apply_transition("unknown action"))

    def test_apply_transition_with_none_to_state_branch(self):
        m = EnvironmentModel()
        m.set_sm_state("idle")
        res = m.apply_transition("read file now")
        self.assertIsNotNone(res)
        self.assertEqual(res["from_state"], "idle")
        self.assertEqual(res["to_state"], "idle")

    def test_assess_risk_levels_and_constraints(self):
        m = EnvironmentModel()
        self.assertEqual(m.assess_risk("rm -rf /")["level"], "critical")
        high = m.assess_risk("delete file")
        self.assertEqual(high["level"], "high")
        self.assertFalse(high["reversible"])

        med = m.assess_risk("deploy patch")
        self.assertEqual(med["level"], "medium")
        self.assertTrue(med["reversible"])

        low = m.assess_risk("read docs")
        self.assertEqual(low["level"], "low")

        m.add_constraint("запрещен deploy production")
        blocked = m.assess_risk("please deploy app")
        self.assertEqual(blocked["blocked_by_constraint"], "запрещен deploy production")

    def test_predict_outcome_deterministic_and_llm(self):
        m = EnvironmentModel()
        m.set_sm_state("idle")
        m.add_constraint("нет internet доступа")

        d = m.predict_outcome("set goal for project")
        self.assertEqual(d["method"], "deterministic")
        self.assertEqual(d["next_state"], "goal_set")
        self.assertIsNone(d["llm_analysis"])

        # no matching transition path
        d2 = m.predict_outcome("completely unknown action")
        self.assertIsNone(d2["next_state"])
        self.assertEqual(d2["effects"], [])

        core = _Core()
        m2 = EnvironmentModel(cognitive_core=core)
        m2.add_constraint("ban deploy")
        l = m2.predict_outcome("deploy now", context={"x": 1})
        self.assertEqual(l["method"], "llm_enhanced")
        self.assertEqual(l["llm_analysis"], "LLM analysis")
        self.assertTrue(core.calls)

    def test_what_if_with_and_without_llm(self):
        m = EnvironmentModel()
        m.set("cpu", 40)
        out = m.what_if({"cpu": 99, "sm_state": "idle"}, "read file abc")
        self.assertIn("cpu: 40 → 99", out["diff_from_current"])
        self.assertIsNone(out["llm_analysis"])
        self.assertTrue(isinstance(out["predicted_effects"], list))

        core = _Core()
        m2 = EnvironmentModel(cognitive_core=core)
        out2 = m2.what_if({"sm_state": "executing", "flag": True}, "complete")
        self.assertEqual(out2["llm_analysis"], "LLM analysis")
        self.assertTrue(core.calls)

    def test_history_snapshot_and_rollback(self):
        m = EnvironmentModel()
        m.set("a", 1)
        m.snapshot()
        m.update({"a": 2}, snapshot=True)
        m.update({"a": 3}, snapshot=True)

        hist_all = m.get_history()
        hist_last = m.get_history(last_n=1)
        self.assertGreaterEqual(len(hist_all), 2)
        self.assertEqual(len(hist_last), 1)

        m.rollback(steps=1)
        self.assertEqual(m.get("a"), 2)

        with self.assertRaises(IndexError):
            m.rollback(steps=10)

    def test_summarize_state_and_log_print_fallback(self):
        m = EnvironmentModel()
        m.set("k", "v")
        m.register_entity("e1", {"x": 1})
        m.add_constraint("constraint")
        text = m._summarize_state()
        self.assertIn("sm_state", text)
        self.assertIn("Сущности", text)
        self.assertIn("Ограничения", text)

        m2 = EnvironmentModel(monitoring=None)
        with patch("builtins.print") as pr:
            m2._log("hello")
            pr.assert_called_once()


if __name__ == "__main__":
    unittest.main()
