# pyright: reportAttributeAccessIssue=false
import json
import importlib.util
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from enum import Enum
from unittest.mock import patch

_PB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "core", "persistent_brain.py")
_PB_SPEC = importlib.util.spec_from_file_location("core.persistent_brain", _PB_PATH)
assert _PB_SPEC is not None
_PB_MOD = importlib.util.module_from_spec(_PB_SPEC)
assert _PB_SPEC.loader is not None
_PB_SPEC.loader.exec_module(_PB_MOD)
sys.modules["core.persistent_brain"] = _PB_MOD
PersistentBrain = _PB_MOD.PersistentBrain


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


class _Cap:
    def __init__(self, name, proficiency=0.5, usage_count=0):
        self.name = name
        self.proficiency = proficiency
        self.usage_count = usage_count


class _GoalObj:
    def __init__(self, gid="g1", desc="d"):
        self.goal_id = gid
        self.description = desc
        self.priority = types.SimpleNamespace(value="high")
        self.status = types.SimpleNamespace(value="active")
        self.parent_id = None
        self.sub_goals = ["g2"]
        self.deadline = 123
        self.success_criteria = "ok"
        self.notes = ["n"]
        self.created_at = 99


class _EpisodeObj:
    def __init__(self):
        self.episode_id = "e1"
        self.goal = "g"
        self.actions = ["a"]
        self.outcome = "o"
        self.success = True
        self.context = {"x": 1}
        self.replayed_count = 2
        self.lessons = ["l"]
        self.created_at = 77


class _Skill:
    def __init__(self, name):
        self.name = name
        self.description = "desc"
        self.strategy = "strat"
        self.tags = ["t"]
        self.use_count = 1
        self.success_count = 1


class _Actor:
    def __init__(self):
        self.name = "User"
        self.relation = types.SimpleNamespace(value="friend")
        self.trust = types.SimpleNamespace(value=2, name="MEDIUM")
        self.preferred_style = types.SimpleNamespace(value="friendly")
        self.interaction_count = 3
        self.notes = ["note"]
        self.last_interaction = 10
        self.preferences = {"lang": "ru"}


class _Proposal:
    def __init__(self):
        self.area = "core"
        self.current_behavior = "a"
        self.proposed_change = "b"
        self.rationale = "c"
        self.priority = 2
        self.status = "proposed"
        self.created_at = 1


class _LRTracker:
    def __init__(self):
        self.loaded = None

    def to_dict(self):
        return {"s": {"q": 1}}

    def load_from_dict(self, d):
        self.loaded = d


class _AttentionMode(Enum):
    IDLE = "idle"
    FOCUS = "focus"


class _TemporalEvent:
    def __init__(self, event_id, description, start, end, tags):
        self.event_id = event_id
        self.description = description
        self.start = start
        self.end = end
        self.tags = tags
        self.relations = []


class _CausalLink:
    def __init__(self, cause, effect, confidence, mechanism=None, context=None):
        self.cause = cause
        self.effect = effect
        self.confidence = confidence
        self.mechanism = mechanism
        self.context = context
        self.observed_count = 1
        self.created_at = None


class _CausalGraph:
    def __init__(self):
        self._links = []

    def add(self, cause, effect, confidence=0.7, mechanism=None, context=None):
        link = _CausalLink(cause, effect, confidence, mechanism, context)
        self._links.append(link)
        return link


class _SandboxVerdict(Enum):
    SAFE = "safe"
    UNSAFE = "unsafe"


class _SimulationRun:
    def __init__(self, run_id="r", action="a"):
        self.run_id = run_id
        self.action = action
        self.verdict = _SandboxVerdict.SAFE
        self.error = None
        self.duration = 0.0
        self.timestamp = 0.0

    def to_dict(self):
        return {
            "run_id": self.run_id,
            "action": self.action,
            "verdict": self.verdict.value,
            "error": self.error,
            "duration": self.duration,
            "timestamp": self.timestamp,
        }


class _BrokenRun:
    def to_dict(self):
        raise ValueError("bad")


class PersistentBrainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mon = _Mon()
        self.pb = PersistentBrain(self.tmp.name, monitoring=self.mon)

    def tearDown(self):
        self.tmp.cleanup()

    def test_path_and_stats_property(self):
        self.assertTrue(self.pb._path("a.json").endswith("a.json"))
        s = self.pb.stats
        s["boot_count"] = -1
        self.assertNotEqual(s["boot_count"], self.pb._stats["boot_count"])

    def test_make_json_safe(self):
        class X(Enum):
            A = "a"
        obj = {X.A: {("k",): [X.A]}}
        out = PersistentBrain._make_json_safe(obj)
        self.assertIn("a", out)

    def test_save_and_load_json(self):
        self.pb._save_json("x.json", {"a": 1})
        self.assertEqual(self.pb._load_json("x.json", {}), {"a": 1})
        self.assertEqual(self.pb._load_json("missing.json", {"d": 1}), {"d": 1})

    def test_save_json_error_and_load_json_error(self):
        with patch.object(_PB_MOD, "open", side_effect=OSError("x")):
            self.pb._save_json("x.json", {"a": 1})
        bad = os.path.join(self.tmp.name, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{")
        self.assertEqual(self.pb._load_json("bad.json", {"z": 1}), {"z": 1})

    def test_count_knowledge_and_disk_usage(self):
        self.assertEqual(self.pb._count_knowledge(), 0)
        k = types.SimpleNamespace(_long_term={"a": 1}, _episodic=[1], _semantic={"b": 2})
        k.knowledge_count = lambda: len(k._long_term) + len(k._episodic) + len(k._semantic)
        self.pb.knowledge = k
        self.assertEqual(self.pb._count_knowledge(), 3)
        with open(os.path.join(self.tmp.name, "f.bin"), "wb") as f:
            f.write(b"abc")
        du = self.pb.disk_usage()
        self.assertIn("used_gb", du)

    def test_record_conversation_lesson_cycle_and_getters(self):
        self.pb.MAX_CONVERSATIONS = 2
        self.pb.record_conversation("user", "m1", "r1")
        self.pb.record_conversation("user", "m2", "r2")
        self.pb.record_conversation("user", "m3", "r3")
        self.assertEqual(len(self.pb._conversations), 2)

        self.pb.record_lesson("g", True, "l", "c")
        self.pb.record_lesson("g", True, "l", "c")
        self.assertEqual(len(self.pb._lessons), 1)

        self.pb.MAX_SOLVER_CASES = 1
        self.pb.record_solver_case("task", "s", "opt", True, 1.2, fitness=1.5)
        self.pb.record_solver_case("task2", "s", "opt", False, -1.0)
        self.assertEqual(len(self.pb._solver_cases), 1)

        self.pb.MAX_SOLVER_JOURNAL = 1
        self.pb.record_exact_solver_journal("s", "c", "promote", True, 0.8)
        self.pb.record_exact_solver_journal("s", "c", "reject", False, 0.1)
        self.assertEqual(len(self.pb._exact_solver_journal), 1)

        self.assertEqual(len(self.pb.get_recent_solver_cases(-5)), 1)
        self.assertEqual(len(self.pb.get_recent_exact_solver_journal(-5)), 1)

        self.pb.record_cycle(True)
        self.pb.record_cycle(False)
        self.assertEqual(self.pb._stats["useful_cycles"], 1)
        self.assertEqual(self.pb._stats["weak_cycles"], 1)

    def test_record_evolution_limit(self):
        self.pb.MAX_EVOLUTION = 1
        self.pb.record_evolution("e1", "d1")
        self.pb.record_evolution("e2", "d2")
        self.assertEqual(len(self.pb._evolution_log), 1)

    def test_aggregate_and_summary_helpers(self):
        self.pb._solver_cases = [
            {"solver": "a", "verification": True, "confidence": 0.5, "optimality": "o1"},
            {"solver": "a", "verification": False, "confidence": 1.0, "optimality": "o2"},
            {"solver": "b", "verification": True, "confidence": 0.2, "optimality": "o1"},
        ]
        self.pb._exact_solver_journal = [
            {"solver": "a", "challenger": "x", "decision": "promote", "verification": True, "confidence": 0.9},
            {"solver": "a", "challenger": "x", "decision": "reject", "verification": False, "confidence": 0.1},
            {"solver": "b", "challenger": "y", "decision": "other", "verification": False, "confidence": 0.3},
        ]
        s = self.pb.summary(max_solver_types=1, max_challengers_per_solver=1)
        self.assertIn("solver_case_by_type", s)
        txt = self.pb.compact_status_text(max_chars=120)
        self.assertTrue(isinstance(txt, str))

        ev_empty = PersistentBrain(self.tmp.name)
        self.assertEqual(ev_empty.evolution_summary()["total"], 0)
        self.pb.record_evolution("z", "d")
        self.assertGreater(self.pb.evolution_summary()["total"], 0)
        self.assertEqual(self.pb.get_evolution(event_type="z", last_n=1)[0]["event"], "z")

    def test_get_memory_context_full(self):
        self.pb._architecture = {"compact_summary": "1-A", "layer_count": 1}
        self.pb._stats.update({"boot_count": 2, "total_cycles": 5, "successful_tasks": 1, "failed_tasks": 1})
        self.pb._lessons = [{"lesson": "learned"}]
        self.pb._conversations = [
            {"role": "user", "message": "build module builder quickly"},
            {"role": "assistant", "message": "ok"},
            {"role": "user", "message": "focus module tests now"},
        ]
        partner = _Actor()
        self.pb.social = types.SimpleNamespace(_actors={"user": partner}, get_actor=lambda aid: partner if aid == "user" else None)
        self.pb.proactive_mind = types.SimpleNamespace(mood="calm")
        self.pb.self_improvement = types.SimpleNamespace(strategies={"s1": "do it"})
        gdict = {"description": "goal a", "status": "active"}
        gobj = types.SimpleNamespace(description="goal b", status="ACTIVE")
        self.pb.goal_manager = types.SimpleNamespace(get_all=lambda: [gdict, gobj])
        caps = [_Cap("coding", 0.9), _Cap("reason", 0.8)]
        self.pb.identity = types.SimpleNamespace(
            _capabilities={"c": caps[0], "r": caps[1]},
            top_capabilities=lambda n=3: sorted(caps, key=lambda c: c.proficiency, reverse=True)[:n]
        )
        self.pb._evolution_log = [{"event": "ev", "details": "detail"}]
        ctx = self.pb.get_memory_context()
        self.assertIn("СИСТЕМНАЯ АРХИТЕКТУРА", ctx)
        self.assertIn("Ключевые темы", ctx)

    def test_get_memory_context_exceptions(self):
        bad_social = types.SimpleNamespace(_actors=None)
        bad_goal = types.SimpleNamespace(get_all=lambda: (_ for _ in ()).throw(ValueError("x")))
        bad_identity = types.SimpleNamespace(_capabilities=None)
        bad_pm = types.SimpleNamespace()
        self.pb.social = bad_social
        self.pb.goal_manager = bad_goal
        self.pb.identity = bad_identity
        self.pb.proactive_mind = bad_pm
        self.pb._conversations = []
        self.assertTrue(isinstance(self.pb.get_memory_context(), str))

    def test_load_save_orchestration_and_autosave(self):
        called = []
        for name in [
            "_load_architecture", "_load_stats", "_load_knowledge", "_load_conversations",
            "_load_lessons", "_load_solver_cases", "_load_exact_solver_journal", "_load_strategies",
            "_load_evolution", "_load_goals", "_load_episodes", "_load_skills", "_load_identity",
            "_load_social", "_load_reflections", "_load_thoughts", "_load_learning_quality",
            "_load_attention", "_load_temporal", "_load_causal", "_load_environment",
            "_load_learning", "_load_sandbox"
        ]:
            setattr(self.pb, name, lambda n=name: called.append(n))
        self.pb.disk_usage = lambda: {"used_gb": 1, "budget_gb": 100, "used_pct": 1, "free_gb": 99}
        self.pb.load()
        self.assertIn("_load_architecture", called)

        saved = []
        for name in [
            "_save_stats", "_save_knowledge", "_save_conversations", "_save_lessons", "_save_solver_cases",
            "_save_exact_solver_journal", "_save_strategies", "_save_evolution", "_save_goals", "_save_episodes",
            "_save_skills", "_save_identity", "_save_social", "_save_reflections", "_save_thoughts",
            "_save_learning_quality", "_save_attention", "_save_temporal", "_save_causal", "_save_environment",
            "_save_learning", "_save_sandbox"
        ]:
            setattr(self.pb, name, lambda n=name: saved.append(n))
        self.pb.save()
        self.assertIn("_save_stats", saved)

        # autosave thread start idempotence + stop
        with patch.object(PersistentBrain, "_autosave_loop", return_value=None):
            self.pb.start_autosave()
            t1 = self.pb._autosave_thread
            self.pb.start_autosave()
            self.assertIs(t1, self.pb._autosave_thread)
        with patch.object(self.pb, "save", return_value=None):
            self.pb.stop()
            self.assertFalse(self.pb._running)

    def test_autosave_loop_error_branch(self):
        pb = PersistentBrain(self.tmp.name, monitoring=self.mon)
        pb._running = True

        def fake_sleep(_):
            pb._running = False

        with patch.object(_PB_MOD.time, "sleep", side_effect=fake_sleep), \
             patch.object(pb, "save", side_effect=OSError("x")):
            pb._autosave_loop()

    def test_load_architecture_found_not_found_and_error(self):
        with patch.object(_PB_MOD.os.path, "isfile", return_value=False):
            self.pb._load_architecture()

        with patch.object(_PB_MOD.os.path, "isfile", return_value=True), patch.object(
            _PB_MOD, "open", side_effect=OSError("x")
        ):
            self.pb._load_architecture()

        arch = os.path.join(self.tmp.name, "arch.txt")
        with open(arch, "w", encoding="utf-8") as f:
            f.write("1. Layer One\n")
            f.write("2. Layer Two\n")

        with patch.object(_PB_MOD.os.path, "isfile", side_effect=lambda p: p == arch), patch.object(
            _PB_MOD.os.path, "dirname", return_value=self.tmp.name
        ):
            self.pb._load_architecture()
            self.assertTrue(isinstance(self.pb._architecture, dict))

    def test_save_load_knowledge(self):
        kn = types.SimpleNamespace(_long_term={"k": 1}, _episodic=[1], _semantic={"s": 2})
        kn.export_state = lambda: {"long_term": kn._long_term, "episodic": kn._episodic, "semantic": kn._semantic}
        self.pb.knowledge = kn
        self.pb._save_knowledge()
        kn2 = types.SimpleNamespace(_long_term={}, _episodic=[], _semantic={})
        def _kn_import(data):
            kn2._long_term = data.get("long_term", {})
            kn2._episodic = data.get("episodic", [])
            kn2._semantic = data.get("semantic", {})
        kn2.import_state = _kn_import
        self.pb.knowledge = kn2
        self.pb._load_knowledge()
        self.assertIn("k", self.pb.knowledge._long_term)

    def test_save_load_goals(self):
        g1 = _GoalObj()
        gm = types.SimpleNamespace(_goals={"g1": g1}, _counter=2, _active_goal_id="g1")
        gm.export_state = lambda: {
            "counter": gm._counter, "active_goal_id": gm._active_goal_id,
            "goals": {gid: {"goal_id": g.goal_id, "description": g.description,
                            "priority": g.priority.value, "status": g.status.value,
                            "parent_id": g.parent_id, "sub_goals": g.sub_goals,
                            "deadline": g.deadline, "success_criteria": g.success_criteria,
                            "notes": g.notes, "created_at": g.created_at}
                      for gid, g in gm._goals.items()}
        }
        self.pb.goal_manager = gm
        self.pb._save_goals()

        class GoalPriority(Enum):
            MEDIUM = "medium"
            HIGH = "high"

        class GoalStatus(Enum):
            PENDING = "pending"
            ACTIVE = "active"

        class Goal:
            def __init__(self, goal_id, description, priority):
                self.goal_id = goal_id
                self.description = description
                self.priority = priority
                self.status = GoalStatus.PENDING
                self.parent_id = None
                self.sub_goals = []
                self.deadline = None
                self.success_criteria = ""
                self.notes = []
                self.created_at = None

        fake_mod = types.ModuleType("core.goal_manager")
        fake_mod.Goal = Goal
        fake_mod.GoalStatus = GoalStatus
        fake_mod.GoalPriority = GoalPriority
        core_pkg = types.ModuleType("core")
        core_pkg.__path__ = []
        with patch.dict(sys.modules, {"core": core_pkg, "core.goal_manager": fake_mod}):
            gm2 = types.SimpleNamespace(_goals={})
            def _gm_import(data):
                for gid, gd in data.get("goals", {}).items():
                    gm2._goals[gid] = Goal(gid, gd.get("description", ""),
                                            GoalPriority(gd.get("priority", "medium")))
            gm2.import_state = _gm_import
            self.pb.goal_manager = gm2
            self.pb._load_goals()
            self.assertIn("g1", self.pb.goal_manager._goals)

        with patch.object(self.pb, "_load_json", return_value={"goals": {"g": {}}}), \
             patch.dict(sys.modules, {"core": core_pkg, "core.goal_manager": fake_mod}):
            self.pb._load_goals()

    def test_save_load_episodes(self):
        exp = types.SimpleNamespace(
            _buffer=[_EpisodeObj()], _patterns=[{"p": 1}], _replay_stats={"total": 1}, _index={}
        )
        exp.export_state = lambda: {
            "episodes": [{"episode_id": e.episode_id, "goal": e.goal, "actions": e.actions,
                          "outcome": e.outcome, "success": e.success, "context": e.context,
                          "replayed_count": e.replayed_count, "lessons": e.lessons,
                          "created_at": e.created_at} for e in exp._buffer],
            "patterns": list(exp._patterns),
            "replay_stats": dict(exp._replay_stats),
        }
        self.pb.experience = exp
        self.pb._save_episodes()

        class Episode:
            def __init__(self, episode_id, goal, actions, outcome, success, context=None):
                self.episode_id = episode_id
                self.goal = goal
                self.actions = actions
                self.outcome = outcome
                self.success = success
                self.context = context
                self.replayed_count = 0
                self.lessons = []
                self.created_at = None

        fake = types.ModuleType("learning.experience_replay")
        fake.Episode = Episode
        with patch.dict("sys.modules", {"learning.experience_replay": fake}):
            exp2 = types.SimpleNamespace(_buffer=[], _patterns=[], _replay_stats={}, _index={})
            def _ep_import(data):
                data_list = data.get("episodes", []) if isinstance(data, dict) else data
                for ed in data_list:
                    ep = Episode(episode_id=ed.get("episode_id",""), goal=ed.get("goal",""),
                                 actions=ed.get("actions",[]), outcome=ed.get("outcome",""),
                                 success=ed.get("success",False), context=ed.get("context"))
                    ep.replayed_count = ed.get("replayed_count", 0)
                    ep.lessons = ed.get("lessons", [])
                    exp2._buffer.append(ep)
                exp2._patterns.extend(data.get("patterns", []) if isinstance(data, dict) else [])
            exp2.import_state = _ep_import
            self.pb.experience = exp2
            self.pb._load_episodes()
            self.assertEqual(len(self.pb.experience._buffer), 1)

        with patch.object(self.pb, "_load_json", return_value=[{"episode_id": "e"}]):
            with patch.dict("sys.modules", {"learning.experience_replay": fake}):
                self.pb._load_episodes()

        with patch.object(self.pb, "_load_json", return_value={"episodes": [{"x": 1}]}):
            with patch.dict("sys.modules", {"learning.experience_replay": fake}):
                self.pb._load_episodes()

    def test_save_load_skills_identity_reflections(self):
        sl = types.SimpleNamespace(_skills={"s": _Skill("s")})
        sl.export_state = lambda: {
            n: {"name": s.name, "description": s.description, "strategy": s.strategy,
                "tags": s.tags, "use_count": s.use_count, "success_count": s.success_count}
            for n, s in sl._skills.items()}
        self.pb.skill_library = sl
        self.pb._save_skills()
        sl2 = types.SimpleNamespace(_skills={"s": _Skill("s")})
        sl2.import_state = lambda data: None
        self.pb.skill_library = sl2
        self.pb._load_skills()

        idn = types.SimpleNamespace(_capabilities={"c": _Cap("c")}, _performance_history=[1, 2])
        idn.export_state = lambda: {
            "capabilities": {n: {"proficiency": c.proficiency, "usage_count": c.usage_count}
                             for n, c in idn._capabilities.items()},
            "performance_history": list(idn._performance_history)}
        self.pb.identity = idn
        self.pb._save_identity()
        idn2 = types.SimpleNamespace(_capabilities={"c": _Cap("c")}, _performance_history=[])
        idn2.import_state = lambda data: None
        self.pb.identity = idn2
        self.pb._load_identity()

        ref = types.SimpleNamespace(_reflections=[1], _insights=[2])
        ref.export_state = lambda: {"reflections": list(ref._reflections), "insights": list(ref._insights)}
        self.pb.reflection = ref
        self.pb._save_reflections()
        ref2 = types.SimpleNamespace(_reflections=[], _insights=[])
        ref2.import_state = lambda data: None
        self.pb.reflection = ref2
        self.pb._load_reflections()

        with patch.object(self.pb, "_load_json", return_value={"capabilities": {"x": {"proficiency": 1}}}):
            self.pb._load_identity()

    def test_save_load_social(self):
        actor = _Actor()
        soc = types.SimpleNamespace(_actors={"user": actor})
        soc.export_state = lambda: {
            "actors": {
                aid: {"name": a.name, "relation": a.relation.value, "trust": a.trust.value,
                      "preferred_style": a.preferred_style.value,
                      "interaction_count": a.interaction_count, "notes": a.notes,
                      "last_interaction": a.last_interaction, "preferences": a.preferences}
                for aid, a in soc._actors.items()}}
        self.pb.social = soc
        self.pb._save_social()

        class RelationshipType(Enum):
            UNKNOWN = "unknown"
            FRIEND = "friend"

        class TrustLevel(Enum):
            LOW = 1
            MEDIUM = 2

        class CommunicationStyle(Enum):
            FRIENDLY = "friendly"

        class SocialActor:
            def __init__(self, aid, name, rel, trust):
                self.actor_id = aid
                self.name = name
                self.relation = rel
                self.trust = trust
                self.preferred_style = CommunicationStyle.FRIENDLY
                self.interaction_count = 0
                self.notes = []
                self.last_interaction = None
                self.preferences = {}

        fake = types.ModuleType("social.social_model")
        fake.SocialActor = SocialActor
        fake.RelationshipType = RelationshipType
        fake.TrustLevel = TrustLevel
        fake.CommunicationStyle = CommunicationStyle

        with patch.dict("sys.modules", {"social.social_model": fake}):
            soc2 = types.SimpleNamespace(_actors={})
            def _soc_import(data):
                actors_data = data.get("actors", data) if isinstance(data, dict) else {}
                for aid, ad in actors_data.items():
                    if isinstance(ad, dict):
                        soc2._actors[aid] = SocialActor(
                            aid, ad.get("name", ""),
                            RelationshipType(ad.get("relation", "unknown")),
                            TrustLevel(ad.get("trust", 1)))
            soc2.import_state = _soc_import
            self.pb.social = soc2
            self.pb._load_social()
            self.assertIn("user", self.pb.social._actors)

        with patch.object(self.pb, "_load_json", return_value={"u": {"trust": "MEDIUM", "preferred_style": "friendly"}}):
            with patch.dict("sys.modules", {"social.social_model": fake}):
                self.pb._load_social()

    def test_save_load_strategies_thoughts_learning_quality(self):
        si = types.SimpleNamespace(
            _strategy_store={"k": "v"}, _applied=["a"], _proposals=[_Proposal()]
        )
        si.export_state = lambda: {
            "strategies": dict(si._strategy_store),
            "applied": list(si._applied),
            "proposals": [{"area": p.area, "current_behavior": p.current_behavior,
                           "proposed_change": p.proposed_change, "rationale": p.rationale,
                           "priority": p.priority, "status": p.status,
                           "created_at": p.created_at} for p in si._proposals],
        }
        self.pb.self_improvement = si
        self.pb._save_strategies()

        class ImprovementProposal:
            def __init__(self, area, current_behavior, proposed_change, rationale, priority):
                self.area = area
                self.current_behavior = current_behavior
                self.proposed_change = proposed_change
                self.rationale = rationale
                self.priority = priority
                self.status = "proposed"
                self.created_at = None

        fake = types.ModuleType("self_improvement.self_improvement")
        fake.ImprovementProposal = ImprovementProposal

        with patch.dict("sys.modules", {"self_improvement.self_improvement": fake}):
            si2 = types.SimpleNamespace(_strategy_store={}, _applied=[], _proposals=[])
            def _si_import(data):
                si2._strategy_store.update(data.get("strategies", {}))
                si2._applied.extend(data.get("applied", []))
                for pd in data.get("proposals", []):
                    si2._proposals.append(ImprovementProposal(
                        pd.get("area",""), pd.get("current_behavior",""),
                        pd.get("proposed_change",""), pd.get("rationale",""),
                        pd.get("priority",0)))
            si2.import_state = _si_import
            self.pb.self_improvement = si2
            self.pb._load_strategies()
            self.assertTrue(self.pb.self_improvement._proposals)

        pm = types.SimpleNamespace(_thoughts=["t"], _mood="m", _cycle_count=3)
        pm.export_state = lambda: {"thoughts": list(pm._thoughts), "mood": pm._mood, "cycle_count": pm._cycle_count}
        self.pb.proactive_mind = pm
        self.pb._save_thoughts()
        pm2 = types.SimpleNamespace(_thoughts=[], _mood="", _cycle_count=0)
        pm2.import_state = lambda data: None
        self.pb.proactive_mind = pm2
        self.pb._load_thoughts()

        tracker = _LRTracker()
        self.pb.autonomous_loop = types.SimpleNamespace(learning_quality=tracker)
        self.pb._save_learning_quality()
        self.pb._load_learning_quality()
        self.assertEqual(tracker.loaded, {"s": {"q": 1}})

        # pending path without loop
        self.pb.autonomous_loop = None
        self.pb._load_learning_quality()

    def test_save_load_attention_temporal_causal_environment_learning(self):
        fake_att = types.ModuleType("attention.attention_focus")
        fake_att.AttentionMode = _AttentionMode

        att = types.SimpleNamespace(
            _mode=_AttentionMode.FOCUS,
            _counter=1,
            _noise_keywords=["n"],
            _history=[1],
        )
        att.export_state = lambda: {
            "mode": att._mode.value if att._mode else None, "counter": att._counter,
            "noise_keywords": list(att._noise_keywords), "history": list(att._history)}
        self.pb.attention = att
        self.pb._save_attention()
        att2 = types.SimpleNamespace(_mode=None, _counter=0, _noise_keywords=[], _history=[])
        att2.import_state = lambda data: None
        self.pb.attention = att2
        with patch.dict("sys.modules", {"attention.attention_focus": fake_att}):
            self.pb._load_attention()

        fake_tmp = types.ModuleType("reasoning.temporal_reasoning")
        fake_tmp.TemporalEvent = _TemporalEvent
        ev = types.SimpleNamespace(event_id="e", description="d", start=1, end=2, tags=["t"], relations=["r"])
        tmp = types.SimpleNamespace(_counter=1, _timeline=["e"], _events={"e": ev})
        tmp.export_state = lambda: {
            "counter": tmp._counter, "timeline": list(tmp._timeline),
            "events": {eid: {"event_id": e.event_id, "description": e.description,
                             "start": e.start, "end": e.end, "tags": e.tags,
                             "relations": e.relations}
                       for eid, e in tmp._events.items()}}
        self.pb.temporal = tmp
        self.pb._save_temporal()
        tmp2 = types.SimpleNamespace(_counter=0, _timeline=[], _events={})
        tmp2.import_state = lambda data: None
        self.pb.temporal = tmp2
        with patch.dict("sys.modules", {"reasoning.temporal_reasoning": fake_tmp}):
            self.pb._load_temporal()

        graph = _CausalGraph()
        graph._links = [
            _CausalLink("a", "b", 0.8, mechanism="m", context={"x": 1})
        ]
        causal = types.SimpleNamespace(graph=graph)
        causal.export_state = lambda: [
            {"cause": lk.cause, "effect": lk.effect, "confidence": lk.confidence,
             "mechanism": lk.mechanism, "context": lk.context,
             "observed_count": lk.observed_count, "created_at": lk.created_at}
            for lk in causal.graph._links]
        self.pb.causal = causal
        self.pb._save_causal()
        causal2 = types.SimpleNamespace(graph=_CausalGraph())
        causal2.import_state = lambda data: None
        self.pb.causal = causal2
        self.pb._load_causal()

        env = types.SimpleNamespace(
            _state={"s": 1}, _entities={"e": 1}, _relations=[1], _constraints=[2]
        )
        env.export_state = lambda: {
            "state": dict(env._state), "entities": dict(env._entities),
            "relations": list(env._relations), "constraints": list(env._constraints)}
        self.pb.environment_model = env
        self.pb._save_environment()
        env2 = types.SimpleNamespace(_state={}, _entities={}, _relations=[], _constraints=[])
        env2.import_state = lambda data: None
        self.pb.environment_model = env2
        self.pb._load_environment()

        lrn = types.SimpleNamespace(
            _learned=[{"a": 1}], _queue=[{"u": 1, "fetch_fn": object()}]
        )
        lrn.export_state = lambda: {
            "learned": list(lrn._learned),
            "queue": [{k: v for k, v in q.items() if k != "fetch_fn"} for q in lrn._queue]}
        self.pb.learning = lrn
        self.pb._save_learning()
        lrn2 = types.SimpleNamespace(_learned=[], _queue=[])
        lrn2.import_state = lambda data: None
        self.pb.learning = lrn2
        self.pb._load_learning()

        with patch.dict("sys.modules", {"reasoning.temporal_reasoning": fake_tmp, "attention.attention_focus": fake_att}):
            with patch.object(self.pb, "_load_json", return_value={"events": {"x": {}}}):
                self.pb._load_temporal()

    def test_save_load_sandbox_and_simple_saves(self):
        fake_sb = types.ModuleType("environment.sandbox")
        fake_sb.SimulationRun = _SimulationRun
        fake_sb.SandboxResult = _SandboxVerdict

        sb = types.SimpleNamespace(_runs=[_SimulationRun("r1", "a1"), _BrokenRun()])
        def _sb_export():
            runs = []
            for r in sb._runs:
                try:
                    runs.append(r.to_dict())
                except Exception:
                    pass
            return {"runs": runs}
        sb.export_state = _sb_export
        self.pb.sandbox = sb
        self.pb._save_sandbox()

        sb2 = types.SimpleNamespace(_runs=[])
        sb2.import_state = lambda data: None
        self.pb.sandbox = sb2
        with patch.dict("sys.modules", {"environment.sandbox": fake_sb}):
            self.pb._load_sandbox()

        with patch.object(self.pb, "_load_json", return_value={"runs": [{"run_id": "x", "action": "a", "verdict": "bad"}]}):
            with patch.dict("sys.modules", {"environment.sandbox": fake_sb}):
                self.pb._load_sandbox()

        self.pb._conversations = [{"x": 1}]
        self.pb._lessons = [{"x": 1}]
        self.pb._solver_cases = [{"x": 1}]
        self.pb._exact_solver_journal = [{"x": 1}]
        self.pb._evolution_log = [{"x": 1}]
        self.pb._save_conversations()
        self.pb._save_lessons()
        self.pb._save_solver_cases()
        self.pb._save_exact_solver_journal()
        self.pb._save_evolution()
        self.pb._save_stats()
        self.pb._load_conversations()
        self.pb._load_lessons()
        self.pb._load_solver_cases()
        self.pb._load_exact_solver_journal()
        self.pb._load_evolution()
        self.pb._load_stats()

    def test_load_stats_defaults(self):
        with patch.object(self.pb, "_load_json", return_value={"total_cycles": 1}):
            self.pb._stats.pop("useful_cycles", None)
            self.pb._stats.pop("weak_cycles", None)
            self.pb._load_stats()
            self.assertIn("useful_cycles", self.pb._stats)

    def test_log_uses_monitoring_level(self):
        self.pb._log("x", level="warning")
        self.pb._log("x", level="error")
        self.pb._log("x", level="unknown")
        self.assertTrue(self.mon.warning_calls)

    def test_disk_usage_getsize_error_and_load_disk_usage_error_branch(self):
        with patch.object(_PB_MOD.os.path, "getsize", side_effect=OSError("x")):
            du = self.pb.disk_usage()
            self.assertIn("ok", du)

        called = []
        for name in [
            "_load_architecture", "_load_stats", "_load_knowledge", "_load_conversations",
            "_load_lessons", "_load_solver_cases", "_load_exact_solver_journal", "_load_strategies",
            "_load_evolution", "_load_goals", "_load_episodes", "_load_skills", "_load_identity",
            "_load_social", "_load_reflections", "_load_thoughts", "_load_learning_quality",
            "_load_attention", "_load_temporal", "_load_causal", "_load_environment",
            "_load_learning", "_load_sandbox"
        ]:
            setattr(self.pb, name, lambda n=name: called.append(n))
        self.pb.disk_usage = lambda: (_ for _ in ()).throw(ValueError("x"))
        self.pb.load()
        self.assertTrue(called)

    def test_all_no_component_return_branches(self):
        pb = PersistentBrain(self.tmp.name)
        pb._save_knowledge()
        pb._load_knowledge()
        pb._save_goals()
        pb._load_goals()
        pb._save_episodes()
        pb._load_episodes()
        pb._save_skills()
        pb._load_skills()
        pb._save_identity()
        pb._load_identity()
        pb._save_social()
        pb._load_social()
        pb._save_reflections()
        pb._load_reflections()
        pb._save_strategies()
        pb._load_strategies()
        pb._save_thoughts()
        pb._load_thoughts()
        pb._save_learning_quality()
        pb._load_learning_quality()
        pb._save_attention()
        pb._load_attention()
        pb._save_temporal()
        pb._load_temporal()
        pb._save_causal()
        pb._load_causal()
        pb._save_environment()
        pb._load_environment()
        pb._save_learning()
        pb._load_learning()
        pb._save_sandbox()
        pb._load_sandbox()

    def test_error_paths_for_loaders(self):
        # skills error
        self.pb.skill_library = types.SimpleNamespace(_skills=None)
        with patch.object(self.pb, "_load_json", return_value={"s": {"use_count": 1}}):
            self.pb._load_skills()

        # identity error
        self.pb.identity = types.SimpleNamespace(_capabilities=None)
        with patch.object(self.pb, "_load_json", return_value={"capabilities": {"x": {"proficiency": 0.9}}}):
            self.pb._load_identity()

        # reflections error
        self.pb.reflection = types.SimpleNamespace(_reflections=None, _insights=None)
        with patch.object(self.pb, "_load_json", return_value={"reflections": [1], "insights": [2]}):
            self.pb._load_reflections()

        # thoughts error
        self.pb.proactive_mind = types.SimpleNamespace()
        with patch.object(self.pb, "_load_json", return_value={"thoughts": [1], "mood": "m", "cycle_count": 1}):
            self.pb._load_thoughts()

        # attention error (bad enum import shape)
        fake_att = types.ModuleType("attention.attention_focus")
        fake_att.AttentionMode = object
        self.pb.attention = types.SimpleNamespace(_mode=None, _counter=0, _noise_keywords=[], _history=[])
        with patch.dict(sys.modules, {"attention.attention_focus": fake_att}), \
             patch.object(self.pb, "_load_json", return_value={"mode": "focus", "history": [1]}):
            self.pb._load_attention()

        # temporal error
        self.pb.temporal = types.SimpleNamespace(_counter=0, _timeline=[], _events={})
        fake_tmp = types.ModuleType("reasoning.temporal_reasoning")
        fake_tmp.TemporalEvent = _TemporalEvent
        with patch.dict(sys.modules, {"reasoning.temporal_reasoning": fake_tmp}), \
             patch.object(self.pb, "_load_json", return_value={"events": {"e": {}}}):
            self.pb._load_temporal()

        # causal errors
        self.pb.causal = types.SimpleNamespace(graph=_CausalGraph())
        with patch.object(self.pb, "_load_json", return_value={"not": "list"}):
            self.pb._load_causal()
        with patch.object(self.pb, "_load_json", return_value=[{"x": 1}]):
            self.pb._load_causal()

        # environment error
        self.pb.environment_model = types.SimpleNamespace(_state=None, _entities=None, _relations=None, _constraints=None)
        with patch.object(self.pb, "_load_json", return_value={"state": {"a": 1}}):
            self.pb._load_environment()

        # learning error
        self.pb.learning = types.SimpleNamespace(_learned=None, _queue=None)
        with patch.object(self.pb, "_load_json", return_value={"learned": [1], "queue": [2]}):
            self.pb._load_learning()

        # sandbox error: import fails or malformed data
        self.pb.sandbox = types.SimpleNamespace(_runs=[])
        fake_sb = types.ModuleType("environment.sandbox")
        fake_sb.SimulationRun = _SimulationRun
        fake_sb.SandboxResult = _SandboxVerdict
        with patch.dict(sys.modules, {"environment.sandbox": fake_sb}), \
             patch.object(self.pb, "_load_json", return_value={"runs": [{"x": 1}]}):
            self.pb._load_sandbox()

    def test_remaining_branches_for_full_coverage(self):
        # lesson failed branch (failed_tasks increment)
        before = self.pb._stats["failed_tasks"]
        self.pb.record_lesson("g2", False, "l2", "")
        self.assertEqual(self.pb._stats["failed_tasks"], before + 1)

        # get_memory_context social KeyError path
        bad_get = types.SimpleNamespace(get=lambda *_: (_ for _ in ()).throw(KeyError("x")))
        self.pb.social = types.SimpleNamespace(_actors=bad_get)
        self.pb.goal_manager = types.SimpleNamespace(get_all=lambda: [types.SimpleNamespace(description="obj", status="ACTIVE")])
        self.pb._conversations = [{"role": "user", "message": "this and with tokens"}]
        self.pb.get_memory_context()

        class _BadMood:
            def __getattribute__(self, name):
                if name == "_mood":
                    raise TypeError("boom")
                return object.__getattribute__(self, name)

        self.pb.proactive_mind = _BadMood()
        self.pb.get_memory_context()

        # architecture successful parse (real file from repo root)
        self.pb._load_architecture()

        # _load_goals no-goals return
        self.pb.goal_manager = types.SimpleNamespace(_goals={})
        with patch.object(self.pb, "_load_json", return_value={}):
            self.pb._load_goals()

        # _load_episodes error path
        fake_ep_mod = types.ModuleType("learning.experience_replay")
        class Episode2:
            def __init__(self, **kwargs):
                self.episode_id = kwargs.get("episode_id", "")
        fake_ep_mod.Episode = Episode2
        self.pb.experience = types.SimpleNamespace(_buffer=[], _patterns=[], _replay_stats={}, _index={})
        with patch.dict(sys.modules, {"learning.experience_replay": fake_ep_mod}), \
             patch.object(self.pb, "_load_json", return_value={"episodes": [1]}):
            self.pb._load_episodes()
        with patch.object(self.pb, "_load_json", return_value={"episodes": []}):
            self.pb._load_episodes()

        # _load_skills: empty + except
        self.pb.skill_library = types.SimpleNamespace(_skills={"s": _Skill("s")})
        with patch.object(self.pb, "_load_json", return_value={}):
            self.pb._load_skills()
        self.pb.skill_library = types.SimpleNamespace(_skills=1)
        with patch.object(self.pb, "_load_json", return_value={"s": {"use_count": 1}}):
            self.pb._load_skills()

        # _load_identity: empty + except
        self.pb.identity = types.SimpleNamespace(_capabilities={"c": _Cap("c")}, _performance_history=[])
        with patch.object(self.pb, "_load_json", return_value={}):
            self.pb._load_identity()
        self.pb.identity = types.SimpleNamespace(_capabilities=1, _performance_history=[])
        with patch.object(self.pb, "_load_json", return_value={"capabilities": {"c": {"proficiency": 1}}}):
            self.pb._load_identity()

        # _load_social: empty + existing actor + except
        class RelationshipType(Enum):
            UNKNOWN = "unknown"
            FRIEND = "friend"
        class TrustLevel(Enum):
            LOW = 1
            MEDIUM = 2
        class CommunicationStyle(Enum):
            FRIENDLY = "friendly"
        class SocialActor:
            def __init__(self, _aid, name, rel, trust):  # noqa: ARG004
                self.name = name
                self.relation = rel
                self.trust = trust
                self.preferred_style = CommunicationStyle.FRIENDLY
                self.interaction_count = 0
                self.notes = []
                self.last_interaction = None
                self.preferences = {}
        fake_social = types.ModuleType("social.social_model")
        fake_social.SocialActor = SocialActor
        fake_social.RelationshipType = RelationshipType
        fake_social.TrustLevel = TrustLevel
        fake_social.CommunicationStyle = CommunicationStyle

        self.pb.social = types.SimpleNamespace(_actors={"user": _Actor()})
        with patch.object(self.pb, "_load_json", return_value={}):
            self.pb._load_social()
        with patch.dict(sys.modules, {"social.social_model": fake_social}), \
             patch.object(self.pb, "_load_json", return_value={"user": {"name": "U", "relation": "friend", "trust": 2}}):
            self.pb._load_social()
        self.pb.social = types.SimpleNamespace(_actors=None)
        with patch.dict(sys.modules, {"social.social_model": fake_social}), \
             patch.object(self.pb, "_load_json", return_value={"u": {"name": "U", "relation": "friend", "trust": 2}}):
            self.pb._load_social()

        # reflections/thoughts extra branches
        self.pb.reflection = types.SimpleNamespace(_reflections=[], _insights=[])
        with patch.object(self.pb, "_load_json", return_value={}):
            self.pb._load_reflections()
        self.pb.reflection = types.SimpleNamespace(_reflections=1, _insights=1)
        with patch.object(self.pb, "_load_json", return_value={"reflections": [1], "insights": [2]}):
            self.pb._load_reflections()

        self.pb.proactive_mind = types.SimpleNamespace(_thoughts=[], _mood="", _cycle_count=0)
        with patch.object(self.pb, "_load_json", return_value={}):
            self.pb._load_thoughts()
        self.pb.proactive_mind = 1
        with patch.object(self.pb, "_load_json", return_value={"thoughts": [1], "mood": "m", "cycle_count": 1}):
            self.pb._load_thoughts()

        with patch.object(self.pb, "_load_json", return_value={"proposals": [{}]}):
            self.pb.self_improvement = types.SimpleNamespace(_strategy_store={}, _applied=[], _proposals=[])
            self.pb._load_strategies()

        # learning_quality branches
        self.pb.autonomous_loop = types.SimpleNamespace(learning_quality=None)
        self.pb._save_learning_quality()
        self.pb._load_learning_quality()

        # attention branches
        self.pb.attention = types.SimpleNamespace(_mode=None, _counter=0, _noise_keywords=[], _history=[])
        with patch.object(self.pb, "_load_json", return_value={}):
            self.pb._load_attention()
        fake_att = types.ModuleType("attention.attention_focus")
        fake_att.AttentionMode = _AttentionMode
        self.pb.attention = types.SimpleNamespace(_mode=None, _counter=0, _noise_keywords=[], _history=None)
        with patch.dict(sys.modules, {"attention.attention_focus": fake_att}), \
             patch.object(self.pb, "_load_json", return_value={"mode": "focus", "history": [1]}):
            self.pb._load_attention()

        # temporal branches
        self.pb.temporal = types.SimpleNamespace(_counter=0, _timeline=[], _events={})
        with patch.object(self.pb, "_load_json", return_value={"timeline": []}):
            self.pb._load_temporal()
        fake_tmp = types.ModuleType("reasoning.temporal_reasoning")
        fake_tmp.TemporalEvent = _TemporalEvent
        with patch.dict(sys.modules, {"reasoning.temporal_reasoning": fake_tmp}), \
             patch.object(self.pb, "_load_json", return_value={"events": {"e": {}}, "timeline": []}):
            self.pb._load_temporal()

        # causal branches
        self.pb.causal = types.SimpleNamespace(graph=None, export_state=lambda: [])
        self.pb._save_causal()
        with patch.object(self.pb, "_load_json", return_value=[{"cause": "a", "effect": "b"}]):
            self.pb._load_causal()
        self.pb.causal = types.SimpleNamespace(graph=_CausalGraph())
        with patch.object(self.pb, "_load_json", return_value=[{"x": 1}]):
            self.pb._load_causal()
        with patch.object(self.pb, "_load_json", return_value=[{"cause": "a", "effect": "b", "created_at": 10}]):
            self.pb._load_causal()

        # environment/learning branches
        self.pb.environment_model = types.SimpleNamespace(_state={}, _entities={}, _relations=[], _constraints=[])
        with patch.object(self.pb, "_load_json", return_value={}):
            self.pb._load_environment()
        self.pb.environment_model = types.SimpleNamespace(_state=None, _entities={}, _relations=[], _constraints=[])
        with patch.object(self.pb, "_load_json", return_value={"state": {"a": 1}}):
            self.pb._load_environment()

        self.pb.learning = types.SimpleNamespace(_learned=[], _queue=[])
        with patch.object(self.pb, "_load_json", return_value={}):
            self.pb._load_learning()
        self.pb.learning = types.SimpleNamespace(_learned=None, _queue=[])
        with patch.object(self.pb, "_load_json", return_value={"learned": [1]}):
            self.pb._load_learning()

        # sandbox branches
        self.pb.sandbox = types.SimpleNamespace(_runs=[])
        with patch.object(self.pb, "_load_json", return_value={}):
            self.pb._load_sandbox()
        fake_sb = types.ModuleType("environment.sandbox")
        fake_sb.SimulationRun = _SimulationRun
        fake_sb.SandboxResult = _SandboxVerdict
        self.pb.sandbox = types.SimpleNamespace(_runs=None)
        with patch.dict(sys.modules, {"environment.sandbox": fake_sb}), \
             patch.object(self.pb, "_load_json", return_value={"runs": [{"run_id": "x", "action": "a", "duration": "bad"}]}):
            self.pb._load_sandbox()

        self.pb.sandbox = types.SimpleNamespace(_runs=[])
        with patch.dict(sys.modules, {"environment.sandbox": fake_sb}), \
             patch.object(self.pb, "_load_json", return_value=[]):
            self.pb._load_sandbox()

        self.pb.sandbox = types.SimpleNamespace(_runs=[])
        with patch.dict(sys.modules, {"environment.sandbox": fake_sb}), \
             patch.object(self.pb, "_load_json", return_value=[1]):
            self.pb._load_sandbox()


if __name__ == "__main__":
    unittest.main()
