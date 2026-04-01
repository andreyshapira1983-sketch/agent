"""100% coverage tests for core/long_horizon_planning.py"""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.long_horizon_planning import (
    PlanMetrics, HorizonScale, Milestone, Roadmap,
    LongHorizonPlanning, LongHorizonPlanner,
)


# ── PlanMetrics ──────────────────────────────────────────────────────────────

class PlanMetricsTests(unittest.TestCase):

    def test_defaults(self):
        m = PlanMetrics()
        self.assertEqual(m.resource_efficiency, 0.0)
        self.assertEqual(m.survival_probability, 0.0)
        self.assertEqual(m.influence_score, 0.0)
        self.assertEqual(m.risk_score, 0.0)
        self.assertEqual(m.adaptation_rate, 0.0)
        self.assertEqual(m.discount_factor, 0.9)
        self.assertEqual(m.raw, {})

    def test_expected_utility_zero(self):
        m = PlanMetrics()
        self.assertEqual(m.expected_utility(), 0.0)

    def test_expected_utility_positive(self):
        m = PlanMetrics()
        m.resource_efficiency = 1.0
        m.survival_probability = 1.0
        m.influence_score = 1.0
        m.risk_score = 0.0
        eu = m.expected_utility()
        # 1*0.25 + 1*0.30 + 1*0.25 - 0*0.20 = 0.80
        self.assertAlmostEqual(eu, 0.8)

    def test_expected_utility_clamped(self):
        m = PlanMetrics()
        m.risk_score = 1.0  # negative contribution: -0.20
        # all others 0 → reward = -0.20 → clamped to 0
        self.assertEqual(m.expected_utility(), 0.0)

    def test_expected_utility_clamped_high(self):
        m = PlanMetrics()
        m.resource_efficiency = 1.0
        m.survival_probability = 1.0
        m.influence_score = 1.0
        m.risk_score = 0.0
        # 0.80 → within range, fine
        self.assertLessEqual(m.expected_utility(), 1.0)

    def test_to_dict(self):
        m = PlanMetrics()
        m.resource_efficiency = 0.1234
        d = m.to_dict()
        self.assertEqual(d['resource_efficiency'], 0.123)
        self.assertIn('expected_utility', d)
        for key in ('survival_probability', 'influence_score',
                     'risk_score', 'adaptation_rate'):
            self.assertIn(key, d)


# ── HorizonScale ─────────────────────────────────────────────────────────────

class HorizonScaleTests(unittest.TestCase):

    def test_values(self):
        self.assertEqual(HorizonScale.DAY.value, 'day')
        self.assertEqual(HorizonScale.WEEK.value, 'week')
        self.assertEqual(HorizonScale.MONTH.value, 'month')
        self.assertEqual(HorizonScale.QUARTER.value, 'quarter')
        self.assertEqual(HorizonScale.YEAR.value, 'year')


# ── Milestone ────────────────────────────────────────────────────────────────

class MilestoneTests(unittest.TestCase):

    def test_basic_creation(self):
        ms = Milestone('m1', 'Test milestone', time.time() + 86400)
        self.assertEqual(ms.milestone_id, 'm1')
        self.assertEqual(ms.description, 'Test milestone')
        self.assertFalse(ms.completed)
        self.assertIsNone(ms.completed_at)
        self.assertEqual(ms.progress, 0.0)
        self.assertEqual(ms.deliverables, [])

    def test_deliverables(self):
        ms = Milestone('m1', 'Test', time.time(), deliverables=['d1', 'd2'])
        self.assertEqual(ms.deliverables, ['d1', 'd2'])

    def test_days_remaining(self):
        ms = Milestone('m1', 'Test', time.time() + 2 * 86400)
        self.assertAlmostEqual(ms.days_remaining, 2.0, delta=0.1)

    def test_days_remaining_negative(self):
        ms = Milestone('m1', 'Test', time.time() - 86400)
        self.assertLess(ms.days_remaining, 0)

    def test_to_dict(self):
        ms = Milestone('m1', 'desc', time.time() + 86400, ['del1'])
        d = ms.to_dict()
        self.assertEqual(d['milestone_id'], 'm1')
        self.assertEqual(d['description'], 'desc')
        self.assertIn('days_remaining', d)
        self.assertFalse(d['completed'])
        self.assertEqual(d['deliverables'], ['del1'])


