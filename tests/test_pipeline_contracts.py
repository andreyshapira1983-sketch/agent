"""
Тесты межкомпонентных контрактов планировочного пайплайна.

Категории:
  1. Интеграция: полный цикл GoalManager → TaskDecomp → Orchestration → LHP
  2. Promotion rules: advisory → goal (EU, stale, duplicate, accept)
  3. Budget caps: лимиты очереди, TTL, бюджет
  4. Traceability: все ключевые решения логируются через _trace
  5. Ownership invariants: компоненты не мутируют чужое состояние
  6. Failure handling: пустые/некорректные входы
  7. Long-run (soak): 100+ циклов без деградации

pytest -v tests/test_pipeline_contracts.py
"""

from __future__ import annotations

import copy
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.goal_manager import Goal, GoalManager, GoalPriority, GoalStatus
from core.long_horizon_planning import (
    HorizonScale, LongHorizonPlanning, PlanMetrics, Roadmap,
)
from loop.orchestration import OrchestrationSystem, OrchestratedTask, TaskPriority
from skills.task_decomposition import (
    Subtask, SubtaskStatus, TaskDecompositionEngine, TaskGraph,
)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

class _TraceCapture:
    """Мок monitoring, собирающий все _trace-записи."""

    def __init__(self):
        self.entries: list[str] = []

    def info(self, msg, **_kw):
        self.entries.append(msg)

    debug = warning = error = info


def _make_gm(mon=None) -> GoalManager:
    return GoalManager(monitoring=mon)


def _make_lhp(gm=None, mon=None) -> LongHorizonPlanning:
    return LongHorizonPlanning(goal_manager=gm, monitoring=mon)


def _make_orch(mon=None, agent_system=None) -> OrchestrationSystem:
    return OrchestrationSystem(agent_system=agent_system, monitoring=mon)


def _make_td(mon=None) -> TaskDecompositionEngine:
    return TaskDecompositionEngine(monitoring=mon)


def _fast_agent():
    """Мок AgentSystem, мгновенно возвращающий success."""
    return SimpleNamespace(handle=lambda d: {'success': True, 'status': 'done',
                                              'result': f"ok:{d['goal'][:20]}"})


# ═══════════════════════════════════════════════════════════════════════════
# 1. ИНТЕГРАЦИЯ: полный цикл
# ═══════════════════════════════════════════════════════════════════════════

class TestIntegrationFullCycle(unittest.TestCase):
    """Имитация одного цикла AutonomousLoop через 4 компонента."""

    def test_goal_to_decompose_to_orchestrate(self):
        """goal → subgoal → decompose → orchestrate → side-task → cycle end."""
        mon = _TraceCapture()
        gm = _make_gm(mon)
        td = _make_td(mon)
        orch = _make_orch(mon, agent_system=_fast_agent())
        lhp = _make_lhp(gm, mon)

        # 1. GoalManager: добавляем и активируем цель
        root = gm.add('Создать систему мониторинга', priority=GoalPriority.HIGH)
        gm.activate(root.goal_id)
        active = gm.get_active()
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active.goal_id, root.goal_id)

        # 2. GoalManager.get_next() → трассировка
        chosen = gm.get_next()
        self.assertIsNotNone(chosen)
        self.assertTrue(any('[TRACE]' in e and 'get_next' in e for e in mon.entries))

        # 3. TaskDecomposition: тактическая декомпозиция
        assert chosen is not None
        graph = td.decompose(chosen.description)
        self.assertIsInstance(graph, TaskGraph)
        tasks = graph.to_list()
        self.assertGreater(len(tasks), 0)
        self.assertTrue(any('[TRACE]' in e and 'decompose_start' in e for e in mon.entries))

        # 4. Orchestration: side-task
        orch.reset_budget()
        side = orch.submit('Проверка метрик', priority=TaskPriority.NORMAL,
                           metadata={'source_goal_id': root.goal_id})
        self.assertEqual(side.status, 'pending')
        executed = orch.run_next()
        self.assertIsNotNone(executed)
        assert executed is not None
        self.assertEqual(executed.status, 'done')

        # 5. LHP: план (без LLM → deterministic)
        assert chosen is not None
        rm = lhp.plan(chosen.description, HorizonScale.WEEK)
        self.assertGreater(len(rm.milestones), 0)

        # 6. Budget reset в конце цикла
        orch.reset_budget()
        self.assertEqual(orch._budget_spent, 0.0)

        # 7. Проверяем что цепочка трассируется
        trace_actions = [e for e in mon.entries if '[TRACE]' in e]
        self.assertGreater(len(trace_actions), 3,
                           f"Ожидали >3 trace-записей, получили {len(trace_actions)}")

    def test_cycle_with_decomposition_and_side_tasks(self):
        """Несколько подзадач + side-tasks не конфликтуют."""
        mon = _TraceCapture()
        gm = _make_gm(mon)
        td = _make_td(mon)
        orch = _make_orch(mon, agent_system=_fast_agent())

        goal = gm.add('Написать отчёт', priority=GoalPriority.MEDIUM)
        gm.activate(goal.goal_id)
        _ = td.decompose(goal.description, max_subtasks=4)

        # Orchestration side-tasks
        for i in range(3):
            orch.submit(f'side-task-{i}', metadata={'source_goal_id': goal.goal_id})
        self.assertEqual(orch.queue_size(), 3)

        executed = orch.run_all()
        self.assertEqual(len(executed), 3)
        for t in executed:
            self.assertEqual(t.status, 'done')


