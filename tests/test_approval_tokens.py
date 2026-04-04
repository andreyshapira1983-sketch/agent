# Tests: Approval Token System — formal_contracts_spec §7-§8, §12 (Approval)
# Covers: signed tokens, single-use, expiry, action hash binding, subject binding

import time
import threading
import unittest

from safety.approval_tokens import (
    ApprovalService, ApprovalRequest, ApprovalToken,
    TokenExpiredError, TokenReusedError, TokenSignatureError, TokenMismatchError,
    compute_action_hash,
)
from safety.human_approval import HumanApprovalLayer


class TestComputeActionHash(unittest.TestCase):

    def test_deterministic(self):
        h1 = compute_action_hash('delete', {'path': '/tmp/x'})
        h2 = compute_action_hash('delete', {'path': '/tmp/x'})
        self.assertEqual(h1, h2)

    def test_starts_with_sha256(self):
        h = compute_action_hash('run', {'cmd': 'ls'})
        self.assertTrue(h.startswith('sha256:'))

    def test_different_params_different_hash(self):
        h1 = compute_action_hash('delete', {'path': '/a'})
        h2 = compute_action_hash('delete', {'path': '/b'})
        self.assertNotEqual(h1, h2)

    def test_key_order_irrelevant(self):
        h1 = compute_action_hash('x', {'a': 1, 'b': 2})
        h2 = compute_action_hash('x', {'b': 2, 'a': 1})
        self.assertEqual(h1, h2)


class TestApprovalRequest(unittest.TestCase):

    def setUp(self):
        self.svc = ApprovalService(
            human_approval=HumanApprovalLayer(mode='auto_approve'),
            signing_key='test-secret',
        )

    def test_create_request_fields(self):
        req = self.svc.create_request(
            task_id='tsk_1', step_id='stp_1',
            action_type='delete', parameters={'path': '/x'},
            risk_class='dangerous', summary='Delete /x',
            impact_scope={'resource_type': 'file', 'resource_ids': ['/x']},
            rollback_plan='Restore from backup',
        )
        self.assertTrue(req.approval_id.startswith('appr_'))
        self.assertEqual(req.task_id, 'tsk_1')
        self.assertEqual(req.step_id, 'stp_1')
        self.assertTrue(req.action_hash.startswith('sha256:'))
        self.assertEqual(req.risk_class, 'dangerous')
        self.assertGreater(req.expires_at, time.time())

    def test_to_dict(self):
        req = self.svc.create_request(
            task_id='t', step_id='s',
            action_type='x', parameters={},
        )
        d = req.to_dict()
        self.assertIn('approval_id', d)
        self.assertIn('action_hash', d)


class TestApprovalTokenIssuance(unittest.TestCase):

    def setUp(self):
        self.svc = ApprovalService(
            human_approval=HumanApprovalLayer(mode='auto_approve'),
            signing_key='test-key',
            default_ttl=60.0,
        )

    def test_approved_returns_token(self):
        req = self.svc.create_request(
            task_id='tsk_1', step_id='stp_1',
            action_type='delete', parameters={'path': '/x'},
        )
        token = self.svc.request_and_issue(req, subject='worker-1')
        self.assertIsNotNone(token)
        assert token is not None
        self.assertIsInstance(token, ApprovalToken)
        self.assertEqual(token.task_id, 'tsk_1')
        self.assertEqual(token.step_id, 'stp_1')
        self.assertEqual(token.subject, 'worker-1')
        self.assertTrue(token.single_use)

    def test_rejected_returns_none(self):
        svc = ApprovalService(
            human_approval=HumanApprovalLayer(mode='auto_reject'),
            signing_key='key',
        )
        req = svc.create_request(
            task_id='t', step_id='s',
            action_type='x', parameters={},
        )
        token = svc.request_and_issue(req, subject='w')
        self.assertIsNone(token)

    def test_no_human_approval_fails_closed(self):
        """Без HumanApprovalLayer → fail-closed → None."""
        svc = ApprovalService(human_approval=None, signing_key='key')
        req = svc.create_request(
            task_id='t', step_id='s',
            action_type='x', parameters={},
        )
        token = svc.request_and_issue(req, subject='w')
        self.assertIsNone(token)

    def test_expired_request_returns_none(self):
        req = self.svc.create_request(
            task_id='t', step_id='s',
            action_type='x', parameters={},
            ttl=0.0,  # expires immediately
        )
        time.sleep(0.01)
        token = self.svc.request_and_issue(req, subject='w')
        self.assertIsNone(token)


