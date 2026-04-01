import time
import unittest
from types import SimpleNamespace

from core.goal_manager import Goal, GoalManager, GoalPriority, GoalStatus


class GoalTests(unittest.TestCase):
    """Покрытие класса Goal: свойства, to_dict, urgency."""

    def test_basic_creation(self):
        g = Goal("g1", "Test goal")
        self.assertEqual(g.goal_id, "g1")
        self.assertEqual(g.description, "Test goal")
        self.assertEqual(g.priority, GoalPriority.MEDIUM)
        self.assertEqual(g.status, GoalStatus.PENDING)
        self.assertEqual(g.progress, 0.0)
        self.assertIsNone(g.parent_id)
        self.assertIsNone(g.deadline)
        self.assertIsNone(g.success_criteria)
        self.assertEqual(g.tags, [])
        self.assertEqual(g.sub_goals, [])
        self.assertIsNone(g.started_at)
        self.assertIsNone(g.completed_at)
        self.assertEqual(g.notes, [])

    def test_creation_with_params(self):
        g = Goal("g2", "Goal with params", priority=GoalPriority.HIGH,
                 parent_id="g1", deadline=time.time() + 3600,
                 success_criteria="done", tags=["tag1"])
        self.assertEqual(g.priority, GoalPriority.HIGH)
        self.assertEqual(g.parent_id, "g1")
        self.assertEqual(g.success_criteria, "done")
        self.assertEqual(g.tags, ["tag1"])

    def test_is_overdue_no_deadline(self):
        g = Goal("g1", "No deadline")
        self.assertFalse(g.is_overdue)

    def test_is_overdue_future(self):
        g = Goal("g1", "Future", deadline=time.time() + 9999)
        self.assertFalse(g.is_overdue)

    def test_is_overdue_past(self):
        g = Goal("g1", "Past", deadline=time.time() - 100)
        self.assertTrue(g.is_overdue)

    def test_urgency_no_deadline(self):
        g = Goal("g1", "X", priority=GoalPriority.CRITICAL)
        self.assertAlmostEqual(g.urgency_score, 1.0, places=1)

        g2 = Goal("g2", "X", priority=GoalPriority.DEFERRED)
        self.assertAlmostEqual(g2.urgency_score, 0.2, places=1)

    def test_urgency_with_deadline(self):
        # Near deadline → higher urgency
        g_near = Goal("g1", "X", priority=GoalPriority.MEDIUM, deadline=time.time() + 10)
        g_far = Goal("g2", "X", priority=GoalPriority.MEDIUM, deadline=time.time() + 999999)
        self.assertGreater(g_near.urgency_score, g_far.urgency_score)

    def test_urgency_past_deadline(self):
        g = Goal("g1", "X", priority=GoalPriority.MEDIUM, deadline=time.time() - 100)
        # Past deadline → remaining=0 → urgency = 1/(1+0) = 1.0
        self.assertGreater(g.urgency_score, 0.5)

    def test_to_dict(self):
        g = Goal("g1", "Test", priority=GoalPriority.HIGH, deadline=time.time() + 3600)
        g.sub_goals = ["g2"]
        d = g.to_dict()
        self.assertEqual(d["goal_id"], "g1")
        self.assertEqual(d["priority"], "HIGH")
        self.assertEqual(d["status"], "pending")
        self.assertEqual(d["sub_goals"], ["g2"])
        self.assertIn("urgency_score", d)
        self.assertIn("is_overdue", d)