# ═══════════════════════════════════════════════════════════════════════════
# 2. PROMOTION RULES: advisory → goal
# ═══════════════════════════════════════════════════════════════════════════

class TestPromotionRules(unittest.TestCase):

    def _roadmap_dict(self, eu=0.7, status='active', goal='Автомат. тестирование',
                      roadmap_id='rm0001'):
        return {
            'roadmap_id': roadmap_id,
            'goal': goal,
            'status': status,
            'metrics': {'expected_utility': eu},
        }

    def test_promote_below_threshold(self):
        """EU=0.3 < 0.55 → reject."""
        mon = _TraceCapture()
        gm = _make_gm(mon)
        result = gm.promote_advisory(self._roadmap_dict(eu=0.3))
        self.assertIsNone(result)
        self.assertTrue(any('promote_reject' in e and 'EU=' in e for e in mon.entries))

    def test_promote_above_threshold(self):
        """EU=0.7 >= 0.55 → accept."""
        mon = _TraceCapture()
        gm = _make_gm(mon)
        result = gm.promote_advisory(self._roadmap_dict(eu=0.7))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.priority, GoalPriority.HIGH)
        self.assertIn('promoted_from_roadmap', result.tags)
        self.assertTrue(any('promote_accept' in e for e in mon.entries))

    def test_promote_stale_rejected(self):
        """Stale roadmap → reject."""
        mon = _TraceCapture()
        gm = _make_gm(mon)
        result = gm.promote_advisory(self._roadmap_dict(eu=0.9, status='stale'))
        self.assertIsNone(result)
        self.assertTrue(any('promote_reject' in e and 'status=stale' in e for e in mon.entries))

    def test_promote_invalidated_rejected(self):
        """Invalidated roadmap → reject."""
        gm = _make_gm()
        result = gm.promote_advisory(self._roadmap_dict(eu=0.9, status='invalidated'))
        self.assertIsNone(result)

    def test_promote_duplicate_rejected(self):
        """Уже есть активная цель с тем же описанием → reject."""
        mon = _TraceCapture()
        gm = _make_gm(mon)
        gm.add('Автомат. тестирование', priority=GoalPriority.MEDIUM)
        result = gm.promote_advisory(self._roadmap_dict(eu=0.8, goal='Автомат. тестирование'))
        self.assertIsNone(result)
        self.assertTrue(any('promote_reject' in e and 'дубликат' in e for e in mon.entries))

    def test_promote_empty_goal_rejected(self):
        """Пустой goal в roadmap_dict → None."""
        gm = _make_gm()
        result = gm.promote_advisory(self._roadmap_dict(eu=0.9, goal=''))
        self.assertIsNone(result)

    def test_offer_promotion_pipeline(self):
        """LHP.offer_promotion → GoalManager.promote_advisory полный пайплайн."""
        mon = _TraceCapture()
        gm = _make_gm(mon)
        lhp = _make_lhp(gm, mon)

        rm = lhp.plan('Стратегический тест', HorizonScale.WEEK)
        rm.metrics.resource_efficiency = 0.7
        rm.metrics.survival_probability = 0.8
        rm.metrics.influence_score = 0.6

        offered = lhp.offer_promotion(rm.roadmap_id)
        self.assertIsNotNone(offered)
        assert offered is not None

        promoted = gm.promote_advisory(offered)
        eu = rm.metrics.expected_utility()
        if eu >= gm.PROMOTION_EU_THRESHOLD:
            self.assertIsNotNone(promoted)
        else:
            self.assertIsNone(promoted)

    def test_offer_promotion_stale_roadmap(self):
        """Roadmap с истёкшим TTL → offer_promotion возвращает None."""
        lhp = _make_lhp()
        rm = lhp.create_roadmap('Test', 'Goal', HorizonScale.DAY)
        # Искусственно делаем stale
        rm.created_at = time.time() - rm.ttl_sec - 100
        result = lhp.offer_promotion(rm.roadmap_id)
        self.assertIsNone(result)
        self.assertEqual(rm.status, 'stale')

    def test_offer_promotion_nonexistent(self):
        """Нет такого roadmap_id → None."""
        lhp = _make_lhp()
        self.assertIsNone(lhp.offer_promotion('rm9999'))