# ── Roadmap ──────────────────────────────────────────────────────────────────

class RoadmapTests(unittest.TestCase):

    def test_creation(self):
        rm = Roadmap('rm1', 'Title', 'Goal', HorizonScale.MONTH)
        self.assertEqual(rm.roadmap_id, 'rm1')
        self.assertEqual(rm.title, 'Title')
        self.assertEqual(rm.goal, 'Goal')
        self.assertEqual(rm.horizon, HorizonScale.MONTH)
        self.assertEqual(rm.status, 'active')
        self.assertEqual(rm.milestones, [])
        self.assertEqual(rm.phases, [])
        self.assertEqual(rm.risks, [])
        self.assertEqual(rm.assumptions, [])
        self.assertIsInstance(rm.metrics, PlanMetrics)

    def test_start_date_default(self):
        before = time.time()
        rm = Roadmap('rm1', 'T', 'G', HorizonScale.DAY)
        self.assertGreaterEqual(rm.start_date, before)

    def test_start_date_custom(self):
        rm = Roadmap('rm1', 'T', 'G', HorizonScale.DAY, start_date=1000.0)
        self.assertEqual(rm.start_date, 1000.0)

    def test_overall_progress_no_milestones(self):
        rm = Roadmap('rm1', 'T', 'G', HorizonScale.DAY)
        self.assertEqual(rm.overall_progress, 0.0)

    def test_overall_progress_with_milestones(self):
        rm = Roadmap('rm1', 'T', 'G', HorizonScale.DAY)
        m1 = Milestone('m1', 'd', time.time())
        m1.completed = True
        m2 = Milestone('m2', 'd', time.time())
        rm.milestones = [m1, m2]
        self.assertAlmostEqual(rm.overall_progress, 0.5)

    def test_next_milestone_none(self):
        rm = Roadmap('rm1', 'T', 'G', HorizonScale.DAY)
        self.assertIsNone(rm.next_milestone())

    def test_next_milestone_all_completed(self):
        rm = Roadmap('rm1', 'T', 'G', HorizonScale.DAY)
        m1 = Milestone('m1', 'd', time.time())
        m1.completed = True
        rm.milestones = [m1]
        self.assertIsNone(rm.next_milestone())

    def test_next_milestone_returns_earliest(self):
        rm = Roadmap('rm1', 'T', 'G', HorizonScale.DAY)
        m1 = Milestone('m1', 'later', time.time() + 200)
        m2 = Milestone('m2', 'sooner', time.time() + 100)
        rm.milestones = [m1, m2]
        self.assertEqual(rm.next_milestone().milestone_id, 'm2')

    def test_to_dict(self):
        rm = Roadmap('rm1', 'T', 'G', HorizonScale.WEEK)
        d = rm.to_dict()
        self.assertEqual(d['roadmap_id'], 'rm1')
        self.assertEqual(d['horizon'], 'week')
        self.assertIn('metrics', d)
        self.assertIn('overall_progress', d)


# ── LongHorizonPlanning ─────────────────────────────────────────────────────

class LHPInitTests(unittest.TestCase):

    def test_defaults(self):
        lhp = LongHorizonPlanning()
        self.assertIsNone(lhp.cognitive_core)
        self.assertIsNone(lhp.goal_manager)
        self.assertIsNone(lhp.knowledge)
        self.assertIsNone(lhp.monitoring)
        self.assertEqual(lhp._roadmaps, {})
        self.assertEqual(lhp._counter, 0)

    def test_custom_params(self):
        cc = MagicMock()
        gm = MagicMock()
        ks = MagicMock()
        mon = MagicMock()
        lhp = LongHorizonPlanning(cc, gm, ks, mon)
        self.assertIs(lhp.cognitive_core, cc)
        self.assertIs(lhp.goal_manager, gm)
        self.assertIs(lhp.knowledge, ks)
        self.assertIs(lhp.monitoring, mon)