class GoalManagerBasicTests(unittest.TestCase):
    """Покрытие GoalManager: add, activate, complete, fail, cancel, progress."""

    def setUp(self):
        self.gm = GoalManager()

    def test_add_goal(self):
        g = self.gm.add("Do something")
        self.assertEqual(g.goal_id, "g0001")
        self.assertEqual(g.description, "Do something")
        self.assertIn("g0001", self.gm._goals)

    def test_add_with_parent(self):
        parent = self.gm.add("Parent")
        child = self.gm.add("Child", parent_id=parent.goal_id)
        self.assertEqual(child.parent_id, parent.goal_id)
        self.assertIn(child.goal_id, parent.sub_goals)

    def test_add_with_missing_parent(self):
        child = self.gm.add("Child", parent_id="nonexistent")
        self.assertEqual(child.parent_id, "nonexistent")
        # Doesn't crash, but doesn't add to parent's sub_goals

    def test_add_with_all_params(self):
        g = self.gm.add("Goal", priority=GoalPriority.CRITICAL,
                         deadline=time.time() + 3600,
                         success_criteria="done", tags=["a", "b"])
        self.assertEqual(g.priority, GoalPriority.CRITICAL)
        self.assertEqual(g.success_criteria, "done")
        self.assertEqual(g.tags, ["a", "b"])

    def test_activate(self):
        g = self.gm.add("Goal")
        self.gm.activate(g.goal_id)
        self.assertEqual(g.status, GoalStatus.ACTIVE)
        self.assertIsNotNone(g.started_at)
        self.assertEqual(self.gm._active_goal_id, g.goal_id)

    def test_activate_preserves_started_at(self):
        g = self.gm.add("Goal")
        self.gm.activate(g.goal_id)
        first_start = g.started_at
        self.gm.activate(g.goal_id)
        self.assertEqual(g.started_at, first_start)

    def test_activate_nonexistent(self):
        self.gm.activate("nope")  # should not crash

    def test_complete(self):
        g = self.gm.add("Goal")
        self.gm.complete(g.goal_id)
        self.assertEqual(g.status, GoalStatus.COMPLETED)
        self.assertEqual(g.progress, 1.0)
        self.assertIsNotNone(g.completed_at)

    def test_complete_nonexistent(self):
        self.gm.complete("nope")  # should not crash

    def test_complete_updates_parent(self):
        parent = self.gm.add("Parent")
        c1 = self.gm.add("C1", parent_id=parent.goal_id)
        c2 = self.gm.add("C2", parent_id=parent.goal_id)
        self.gm.complete(c1.goal_id)
        self.assertAlmostEqual(parent.progress, 0.5, places=1)
        self.gm.complete(c2.goal_id)
        self.assertEqual(parent.status, GoalStatus.COMPLETED)

    def test_fail(self):
        g = self.gm.add("Goal")
        self.gm.fail(g.goal_id, reason="broken")
        self.assertEqual(g.status, GoalStatus.FAILED)
        self.assertIn("broken", g.notes[0])

    def test_fail_no_reason(self):
        g = self.gm.add("Goal")
        self.gm.fail(g.goal_id)
        self.assertEqual(g.status, GoalStatus.FAILED)
        self.assertEqual(g.notes, [])

    def test_fail_nonexistent(self):
        self.gm.fail("nope")

    def test_cancel(self):
        g = self.gm.add("Goal")
        self.gm.cancel(g.goal_id)
        self.assertEqual(g.status, GoalStatus.CANCELLED)

    def test_cancel_nonexistent(self):
        self.gm.cancel("nope")

    def test_update_progress(self):
        g = self.gm.add("Goal")
        self.gm.update_progress(g.goal_id, 0.5, note="halfway")
        self.assertEqual(g.progress, 0.5)
        self.assertIn("halfway", g.notes)

    def test_update_progress_clamp(self):
        g = self.gm.add("Goal")
        self.gm.update_progress(g.goal_id, 1.5)
        self.assertEqual(g.progress, 1.0)
        self.assertEqual(g.status, GoalStatus.COMPLETED)

    def test_update_progress_negative(self):
        g = self.gm.add("Goal")
        self.gm.update_progress(g.goal_id, -0.5)
        self.assertEqual(g.progress, 0.0)

    def test_update_progress_completes(self):
        g = self.gm.add("Goal")
        self.gm.update_progress(g.goal_id, 1.0)
        self.assertEqual(g.status, GoalStatus.COMPLETED)

    def test_update_progress_nonexistent(self):
        self.gm.update_progress("nope", 0.5)

    def test_update_progress_no_note(self):
        g = self.gm.add("Goal")
        self.gm.update_progress(g.goal_id, 0.3)
        self.assertEqual(g.notes, [])