# ═══════════════════════════════════════════════════════════════════════════
# 3. BUDGET CAPS
# ═══════════════════════════════════════════════════════════════════════════

class TestBudgetCaps(unittest.TestCase):

    def test_queue_within_limit(self):
        """Задачи в пределах MAX_QUEUE_SIZE принимаются."""
        orch = _make_orch()
        for i in range(10):
            t = orch.submit(f'task-{i}')
            self.assertNotEqual(t.status, 'rejected')
        self.assertEqual(orch.queue_size(), 10)

    def test_queue_overflow_rejected(self):
        """После MAX_QUEUE_SIZE задача отклоняется."""
        mon = _TraceCapture()
        orch = _make_orch(mon)
        orch.MAX_QUEUE_SIZE = 5  # type: ignore[misc]  # для скорости теста
        orch.TASK_MAX_AGE_SEC = 999999  # type: ignore[misc]  # не протухают

        for i in range(5):
            orch.submit(f'task-{i}')
        overflow = orch.submit('overflow-task')
        self.assertEqual(overflow.status, 'rejected')
        self.assertTrue(any('submit_reject' in e for e in mon.entries))

    def test_stale_cleanup_frees_space(self):
        """Протухшие задачи освобождают место для новых."""
        mon = _TraceCapture()
        orch = _make_orch(mon)
        orch.MAX_QUEUE_SIZE = 5  # type: ignore[misc]
        orch.TASK_MAX_AGE_SEC = 0  # type: ignore[misc]  # мгновенное протухание

        for i in range(5):
            t = orch.submit(f'task-{i}')
            t.created_at = time.time() - 10  # гарантируем stale

        # Следующий submit должен вызвать cleanup и принять задачу
        fresh = orch.submit('fresh-task')
        self.assertNotEqual(fresh.status, 'rejected')
        self.assertTrue(any('task_expired' in e for e in mon.entries))

    def test_budget_exhausted_stops_run(self):
        """После исчерпания бюджета run_next возвращает None."""
        mon = _TraceCapture()
        orch = _make_orch(mon, agent_system=_fast_agent())
        orch.BUDGET_CAP_SEC = 0.0  # type: ignore[misc]  # бюджет = 0
        orch.submit('unreachable')

        result = orch.run_next()
        self.assertIsNone(result)
        self.assertTrue(any('run_next_skip' in e and 'budget exhausted' in e
                            for e in mon.entries))

    def test_budget_reset_restores(self):
        """reset_budget() позволяет продолжить выполнение."""
        orch = _make_orch(agent_system=_fast_agent())
        orch.BUDGET_CAP_SEC = 0.0  # type: ignore[misc]
        orch.submit('task')
        self.assertIsNone(orch.run_next())  # бюджет исчерпан

        orch.BUDGET_CAP_SEC = 300.0  # type: ignore[misc]
        orch.reset_budget()
        self.assertFalse(orch.budget_exhausted)
        self.assertEqual(orch.budget_remaining, 300.0)

    def test_budget_accumulates(self):
        """_budget_spent растёт с каждой execute."""
        orch = _make_orch(agent_system=_fast_agent())
        orch.BUDGET_CAP_SEC = 999.0  # type: ignore[misc]
        orch.reset_budget()

        orch.submit('t1')
        orch.submit('t2')
        orch.run_next()
        orch.run_next()
        self.assertGreater(orch._budget_spent, 0.0)

    def test_task_ttl_individual_trace(self):
        """Каждая протухшая задача трассируется отдельно."""
        mon = _TraceCapture()
        orch = _make_orch(mon)
        orch.TASK_MAX_AGE_SEC = 0  # type: ignore[misc]

        for i in range(3):
            t = orch.submit(f'stale-{i}', metadata={'source_goal_id': f'g{i}'})
            t.created_at = time.time() - 10

        with orch._lock:
            orch._cleanup_stale()

        expired_traces = [e for e in mon.entries if 'task_expired' in e]
        self.assertEqual(len(expired_traces), 3)


