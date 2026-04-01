import os
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch

from core.proactive_mind import ProactiveMind


class _Mon:
    def __init__(self):
        self.info_calls = []
        self.warning_calls = []
        self.error_calls = []

    def info(self, message, source=None, data=None):
        self.info_calls.append((message, source, data))

    def warning(self, message, source=None, data=None):
        self.warning_calls.append((message, source, data))

    def error(self, message, source=None, data=None):
        self.error_calls.append((message, source, data))


class _Thread:
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


class _Metrics:
    def __init__(self, cpu, mem, disk=None):
        self.cpu_percent = cpu
        self.memory_percent = mem
        self.disk_percent = disk


class ProactiveMindTests(unittest.TestCase):
    def setUp(self):
        self.mon = _Mon()
        self.llm = types.SimpleNamespace(infer=MagicMock(return_value="Привет!"))
        self.core = types.SimpleNamespace(llm=self.llm)
        self.bot = types.SimpleNamespace(send=MagicMock())
        self.pm = ProactiveMind(
            cognitive_core=self.core,
            telegram_bot=self.bot,
            chat_id=123,
            monitoring=self.mon,
        )

    def test_state_helpers(self):
        self.pm._set_last_message_time(10.0)
        self.pm._set_startup_done(True)
        self.pm._increment_cycle()
        self.pm._set_running(True)
        self.pm._set_user_last_active(11.0)
        self.pm._set_last_idle_attempt_time(12.0)

        last_msg, startup_done, cycles = self.pm._get_state_snapshot()
        user_last_active, last_idle_attempt = self.pm._idle_timing_snapshot()

        self.assertEqual(last_msg, 10.0)
        self.assertTrue(startup_done)
        self.assertEqual(cycles, 1)
        self.assertTrue(self.pm._is_running())
        self.assertEqual(user_last_active, 11.0)
        self.assertEqual(last_idle_attempt, 12.0)

    def test_start_and_stop_paths(self):
        acq = types.SimpleNamespace(
            start_auto_refresh=MagicMock(),
            stop_auto_refresh=MagicMock(),
        )
        pm = ProactiveMind(
            cognitive_core=self.core,
            telegram_bot=self.bot,
            chat_id=1,
            monitoring=self.mon,
            acquisition=acq,
        )
        with patch("core.proactive_mind.threading.Thread", side_effect=_Thread):
            pm.start()
            self.assertTrue(pm._is_running())
            self.assertTrue(pm._thread.started)
            acq.start_auto_refresh.assert_called_once_with(interval_seconds=1800)

            # idempotent start
            thread_ref = pm._thread
            pm.start()
            self.assertIs(pm._thread, thread_ref)

            pm.stop()
            self.assertFalse(pm._is_running())
            acq.stop_auto_refresh.assert_called_once()
            self.assertTrue(pm._thread.joined)

    def test_greet_early_returns(self):
        pm = ProactiveMind(monitoring=self.mon)
        pm._set_startup_done(True)
        pm.greet()
        pm._set_startup_done(False)
        pm.greet()

    def test_greet_success_and_memory_context(self):
        pm = self.pm
        pm.hardware = types.SimpleNamespace(collect=lambda: _Metrics(55, 44, 10))
        pm.spec_docs = ["doc"]
        pm.spec_policy_digest = "- policy"
        pm.brain = types.SimpleNamespace(
            get_memory_context=lambda: "ctx",
            stats={"boot_count": 2},
        )
        with patch("core.proactive_mind.time.localtime", return_value=types.SimpleNamespace(tm_hour=13)):
            with patch("core.proactive_mind.os.makedirs"):
                with patch("core.proactive_mind.open", create=True, side_effect=OSError("nope")):
                    pm.greet()
        self.bot.send.assert_called_once()
        self.assertTrue(pm._get_state_snapshot()[1])

    def test_greet_time_context_branches_and_errors(self):
        pm = self.pm
        pm.hardware = types.SimpleNamespace(collect=lambda: (_ for _ in ()).throw(RuntimeError("x")))

        for hour in (1, 8, 15, 21):
            with patch("core.proactive_mind.time.localtime", return_value=types.SimpleNamespace(tm_hour=hour)):
                pm._set_startup_done(False)
                pm.greet()

        pm.core = types.SimpleNamespace(llm=types.SimpleNamespace(infer=lambda *_a, **_k: (_ for _ in ()).throw(ValueError("bad"))))
        pm._set_startup_done(False)
        pm.greet()
        self.assertTrue(self.mon.error_calls)

    def test_think_loop_recent_greeting_and_cycle(self):
        pm = self.pm
        with tempfile.TemporaryDirectory() as td:
            old = os.getcwd()
            os.chdir(td)
            try:
                os.makedirs(".agent_memory", exist_ok=True)
                with open(os.path.join(".agent_memory", "_last_greeting.json"), "w", encoding="utf-8") as f:
                    f.write('{"ts": %s}' % time.time())

                calls = {"sleep": 0}

                def fake_sleep(_):
                    calls["sleep"] += 1
                    if calls["sleep"] > 1:
                        pm._set_running(False)

                pm._set_running(True)
                pm._think_cycle = MagicMock()
                with patch("core.proactive_mind.time.sleep", side_effect=fake_sleep), \
                     patch("core.proactive_mind.random.uniform", return_value=0):
                    pm._think_loop()

                self.assertTrue(pm._get_state_snapshot()[1])
                self.assertGreaterEqual(pm._get_state_snapshot()[2], 1)
                pm._think_cycle.assert_called()
            finally:
                os.chdir(old)

    def test_think_loop_greet_and_exception_paths(self):
        pm = self.pm
        with tempfile.TemporaryDirectory() as td:
            old = os.getcwd()
            os.chdir(td)
            try:
                pm._set_running(True)
                pm.greet = MagicMock()

                def bad_cycle():
                    raise TypeError("boom")

                pm._think_cycle = bad_cycle
                sleep_calls = {"n": 0}

                def fake_sleep(_):
                    sleep_calls["n"] += 1
                    if sleep_calls["n"] >= 2:
                        pm._set_running(False)

                with patch("core.proactive_mind.time.sleep", side_effect=fake_sleep), \
                     patch("core.proactive_mind.random.uniform", return_value=-100), \
                     patch("core.proactive_mind.open", create=True, side_effect=ValueError("bad")):
                    pm._think_loop()

                pm.greet.assert_called_once()
                self.assertTrue(self.mon.error_calls)
            finally:
                os.chdir(old)

    def test_think_loop_greeting_file_read_exception_branch(self):
        pm = self.pm
        with tempfile.TemporaryDirectory() as td:
            old = os.getcwd()
            os.chdir(td)
            try:
                os.makedirs(".agent_memory", exist_ok=True)
                # файл существует, но чтение ломается -> покрываем except (OSError, ValueError, KeyError)
                with open(os.path.join(".agent_memory", "_last_greeting.json"), "w", encoding="utf-8") as f:
                    f.write("{}")

                pm._set_running(False)
                pm.greet = MagicMock()
                with patch("core.proactive_mind.time.sleep", return_value=None), \
                     patch("core.proactive_mind.open", create=True, side_effect=OSError("no read")):
                    pm._think_loop()
                pm.greet.assert_called_once()
            finally:
                os.chdir(old)

    def test_think_cycle_loop_and_speaking_paths(self):
        pm = self.pm
        pm._set_last_message_time(0)
        cycle_ok = types.SimpleNamespace(success=True, action_result={"execution": {"summary": "done"}}, errors=[])
        loop = types.SimpleNamespace(is_running=False, current_goal="g", step=lambda: cycle_ok)
        pm.loop = loop
        pm._observe = lambda: {"critical": ["c"], "interesting": [], "neutral": []}
        pm._speak = MagicMock()
        with patch("core.proactive_mind.time.time", return_value=10_000):
            pm._think_cycle()
        self.bot.send.assert_called()
        pm._speak.assert_called_once()

        # failure with errors
        pm.bot.send.reset_mock()
        cycle_fail = types.SimpleNamespace(success=False, action_result={}, errors=["e1", "e2", "e3"])
        pm.loop = types.SimpleNamespace(is_running=False, current_goal="g", step=lambda: cycle_fail)
        pm._observe = lambda: {"critical": [], "interesting": ["i"], "neutral": []}
        with patch("core.proactive_mind.time.time", return_value=pm.MIN_INTERVAL + 100):
            pm._think_cycle()
        self.bot.send.assert_called()

        # exception in loop.step
        pm.loop = types.SimpleNamespace(
            is_running=False,
            current_goal="g",
            step=lambda: (_ for _ in ()).throw(RuntimeError("step fail")),
        )
        pm._observe = lambda: {"critical": [], "interesting": [], "neutral": []}
        with patch("core.proactive_mind.time.time", return_value=pm.MAX_INTERVAL + 1):
            pm._think_cycle()

    def test_think_cycle_idle_decision_paths(self):
        pm = self.pm
        pm.loop = None
        pm._speak = MagicMock()

        # idle allowed
        pm._set_last_message_time(0)
        pm._observe = lambda: {"critical": [], "interesting": [], "neutral": []}
        pm._should_emit_idle_thought = lambda now, obs: True
        with patch("core.proactive_mind.time.time", return_value=pm.MAX_INTERVAL + 100):
            pm._think_cycle()
        pm._speak.assert_called_once()

        # idle denied -> no speak
        pm._speak.reset_mock()
        pm._should_emit_idle_thought = lambda now, obs: False
        with patch("core.proactive_mind.time.time", return_value=pm.MAX_INTERVAL + 100):
            pm._think_cycle()
        pm._speak.assert_not_called()

    def test_think_cycle_interesting_branch(self):
        pm = self.pm
        pm.loop = None
        pm._set_last_message_time(0)
        pm._observe = lambda: {"critical": [], "interesting": ["cpu high"], "neutral": []}
        pm._speak = MagicMock()
        with patch("core.proactive_mind.time.time", return_value=pm.MIN_INTERVAL + 1):
            pm._think_cycle()
        pm._speak.assert_called_once()

    def test_observe_branches(self):
        pm = self.pm
        pm.hardware = types.SimpleNamespace(collect=lambda: _Metrics(95, 91, 92))
        pm.goals = types.SimpleNamespace(get_all=lambda: [{"status": "active"}, types.SimpleNamespace(status="ACTIVE")])
        obs = pm._observe()
        self.assertTrue(obs["critical"])
        self.assertTrue(obs["neutral"])

        pm.hardware = types.SimpleNamespace(collect=lambda: _Metrics(75, 50, None))
        pm.goals = types.SimpleNamespace(get_all=lambda: [{"status": "pending"}])
        obs = pm._observe()
        self.assertTrue(obs["interesting"])
        self.assertIn("Нет активных целей", obs["neutral"])

        pm.hardware = types.SimpleNamespace(collect=lambda: (_ for _ in ()).throw(OSError("x")))
        pm.goals = types.SimpleNamespace(get_all=lambda: (_ for _ in ()).throw(TypeError("x")))
        obs = pm._observe()
        self.assertIsInstance(obs, dict)

    def test_speak_guard_and_urgency_critical(self):
        pm = ProactiveMind(cognitive_core=None, telegram_bot=None, chat_id=None, monitoring=self.mon)
        pm._speak({}, 3)

        pm = self.pm
        pm.core.llm.infer = MagicMock(return_value="critical text")
        pm._speak({"critical": ["x"], "interesting": [], "neutral": []}, urgency=3)
        self.bot.send.assert_called()

    def test_speak_interesting_idle_duplicates_and_errors(self):
        pm = self.pm

        pm.core.llm.infer = MagicMock(return_value="Как думаешь?")
        pm._speak({"critical": [], "interesting": ["i"], "neutral": []}, urgency=1)
        sent_text = self.bot.send.call_args[0][1]
        self.assertIn("Перехожу", sent_text)

        # idle: knowledge adds topic and duplicate suppression
        pm.knowledge = types.SimpleNamespace(get_relevant_knowledge=lambda _q: ["x"])
        pm._thoughts = ["план принят. начинаю выполнять это сейчас."]
        pm.core.llm.infer = MagicMock(return_value="План принят. Начинаю выполнять это сейчас.")
        with patch("core.proactive_mind.random.choice", return_value="topic"):
            pm._speak({"critical": [], "interesting": [], "neutral": []}, urgency=0)

        # idle short text branch
        pm.core.llm.infer = MagicMock(return_value="ok")
        pm._speak({"critical": [], "interesting": [], "neutral": []}, urgency=0)

        # knowledge retrieval error branch
        pm.knowledge = types.SimpleNamespace(get_relevant_knowledge=lambda _q: (_ for _ in ()).throw(KeyError("x")))
        pm.core.llm.infer = MagicMock(return_value="Могу сделать")
        with patch("core.proactive_mind.random.choice", return_value="topic"):
            pm._speak({"critical": [], "interesting": [], "neutral": []}, urgency=0)

        # infer error
        pm.core.llm.infer = MagicMock(side_effect=RuntimeError("bad infer"))
        pm._speak({"critical": [], "interesting": [], "neutral": []}, urgency=1)
        self.assertTrue(self.mon.error_calls)

    def test_should_emit_idle_thought(self):
        now = 1000.0
        self.assertTrue(self.pm._should_emit_idle_thought(now, {"critical": ["x"], "interesting": []}))
        self.assertTrue(self.pm._should_emit_idle_thought(now, {"critical": [], "interesting": ["x"]}))

        self.pm._set_user_last_active(0.0)
        self.pm._set_last_idle_attempt_time(0.0)
        self.assertFalse(self.pm._should_emit_idle_thought(now, {"critical": [], "interesting": []}))

        now_large = 100_000.0
        self.pm._set_user_last_active(now_large - 13 * 3600)
        self.assertFalse(self.pm._should_emit_idle_thought(now_large, {"critical": [], "interesting": []}))

        self.pm._set_user_last_active(now - 100)
        self.pm._set_last_idle_attempt_time(now - 10)
        self.assertFalse(self.pm._should_emit_idle_thought(now, {"critical": [], "interesting": []}))

        self.pm._set_last_idle_attempt_time(0)
        self.assertTrue(self.pm._should_emit_idle_thought(now, {"critical": [], "interesting": []}))

    def test_force_non_question_tone(self):
        self.assertEqual(ProactiveMind._force_non_question_tone(""), "")
        out = ProactiveMind._force_non_question_tone("Как думаешь? Что думаешь? Хочешь сделать? Могу помочь")
        self.assertNotIn("?", out)
        self.assertTrue(out.endswith(".") or out.endswith("!"))

    def test_user_hooks_and_markers(self):
        self.pm.on_user_message("hello")
        user_last_active, _ = self.pm._idle_timing_snapshot()
        self.assertGreater(user_last_active, 0)

        self.pm.mark_startup_done()
        self.assertTrue(self.pm._get_state_snapshot()[1])

    def test_load_specs_and_digest(self):
        pm = self.pm
        with tempfile.TemporaryDirectory() as td:
            p1 = os.path.join(td, "a.txt")
            with open(p1, "w", encoding="utf-8") as f:
                f.write("Партнер действует автономно и прозрачно в безопасном режиме.\n")
                f.write("коротко\n")

            p2 = os.path.join(td, "b.txt")
            with open(p2, "w", encoding="utf-8") as f:
                f.write("Партнер действует автономно и прозрачно в безопасном режиме.\n")
                f.write("Цель системы: безопасное действие и контроль ошибок в процессе.\n")

            pm.load_specs([p1, p2, os.path.join(td, "missing.txt")])
            self.assertEqual(len(pm.spec_docs), 2)
            self.assertTrue(pm.spec_policy_digest.startswith("- "))

        with patch("core.proactive_mind.os.path.exists", return_value=True), \
             patch("core.proactive_mind.open", create=True, side_effect=UnicodeDecodeError("utf-8", b"x", 0, 1, "bad")):
            pm.load_specs(["bad.txt"])

        self.assertEqual(ProactiveMind._build_policy_digest([]), "")
        digest = ProactiveMind._build_policy_digest(
            [
                "Строка без ключей и достаточно длинная чтобы пройти фильтр\n"
                "Критичный риск требует прозрачного контроля и согласованного действия\n"
                "Критичный риск требует прозрачного контроля и согласованного действия\n"
            ],
            max_lines=1,
        )
        self.assertEqual(len(digest.splitlines()), 1)

    def test_personality_and_log(self):
        text = ProactiveMind._default_personality()
        self.assertIn("автономный помощник", text)

        self.pm._log("warn", level="warning")
        self.pm._log("err", level="error")
        self.pm._log("unknown", level="x")
        self.assertTrue(self.mon.warning_calls)

        pm_no_mon = ProactiveMind()
        pm_no_mon._log("silent")


if __name__ == "__main__":
    unittest.main()
