# Tests: Reliability DLQ + error classification + State Machine transition validation
# Covers formal_contracts_spec §11 and security_behavior_hardening_plan §3.1-3.3

import threading
import time
import unittest

from loop.reliability import (
    ReliabilitySystem, RetryStrategy, ErrorClass, DLQEntry, classify_error,
)
from state.state_manager import (
    StateManager, TaskState, TaskStateMachine, InvalidTransitionError, SessionStatus,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Reliability: Error Classification
# ═══════════════════════════════════════════════════════════════════════════════

class TestErrorClassification(unittest.TestCase):

    def test_transient_by_exception_type(self):
        self.assertEqual(classify_error(ConnectionError("reset")), ErrorClass.TRANSIENT)
        self.assertEqual(classify_error(TimeoutError("timeout")), ErrorClass.TRANSIENT)
        self.assertEqual(classify_error(OSError("broken pipe")), ErrorClass.TRANSIENT)

    def test_transient_by_message(self):
        self.assertEqual(classify_error(Exception("rate limit exceeded")), ErrorClass.TRANSIENT)
        self.assertEqual(classify_error(Exception("503 service unavailable")), ErrorClass.TRANSIENT)
        self.assertEqual(classify_error(Exception("server overloaded")), ErrorClass.TRANSIENT)

    def test_permanent_by_message(self):
        self.assertEqual(classify_error(Exception("404 not found")), ErrorClass.PERMANENT)
        self.assertEqual(classify_error(Exception("401 unauthorized")), ErrorClass.PERMANENT)
        self.assertEqual(classify_error(Exception("validation error")), ErrorClass.PERMANENT)
        self.assertEqual(classify_error(Exception("permission denied")), ErrorClass.PERMANENT)

    def test_unknown_error(self):
        self.assertEqual(classify_error(Exception("something weird")), ErrorClass.UNKNOWN)
        self.assertEqual(classify_error(RuntimeError("oops")), ErrorClass.UNKNOWN)


# ═══════════════════════════════════════════════════════════════════════════════
# Reliability: DLQ
# ═══════════════════════════════════════════════════════════════════════════════

class TestDLQ(unittest.TestCase):

    def setUp(self):
        self.rs = ReliabilitySystem(default_retries=2, default_delay=0.01)

    def test_failed_retry_goes_to_dlq(self):
        call_count = 0

        def always_fail():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("network down")

        result = self.rs.retry(always_fail, retries=2, delay=0.01, fallback="nope")
        self.assertEqual(result, "nope")
        self.assertEqual(self.rs.dlq_size(), 1)

        dlq = self.rs.get_dlq()
        self.assertEqual(dlq[0]['func_name'], 'always_fail')
        self.assertIn('network down', dlq[0]['last_error'])
        self.assertEqual(dlq[0]['error_class'], 'transient')
        self.assertEqual(dlq[0]['attempts'], 2)

    def test_permanent_error_stops_retry_early(self):
        call_count = 0

        def perm_fail():
            nonlocal call_count
            call_count += 1
            raise Exception("404 not found")

        result = self.rs.retry(perm_fail, retries=5, delay=0.01, fallback="stopped")
        self.assertEqual(result, "stopped")
        # permanent → should stop after 1st attempt, not retry 5 times
        self.assertEqual(call_count, 1)
        self.assertEqual(self.rs.dlq_size(), 1)
        self.assertEqual(self.rs.get_dlq()[0]['error_class'], 'permanent')

    def test_successful_call_no_dlq(self):
        result = self.rs.retry(lambda: 42, retries=3, delay=0.01)
        self.assertEqual(result, 42)
        self.assertEqual(self.rs.dlq_size(), 0)

    def test_dlq_pop_and_clear(self):
        def fail():
            raise Exception("timeout")

        self.rs.retry(fail, retries=1, delay=0.01, fallback=None)
        self.rs.retry(fail, retries=1, delay=0.01, fallback=None)
        self.assertEqual(self.rs.dlq_size(), 2)

        entry = self.rs.dlq_pop()
        self.assertIsInstance(entry, DLQEntry)
        self.assertEqual(self.rs.dlq_size(), 1)

        self.rs.dlq_clear()
        self.assertEqual(self.rs.dlq_size(), 0)

    def test_exhaustion_notification(self):
        notifications = []

        def on_exhaust(entry: DLQEntry):
            notifications.append(entry)

        self.rs.on_exhaustion(on_exhaust)

        def fail():
            raise RuntimeError("crash")

        self.rs.retry(fail, retries=1, delay=0.01, fallback=None)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].func_name, 'fail')

    def test_dlq_max_size(self):
        rs = ReliabilitySystem(default_retries=1, default_delay=0.001, dlq_max_size=3)
        for _ in range(5):
            rs.retry(lambda: (_ for _ in ()).throw(Exception("err")),
                     retries=1, delay=0.001, fallback=None)
        self.assertEqual(rs.dlq_size(), 3)  # bounded

    def test_classify_disabled(self):
        """With classify=False, permanent errors should still be retried."""
        call_count = 0

        def perm_fail():
            nonlocal call_count
            call_count += 1
            raise Exception("404 not found")

        self.rs.retry(perm_fail, retries=3, delay=0.01, fallback="x", classify=False)
        self.assertEqual(call_count, 3)  # all 3 retries attempted