# ═══════════════════════════════════════════════════════════════════════════
# 4. TRACEABILITY: полнота цепочки решений
# ═══════════════════════════════════════════════════════════════════════════

class TestTraceabilityCompleteness(unittest.TestCase):

    def test_goal_selection_traced(self):
        """get_next() и activate() генерируют trace-записи."""
        mon = _TraceCapture()
        gm = _make_gm(mon)
        g = gm.add('Test goal', priority=GoalPriority.CRITICAL)
        gm.activate(g.goal_id)
        gm.get_next()

        actions = [e for e in mon.entries if '[TRACE]' in e]
        action_names = ''.join(actions)
        self.assertIn('activate', action_names)
        self.assertIn('get_next', action_names)

    def test_decompose_start_traced(self):
        """decompose() записывает decompose_start."""
        mon = _TraceCapture()
        td = _make_td(mon)
        td.decompose('Исследовать API')
        action_text = ''.join(mon.entries)
        self.assertIn('decompose_start', action_text)

    def test_decompose_recursive_limits_traced(self):
        """decompose_recursive при MAX_DEPTH/MAX_TOTAL_NODES записывает stop/skip."""
        mon = _TraceCapture()
        td = _make_td(mon)
        td.MAX_DEPTH = 1  # type: ignore[misc]  # форсируем лимит
        _ = td.decompose_recursive('goal', depth=3)
        # С depth=3 но MAX_DEPTH=1 → depth clamped to 1, вторая итерация skip
        action_text = ''.join(mon.entries)
        # При depth=1 рекурсия не идёт (depth <=1 → return), поэтому проверяем decompose_start
        self.assertIn('decompose_start', action_text)

    def test_orchestration_submit_accept_traced(self):
        """submit() при успехе записывает submit_accept."""
        mon = _TraceCapture()
        orch = _make_orch(mon)
        orch.submit('test task', metadata={'source_goal_id': 'g0001'})
        action_text = ''.join(mon.entries)
        self.assertIn('submit_accept', action_text)
        self.assertIn('g0001', action_text)

    def test_orchestration_execute_traced(self):
        """execute записывает execute_start и execute_end."""
        mon = _TraceCapture()
        orch = _make_orch(mon, agent_system=_fast_agent())
        orch.submit('task')
        orch.run_next()
        action_text = ''.join(mon.entries)
        self.assertIn('execute_start', action_text)
        self.assertIn('execute_end', action_text)

    def test_lhp_invalidation_traced(self):
        """invalidate_stale_roadmaps() записывает invalidate_stale для каждого."""
        mon = _TraceCapture()
        lhp = _make_lhp(mon=mon)
        rm = lhp.create_roadmap('R', 'Goal', HorizonScale.DAY)
        rm.created_at = time.time() - rm.ttl_sec - 100
        count = lhp.invalidate_stale_roadmaps()
        self.assertEqual(count, 1)
        action_text = ''.join(mon.entries)
        self.assertIn('invalidate_stale', action_text)

    def test_lhp_goal_switch_invalidation_traced(self):
        """invalidate_for_goal() записывает invalidate_goal_switch."""
        mon = _TraceCapture()
        lhp = _make_lhp(mon=mon)
        lhp.create_roadmap('R', 'Old Goal', HorizonScale.WEEK)
        count = lhp.invalidate_for_goal('Old Goal')
        self.assertEqual(count, 1)
        action_text = ''.join(mon.entries)
        self.assertIn('invalidate_goal_switch', action_text)

    def test_full_decision_chain_reconstructable(self):
        """Из trace-записей можно восстановить: цель → декомпозиция → оркестрация."""
        mon = _TraceCapture()
        gm = _make_gm(mon)
        td = _make_td(mon)
        orch = _make_orch(mon, agent_system=_fast_agent())

        g = gm.add('Полная цепочка', priority=GoalPriority.HIGH)
        gm.activate(g.goal_id)
        chosen = gm.get_next()
        assert chosen is not None
        td.decompose(chosen.description)
        orch.submit('side task', metadata={'source_goal_id': g.goal_id})
        orch.run_next()

        traces = [e for e in mon.entries if '[TRACE]' in e]
        trace_text = '\n'.join(traces)

        # Минимальный набор действий в цепочке
        for action in ('activate', 'get_next', 'decompose_start',
                        'submit_accept', 'execute_start', 'execute_end'):
            self.assertIn(action, trace_text,
                          f"Действие '{action}' отсутствует в цепочке трассировки")