class TestTokenValidation(unittest.TestCase):
    """Contract tests из formal_contracts_spec §12 → Approval."""

    def setUp(self):
        self.svc = ApprovalService(
            human_approval=HumanApprovalLayer(mode='auto_approve'),
            signing_key='secure-key-42',
            default_ttl=60.0,
        )
        self.action_hash = compute_action_hash('delete', {'path': '/x'})
        req = self.svc.create_request(
            task_id='tsk_1', step_id='stp_1',
            action_type='delete', parameters={'path': '/x'},
        )
        token = self.svc.request_and_issue(req, subject='executor-worker')
        assert token is not None
        self.token = token

    def test_valid_token_accepted(self):
        result = self.svc.validate_token(
            self.token,
            expected_task_id='tsk_1',
            expected_step_id='stp_1',
            expected_action_hash=self.action_hash,
            expected_subject='executor-worker',
        )
        self.assertTrue(result)

    def test_expired_token_rejected(self):
        """§12: expired token rejected."""
        svc = ApprovalService(
            human_approval=HumanApprovalLayer(mode='auto_approve'),
            signing_key='k',
            default_ttl=0.0,  # instant expiry
        )
        req = svc.create_request(
            task_id='t', step_id='s',
            action_type='x', parameters={},
        )
        # Issue before expiry (ttl=0 means expires_at = now, token issued at creation)
        token = svc._issue_token(req, 'w')
        time.sleep(0.01)
        with self.assertRaises(TokenExpiredError):
            svc.validate_token(token, 't', 's',
                               compute_action_hash('x', {}), 'w')

    def test_reused_token_rejected(self):
        """§12: reused token rejected."""
        # First use — valid
        self.svc.validate_token(
            self.token, 'tsk_1', 'stp_1', self.action_hash, 'executor-worker',
        )
        # Second use — rejected
        with self.assertRaises(TokenReusedError):
            self.svc.validate_token(
                self.token, 'tsk_1', 'stp_1', self.action_hash, 'executor-worker',
            )

    def test_action_hash_mismatch_rejected(self):
        """§12: action hash mismatch rejected."""
        wrong_hash = compute_action_hash('different_action', {'other': True})
        with self.assertRaises(TokenMismatchError):
            self.svc.validate_token(
                self.token, 'tsk_1', 'stp_1', wrong_hash, 'executor-worker',
            )

    def test_wrong_subject_rejected(self):
        """Token не переносим между workers."""
        with self.assertRaises(TokenMismatchError):
            self.svc.validate_token(
                self.token, 'tsk_1', 'stp_1', self.action_hash, 'other-worker',
            )

    def test_wrong_task_id_rejected(self):
        with self.assertRaises(TokenMismatchError):
            self.svc.validate_token(
                self.token, 'tsk_999', 'stp_1', self.action_hash, 'executor-worker',
            )

    def test_wrong_step_id_rejected(self):
        with self.assertRaises(TokenMismatchError):
            self.svc.validate_token(
                self.token, 'tsk_1', 'stp_999', self.action_hash, 'executor-worker',
            )

    def test_tampered_signature_rejected(self):
        """Подделанная подпись отклоняется."""
        tampered = ApprovalToken(
            token='forged_signature_abc123',
            approval_id=self.token.approval_id,
            task_id=self.token.task_id,
            step_id=self.token.step_id,
            action_hash=self.token.action_hash,
            subject=self.token.subject,
            expires_at=self.token.expires_at,
        )
        with self.assertRaises(TokenSignatureError):
            self.svc.validate_token(
                tampered, 'tsk_1', 'stp_1', self.action_hash, 'executor-worker',
            )


class TestApprovalServiceConcurrency(unittest.TestCase):

    def test_concurrent_validation_single_use(self):
        """Only one thread should successfully validate a single-use token."""
        svc = ApprovalService(
            human_approval=HumanApprovalLayer(mode='auto_approve'),
            signing_key='key',
            default_ttl=60.0,
        )
        action_hash = compute_action_hash('act', {'p': 1})
        req = svc.create_request(
            task_id='t', step_id='s',
            action_type='act', parameters={'p': 1},
        )
        token = svc.request_and_issue(req, subject='w')
        assert token is not None

        successes = []
        failures = []
        lock = threading.Lock()

        def try_validate():
            try:
                svc.validate_token(token, 't', 's', action_hash, 'w')
                with lock:
                    successes.append(True)
            except TokenReusedError:
                with lock:
                    failures.append(True)

        threads = [threading.Thread(target=try_validate) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 19)


class TestApprovalServiceHelpers(unittest.TestCase):

    def setUp(self):
        self.svc = ApprovalService(
            human_approval=HumanApprovalLayer(mode='auto_approve'),
            signing_key='k',
            default_ttl=60.0,
        )

    def test_pending_requests(self):
        self.svc.create_request(
            task_id='t1', step_id='s1',
            action_type='x', parameters={},
        )
        self.svc.create_request(
            task_id='t2', step_id='s2',
            action_type='y', parameters={},
        )
        pending = self.svc.get_pending_requests()
        self.assertEqual(len(pending), 2)

    def test_cleanup_expired(self):
        self.svc.create_request(
            task_id='t', step_id='s',
            action_type='x', parameters={},
            ttl=0.0,
        )
        time.sleep(0.01)
        removed = self.svc.cleanup_expired()
        self.assertEqual(removed, 1)
        self.assertEqual(len(self.svc.get_pending_requests()), 0)

    def test_is_consumed(self):
        action_hash = compute_action_hash('a', {})
        req = self.svc.create_request(
            task_id='t', step_id='s',
            action_type='a', parameters={},
        )
        token = self.svc.request_and_issue(req, subject='w')
        assert token is not None
        self.assertFalse(self.svc.is_consumed(token))
        self.svc.validate_token(token, 't', 's', action_hash, 'w')
        self.assertTrue(self.svc.is_consumed(token))

    def test_token_to_dict(self):
        req = self.svc.create_request(
            task_id='t', step_id='s',
            action_type='a', parameters={},
        )
        token = self.svc.request_and_issue(req, subject='w')
        assert token is not None
        d = token.to_dict()
        self.assertIn('approval_token', d)
        self.assertIn('single_use', d)
        self.assertTrue(d['single_use'])


if __name__ == '__main__':
    unittest.main()