class GoalManagerQueryTests(unittest.TestCase):
    """Покрытие get_next, get_active, get_goal, get_open_subgoals, get_all, get_tree, summary."""

    def setUp(self):
        self.gm = GoalManager()

    def test_get_next_empty(self):
        self.assertIsNone(self.gm.get_next())

    def test_get_next_highest_urgency(self):
        g_low = self.gm.add("Low", priority=GoalPriority.LOW)
        g_crit = self.gm.add("Critical", priority=GoalPriority.CRITICAL)
        nxt = self.gm.get_next()
        self.assertEqual(nxt.goal_id, g_crit.goal_id)

    def test_get_next_skips_completed(self):
        g1 = self.gm.add("Done")
        g2 = self.gm.add("Pending")
        self.gm.complete(g1.goal_id)
        self.assertEqual(self.gm.get_next().goal_id, g2.goal_id)

    def test_get_next_prefers_leaf(self):
        parent = self.gm.add("Parent", priority=GoalPriority.HIGH)
        child = self.gm.add("Child", parent_id=parent.goal_id, priority=GoalPriority.MEDIUM)
        # Parent has sub_goals, so child (leaf) should be preferred
        nxt = self.gm.get_next()
        self.assertEqual(nxt.goal_id, child.goal_id)

    def test_get_next_fallback_to_non_leaf(self):
        # Parent with two children; complete one so parent stays active
        parent = self.gm.add("Parent", priority=GoalPriority.HIGH)
        child1 = self.gm.add("Child1", parent_id=parent.goal_id)
        child2 = self.gm.add("Child2", parent_id=parent.goal_id)
        self.gm.complete(child1.goal_id)
        # child1 completed, child2 still pending (leaf candidate)
        nxt = self.gm.get_next()
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.goal_id, child2.goal_id)

    def test_get_active(self):
        self.assertIsNone(self.gm.get_active())
        g = self.gm.add("Goal")
        self.gm.activate(g.goal_id)
        self.assertEqual(self.gm.get_active().goal_id, g.goal_id)

    def test_get_goal(self):
        g = self.gm.add("Goal")
        self.assertEqual(self.gm.get_goal(g.goal_id), g)
        self.assertIsNone(self.gm.get_goal("nope"))

    def test_get_open_subgoals(self):
        parent = self.gm.add("Parent")
        c1 = self.gm.add("C1", parent_id=parent.goal_id)
        c2 = self.gm.add("C2", parent_id=parent.goal_id)
        c3 = self.gm.add("C3", parent_id=parent.goal_id)
        self.gm.complete(c2.goal_id)
        open_subs = self.gm.get_open_subgoals(parent.goal_id)
        ids = [s.goal_id for s in open_subs]
        self.assertIn(c1.goal_id, ids)
        self.assertNotIn(c2.goal_id, ids)
        self.assertIn(c3.goal_id, ids)

    def test_get_open_subgoals_nonexistent(self):
        self.assertEqual(self.gm.get_open_subgoals("nope"), [])

    def test_get_all(self):
        self.gm.add("A")
        self.gm.add("B")
        all_goals = self.gm.get_all()
        self.assertEqual(len(all_goals), 2)

    def test_get_all_with_status(self):
        g1 = self.gm.add("A")
        g2 = self.gm.add("B")
        self.gm.complete(g1.goal_id)
        completed = self.gm.get_all(status=GoalStatus.COMPLETED)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["goal_id"], g1.goal_id)

    def test_get_tree_from_root(self):
        parent = self.gm.add("Parent")
        child = self.gm.add("Child", parent_id=parent.goal_id)
        tree = self.gm.get_tree(parent.goal_id)
        self.assertEqual(tree["goal_id"], parent.goal_id)
        self.assertEqual(len(tree["children"]), 1)
        self.assertEqual(tree["children"][0]["goal_id"], child.goal_id)

    def test_get_tree_all_roots(self):
        self.gm.add("Root1")
        self.gm.add("Root2")
        tree = self.gm.get_tree()
        self.assertEqual(len(tree["roots"]), 2)

    def test_get_tree_nonexistent(self):
        self.assertEqual(self.gm.get_tree("nope"), {})

    def test_summary(self):
        self.gm.add("A")
        g2 = self.gm.add("B")
        self.gm.complete(g2.goal_id)
        s = self.gm.summary()
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["completed"], 1)
        self.assertEqual(s["pending"], 1)