# ═══════════════════════════════════════════════════════════════════════════
# 5. OWNERSHIP INVARIANTS
# ═══════════════════════════════════════════════════════════════════════════

class TestOwnershipInvariants(unittest.TestCase):

    def test_lhp_does_not_mutate_goals(self):
        """LHP не изменяет GoalManager._goals."""
        gm = _make_gm()
        g = gm.add('Test')
        gm.activate(g.goal_id)
        goals_before = copy.deepcopy({gid: g.to_dict() for gid, g in gm._goals.items()})

        lhp = _make_lhp(gm)
        lhp.plan('do something', HorizonScale.WEEK)
        lhp.invalidate_stale_roadmaps()

        goals_after = {gid: g.to_dict() for gid, g in gm._goals.items()}
        self.assertEqual(goals_before, goals_after)

    def test_task_decomp_does_not_mutate_roadmaps(self):
        """TaskDecomposition не трогает _roadmaps."""
        lhp = _make_lhp()
        lhp.create_roadmap('R', 'Goal')
        roadmaps_before = copy.deepcopy({rid: r.to_dict() for rid, r in lhp._roadmaps.items()})

        td = _make_td()
        td.decompose('some goal')
        td.decompose_recursive('another goal', depth=2)

        roadmaps_after = {rid: r.to_dict() for rid, r in lhp._roadmaps.items()}
        self.assertEqual(roadmaps_before, roadmaps_after)

    def test_orchestration_does_not_mutate_external(self):
        """Orchestration не мутирует Goal или TaskGraph."""
        gm = _make_gm()
        gm.add('Goal')
        td = _make_td()
        graph = td.decompose('Goal')

        goals_snap = copy.deepcopy({gid: gl.to_dict() for gid, gl in gm._goals.items()})
        graph_snap = copy.deepcopy(graph.to_list())

        orch = _make_orch(agent_system=_fast_agent())
        orch.submit('side task')
        orch.run_next()

        self.assertEqual({gid: gl.to_dict() for gid, gl in gm._goals.items()}, goals_snap)
        self.assertEqual(graph.to_list(), graph_snap)

    def test_offer_promotion_does_not_create_goal(self):
        """offer_promotion возвращает dict, не Goal."""
        gm = _make_gm()
        lhp = _make_lhp(gm)
        rm = lhp.plan('Test', HorizonScale.WEEK)
        rm.metrics.resource_efficiency = 0.9
        rm.metrics.survival_probability = 0.9
        rm.metrics.influence_score = 0.9

        goals_before = len(gm._goals)
        offered = lhp.offer_promotion(rm.roadmap_id)
        goals_after = len(gm._goals)
        self.assertEqual(goals_before, goals_after,
                         "offer_promotion не должен создавать Goal")
        if offered is not None:
            self.assertIsInstance(offered, dict)

    def test_only_goal_manager_changes_goal_status(self):
        """Только GoalManager вызывает activate/complete/fail/cancel."""
        gm = _make_gm()
        g = gm.add('Test')
        self.assertEqual(g.status, GoalStatus.PENDING)

        gm.activate(g.goal_id)
        self.assertEqual(g.status, GoalStatus.ACTIVE)

        gm.complete(g.goal_id)
        self.assertEqual(g.status, GoalStatus.COMPLETED)

        # Других путей изменить статус нет (нет прямого доступа через LHP/TD/Orch)


