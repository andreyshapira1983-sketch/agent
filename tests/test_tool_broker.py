# Tests: Tool Broker — formal_contracts_spec §5-§6, §12 (Tooling)
# Covers: capability matrix, deny-by-default, approval enforcement,
#         request/response contracts, receipt_id, audit logging, prohibited actions

import threading
import time
import unittest

from tools.tool_broker import (
    ToolBroker, ToolRequest, ToolResponse,
    CapabilityDeniedError, ApprovalRequiredError, ProhibitedActionError,
)
from safety.approval_tokens import (
    ApprovalService, ApprovalToken, compute_action_hash,
    TokenMismatchError,
)
from safety.human_approval import HumanApprovalLayer


# ── Minimal ToolLayer stub ────────────────────────────────────────────────────

class FakeTool:
    def __init__(self, name, description=''):
        self.name = name
        self.description = description

    def __call__(self, **kwargs):
        return {'tool': self.name, 'params': kwargs}


class FakeToolLayer:
    """Минимальный stub ToolLayer для тестов."""

    def __init__(self):
        self._tools: dict = {}

    def register(self, tool):
        self._tools[tool.name] = tool

    def get(self, name):
        return self._tools.get(name)

    def use(self, tool_name, **kwargs):
        tool = self._tools.get(tool_name)
        if not tool:
            raise KeyError(f"Tool '{tool_name}' not found")
        return tool(**kwargs)

    def list(self):
        return list(self._tools.keys())

    def describe(self):
        return [{'name': t.name, 'description': t.description}
                for t in self._tools.values()]