# ═══════════════════════════════════════════════════════════════════════════════
# State Machine: Transition Validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestTaskStateMachine(unittest.TestCase):

    def test_valid_lifecycle(self):
        """created → planned → running → completed — нормальный жизненный цикл."""
        sm = TaskStateMachine('task_1')
        self.assertEqual(sm.state, TaskState.CREATED)

        sm.transition(TaskState.PLANNED, reason='plan built')
        self.assertEqual(sm.state, TaskState.PLANNED)

        sm.transition(TaskState.RUNNING, reason='dispatched')
        self.assertEqual(sm.state, TaskState.RUNNING)

        sm.transition(TaskState.COMPLETED, reason='done')
        self.assertEqual(sm.state, TaskState.COMPLETED)

    def test_created_to_completed_forbidden(self):
        """formal_contracts_spec §11: created → completed запрещён."""
        sm = TaskStateMachine('task_2')
        with self.assertRaises(InvalidTransitionError):
            sm.transition(TaskState.COMPLETED)

    def test_failed_to_completed_forbidden(self):
        """formal_contracts_spec §11: failed → completed запрещён без recovery."""
        sm = TaskStateMachine('task_3')
        sm.transition(TaskState.PLANNED)
        sm.transition(TaskState.RUNNING)
        sm.transition(TaskState.FAILED, reason='error')
        with self.assertRaises(InvalidTransitionError):
            sm.transition(TaskState.COMPLETED)

    def test_failed_recovery_path(self):
        """failed → running (recovery) допустим."""
        sm = TaskStateMachine('task_4')
        sm.transition(TaskState.PLANNED)
        sm.transition(TaskState.RUNNING)
        sm.transition(TaskState.FAILED)
        sm.transition(TaskState.RUNNING, reason='recovery')
        sm.transition(TaskState.COMPLETED)
        self.assertEqual(sm.state, TaskState.COMPLETED)

    def test_awaiting_approval_needs_token(self):
        """formal_contracts_spec §11: awaiting_approval → running без approval запрещён."""
        sm = TaskStateMachine('task_5')
        sm.transition(TaskState.PLANNED)
        sm.transition(TaskState.AWAITING_APPROVAL)

        # Без approval
        with self.assertRaises(InvalidTransitionError):
            sm.transition(TaskState.RUNNING)

        # С approval
        sm.transition(TaskState.RUNNING, approval_valid=True)
        self.assertEqual(sm.state, TaskState.RUNNING)

    def test_terminal_states(self):
        """completed и cancelled — терминальные, из них нет переходов."""
        sm = TaskStateMachine('task_6')
        sm.transition(TaskState.PLANNED)
        sm.transition(TaskState.CANCELLED)
        with self.assertRaises(InvalidTransitionError):
            sm.transition(TaskState.RUNNING)

    def test_can_transition(self):
        sm = TaskStateMachine('task_7')
        self.assertTrue(sm.can_transition(TaskState.PLANNED))
        self.assertFalse(sm.can_transition(TaskState.COMPLETED))

    def test_transition_history(self):
        sm = TaskStateMachine('task_8')
        sm.transition(TaskState.PLANNED, reason='plan')
        sm.transition(TaskState.RUNNING, reason='exec')
        history = sm.transition_history
        self.assertEqual(len(history), 3)  # init + 2 transitions
        self.assertEqual(history[1]['from'], 'created')
        self.assertEqual(history[1]['to'], 'planned')
        self.assertEqual(history[2]['reason'], 'exec')

    def test_to_dict(self):
        sm = TaskStateMachine('task_9')
        d = sm.to_dict()
        self.assertEqual(d['task_id'], 'task_9')
        self.assertEqual(d['current_state'], 'created')