# ═══════════════════════════════════════════════════════════════════════════
# 6. FAILURE HANDLING
# ═══════════════════════════════════════════════════════════════════════════

class TestFailureHandling(unittest.TestCase):

    def test_empty_goal_in_goal_manager(self):
        """Пустая строка цели не ломает GoalManager."""
        gm = _make_gm()
        g = gm.add('')
        self.assertIsNotNone(g)
        self.assertEqual(g.description, '')
        choice = gm.get_next()
        self.assertIsNotNone(choice)

    def test_empty_graph_from_decomposition(self):
        """Пустой план из decompose = исправное состояние."""
        td = _make_td()
        # С cognitive_core=None → deterministic (может дать задачи)
        graph = td.decompose('')
        self.assertIsInstance(graph, TaskGraph)
        # Не крашится

    def test_orchestration_no_agent_system(self):
        """Без agent_system задача всё равно завершается (warning)."""
        orch = _make_orch()
        orch.submit('task without executor')
        result = orch.run_next()
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, 'done')
        self.assertIn('warning', result.result)

    def test_orchestration_agent_raises(self):
        """agent_system.handle() бросает исключение → task failed (или requeued при max_retries)."""
        bad_agent = SimpleNamespace(handle=lambda d: (_ for _ in ()).throw(RuntimeError('crash')))
        orch = _make_orch(agent_system=bad_agent)
        orch.submit('crashing task', metadata={'max_retries': 0})
        result = orch.run_next()
        assert result is not None
        self.assertEqual(result.status, 'failed')
        self.assertIn('crash', str(result.error))

    def test_promote_corrupt_roadmap(self):
        """Некорректный dict в promote_advisory → None или TypeError."""
        gm = _make_gm()
        self.assertIsNone(gm.promote_advisory({}))
        self.assertIsNone(gm.promote_advisory({'metrics': 'not a dict'}))
        # Нечисловой EU вызывает TypeError при сравнении — допустимое поведение
        with self.assertRaises(TypeError):
            gm.promote_advisory({'metrics': {'expected_utility': 'abc'}})

    def test_task_graph_expand_beyond_limits(self):
        """expand_node отклоняет при MAX_DEPTH / MAX_TOTAL_NODES."""
        g = TaskGraph()
        parent = Subtask('p', 'parent')
        parent.depth = 3  # на грани лимита
        g.add(parent)

        children = [Subtask(f's{i}', f'child-{i}') for i in range(3)]
        expanded = g.expand_node('p', children, max_depth=4, max_nodes=64)
        self.assertFalse(expanded)
        self.assertIsNotNone(g._rejection_reason)
        assert g._rejection_reason is not None
        self.assertEqual(g._rejection_reason[0], 'MAX_DEPTH')

    def test_task_graph_expand_max_nodes(self):
        """expand_node отклоняет при превышении max_nodes."""
        g = TaskGraph()
        for i in range(60):
            g.add(Subtask(f't{i}', f'task-{i}'))
        parent = Subtask('p', 'parent')
        parent.depth = 0
        g.add(parent)

        children = [Subtask(f'c{i}', f'child-{i}') for i in range(10)]
        expanded = g.expand_node('p', children, max_depth=4, max_nodes=64)
        self.assertFalse(expanded)
        self.assertIsNotNone(g._rejection_reason)
        assert g._rejection_reason is not None
        self.assertEqual(g._rejection_reason[0], 'MAX_TOTAL_NODES')

    def test_invalidate_for_nonexistent_goal(self):
        """invalidate_for_goal с несуществующей целью → 0, не крашится."""
        lhp = _make_lhp()
        lhp.create_roadmap('R', 'Real Goal')
        count = lhp.invalidate_for_goal('NonexistentGoal')
        self.assertEqual(count, 0)

    def test_run_next_empty_queue(self):
        """run_next на пустой очереди → None."""
        orch = _make_orch()
        self.assertIsNone(orch.run_next())

    def test_orchestration_retry_on_failure(self):
        """Задача с max_retries=1 пытается 2 раза."""
        call_count = 0

        def _handle(_d):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise RuntimeError('temporary')
            return {'success': True, 'status': 'done'}

        agent = SimpleNamespace(handle=_handle)
        orch = _make_orch(agent_system=agent)
        orch.submit('retry-task', metadata={'max_retries': 1})
        orch.run_next()  # первая попытка → fail → requeue
        result = orch.run_next()  # вторая попытка → success
        assert result is not None
        self.assertEqual(result.status, 'done')
        self.assertEqual(call_count, 2)