class CreateRoadmapTests(unittest.TestCase):

    def test_create(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('Title', 'Goal')
        self.assertEqual(rm.roadmap_id, 'rm0001')
        self.assertEqual(rm.title, 'Title')
        self.assertEqual(rm.goal, 'Goal')
        self.assertEqual(rm.horizon, HorizonScale.MONTH)
        self.assertIn('rm0001', lhp._roadmaps)

    def test_create_increments_counter(self):
        lhp = LongHorizonPlanning()
        lhp.create_roadmap('A', 'G')
        lhp.create_roadmap('B', 'G')
        self.assertEqual(lhp._counter, 2)
        self.assertIn('rm0002', lhp._roadmaps)


class PlanDeterministicTests(unittest.TestCase):

    def test_no_core_day(self):
        lhp = LongHorizonPlanning()
        rm = lhp.plan('test goal', HorizonScale.DAY)
        self.assertGreater(len(rm.phases), 0)
        self.assertGreater(len(rm.milestones), 0)

    def test_no_core_week(self):
        lhp = LongHorizonPlanning()
        rm = lhp.plan('test', HorizonScale.WEEK)
        self.assertGreater(len(rm.milestones), 0)

    def test_no_core_month(self):
        lhp = LongHorizonPlanning()
        rm = lhp.plan('test', HorizonScale.MONTH)
        self.assertGreater(len(rm.milestones), 0)

    def test_no_core_quarter(self):
        lhp = LongHorizonPlanning()
        rm = lhp.plan('test', HorizonScale.QUARTER)
        self.assertGreater(len(rm.phases), 0)

    def test_no_core_year(self):
        lhp = LongHorizonPlanning()
        rm = lhp.plan('test', HorizonScale.YEAR)
        self.assertGreater(len(rm.phases), 0)

    def test_plan_deterministic_daily(self):
        lhp = LongHorizonPlanning()
        phases = lhp._plan_deterministic('goal', 5)
        self.assertEqual(len(phases), 4)
        self.assertEqual(phases[0]['duration'], '1 day')
        self.assertEqual(phases[0]['dependencies'], [])
        self.assertEqual(phases[1]['dependencies'], [1])

    def test_plan_deterministic_weekly(self):
        lhp = LongHorizonPlanning()
        phases = lhp._plan_deterministic('goal', 20)
        self.assertEqual(len(phases), 5)
        self.assertEqual(phases[0]['duration'], '1 week')

    def test_plan_deterministic_monthly(self):
        lhp = LongHorizonPlanning()
        phases = lhp._plan_deterministic('goal', 60)
        self.assertEqual(len(phases), 5)
        self.assertEqual(phases[0]['duration'], '1 month')


class PlanWithCoreTests(unittest.TestCase):

    def _make_core(self, response):
        return SimpleNamespace(reasoning=lambda x: response)

    def test_tactical_plan(self):
        response = (
            "ЭТАП: Подготовка\nСРОК: 5\nРЕЗУЛЬТАТ: Готовый план\n"
            "ЭТАП: Выполнение\nСРОК: 15\nРЕЗУЛЬТАТ: Результат\n"
            "РИСК: Нехватка ресурсов\n"
            "ДОПУЩЕНИЕ: Ресурсы есть\n"
            "RESOURCE_EFFICIENCY: 0.7\n"
            "SURVIVAL_PROBABILITY: 0.8\n"
            "INFLUENCE_SCORE: 0.6\n"
            "RISK_SCORE: 0.3\n"
        )
        core = self._make_core(response)
        lhp = LongHorizonPlanning(cognitive_core=core)
        rm = lhp.plan('short goal', HorizonScale.MONTH)
        self.assertEqual(len(rm.milestones), 2)
        self.assertEqual(len(rm.risks), 1)
        self.assertEqual(len(rm.assumptions), 1)
        self.assertGreater(rm.metrics.resource_efficiency, 0)

    def test_strategic_plan_long_goal(self):
        """Goals > 120 chars are strategic."""
        long_goal = 'x' * 121
        response = (
            "ЭТАП: Анализ рынка\nСРОК: 10\nРЕЗУЛЬТАТ: Отчёт\n"
            "РИСК: Конкуренты\nДОПУЩЕНИЕ: Рынок стабилен\n"
            "RESOURCE_EFFICIENCY: 0.5\n"
            "SURVIVAL_PROBABILITY: 0.6\n"
            "INFLUENCE_SCORE: 0.4\n"
            "RISK_SCORE: 0.2\n"
            "ADAPTATION_RATE: 0.7\n"
            "СОСТОЯНИЯ (S): A, B, C\n"
            "АЛГОРИТМ: MDP\n"
        )
        core = self._make_core(response)
        lhp = LongHorizonPlanning(cognitive_core=core)
        rm = lhp.plan(long_goal, HorizonScale.QUARTER)
        # Check metrics parsed
        self.assertAlmostEqual(rm.metrics.adaptation_rate, 0.7)

    def test_strategic_plan_keyword(self):
        response = "ЭТАП: Start\nСРОК: 5\nРЕЗУЛЬТАТ: Done\n"
        core = self._make_core(response)
        lhp = LongHorizonPlanning(cognitive_core=core)
        rm = lhp.plan('стратегия развития', HorizonScale.MONTH)
        self.assertGreater(len(rm.milestones), 0)

    def test_plan_stores_to_knowledge(self):
        response = "ЭТАП: A\nСРОК: 1\nРЕЗУЛЬТАТ: B\n"
        core = self._make_core(response)
        ks = MagicMock()
        lhp = LongHorizonPlanning(cognitive_core=core, knowledge_system=ks)
        rm = lhp.plan('goal', HorizonScale.WEEK)
        ks.store_long_term.assert_called_once()


class StrategicPlanTests(unittest.TestCase):

    def test_no_core(self):
        lhp = LongHorizonPlanning()
        rm = lhp.strategic_plan('goal', HorizonScale.QUARTER)
        self.assertGreater(len(rm.phases), 0)
        self.assertIn('[STRATEGY]', rm.title)

    def test_no_core_all_horizons(self):
        lhp = LongHorizonPlanning()
        for h in HorizonScale:
            rm = lhp.strategic_plan('goal', h)
            self.assertIsNotNone(rm)

    def test_with_core(self):
        response = (
            "ЭТАП: Phase1\nСРОК: 30\nРЕЗУЛЬТАТ: Output\n"
            "ЭТАП: Phase2\nСРОК: 60\nРЕЗУЛЬТАТ: Output2\n"
            "ЭТАП: Phase3\nСРОК: 80\nРЕЗУЛЬТАТ: Output3\n"
            "ЭТАП: Phase4\nСРОК: 90\nРЕЗУЛЬТАТ: Output4\n"
            "RESOURCE_EFFICIENCY: 0.8\nSURVIVAL_PROBABILITY: 0.9\n"
            "INFLUENCE_SCORE: 0.7\nRISK_SCORE: 0.1\nADAPTATION_RATE: 0.6\n"
            "РИСК: Задержка — ВЕРОЯТНОСТЬ: 0.3 — МИТИГАЦИЯ: Буфер\n"
            "ТРИГГЕР_АДАПТАЦИИ: Если сроки сдвинутся > 2 нед\n"
            "ДОПУЩЕНИЕ: Команда стабильна\n"
            "СОСТОЯНИЯ (S): idle, active\n"
            "АЛГОРИТМ: MDP\n"
        )
        core = SimpleNamespace(reasoning=lambda x: response)
        ks = MagicMock()
        lhp = LongHorizonPlanning(cognitive_core=core, knowledge_system=ks)
        rm = lhp.strategic_plan('goal', HorizonScale.QUARTER)
        self.assertEqual(len(rm.milestones), 4)
        self.assertGreater(len(rm.risks), 0)
        # adaptation trigger added to phases
        triggers = [p for p in rm.phases if isinstance(p, dict) and p.get('type') == 'adaptation_trigger']
        self.assertGreater(len(triggers), 0)
        ks.store_long_term.assert_called_once()
        # meta phase present
        meta = [p for p in rm.phases if isinstance(p, dict) and p.get('type') == 'meta']
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0]['mode'], 'strategic_mdp')