def _make_broker(**overrides):
    """Создаёт broker с fake tool layer и базовой матрицей."""
    tl = FakeToolLayer()
    tl.register(FakeTool('search'))
    tl.register(FakeTool('terminal'))
    tl.register(FakeTool('filesystem'))
    tl.register(FakeTool('python_runtime'))

    defaults = {
        'tool_layer': tl,
        'capability_matrix': {
            'researcher': {'search'},
            'coder': {'terminal', 'filesystem', 'python_runtime', 'search'},
            'executor': {'terminal', 'filesystem'},
        },
        'tool_risk': {
            'search': 'safe',
            'filesystem': 'guarded',
            'terminal': 'dangerous',
            'python_runtime': 'guarded',
        },
    }
    defaults.update(overrides)
    return ToolBroker(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# Capability Matrix: deny-by-default
# ═══════════════════════════════════════════════════════════════════════════════

class TestCapabilityMatrix(unittest.TestCase):

    def test_allowed_tool_succeeds(self):
        broker = _make_broker()
        resp = broker.request(
            task_id='t1', step_id='s1', worker_id='researcher',
            tool_name='search', parameters={'query': 'test'},
        )
        self.assertEqual(resp.status, 'ok')
        self.assertEqual(resp.structured_result['tool'], 'search')

    def test_denied_tool_raises(self):
        """Deny-by-default: researcher не имеет доступа к terminal."""
        broker = _make_broker()
        with self.assertRaises(CapabilityDeniedError):
            broker.request(
                task_id='t1', step_id='s1', worker_id='researcher',
                tool_name='terminal',
            )

    def test_unknown_worker_denied(self):
        """Deny-by-default: неизвестный worker отклоняется."""
        broker = _make_broker()
        with self.assertRaises(CapabilityDeniedError):
            broker.request(
                task_id='t1', step_id='s1', worker_id='unknown_worker',
                tool_name='search',
            )

    def test_grant_capability(self):
        broker = _make_broker()
        broker.grant_capability('researcher', 'filesystem')
        resp = broker.request(
            task_id='t1', step_id='s1', worker_id='researcher',
            tool_name='filesystem',
        )
        self.assertEqual(resp.status, 'ok')

    def test_revoke_capability(self):
        broker = _make_broker()
        broker.revoke_capability('researcher', 'search')
        with self.assertRaises(CapabilityDeniedError):
            broker.request(
                task_id='t1', step_id='s1', worker_id='researcher',
                tool_name='search',
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Prohibited actions
# ═══════════════════════════════════════════════════════════════════════════════

class TestProhibitedActions(unittest.TestCase):

    def test_prohibited_tool_rejected(self):
        """§12 Tooling: forbidden tool/action combination rejected."""
        broker = _make_broker()
        broker.grant_capability('coder', 'evil_tool')
        broker.set_tool_risk('evil_tool', 'prohibited')
        with self.assertRaises(ProhibitedActionError):
            broker.request(
                task_id='t1', step_id='s1', worker_id='coder',
                tool_name='evil_tool',
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Approval enforcement for dangerous tools
# ═══════════════════════════════════════════════════════════════════════════════

class TestApprovalEnforcement(unittest.TestCase):

    def test_dangerous_without_token_rejected(self):
        """Dangerous tool без approval token → ApprovalRequiredError."""
        broker = _make_broker()
        with self.assertRaises(ApprovalRequiredError):
            broker.request(
                task_id='t1', step_id='s1', worker_id='coder',
                tool_name='terminal',  # risk=dangerous
            )

    def test_dangerous_with_valid_token_succeeds(self):
        """Dangerous tool с valid approval token → success."""
        svc = ApprovalService(
            human_approval=HumanApprovalLayer(mode='auto_approve'),
            signing_key='broker-test-key',
            default_ttl=60.0,
        )
        broker = _make_broker(approval_service=svc)

        req = svc.create_request(
            task_id='t1', step_id='s1',
            action_type='terminal', parameters={'cmd': 'ls'},
        )
        token = svc.request_and_issue(req, subject='coder')

        resp = broker.request(
            task_id='t1', step_id='s1', worker_id='coder',
            tool_name='terminal',
            action='terminal',
            parameters={'cmd': 'ls'},
            approval_token=token,
        )
        self.assertEqual(resp.status, 'ok')

    def test_safe_tool_no_token_needed(self):
        """Safe tool не требует approval token."""
        broker = _make_broker()
        resp = broker.request(
            task_id='t1', step_id='s1', worker_id='researcher',
            tool_name='search', parameters={'query': 'test'},
        )
        self.assertEqual(resp.status, 'ok')

    def test_guarded_tool_no_token_needed(self):
        """Guarded tool не требует approval token."""
        broker = _make_broker()
        resp = broker.request(
            task_id='t1', step_id='s1', worker_id='coder',
            tool_name='filesystem', parameters={'path': '/tmp'},
        )
        self.assertEqual(resp.status, 'ok')


# ═══════════════════════════════════════════════════════════════════════════════
# Request/Response contracts
# ═══════════════════════════════════════════════════════════════════════════════

class TestRequestResponseContract(unittest.TestCase):

    def test_response_has_receipt_id(self):
        """§12 Tooling: receipt always generated."""
        broker = _make_broker()
        resp = broker.request(
            task_id='t1', step_id='s1', worker_id='researcher',
            tool_name='search', parameters={'query': 'test'},
        )
        self.assertTrue(resp.receipt_id.startswith('rcpt_'))

    def test_response_has_timing(self):
        broker = _make_broker()
        resp = broker.request(
            task_id='t1', step_id='s1', worker_id='researcher',
            tool_name='search',
        )
        self.assertGreaterEqual(resp.timing_ms, 0)

    def test_error_response_has_receipt(self):
        """Даже при ошибке tool → receipt_id генерируется."""
        tl = FakeToolLayer()
        tl.register(FakeTool('search'))

        class FailTool:
            name = 'fail_tool'
            description = ''
            def __call__(self, **kw):
                raise ValueError("tool crashed")

        tl.register(FailTool())
        broker = ToolBroker(
            tool_layer=tl,
            capability_matrix={'w': {'fail_tool'}},
            tool_risk={'fail_tool': 'safe'},
        )
        resp = broker.request(
            task_id='t', step_id='s', worker_id='w',
            tool_name='fail_tool',
        )
        self.assertEqual(resp.status, 'error')
        self.assertTrue(resp.receipt_id.startswith('rcpt_'))
        self.assertIsNotNone(resp.error)
        self.assertIn('ValueError', resp.error)  # type: ignore[arg-type]

    def test_request_to_dict(self):
        req = ToolRequest(
            request_id='r1', task_id='t1', step_id='s1',
            worker_id='w1', tool_name='search', action='query',
            parameters={'q': 'x'}, risk_class='safe',
            approval_token=None, capability_scope='search:read',
        )
        d = req.to_dict()
        self.assertEqual(d['request_id'], 'r1')
        self.assertIsNone(d['approval_token'])

    def test_response_to_dict(self):
        resp = ToolResponse(
            request_id='r1', task_id='t1', step_id='s1',
            status='ok', receipt_id='rcpt_1',
            structured_result={'data': 1}, timing_ms=42.5,
        )
        d = resp.to_dict()
        self.assertEqual(d['receipt_id'], 'rcpt_1')
        self.assertIsNone(d['error'])


# ═══════════════════════════════════════════════════════════════════════════════
# Audit logging
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditLogging(unittest.TestCase):

    def test_allow_logged(self):
        """§12 Audit: allow path emits event."""
        audit = []
        broker = _make_broker(audit_journal=audit)
        broker.request(
            task_id='t1', step_id='s1', worker_id='researcher',
            tool_name='search',
        )
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]['event_type'], 'TOOL_CALL_OK')
        self.assertEqual(audit[0]['actor']['id'], 'researcher')

    def test_deny_logged(self):
        """§12 Audit: deny path emits event."""
        audit = []
        broker = _make_broker(audit_journal=audit)
        try:
            broker.request(
                task_id='t1', step_id='s1', worker_id='researcher',
                tool_name='terminal',
            )
        except CapabilityDeniedError:
            pass
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]['event_type'], 'TOOL_CALL_DENIED')