class DuplicateGoalTests(unittest.TestCase):
    """Покрытие дедупликации целей."""

    def setUp(self):
        self.gm = GoalManager()

    def test_duplicate_reuse(self):
        g1 = self.gm.add("Do something")
        g2 = self.gm.add("Do something")
        self.assertEqual(g1.goal_id, g2.goal_id)
        self.assertEqual(len(self.gm._goals), 1)

    def test_duplicate_enriches(self):
        g1 = self.gm.add("Do something")
        g2 = self.gm.add("Do something", deadline=12345.0,
                          success_criteria="done", tags=["new_tag"])
        self.assertEqual(g1.deadline, 12345.0)
        self.assertEqual(g1.success_criteria, "done")
        self.assertIn("new_tag", g1.tags)

    def test_duplicate_different_parent(self):
        p1 = self.gm.add("Parent 1")
        p2 = self.gm.add("Parent 2")
        g1 = self.gm.add("Child", parent_id=p1.goal_id)
        g2 = self.gm.add("Child", parent_id=p2.goal_id)
        self.assertNotEqual(g1.goal_id, g2.goal_id)

    def test_duplicate_completed_not_reused(self):
        g1 = self.gm.add("Task")
        self.gm.complete(g1.goal_id)
        g2 = self.gm.add("Task")
        self.assertNotEqual(g1.goal_id, g2.goal_id)

    def test_empty_description(self):
        dup = self.gm._find_duplicate_goal("", None)
        self.assertIsNone(dup)

    def test_duplicate_no_overwrite_existing_deadline(self):
        g1 = self.gm.add("Task", deadline=100.0)
        g2 = self.gm.add("Task", deadline=200.0)
        self.assertEqual(g1.deadline, 100.0)  # keeps original

    def test_duplicate_no_overwrite_existing_criteria(self):
        g1 = self.gm.add("Task", success_criteria="original")
        g2 = self.gm.add("Task", success_criteria="new")
        self.assertEqual(g1.success_criteria, "original")


class NormalizeSubgoalTests(unittest.TestCase):
    """Покрытие _normalize_subgoal_text и _normalize_goal_text."""

    def test_empty(self):
        self.assertEqual(GoalManager._normalize_subgoal_text(""), "")
        self.assertEqual(GoalManager._normalize_subgoal_text(None), "")

    def test_markdown_cleanup(self):
        self.assertEqual(GoalManager._normalize_subgoal_text("- **Bold** text"), "Bold text")
        self.assertEqual(GoalManager._normalize_subgoal_text("1. Item"), "Item")
        self.assertEqual(GoalManager._normalize_subgoal_text("* starred"), "starred")
        self.assertEqual(GoalManager._normalize_subgoal_text("`code`"), "code")
        self.assertEqual(GoalManager._normalize_subgoal_text("## Header"), "Header")

    def test_subgoal_prefix(self):
        result = GoalManager._normalize_subgoal_text("подцель: описание задачи")
        self.assertEqual(result, "описание задачи")

    def test_whitespace(self):
        self.assertEqual(GoalManager._normalize_subgoal_text("  lots   of   spaces  "), "lots of spaces")

    def test_normalize_goal_text_casefold(self):
        self.assertEqual(GoalManager._normalize_goal_text("HELLO"), "hello")


class ConflictTests(unittest.TestCase):
    """Покрытие detect_conflicts и resolve_conflict."""

    def test_detect_no_core(self):
        gm = GoalManager()
        gm.add("A")
        gm.add("B")
        self.assertEqual(gm.detect_conflicts(), [])

    def test_detect_one_goal(self):
        gm = GoalManager(cognitive_core=SimpleNamespace(reasoning=lambda x: ""))
        gm.add("A")
        self.assertEqual(gm.detect_conflicts(), [])

    def test_detect_with_conflicts(self):
        core = SimpleNamespace(
            reasoning=lambda x: "КОНФЛИКТ: g0001 vs g0002 — противоречат друг другу"
        )
        gm = GoalManager(cognitive_core=core)
        gm.add("Scale up")
        gm.add("Cut costs")
        conflicts = gm.detect_conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0][0], "g0001")
        self.assertEqual(conflicts[0][1], "g0002")

    def test_detect_no_conflicts(self):
        core = SimpleNamespace(reasoning=lambda x: "Конфликтов нет")
        gm = GoalManager(cognitive_core=core)
        gm.add("A")
        gm.add("B")
        self.assertEqual(gm.detect_conflicts(), [])

    def test_resolve_g1_wins(self):
        gm = GoalManager()
        g1 = gm.add("G1", priority=GoalPriority.CRITICAL)
        g2 = gm.add("G2", priority=GoalPriority.LOW)
        winner = gm.resolve_conflict(g1.goal_id, g2.goal_id)
        self.assertEqual(winner, g1.goal_id)
        self.assertEqual(g2.status, GoalStatus.PAUSED)

    def test_resolve_g2_wins(self):
        gm = GoalManager()
        g1 = gm.add("G1", priority=GoalPriority.LOW)
        g2 = gm.add("G2", priority=GoalPriority.CRITICAL)
        winner = gm.resolve_conflict(g1.goal_id, g2.goal_id)
        self.assertEqual(winner, g2.goal_id)
        self.assertEqual(g1.status, GoalStatus.PAUSED)

    def test_resolve_nonexistent(self):
        gm = GoalManager()
        result = gm.resolve_conflict("nope1", "nope2")
        self.assertEqual(result, "nope1")