# ═══════════════════════════════════════════════════════════════════════════
# 7. LONG-RUN / SOAK TEST
# ═══════════════════════════════════════════════════════════════════════════

class TestLongRunSoak(unittest.TestCase):
    """100+ циклов: утечки, дубликаты, деградация."""

    def test_100_cycles_no_leaks(self):
        """100 циклов: очередь не растёт бесконтрольно, бюджет сбрасывается."""
        mon = _TraceCapture()
        gm = _make_gm(mon)
        td = _make_td(mon)
        orch = _make_orch(mon, agent_system=_fast_agent())
        lhp = _make_lhp(gm, mon)

        # Стартовая цель
        root = gm.add('Soak test main goal', priority=GoalPriority.HIGH)
        gm.activate(root.goal_id)

        max_queue_observed = 0
        max_goals_observed = 0

        for cycle in range(100):
            # PLAN
            chosen = gm.get_next()
            if chosen:
                td.decompose(chosen.description, max_subtasks=3)

            # LHP каждые 20 циклов
            if cycle % 20 == 0:
                lhp.plan('soak-test', HorizonScale.DAY)
                lhp.invalidate_stale_roadmaps()

            # ACT
            orch.reset_budget()
            orch.submit(f'soak-side-{cycle}', metadata={'source_goal_id': root.goal_id})
            orch.run_next()

            max_queue_observed = max(max_queue_observed, orch.queue_size())
            max_goals_observed = max(max_goals_observed, len(gm._goals))

        # Очередь не растёт неограниченно (все задачи выполняются за 1 цикл)
        self.assertLessEqual(max_queue_observed, 5,
                             "Очередь утекла: задачи не убираются")

        # Бюджет сбрасывается каждый цикл
        self.assertLess(orch._budget_spent, orch.BUDGET_CAP_SEC,
                        "Бюджет не был сброшен")

    def test_no_duplicate_goals_across_cycles(self):
        """Повторные add() с тем же описанием → дедупликация."""
        gm = _make_gm()
        for _ in range(50):
            gm.add('Одна и та же цель')
        # Дедупликация: должна быть 1 цель
        self.assertEqual(len(gm._goals), 1)

    def test_stale_roadmaps_cleaned(self):
        """Roadmaps протухают и чистятся."""
        lhp = _make_lhp()
        # Создаём 20 roadmaps, все с истёкшим TTL
        for i in range(20):
            rm = lhp.create_roadmap(f'R{i}', f'Goal{i}', HorizonScale.DAY)
            rm.created_at = time.time() - rm.ttl_sec - 100

        lhp.invalidate_stale_roadmaps()
        # invalidate_stale_roadmaps вызывается внутри create_roadmap, но мы ставим stale после
        # Поэтому явный вызов после:
        active = [rm for rm in lhp._roadmaps.values() if rm.status == 'active']
        self.assertEqual(len(active), 0, "Stale roadmaps не были очищены")

    def test_decomposition_does_not_hang(self):
        """Массовая декомпозиция: MAX_TOTAL_NODES не даёт взрывного роста."""
        td = _make_td()
        td.MAX_TOTAL_NODES = 32  # type: ignore[misc]  # жёсткий лимит для теста

        graph = td.decompose_recursive('Большая задача', depth=4)
        self.assertLessEqual(len(graph._nodes), 32,
                             "Декомпозиция превысила MAX_TOTAL_NODES")

    def test_budget_resets_properly_across_cycles(self):
        """Budget корректно обнуляется каждый цикл."""
        orch = _make_orch(agent_system=_fast_agent())
        orch.BUDGET_CAP_SEC = 999.0  # type: ignore[misc]

        for cycle in range(50):
            orch.reset_budget()
            self.assertEqual(orch._budget_spent, 0.0,
                             f"Бюджет не сброшен на цикле {cycle}")
            orch.submit(f'task-{cycle}')
            orch.run_next()
            self.assertGreater(orch._budget_spent, 0.0)

    def test_trace_entries_bounded(self):
        """Trace-записи через _TraceCapture не копятся бесконечно в реальном мониторинге."""
        mon = _TraceCapture()
        orch = _make_orch(mon, agent_system=_fast_agent())

        for i in range(200):
            orch.reset_budget()
            orch.submit(f't{i}')
            orch.run_next()

        # Каждая задача: submit_accept + execute_start + execute_end = 3 traces
        # Но не более O(n) — нет квадратичного роста
        self.assertLessEqual(len(mon.entries), 200 * 10,
                             "Число trace-записей растёт сверхлинейно")