# ═══════════════════════════════════════════════════════════════════════════════
# StateManager: Task Machine Integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestStateManagerTaskMachine(unittest.TestCase):

    def setUp(self):
        self.sm = StateManager()

    def test_create_and_transition(self):
        self.sm.create_task_machine('t1')
        new = self.sm.transition_task('t1', TaskState.PLANNED, reason='test')
        self.assertEqual(new, TaskState.PLANNED)
        self.assertEqual(self.sm.get_task_state('t1'), TaskState.PLANNED)

    def test_invalid_transition_through_manager(self):
        self.sm.create_task_machine('t2')
        with self.assertRaises(InvalidTransitionError):
            self.sm.transition_task('t2', TaskState.COMPLETED)

    def test_auto_create_on_transition(self):
        """transition_task auto-creates machine if missing."""
        self.sm.transition_task('t3', TaskState.PLANNED)
        self.assertEqual(self.sm.get_task_state('t3'), TaskState.PLANNED)

    def test_list_task_machines(self):
        self.sm.create_task_machine('t4')
        self.sm.create_task_machine('t5')
        lst = self.sm.list_task_machines()
        self.assertEqual(len(lst), 2)


# ═══════════════════════════════════════════════════════════════════════════════
# StateManager: Concurrent Idempotency
# ═══════════════════════════════════════════════════════════════════════════════

class TestConcurrentIdempotency(unittest.TestCase):

    def test_run_once_thread_safety(self):
        """run_once should execute the function exactly once even with concurrent calls."""
        sm = StateManager()
        call_count = 0
        lock = threading.Lock()

        def work():
            nonlocal call_count
            time.sleep(0.01)
            with lock:
                call_count += 1
            return 'result'

        results = []
        errors = []

        def runner():
            try:
                r = sm.run_once('step_x', work)
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=runner) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one thread should have executed work
        self.assertEqual(call_count, 1)
        # No errors
        self.assertEqual(len(errors), 0)

    def test_run_once_rollback_on_error(self):
        """If func raises, the step should not be marked as done."""
        sm = StateManager()

        def failing():
            raise ValueError("oops")

        with self.assertRaises(ValueError):
            sm.run_once('step_fail', failing)

        # step not marked as done — can retry
        self.assertFalse(sm.is_done('step_fail'))

    def test_session_advance_thread_safety(self):
        """Concurrent advance() should not lose steps."""
        sm = StateManager()
        session = sm.create_session(goal='test')

        def advance_many():
            for _ in range(100):
                session.advance({'data': 'x'})

        threads = [threading.Thread(target=advance_many) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(session.step, 500)
        self.assertEqual(len(session.history), 500)


if __name__ == '__main__':
    unittest.main()