class DecomposeTests(unittest.TestCase):
    """Покрытие decompose."""

    def test_decompose_no_core(self):
        gm = GoalManager()
        g = gm.add("Goal")
        self.assertEqual(gm.decompose(g.goal_id), [])

    def test_decompose_nonexistent(self):
        gm = GoalManager(cognitive_core=SimpleNamespace(reasoning=lambda x: ""))
        self.assertEqual(gm.decompose("nope"), [])

    def test_decompose_success(self):
        core = SimpleNamespace(
            reasoning=lambda x: (
                "ПОДЦЕЛЬ: Настроить базу данных\n"
                "КРИТЕРИЙ: БД работает\n\n"
                "ПОДЦЕЛЬ: Написать API\n"
                "КРИТЕРИЙ: Эндпоинты отвечают\n\n"
                "ПОДЦЕЛЬ: Написать тесты\n"
                "КРИТЕРИЙ: Покрытие >80%"
            )
        )
        gm = GoalManager(cognitive_core=core)
        parent = gm.add("Build system")
        subs = gm.decompose(parent.goal_id)
        self.assertEqual(len(subs), 3)
        self.assertIn("Настроить базу данных", subs[0].description)
        self.assertEqual(subs[0].parent_id, parent.goal_id)

    def test_decompose_filters_garbage(self):
        core = SimpleNamespace(
            reasoning=lambda x: (
                "ПОДЦЕЛЬ: | таблица | ерунда |\n"
                "ПОДЦЕЛЬ: short\n"
                "ПОДЦЕЛЬ: критерий успеха не подцель\n"
                "ПОДЦЕЛЬ: Нормальная подцель для работы\n"
                "КРИТЕРИЙ: Работает"
            )
        )
        gm = GoalManager(cognitive_core=core)
        parent = gm.add("Goal")
        subs = gm.decompose(parent.goal_id)
        descs = [s.description for s in subs]
        self.assertTrue(any("Нормальная" in d for d in descs))
        # Table rows, short, and garbage should be filtered
        self.assertTrue(len(subs) <= 2)

    def test_decompose_empty_after_normalize(self):
        # "--------" is 8+ chars so passes _is_garbage, but normalizes to ""
        core = SimpleNamespace(
            reasoning=lambda x: "ПОДЦЕЛЬ: --------\nКРИТЕРИЙ: none"
        )
        gm = GoalManager(cognitive_core=core)
        parent = gm.add("Goal")
        subs = gm.decompose(parent.goal_id)
        self.assertEqual(len(subs), 0)


class ConvenienceWrapperTests(unittest.TestCase):
    """Покрытие add_goal, get_next_goal, prioritize."""

    def test_add_goal_default_priority(self):
        gm = GoalManager()
        g = gm.add_goal("Task")
        self.assertEqual(g.priority, GoalPriority.MEDIUM)

    def test_add_goal_with_priority(self):
        gm = GoalManager()
        g = gm.add_goal("Task", priority=GoalPriority.HIGH)
        self.assertEqual(g.priority, GoalPriority.HIGH)

    def test_get_next_goal(self):
        gm = GoalManager()
        gm.add("A")
        self.assertIsNotNone(gm.get_next_goal())

    def test_get_next_goal_empty(self):
        gm = GoalManager()
        self.assertIsNone(gm.get_next_goal())

    def test_prioritize_keywords(self):
        gm = GoalManager()
        goals = [
            {"goal": "обычная задача"},
            {"goal": "срочно исправить баг"},
            {"goal": "важно проверить"},
            {"goal": "простое дело"},
        ]
        result = gm.prioritize(goals)
        self.assertEqual(len(result), 4)
        # All should have priority_score
        for r in result:
            self.assertIn("priority_score", r)
        # "срочно" should be first or near top
        self.assertIn("срочно", result[0]["goal"])

    def test_prioritize_explicit_values(self):
        gm = GoalManager()
        goals = [
            {"goal": "A", "urgency": 0.1, "importance": 0.1, "effort": 0.1},
            {"goal": "B", "urgency": 0.9, "importance": 0.9, "effort": 0.1},
        ]
        result = gm.prioritize(goals)
        self.assertEqual(result[0]["goal"], "B")

    def test_prioritize_empty(self):
        gm = GoalManager()
        self.assertEqual(gm.prioritize([]), [])


