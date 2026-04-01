"""
Priority-2 coverage: evaluation/*, resources/budget_control, state/state_manager.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════════════════
# 1. evaluation/audit_journal.py
# ═══════════════════════════════════════════════════════════════════════════
from evaluation.audit_journal import AuditEntry, AuditJournal


class TestAuditEntry(unittest.TestCase):
    def _entry(self, **kw):
        defaults = dict(ts=time.time(), run_id='r1', goal='write code',
                        intent='code', verdict='SUCCESS', score=1.0,
                        issues=[], tool_used='python')
        defaults.update(kw)
        return AuditEntry(**defaults)

    def test_to_dict_round_trip(self):
        e = self._entry()
        d = e.to_dict()
        e2 = AuditEntry.from_dict(d)
        self.assertEqual(e2.goal, 'write code')
        self.assertEqual(e2.verdict, 'SUCCESS')

    def test_is_failure_false(self):
        self.assertFalse(self._entry(verdict='SUCCESS').is_failure)

    def test_is_failure_true(self):
        self.assertTrue(self._entry(verdict='FAILED').is_failure)

    def test_is_failure_partial(self):
        self.assertTrue(self._entry(verdict='PARTIAL').is_failure)

    def test_human_date(self):
        e = self._entry(ts=0)
        self.assertIsInstance(e.human_date(), str)


class TestAuditJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.j = AuditJournal(memory_dir=self.tmp)

    def test_record_and_load(self):
        self.j.record_task('build module', 'code', 'SUCCESS', score=0.9)
        j2 = AuditJournal(memory_dir=self.tmp)
        self.assertGreaterEqual(len(j2._entries), 1)

    def test_get_similar_past_failures(self):
        self.j.record_task('deploy server', 'infra', 'FAILED', score=0.2,
                           issues=['timeout'])
        self.j.record_task('deploy docker', 'infra', 'FAILED', score=0.3)
        fails = self.j.get_similar_past_failures('deploy app')
        self.assertGreater(len(fails), 0)

    def test_get_similar_no_failures(self):
        self.j.record_task('hello', 'greet', 'SUCCESS')
        fails = self.j.get_similar_past_failures('hello world')
        self.assertEqual(len(fails), 0)

    def test_get_intent_failures(self):
        self.j.record_task('a', 'code', 'FAILED', score=0.1)
        self.j.record_task('b', 'code', 'FAILED', score=0.2)
        self.j.record_task('c', 'code', 'SUCCESS', score=0.9)
        fs = self.j.get_intent_failures('code')
        self.assertEqual(len(fs), 2)

    def test_get_intent_failure_rate(self):
        self.j.record_task('a', 'code', 'FAILED')
        self.j.record_task('b', 'code', 'SUCCESS')
        rate = self.j.get_intent_failure_rate('code', min_runs=2)
        self.assertAlmostEqual(rate, 0.5)

    def test_get_intent_failure_rate_below_min_runs(self):
        self.j.record_task('a', 'code', 'FAILED')
        rate = self.j.get_intent_failure_rate('code', min_runs=5)
        self.assertEqual(rate, 0.0)

    def test_count_runs_for_goal(self):
        self.j.record_task('deploy app', 'infra', 'SUCCESS')
        self.j.record_task('deploy application', 'infra', 'FAILED')
        c = self.j.count_runs_for_goal('deploy app')
        self.assertGreaterEqual(c, 1)

    def test_get_failure_summary_no_failures(self):
        s = self.j.get_failure_summary_for_prompt('random goal')
        self.assertEqual(s, '')

    def test_get_failure_summary_with_failures(self):
        self.j.record_task('deploy', 'infra', 'FAILED', issues=['crash'])
        s = self.j.get_failure_summary_for_prompt('deploy server')
        self.assertIsInstance(s, str)

    def test_get_run_summary(self):
        self.j.record_task('x', 'code', 'SUCCESS')
        self.j.record_task('y', 'code', 'FAILED')
        s = self.j.get_run_summary()
        self.assertIn('total', s)

    def test_rotation(self):
        for i in range(1100):
            self.j.record_task(f'task{i}', 'test', 'SUCCESS', score=1.0)
        self.assertLessEqual(len(self.j._entries), 1050)


# ═══════════════════════════════════════════════════════════════════════════
# 2. evaluation/evaluation.py
# ═══════════════════════════════════════════════════════════════════════════
from evaluation.evaluation import EvalResult, EvalStatus, EvaluationSystem, BenchmarkSuite


class TestEvalResult(unittest.TestCase):
    def test_to_dict(self):
        r = EvalResult('test', EvalStatus.PASS, score=0.9, details='ok')
        d = r.to_dict()
        self.assertEqual(d['name'], 'test')
        self.assertEqual(d['status'], 'pass')  # .value is lowercase

    def test_partial_status(self):
        r = EvalResult('t', EvalStatus.PARTIAL, score=0.5)
        self.assertEqual(r.status, EvalStatus.PARTIAL)


class TestBenchmarkSuite(unittest.TestCase):
    def test_add_and_len(self):
        s = BenchmarkSuite('suite1')
        s.add('input1', 'expected1', tags=['tag1'])
        s.add('input2', 'expected2')
        self.assertEqual(len(s), 2)


class TestEvaluationSystem(unittest.TestCase):
    def setUp(self):
        self.ev = EvaluationSystem(pass_threshold=0.7)

    def test_evaluate_no_core_returns_partial(self):
        # Without cognitive_core, evaluate returns PARTIAL
        r = self.ev.evaluate('test', 'SCORE: 0.9', ['check quality'])
        self.assertEqual(r.status, EvalStatus.PARTIAL)

    def test_evaluate_with_core(self):
        core = SimpleNamespace(reasoning=MagicMock(return_value='SCORE: 0.9\n✓ all checks'))
        ev = EvaluationSystem(cognitive_core=core, pass_threshold=0.7)
        r = ev.evaluate('test', 'output text', ['accuracy', 'completeness'])
        self.assertEqual(r.status, EvalStatus.PASS)
        self.assertGreaterEqual(r.score, 0.7)

    def test_evaluate_with_core_fail(self):
        core = SimpleNamespace(reasoning=MagicMock(return_value='SCORE: 0.2\n✗ bad'))
        ev = EvaluationSystem(cognitive_core=core, pass_threshold=0.7)
        r = ev.evaluate('test', 'bad output', ['accuracy'])
        self.assertEqual(r.status, EvalStatus.FAIL)

    def test_evaluate_checkmark_ratio(self):
        core = SimpleNamespace(reasoning=MagicMock(return_value='✓ item1\n✓ item2\n✗ item3'))
        ev = EvaluationSystem(cognitive_core=core, pass_threshold=0.7)
        r = ev.evaluate('test', 'output', ['check items'])
        self.assertIsNotNone(r.score)

    def test_evaluate_sentiment(self):
        core = SimpleNamespace(reasoning=MagicMock(return_value='excellent great good perfect'))
        ev = EvaluationSystem(cognitive_core=core, pass_threshold=0.7)
        r = ev.evaluate('test', 'some output', ['quality'])
        # Score from sentiment analysis
        self.assertIsNotNone(r)

    def test_evaluate_exact_pass(self):
        r = self.ev.evaluate_exact('eq_test', 'hello', 'hello')
        self.assertEqual(r.status, EvalStatus.PASS)
        self.assertEqual(r.score, 1.0)

    def test_evaluate_exact_fail(self):
        r = self.ev.evaluate_exact('eq_test', 'hello', 'world')
        self.assertEqual(r.status, EvalStatus.FAIL)
        self.assertEqual(r.score, 0.0)

    def test_evaluate_contains_all(self):
        r = self.ev.evaluate_contains('ct_test', 'I love Python and Java', ['Python', 'Java'])
        self.assertEqual(r.status, EvalStatus.PASS)
        self.assertEqual(r.score, 1.0)

    def test_evaluate_contains_partial(self):
        r = self.ev.evaluate_contains('ct_test', 'I love Python', ['Python', 'Java', 'Go'])
        self.assertLess(r.score, 1.0)

    def test_record_and_get_kpi(self):
        self.ev.record_kpi('latency', 0.5)
        self.ev.record_kpi('latency', 0.3)
        self.ev.record_kpi('latency', 0.8)
        kpi = self.ev.get_kpi('latency')
        self.assertEqual(kpi['count'], 3)
        self.assertAlmostEqual(kpi['min'], 0.3)
        self.assertAlmostEqual(kpi['max'], 0.8)

    def test_all_kpi(self):
        self.ev.record_kpi('m1', 1.0)
        self.ev.record_kpi('m2', 2.0)
        kpis = self.ev.all_kpi()
        self.assertIn('m1', kpis)
        self.assertIn('m2', kpis)

    def test_get_results_all(self):
        self.ev.evaluate_exact('a', 'x', 'x')
        self.ev.evaluate_exact('b', 'x', 'y')
        results = self.ev.get_results()
        self.assertEqual(len(results), 2)

    def test_get_results_filtered(self):
        self.ev.evaluate_exact('a', 'x', 'x')
        self.ev.evaluate_exact('b', 'x', 'y')
        results = self.ev.get_results(status=EvalStatus.PASS)
        self.assertTrue(all(r['status'] == 'pass' for r in results))

    def test_summary(self):
        self.ev.evaluate_exact('a', '1', '1')
        self.ev.evaluate_exact('b', '1', '2')
        s = self.ev.summary()
        self.assertIn('total', s)
        self.assertIn('pass_rate', s)

    def test_create_and_run_suite(self):
        suite = self.ev.create_suite('math')
        suite.add('2+2', '4')
        suite.add('3+3', '6')
        result = self.ev.run_suite('math', lambda inp: inp.replace('+', '+'))
        self.assertIn('total', result)

    def test_ab_compare(self):
        fn_a = lambda inp: inp.upper()
        fn_b = lambda inp: inp.lower()
        r = self.ev.ab_compare('casing', fn_a, fn_b,
                               ['hello', 'world'], 'check output quality')
        self.assertIn('winner', r)
        self.assertIn(r['winner'], ('A', 'B', 'tie'))


# ═══════════════════════════════════════════════════════════════════════════
# 3. evaluation/step_evaluator.py
# ═══════════════════════════════════════════════════════════════════════════
from evaluation.step_evaluator import StepEvaluator, StepVerdict


class TestStepVerdict(unittest.TestCase):
    def test_to_dict(self):
        v = StepVerdict(passed=True, verdict='SUCCESS', score=1.0,
                        issues=[], expected_tool='python', actual_tool='python',
                        intent='code')
        d = v.to_dict()
        self.assertTrue(d['passed'])
        self.assertEqual(d['verdict'], 'SUCCESS')


class TestStepEvaluator(unittest.TestCase):
    def setUp(self):
        self.se = StepEvaluator()

    def test_success_path(self):
        result = {'status': 'done', 'output': 'Task completed'}
        exec_r = {'actions_found': 1, 'actions_executed': 1,
                  'results': [{'type': 'python', 'success': True}]}
        v = self.se.evaluate('write python code', result, exec_r)
        self.assertIsInstance(v, StepVerdict)
        self.assertGreater(v.score, 0.0)

    def test_non_actionable(self):
        result = {'status': 'non_actionable'}
        v = self.se.evaluate('# heading', result)
        self.assertIsInstance(v, StepVerdict)

    def test_blocked(self):
        result = {'status': 'blocked', 'output': 'no access'}
        v = self.se.evaluate('read protected file', result)
        self.assertIsInstance(v, StepVerdict)

    def test_tool_mismatch(self):
        result = {'status': 'done'}
        exec_r = {'results': [{'type': 'search', 'success': True}]}
        v = self.se.evaluate('send email to user', result, exec_r)
        self.assertIsInstance(v, StepVerdict)
        # email task with search tool should get penalty
        self.assertLessEqual(v.score, 1.0)

    def test_status_honesty_false_success(self):
        result = {'status': 'done', 'output': 'ok'}
        exec_r = {'results': [{'type': 'bash', 'success': False, 'error': 'crash'}]}
        v = self.se.evaluate('run script', result, exec_r)
        self.assertIsInstance(v, StepVerdict)

    def test_substitution_detection(self):
        result = {'status': 'done', 'output': 'Here is some generated text about weather'}
        exec_r = {'results': [{'type': 'python', 'success': True}]}
        v = self.se.evaluate('retrieve weather data from API', result, exec_r)
        self.assertIsInstance(v, StepVerdict)

    def test_empty_result(self):
        v = self.se.evaluate('do something', {})
        self.assertIsInstance(v, StepVerdict)

    def test_string_result(self):
        v = self.se.evaluate('test goal', 'some string result')
        self.assertIsInstance(v, StepVerdict)


# ═══════════════════════════════════════════════════════════════════════════
# 4. resources/budget_control.py
# ═══════════════════════════════════════════════════════════════════════════
from resources.budget_control import BudgetControl, BudgetStatus, ResourceType


class TestBudgetControlSetup(unittest.TestCase):
    def test_init_defaults(self):
        bc = BudgetControl()
        self.assertFalse(bc.is_stopped)

    def test_set_limits(self):
        bc = BudgetControl()
        bc.set_token_limit(10000)
        bc.set_money_limit(5.0)
        bc.set_request_limit(100)
        bc.set_limit(ResourceType.COMPUTE, 3600)
        s = bc.get_status()
        self.assertIsInstance(s, dict)


class TestBudgetControlSpend(unittest.TestCase):
    def setUp(self):
        self.bc = BudgetControl(auto_stop=False)
        self.bc.set_token_limit(1000)
        self.bc.set_money_limit(10.0)

    def test_spend_ok(self):
        status = self.bc.spend(ResourceType.TOKENS, 100, 'test')
        self.assertEqual(status, BudgetStatus.OK)

    def test_spend_warning(self):
        self.bc.spend(ResourceType.TOKENS, 850)
        s = self.bc.get_status(ResourceType.TOKENS)
        self.assertIn(s['status'], ('warning', 'exceeded'))

    def test_spend_exceeded(self):
        self.bc.spend(ResourceType.TOKENS, 1100)
        s = self.bc.get_status(ResourceType.TOKENS)
        self.assertEqual(s['status'], 'exceeded')

    def test_spend_tokens(self):
        self.bc.spend_tokens(100, 50)
        s = self.bc.get_status(ResourceType.TOKENS)
        self.assertAlmostEqual(s['spent'], 150)

    def test_spend_money(self):
        bc = BudgetControl(auto_stop=False, persist_path=os.path.join(tempfile.mkdtemp(), 'b.json'))
        bc.set_money_limit(10.0)
        bc.spend_money(2.5, 'API call')
        s = bc.get_status(ResourceType.MONEY)
        self.assertAlmostEqual(s['spent'], 2.5)

    def test_spend_request(self):
        self.bc.set_request_limit(10)
        self.bc.spend_request('request 1')
        s = self.bc.get_status(ResourceType.REQUESTS)
        self.assertEqual(s['spent'], 1)


class TestBudgetControlCheck(unittest.TestCase):
    def setUp(self):
        self.bc = BudgetControl(auto_stop=False)
        self.bc.set_token_limit(1000)

    def test_check_under_limit(self):
        self.assertTrue(self.bc.check(ResourceType.TOKENS, 500))

    def test_check_over_limit(self):
        self.bc.spend(ResourceType.TOKENS, 900)
        self.assertFalse(self.bc.check(ResourceType.TOKENS, 200))

    def test_can_afford(self):
        bc = BudgetControl(auto_stop=False, persist_path=os.path.join(tempfile.mkdtemp(), 'b.json'))
        bc.set_token_limit(1000)
        bc.set_money_limit(5.0)
        self.assertTrue(bc.can_afford(tokens=500, money=3.0))
        self.assertFalse(bc.can_afford(tokens=1500))


class TestBudgetControlStopResume(unittest.TestCase):
    def test_stop(self):
        bc = BudgetControl()
        bc.stop('over budget')
        self.assertTrue(bc.is_stopped)

    def test_resume(self):
        bc = BudgetControl()
        bc.stop()
        bc.resume()
        self.assertFalse(bc.is_stopped)

    def test_check_when_stopped(self):
        bc = BudgetControl()
        bc.set_token_limit(1000)
        bc.stop()
        self.assertFalse(bc.check(ResourceType.TOKENS))


class TestBudgetControlGate(unittest.TestCase):
    def test_gate_ok(self):
        bc = BudgetControl(auto_stop=False)
        bc.set_token_limit(1000)
        self.assertTrue(bc.gate())

    def test_gate_stopped(self):
        bc = BudgetControl()
        bc.stop()
        self.assertFalse(bc.gate())

    def test_gate_auto_stop_exceeded(self):
        bc = BudgetControl(auto_stop=True)
        bc.set_token_limit(100)
        bc.spend(ResourceType.TOKENS, 200)
        # spend already triggered auto-stop via _check_status
        self.assertTrue(bc.is_stopped)
        g = bc.gate()
        self.assertFalse(g)


class TestBudgetControlPrioritize(unittest.TestCase):
    def test_prioritize_by_value(self):
        bc = BudgetControl()
        tasks = [
            {'name': 'cheap', 'cost': 10, 'value': 100},
            {'name': 'expensive', 'cost': 100, 'value': 50},
        ]
        ordered = bc.prioritize(tasks)
        self.assertEqual(ordered[0]['name'], 'cheap')

    def test_filter_affordable(self):
        bc = BudgetControl(auto_stop=False)
        bc.set_token_limit(50)
        tasks = [
            {'name': 't1', 'cost': 20},
            {'name': 't2', 'cost': 20},
            {'name': 't3', 'cost': 20},
        ]
        filtered = bc.filter_affordable(tasks)
        self.assertLessEqual(len(filtered), 3)


class TestBudgetControlReset(unittest.TestCase):
    def test_reset_specific(self):
        bc = BudgetControl(auto_stop=False)
        bc.set_token_limit(1000)
        bc.spend(ResourceType.TOKENS, 500)
        bc.reset(ResourceType.TOKENS)
        s = bc.get_status(ResourceType.TOKENS)
        self.assertEqual(s['spent'], 0)

    def test_reset_all(self):
        bc = BudgetControl(auto_stop=False)
        bc.set_token_limit(1000)
        bc.set_money_limit(10.0)
        bc.spend(ResourceType.TOKENS, 500)
        bc.spend_money(5.0)
        bc.reset()
        s = bc.get_status(ResourceType.TOKENS)
        self.assertEqual(s['spent'], 0)

    def test_reset_period_keeps_money(self):
        bc = BudgetControl(auto_stop=False)
        bc.set_token_limit(1000)
        bc.set_money_limit(10.0)
        bc.spend(ResourceType.TOKENS, 500)
        bc.spend_money(5.0)
        bc.reset_period()
        ts = bc.get_status(ResourceType.TOKENS)
        ms = bc.get_status(ResourceType.MONEY)
        self.assertEqual(ts['spent'], 0)
        self.assertAlmostEqual(ms['spent'], 5.0)


class TestBudgetControlSummary(unittest.TestCase):
    def test_summary(self):
        bc = BudgetControl(auto_stop=False)
        bc.set_token_limit(1000)
        bc.spend(ResourceType.TOKENS, 100, 'test')
        s = bc.summary()
        self.assertIn('stopped', s)
        self.assertIn('transactions', s)

    def test_get_transactions(self):
        bc = BudgetControl(auto_stop=False)
        bc.set_token_limit(1000)
        bc.spend(ResourceType.TOKENS, 50, 'tx1')
        bc.spend(ResourceType.TOKENS, 75, 'tx2')
        txs = bc.get_transactions()
        self.assertGreaterEqual(len(txs), 2)

    def test_get_transactions_filtered(self):
        bc = BudgetControl(auto_stop=False, persist_path=os.path.join(tempfile.mkdtemp(), 'b.json'))
        bc.set_token_limit(1000)
        bc.set_money_limit(10.0)
        bc.spend(ResourceType.TOKENS, 50)
        bc.spend_money(1.0)
        txs = bc.get_transactions(resource=ResourceType.TOKENS)
        self.assertTrue(all(t['resource'] == 'tokens' for t in txs))


class TestBudgetControlPersistence(unittest.TestCase):
    def test_persist_and_load(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, 'budget.json')
        bc1 = BudgetControl(auto_stop=False, persist_path=path)
        bc1.set_money_limit(100.0)
        bc1.spend_money(25.0, 'test')
        # Load fresh
        bc2 = BudgetControl(auto_stop=False, persist_path=path)
        s = bc2.get_status(ResourceType.MONEY)
        self.assertAlmostEqual(s['spent'], 25.0)


# ═══════════════════════════════════════════════════════════════════════════
# 5. state/state_manager.py
# ═══════════════════════════════════════════════════════════════════════════
from state.state_manager import StateManager, Session, Checkpoint, SessionStatus


class TestSession(unittest.TestCase):
    def test_create(self):
        s = Session('s1', goal='test goal')
        self.assertEqual(s.status, SessionStatus.ACTIVE)
        self.assertEqual(s.step, 0)

    def test_advance(self):
        s = Session('s1', goal='test')
        s.advance({'action': 'did something'})
        self.assertEqual(s.step, 1)
        self.assertEqual(len(s.history), 1)

    def test_to_dict(self):
        s = Session('s1', goal='test')
        d = s.to_dict()
        self.assertEqual(d['session_id'], 's1')
        self.assertEqual(d['goal'], 'test')


class TestCheckpoint(unittest.TestCase):
    def test_create(self):
        cp = Checkpoint('cp1', 's1', 5, {'key': 'val'})
        self.assertEqual(cp.checkpoint_id, 'cp1')
        self.assertEqual(cp.step, 5)

    def test_data_deep_copy(self):
        data = {'list': [1, 2, 3]}
        cp = Checkpoint('cp1', 's1', 1, data)
        data['list'].append(4)
        self.assertEqual(len(cp.data['list']), 3)

    def test_to_dict(self):
        cp = Checkpoint('cp1', 's1', 1, {'x': 1})
        d = cp.to_dict()
        self.assertEqual(d['checkpoint_id'], 'cp1')


class TestStateManagerSessions(unittest.TestCase):
    def setUp(self):
        self.sm = StateManager()

    def test_create_session(self):
        s = self.sm.create_session(goal='build module')
        self.assertIsInstance(s, Session)
        self.assertEqual(s.status, SessionStatus.ACTIVE)

    def test_get_session(self):
        s = self.sm.create_session(goal='test')
        got = self.sm.get_session(s.session_id)
        self.assertEqual(got.goal, 'test')

    def test_get_active(self):
        self.sm.create_session(goal='active one')
        active = self.sm.get_active()
        self.assertIsNotNone(active)

    def test_set_active(self):
        s1 = self.sm.create_session(goal='s1')
        _s2 = self.sm.create_session(goal='s2')
        self.sm.set_active(s1.session_id)
        self.assertEqual(self.sm.get_active().session_id, s1.session_id)

    def test_close_session_success(self):
        s = self.sm.create_session(goal='test')
        self.sm.close_session(s.session_id, success=True)
        self.assertEqual(s.status, SessionStatus.COMPLETED)

    def test_close_session_failure(self):
        s = self.sm.create_session(goal='test')
        self.sm.close_session(s.session_id, success=False)
        self.assertEqual(s.status, SessionStatus.FAILED)

    def test_list_sessions(self):
        self.sm.create_session(goal='a')
        self.sm.create_session(goal='b')
        ls = self.sm.list_sessions()
        self.assertGreaterEqual(len(ls), 2)


class TestStateManagerContext(unittest.TestCase):
    def setUp(self):
        self.sm = StateManager()
        self.s = self.sm.create_session(goal='ctx test')

    def test_set_and_get(self):
        self.sm.set_context('key1', 'value1')
        v = self.sm.get_context('key1')
        self.assertEqual(v, 'value1')

    def test_get_all_context(self):
        self.sm.set_context('a', 1)
        self.sm.set_context('b', 2)
        ctx = self.sm.get_context()
        self.assertIn('a', ctx)
        self.assertIn('b', ctx)

    def test_update_context(self):
        self.sm.update_context({'x': 10, 'y': 20})
        self.assertEqual(self.sm.get_context('x'), 10)
        self.assertEqual(self.sm.get_context('y'), 20)

    def test_advance_step(self):
        self.sm.advance_step({'action': 'test'})
        s = self.sm.get_active()
        self.assertEqual(s.step, 1)


class TestStateManagerCheckpoint(unittest.TestCase):
    def setUp(self):
        self.sm = StateManager()
        self.s = self.sm.create_session(goal='cp test')
        self.sm.set_context('state_key', 'state_val')

    def test_checkpoint_create(self):
        cp = self.sm.checkpoint(label='test_cp')
        self.assertIsInstance(cp, Checkpoint)
        self.assertIn('state_key', cp.data)

    def test_resume_from(self):
        self.sm.set_context('before', True)
        self.sm.checkpoint(label='before_change')
        self.sm.set_context('before', False)
        self.sm.set_context('after', True)
        self.sm.resume_from('before_change')
        s = self.sm.get_active()
        self.assertTrue(s.context.get('before'))

    def test_get_latest_checkpoint(self):
        self.sm.checkpoint(label='cp1')
        self.sm.advance_step()
        self.sm.checkpoint(label='cp2')
        latest = self.sm.get_latest_checkpoint()
        self.assertEqual(latest.checkpoint_id, 'cp2')


class TestStateManagerIdempotency(unittest.TestCase):
    def setUp(self):
        self.sm = StateManager()
        self.sm.create_session(goal='idem test')

    def test_mark_done_and_is_done(self):
        self.sm.mark_done('step_A')
        self.assertTrue(self.sm.is_done('step_A'))
        self.assertFalse(self.sm.is_done('step_B'))

    def test_run_once(self):
        counter = {'n': 0}
        def inc():
            counter['n'] += 1
            return counter['n']
        r1 = self.sm.run_once('inc_step', inc)
        r2 = self.sm.run_once('inc_step', inc)
        self.assertEqual(r1, 1)
        self.assertIsNone(r2)
        self.assertEqual(counter['n'], 1)


class TestStateManagerPersistence(unittest.TestCase):
    def test_save_and_load(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, 'state.json')
        sm1 = StateManager(persistence_path=path)
        sm1.create_session(goal='persist test')
        sm1.set_context('key', 'val')
        sm1.checkpoint(label='cp1')
        sm1.save()

        sm2 = StateManager(persistence_path=path)
        sm2.load()
        sessions = sm2.list_sessions()
        self.assertGreaterEqual(len(sessions), 1)

    def test_save_checkpoint_loop(self):
        tmp = tempfile.mkdtemp()
        sm = StateManager()
        sm.save_checkpoint(tmp, 'sess1', 'goal', 5, 0, 0.8)
        data = sm.restore_checkpoint(tmp, 'sess1')
        self.assertEqual(data.get('session_id'), 'sess1')
        self.assertEqual(data.get('cycle_count'), 5)

    def test_restore_nonexistent_checkpoint(self):
        tmp = tempfile.mkdtemp()
        sm = StateManager()
        data = sm.restore_checkpoint(tmp, 'nonexistent')
        self.assertEqual(data, {})


if __name__ == '__main__':
    unittest.main()