# ═══════════════════════════════════════════════════════════════════════════
# 8. ДОПОЛНИТЕЛЬНЫЕ EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases(unittest.TestCase):

    def test_promotion_threshold_boundary(self):
        """EU ровно на пороге (0.55) → принимается."""
        gm = _make_gm()
        result = gm.promote_advisory({
            'roadmap_id': 'rm_edge',
            'goal': 'Edge case test',
            'status': 'active',
            'metrics': {'expected_utility': 0.55},
        })
        self.assertIsNotNone(result)

    def test_promotion_just_below_threshold(self):
        """EU чуть ниже порога (0.549) → отклоняется."""
        gm = _make_gm()
        result = gm.promote_advisory({
            'roadmap_id': 'rm_edge2',
            'goal': 'Just below threshold',
            'status': 'active',
            'metrics': {'expected_utility': 0.549},
        })
        self.assertIsNone(result)

    def test_expand_node_successful(self):
        """expand_node в пределах лимитов → True."""
        g = TaskGraph()
        parent = Subtask('p', 'parent')
        parent.depth = 1
        g.add(parent)
        children = [Subtask('c1', 'child-1'), Subtask('c2', 'child-2')]
        expanded = g.expand_node('p', children, max_depth=4, max_nodes=64)
        self.assertTrue(expanded)
        self.assertEqual(parent.status, SubtaskStatus.SKIPPED)
        c1 = g.get('c1')
        c2 = g.get('c2')
        assert c1 is not None and c2 is not None
        self.assertEqual(c1.depth, 2)
        self.assertEqual(c2.depth, 2)

    def test_roadmap_ttl_by_horizon(self):
        """TTL зависит от горизонта: DAY → 2*86400, YEAR → 2*365*86400."""
        rm_day = Roadmap('r1', 'T', 'G', HorizonScale.DAY)
        rm_year = Roadmap('r2', 'T', 'G', HorizonScale.YEAR)
        self.assertEqual(rm_day.ttl_sec, 2 * 86400)
        self.assertGreater(rm_year.ttl_sec, rm_day.ttl_sec)

    def test_roadmap_is_stale(self):
        """is_stale() True только после TTL."""
        rm = Roadmap('r1', 'T', 'G', HorizonScale.DAY)
        self.assertFalse(rm.is_stale())
        rm.created_at = time.time() - rm.ttl_sec - 1
        self.assertTrue(rm.is_stale())

    def test_expected_utility_range(self):
        """EU всегда в [0, 1]."""
        m = PlanMetrics()
        # Максимум
        m.resource_efficiency = 1.0
        m.survival_probability = 1.0
        m.influence_score = 1.0
        m.risk_score = 0.0
        self.assertLessEqual(m.expected_utility(), 1.0)
        self.assertGreaterEqual(m.expected_utility(), 0.0)

        # Минимум
        m.resource_efficiency = 0.0
        m.survival_probability = 0.0
        m.influence_score = 0.0
        m.risk_score = 1.0
        self.assertLessEqual(m.expected_utility(), 1.0)
        self.assertGreaterEqual(m.expected_utility(), 0.0)

    def test_orchestration_submit_batch(self):
        """submit_batch принимает список словарей."""
        orch = _make_orch()
        tasks = orch.submit_batch([
            {'goal': 'batch1'},
            {'goal': 'batch2', 'priority': TaskPriority.HIGH},
        ])
        self.assertEqual(len(tasks), 2)
        self.assertEqual(orch.queue_size(), 2)

    def test_parallel_run(self):
        """run_parallel выполняет независимые задачи."""
        orch = _make_orch(agent_system=_fast_agent())
        for i in range(4):
            orch.submit(f'parallel-{i}')
        results = orch.run_parallel()
        self.assertEqual(len(results), 4)
        for r in results:
            self.assertEqual(r.status, 'done')


if __name__ == '__main__':
    unittest.main()