# ═══════════════════════════════════════════════════════════════════════════════
# Simplified use() proxy
# ═══════════════════════════════════════════════════════════════════════════════

class TestUseProxy(unittest.TestCase):

    def test_use_basic(self):
        """use() proxy works like ToolLayer.use() for allowed tools."""
        broker = _make_broker()
        result = broker.use(
            'search',
            worker_id='researcher',
            query='test',
        )
        self.assertEqual(result['tool'], 'search')

    def test_use_denied(self):
        broker = _make_broker()
        with self.assertRaises(CapabilityDeniedError):
            broker.use('terminal', worker_id='researcher')

    def test_use_dangerous_auto_issues_token(self):
        svc = ApprovalService(
            human_approval=HumanApprovalLayer(mode='auto_approve'),
            signing_key='broker-test-key',
            default_ttl=60.0,
        )
        broker = _make_broker(approval_service=svc)
        result = broker.use(
            'terminal',
            worker_id='coder',
            command='echo hello',
        )
        self.assertEqual(result['tool'], 'terminal')


# ═══════════════════════════════════════════════════════════════════════════════
# Statistics & helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrokerStats(unittest.TestCase):

    def test_stats_tracking(self):
        broker = _make_broker()
        broker.request(
            task_id='t', step_id='s', worker_id='researcher',
            tool_name='search',
        )
        try:
            broker.request(
                task_id='t', step_id='s', worker_id='researcher',
                tool_name='terminal',
            )
        except CapabilityDeniedError:
            pass

        stats = broker.get_stats()
        self.assertEqual(stats['total'], 2)
        self.assertEqual(stats['allowed'], 1)
        self.assertEqual(stats['denied'], 1)

    def test_passthrough_list_describe(self):
        broker = _make_broker()
        tools = broker.list()
        self.assertIn('search', tools)
        descs = broker.describe()
        self.assertTrue(any(d['name'] == 'search' for d in descs))


# ═══════════════════════════════════════════════════════════════════════════════
# Concurrency
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrokerConcurrency(unittest.TestCase):

    def test_concurrent_requests(self):
        """Multiple concurrent requests should all be tracked correctly."""
        broker = _make_broker()
        errors = []

        def call_search():
            try:
                broker.request(
                    task_id='t', step_id='s', worker_id='researcher',
                    tool_name='search', parameters={'q': 'x'},
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=call_search) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        stats = broker.get_stats()
        self.assertEqual(stats['total'], 50)
        self.assertEqual(stats['allowed'], 50)


if __name__ == '__main__':
    unittest.main()