class AuditGoalsTests(unittest.TestCase):
    """Покрытие audit_goals — все категории."""

    def test_audit_empty(self):
        gm = GoalManager()
        result = gm.audit_goals()
        self.assertEqual(result["total"], 0)

    def test_audit_completed(self):
        gm = GoalManager()
        g = gm.add("Task")
        gm.complete(g.goal_id)
        result = gm.audit_goals()
        self.assertEqual(result["completed"], 1)

    def test_audit_progressed(self):
        gm = GoalManager()
        g = gm.add("Task")
        gm.update_progress(g.goal_id, 0.5)
        gm.fail(g.goal_id)
        result = gm.audit_goals()
        self.assertEqual(result["progressed"], 1)

    def test_audit_attempted(self):
        gm = GoalManager()
        g = gm.add("Task")
        gm.activate(g.goal_id)
        g.status = GoalStatus.FAILED  # failed after starting, no progress
        result = gm.audit_goals()
        self.assertEqual(result["attempted"], 1)

    def test_audit_skipped_terminal(self):
        gm = GoalManager()
        g = gm.add("Task")
        gm.cancel(g.goal_id)
        result = gm.audit_goals()
        self.assertEqual(result["skipped"], 1)
        self.assertIn(g.goal_id, result["skipped_ids"])

    def test_audit_skipped_old_pending(self):
        gm = GoalManager()
        g = gm.add("Task")
        g.created_at = time.time() - 100000  # >24h ago
        result = gm.audit_goals()
        self.assertEqual(result["skipped"], 1)

    def test_audit_active_skipped(self):
        gm = GoalManager()
        g = gm.add("Task")
        gm.activate(g.goal_id)
        result = gm.audit_goals()
        # Active goals are not evaluated
        self.assertEqual(result["completed"], 0)
        self.assertEqual(result["attempted"], 0)


class LoggingTests(unittest.TestCase):
    """Покрытие _log с и без monitoring."""

    def test_log_with_monitoring(self):
        logs = []
        mon = SimpleNamespace(info=lambda msg, source=None: logs.append(msg))
        gm = GoalManager(monitoring=mon)
        gm.add("Goal")
        self.assertTrue(any("добавлена" in l for l in logs))

    def test_log_without_monitoring(self, capsys=None):
        gm = GoalManager()
        gm.add("Goal")  # prints to stdout, no crash

    def test_log_custom_level(self):
        logs = {"debug": []}
        mon = SimpleNamespace(
            info=lambda msg, source=None: None,
            debug=lambda msg, source=None: logs["debug"].append(msg),
        )
        gm = GoalManager(monitoring=mon)
        g1 = gm.add("Duplicate test")
        g2 = gm.add("Duplicate test")  # triggers debug log
        self.assertTrue(any("переиспользована" in l for l in logs["debug"]))


class RecalcParentProgressTests(unittest.TestCase):
    """Покрытие _recalc_parent_progress edge cases."""

    def test_nonexistent_parent(self):
        gm = GoalManager()
        gm._recalc_parent_progress("nope")  # no crash

    def test_parent_no_subgoals(self):
        gm = GoalManager()
        g = gm.add("Parent")
        gm._recalc_parent_progress(g.goal_id)  # no crash, no change

    def test_partial_subgoal_completion(self):
        gm = GoalManager()
        parent = gm.add("Parent")
        c1 = gm.add("C1", parent_id=parent.goal_id)
        c2 = gm.add("C2", parent_id=parent.goal_id)
        c3 = gm.add("C3", parent_id=parent.goal_id)
        gm.update_progress(c1.goal_id, 0.6)
        gm._recalc_parent_progress(parent.goal_id)
        self.assertAlmostEqual(parent.progress, 0.2, places=1)


class GoalStatusEnumTests(unittest.TestCase):
    """Enum value checks."""

    def test_statuses(self):
        self.assertEqual(GoalStatus.PENDING.value, "pending")
        self.assertEqual(GoalStatus.ACTIVE.value, "active")
        self.assertEqual(GoalStatus.PAUSED.value, "paused")
        self.assertEqual(GoalStatus.COMPLETED.value, "completed")
        self.assertEqual(GoalStatus.FAILED.value, "failed")
        self.assertEqual(GoalStatus.CANCELLED.value, "cancelled")

    def test_priorities(self):
        self.assertEqual(GoalPriority.CRITICAL.value, 1)
        self.assertEqual(GoalPriority.DEFERRED.value, 5)


if __name__ == "__main__":
    unittest.main()