class EvaluatePlanTests(unittest.TestCase):

    def test_nonexistent(self):
        lhp = LongHorizonPlanning()
        result = lhp.evaluate_plan('nope')
        self.assertIn('error', result)

    def test_perfect_plan(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        rm.metrics.resource_efficiency = 0.8
        rm.metrics.survival_probability = 0.9
        rm.metrics.influence_score = 0.7
        rm.risks = ['risk1']
        rm.assumptions = ['a1']
        rm.phases = [{'type': 'meta', 'reasoning_quality': 0.8},
                     {'алгоритм': 'MDP'}]
        for i in range(3):
            lhp.add_milestone(rm.roadmap_id, f'M{i}', i + 1)
        result = lhp.evaluate_plan(rm.roadmap_id)
        self.assertEqual(result['plan_quality'], 1.0)
        self.assertEqual(result['issues'], [])
        self.assertIn('удовлетворительный', result['recommendation'])

    def test_bad_plan(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        # No metrics (EU=0), no risks, no assumptions, no algorithm, < 3 milestones
        result = lhp.evaluate_plan(rm.roadmap_id)
        self.assertLess(result['plan_quality'], 0.6)
        self.assertGreater(len(result['issues']), 0)
        self.assertIn('strategic_plan', result['recommendation'])

    def test_low_reasoning_quality(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        rm.phases = [{'type': 'meta', 'reasoning_quality': 0.2}]
        result = lhp.evaluate_plan(rm.roadmap_id)
        self.assertTrue(any('качество' in i.lower() for i in result['issues']))

    def test_quality_clamped_at_zero(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        # All penalties: -0.3 -0.2 -0.1 -0.2 -0.1 -0.1 = -1.0
        result = lhp.evaluate_plan(rm.roadmap_id)
        self.assertGreaterEqual(result['plan_quality'], 0.0)


class AdaptTests(unittest.TestCase):

    def test_no_roadmap(self):
        lhp = LongHorizonPlanning()
        result = lhp.adapt('nope', 'change')
        self.assertIsNone(result)

    def test_no_core(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        result = lhp.adapt(rm.roadmap_id, 'change')
        self.assertIs(result, rm)

    def test_with_core(self):
        core = SimpleNamespace(
            reasoning=lambda x: "КОРРЕКТИРОВКА: Сдвинуть сроки\nКОРРЕКТИРОВКА: Добавить ресурс"
        )
        lhp = LongHorizonPlanning(cognitive_core=core)
        rm = lhp.create_roadmap('T', 'G')
        result = lhp.adapt(rm.roadmap_id, 'изменился бюджет')
        self.assertIs(result, rm)
        adaptations = [p for p in rm.phases if isinstance(p, dict) and p.get('type') == 'adaptation']
        self.assertEqual(len(adaptations), 2)

    def test_no_corrections(self):
        core = SimpleNamespace(reasoning=lambda x: "Всё хорошо, менять нечего")
        lhp = LongHorizonPlanning(cognitive_core=core)
        rm = lhp.create_roadmap('T', 'G')
        lhp.adapt(rm.roadmap_id, 'no change')
        adaptations = [p for p in rm.phases if isinstance(p, dict) and p.get('type') == 'adaptation']
        self.assertEqual(len(adaptations), 0)


class MilestoneOperationsTests(unittest.TestCase):

    def test_add_milestone(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        ms = lhp.add_milestone(rm.roadmap_id, 'M1', 5.0, ['d1'])
        self.assertIsNotNone(ms)
        self.assertEqual(ms.description, 'M1')
        self.assertEqual(ms.deliverables, ['d1'])
        self.assertEqual(len(rm.milestones), 1)

    def test_add_milestone_no_roadmap(self):
        lhp = LongHorizonPlanning()
        ms = lhp.add_milestone('nope', 'M', 1.0)
        self.assertIsNone(ms)

    def test_complete_milestone(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        ms = lhp.add_milestone(rm.roadmap_id, 'M1', 5.0)
        lhp.complete_milestone(rm.roadmap_id, ms.milestone_id)
        self.assertTrue(ms.completed)
        self.assertIsNotNone(ms.completed_at)

    def test_complete_milestone_no_roadmap(self):
        lhp = LongHorizonPlanning()
        lhp.complete_milestone('nope', 'm1')  # no crash

    def test_complete_milestone_not_found(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        lhp.complete_milestone(rm.roadmap_id, 'nope')  # no crash


class CheckDeadlinesTests(unittest.TestCase):

    def test_no_roadmaps(self):
        lhp = LongHorizonPlanning()
        self.assertEqual(lhp.check_deadlines(), [])

    def test_approaching_deadline(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        lhp.add_milestone(rm.roadmap_id, 'Soon', 3.0)  # 3 days from now
        warnings = lhp.check_deadlines(warn_days=7.0)
        self.assertEqual(len(warnings), 1)
        self.assertFalse(warnings[0]['overdue'])

    def test_overdue(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        ms = lhp.add_milestone(rm.roadmap_id, 'Past', 1.0)
        ms.target_date = time.time() - 86400  # yesterday
        warnings = lhp.check_deadlines()
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0]['overdue'])

    def test_completed_not_warned(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        ms = lhp.add_milestone(rm.roadmap_id, 'Done', 1.0)
        lhp.complete_milestone(rm.roadmap_id, ms.milestone_id)
        self.assertEqual(lhp.check_deadlines(), [])

    def test_inactive_roadmap_skipped(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        lhp.add_milestone(rm.roadmap_id, 'M', 1.0)
        rm.status = 'archived'
        self.assertEqual(lhp.check_deadlines(), [])

    def test_far_deadline_not_warned(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        lhp.add_milestone(rm.roadmap_id, 'Far', 30.0)
        self.assertEqual(lhp.check_deadlines(warn_days=7.0), [])


class ReviewProgressTests(unittest.TestCase):

    def test_empty(self):
        lhp = LongHorizonPlanning()
        self.assertEqual(lhp.review_progress(), [])

    def test_with_active_roadmap(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        lhp.add_milestone(rm.roadmap_id, 'M1', 5.0)
        result = lhp.review_progress()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['roadmap_id'], rm.roadmap_id)
        self.assertIsNotNone(result[0]['next_milestone'])

    def test_inactive_skipped(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        rm.status = 'archived'
        self.assertEqual(lhp.review_progress(), [])

    def test_no_next_milestone(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        result = lhp.review_progress()
        self.assertIsNone(result[0]['next_milestone'])


class RegistryTests(unittest.TestCase):

    def test_get_roadmap(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        self.assertIs(lhp.get_roadmap(rm.roadmap_id), rm)
        self.assertIsNone(lhp.get_roadmap('nope'))

    def test_list_roadmaps(self):
        lhp = LongHorizonPlanning()
        lhp.create_roadmap('A', 'G1')
        lhp.create_roadmap('B', 'G2')
        lst = lhp.list_roadmaps()
        self.assertEqual(len(lst), 2)

    def test_summary(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        lhp.add_milestone(rm.roadmap_id, 'M', 1.0)
        s = lhp.summary()
        self.assertEqual(s['total_roadmaps'], 1)
        self.assertEqual(s['active'], 1)
        self.assertIn('avg_progress', s)
        self.assertIn('deadline_warnings', s)

    def test_summary_empty(self):
        lhp = LongHorizonPlanning()
        s = lhp.summary()
        self.assertEqual(s['total_roadmaps'], 0)
        self.assertEqual(s['avg_progress'], 0.0)


class AddDefaultMilestonesTests(unittest.TestCase):

    def test_add_default(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G', HorizonScale.MONTH)
        lhp._add_default_milestones(rm, 3)
        self.assertEqual(len(rm.milestones), 3)

    def test_unknown_horizon(self):
        """If horizon not in days_map, defaults to 30."""
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G', HorizonScale.DAY)
        lhp._add_default_milestones(rm, 2)
        self.assertEqual(len(rm.milestones), 2)


class ParseRoadmapTests(unittest.TestCase):

    def test_full_parse(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        raw = (
            "ЭТАП: Phase1\nСРОК: 10\nРЕЗУЛЬТАТ: Deliverable1\n"
            "ЭТАП: Phase2\nСРОК: 20\nРЕЗУЛЬТАТ: Deliverable2\n"
            "РИСК: R1\nРИСК: R2\n"
            "ДОПУЩЕНИЕ: A1\n"
        )
        lhp._parse_roadmap(rm, raw, 30)
        self.assertEqual(len(rm.milestones), 2)
        self.assertEqual(len(rm.risks), 2)
        self.assertEqual(len(rm.assumptions), 1)

    def test_missing_deadlines(self):
        """When no СРОК, uses calculated default."""
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        raw = "ЭТАП: Phase1\nЭТАП: Phase2\n"
        lhp._parse_roadmap(rm, raw, 30)
        self.assertEqual(len(rm.milestones), 2)

    def test_missing_results(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        raw = "ЭТАП: Phase1\nСРОК: 5\n"
        lhp._parse_roadmap(rm, raw, 30)
        self.assertEqual(rm.milestones[0].deliverables, [])


class ParseMetricsTests(unittest.TestCase):

    def test_full_metrics(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        raw = (
            "RESOURCE_EFFICIENCY: 0.8\n"
            "SURVIVAL_PROBABILITY: 0.9\n"
            "INFLUENCE_SCORE: 0.7\n"
            "RISK_SCORE: 0.2\n"
            "ADAPTATION_RATE: 0.6\n"
        )
        lhp._parse_metrics(rm, raw)
        self.assertAlmostEqual(rm.metrics.resource_efficiency, 0.8)
        self.assertAlmostEqual(rm.metrics.survival_probability, 0.9)
        self.assertAlmostEqual(rm.metrics.influence_score, 0.7)
        self.assertAlmostEqual(rm.metrics.risk_score, 0.2)
        self.assertAlmostEqual(rm.metrics.adaptation_rate, 0.6)

    def test_no_metrics(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        lhp._parse_metrics(rm, "no metrics here")
        self.assertEqual(rm.metrics.resource_efficiency, 0.0)

    def test_clamp_values(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        lhp._parse_metrics(rm, "RESOURCE_EFFICIENCY: 5.0\nRISK_SCORE: -1.0")
        self.assertEqual(rm.metrics.resource_efficiency, 1.0)
        self.assertEqual(rm.metrics.risk_score, 0.0)

    def test_invalid_value(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        # "." matches [\d.]+ but float('.') raises ValueError — covers except branch
        lhp._parse_metrics(rm, "RESOURCE_EFFICIENCY: .")
        self.assertEqual(rm.metrics.resource_efficiency, 0.0)


class ParseRisksExtendedTests(unittest.TestCase):

    def test_full_risk(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        raw = "РИСК: Задержка\nВЕРОЯТНОСТЬ: 0.3\nМИТИГАЦИЯ: Буфер\n"
        lhp._parse_risks_extended(rm, raw)
        self.assertEqual(len(rm.risks), 1)
        self.assertIn('[p=0.3]', rm.risks[0])
        self.assertIn('Буфер', rm.risks[0])

    def test_risk_without_prob_mitigation(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        raw = "РИСК: Простой риск\n"
        lhp._parse_risks_extended(rm, raw)
        self.assertEqual(len(rm.risks), 1)

    def test_triggers(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        raw = "ТРИГГЕР_АДАПТАЦИИ: Если сроки > 2 нед\n"
        lhp._parse_risks_extended(rm, raw)
        triggers = [p for p in rm.phases if isinstance(p, dict) and p.get('type') == 'adaptation_trigger']
        self.assertEqual(len(triggers), 1)

    def test_no_duplicate_risks(self):
        lhp = LongHorizonPlanning()
        rm = lhp.create_roadmap('T', 'G')
        rm.risks = ['Задержка']
        raw = "РИСК: Задержка\n"
        lhp._parse_risks_extended(rm, raw)
        self.assertEqual(len(rm.risks), 1)


class ScoreReasoningQualityTests(unittest.TestCase):

    def test_empty(self):
        lhp = LongHorizonPlanning()
        self.assertEqual(lhp._score_reasoning_quality('', False), 0.0)

    def test_basic_components(self):
        lhp = LongHorizonPlanning()
        raw = "ЭТАП: x\nРИСК: y\nРЕЗУЛЬТАТ: z\n"
        score = lhp._score_reasoning_quality(raw, False)
        self.assertAlmostEqual(score, 0.5)  # 0.2 + 0.15 + 0.15

    def test_with_metrics(self):
        lhp = LongHorizonPlanning()
        raw = "ЭТАП: x\nresource_efficiency: 0.5\n"
        score = lhp._score_reasoning_quality(raw, False)
        self.assertAlmostEqual(score, 0.4)  # 0.2 + 0.2

    def test_strategic_full(self):
        lhp = LongHorizonPlanning()
        raw = (
            "ЭТАП: x\nРИСК: y\nРЕЗУЛЬТАТ: z\n"
            "resource_efficiency: 0.5\n"
            "СОСТОЯНИЯ (S): a, b\n"
            "АЛГОРИТМ: MDP\n"
            "ТРИГГЕР: if x:\n"
        )
        score = lhp._score_reasoning_quality(raw, is_strategic=True)
        # 0.2 + 0.15 + 0.15 + 0.2 + 0.15 + 0.1 + 0.05 = 1.0
        self.assertEqual(score, 1.0)

    def test_non_strategic_ignores_mdp(self):
        lhp = LongHorizonPlanning()
        raw = "АЛГОРИТМ: MDP\nСОСТОЯНИЯ (S): a\n"
        score = lhp._score_reasoning_quality(raw, is_strategic=False)
        self.assertEqual(score, 0.0)


class IsStrategicGoalTests(unittest.TestCase):

    def test_short_non_strategic(self):
        lhp = LongHorizonPlanning()
        self.assertFalse(lhp._is_strategic_goal('написать тест'))

    def test_long_goal(self):
        lhp = LongHorizonPlanning()
        self.assertTrue(lhp._is_strategic_goal('a' * 121))

    def test_keyword_russian(self):
        lhp = LongHorizonPlanning()
        self.assertTrue(lhp._is_strategic_goal('стратегия развития'))
        self.assertTrue(lhp._is_strategic_goal('анализ рынок акций'))
        self.assertTrue(lhp._is_strategic_goal('архитектурный план'))

    def test_keyword_english(self):
        lhp = LongHorizonPlanning()
        self.assertTrue(lhp._is_strategic_goal('market strategy'))
        self.assertTrue(lhp._is_strategic_goal('platform development'))
        self.assertTrue(lhp._is_strategic_goal('competitive analysis'))


class BuildPromptsTests(unittest.TestCase):

    def test_tactical_prompt(self):
        lhp = LongHorizonPlanning()
        p = lhp._build_tactical_prompt('goal', 30, 5)
        self.assertIn('30', p)
        self.assertIn('goal', p)
        self.assertIn('ЭТАП', p)

    def test_strategic_prompt(self):
        lhp = LongHorizonPlanning()
        p = lhp._build_strategic_prompt('goal', 90, 4)
        self.assertIn('90', p)
        self.assertIn('MDP', p)
        self.assertIn('АЛГОРИТМ', p)


class LogTests(unittest.TestCase):

    def test_with_monitoring(self):
        mon = MagicMock()
        lhp = LongHorizonPlanning(monitoring=mon)
        lhp._log("test")
        mon.info.assert_called_once_with("test", source='long_horizon_planning')

    def test_without_monitoring(self):
        lhp = LongHorizonPlanning()
        with patch('builtins.print') as mock_print:
            lhp._log("hello")
            mock_print.assert_called_once()
            self.assertIn("hello", mock_print.call_args[0][0])


class AliasTests(unittest.TestCase):

    def test_alias(self):
        self.assertIs(LongHorizonPlanner, LongHorizonPlanning)


if __name__ == '__main__':
    unittest.main()
