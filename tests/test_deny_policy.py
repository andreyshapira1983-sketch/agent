# Tests: DenyByDefaultPolicy — PolicyEnforcedToolLayer
# formal_contracts_spec §5-§6, §12
# Covers: proxy routing, capability enforcement, deny logging,
#         fallback behaviour, worker_id context, __getattr__ proxy

import unittest
from unittest.mock import MagicMock, patch

from safety.deny_policy import PolicyEnforcedToolLayer, _is_broker_error
from tools.tool_broker import (
    ToolBroker, CapabilityDeniedError, ApprovalRequiredError,
    ProhibitedActionError, BrokerError,
)


def _make_tool_layer():
    """Создаёт mock ToolLayer с .use() и .get()."""
    tl = MagicMock()
    tl.use.return_value = {'success': True, 'output': 'hello'}
    tl.working_dir = '/tmp'
    return tl


def _make_broker(tool_layer, **kwargs):
    """Создаёт реальный ToolBroker с указанным ToolLayer."""
    return ToolBroker(tool_layer=tool_layer, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════════
# PolicyEnforcedToolLayer — transparent proxy
# ═══════════════════════════════════════════════════════════════════════════════

class TestProxyWithoutBroker(unittest.TestCase):
    """Без broker → прямой проброс к ToolLayer."""

    def test_use_forwards_to_tool_layer(self):
        tl = _make_tool_layer()
        proxy = PolicyEnforcedToolLayer(tl, broker=None)
        result = proxy.use('search', query='test')
        tl.use.assert_called_once_with('search', query='test')
        self.assertEqual(result, {'success': True, 'output': 'hello'})

    def test_getattr_proxy(self):
        tl = _make_tool_layer()
        proxy = PolicyEnforcedToolLayer(tl, broker=None)
        self.assertEqual(proxy.working_dir, '/tmp')

    def test_stats_track_total(self):
        tl = _make_tool_layer()
        proxy = PolicyEnforcedToolLayer(tl, broker=None)
        proxy.use('search', query='a')
        proxy.use('search', query='b')
        s = proxy.get_stats()
        self.assertEqual(s['total'], 2)


# ═══════════════════════════════════════════════════════════════════════════════
# With ToolBroker — capability enforcement
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnforcedAllowed(unittest.TestCase):
    """Worker имеет capability → вызов проходит."""

    def test_allowed_tool(self):
        tl = _make_tool_layer()
        broker = _make_broker(tl)
        proxy = PolicyEnforcedToolLayer(tl, broker=broker, worker_id='researcher')
        result = proxy.use('search', query='test')
        self.assertEqual(result, {'success': True, 'output': 'hello'})

    def test_stats_enforced(self):
        tl = _make_tool_layer()
        broker = _make_broker(tl)
        proxy = PolicyEnforcedToolLayer(tl, broker=broker, worker_id='researcher')
        proxy.use('search', query='q')
        s = proxy.get_stats()
        self.assertEqual(s['enforced'], 1)
        self.assertEqual(s['denied'], 0)


class TestEnforcedDenied(unittest.TestCase):
    """Worker НЕ имеет capability → CapabilityDeniedError."""

    def test_denied_raises(self):
        tl = _make_tool_layer()
        broker = _make_broker(tl)
        proxy = PolicyEnforcedToolLayer(tl, broker=broker, worker_id='researcher')
        with self.assertRaises(CapabilityDeniedError):
            proxy.use('terminal', command='ls')

    def test_denied_stats(self):
        tl = _make_tool_layer()
        broker = _make_broker(tl)
        proxy = PolicyEnforcedToolLayer(tl, broker=broker, worker_id='researcher')
        try:
            proxy.use('terminal', command='ls')
        except CapabilityDeniedError:
            pass
        s = proxy.get_stats()
        self.assertEqual(s['denied'], 1)
        self.assertEqual(s['enforced'], 0)

    def test_denied_does_not_call_tool_layer(self):
        tl = _make_tool_layer()
        broker = _make_broker(tl)
        proxy = PolicyEnforcedToolLayer(tl, broker=broker, worker_id='planner')
        try:
            proxy.use('terminal', command='rm -rf /')
        except CapabilityDeniedError:
            pass
        # tool_layer.use не должен вызываться при deny
        tl.use.assert_not_called()


class TestApprovalRequired(unittest.TestCase):
    """Dangerous tool без approval → ApprovalRequiredError."""

    def test_dangerous_without_token(self):
        tl = _make_tool_layer()
        broker = _make_broker(tl)
        # 'executor' has 'terminal' in capabilities, но terminal=dangerous
        proxy = PolicyEnforcedToolLayer(tl, broker=broker, worker_id='executor')
        with self.assertRaises(ApprovalRequiredError):
            proxy.use('terminal', command='echo hi')


class TestProhibitedTool(unittest.TestCase):
    """Prohibited tool → ProhibitedActionError."""

    def test_prohibited_raises(self):
        tl = _make_tool_layer()
        broker = _make_broker(
            tl,
            capability_matrix={'tester': {'nuke'}},
            tool_risk={'nuke': 'prohibited'},
        )
        proxy = PolicyEnforcedToolLayer(tl, broker=broker, worker_id='tester')
        with self.assertRaises(ProhibitedActionError):
            proxy.use('nuke')


# ═══════════════════════════════════════════════════════════════════════════════
# Fallback mode
# ═══════════════════════════════════════════════════════════════════════════════

class TestFallbackMode(unittest.TestCase):
    """fallback_on_broker_error=True → при deny используем ToolLayer."""

    def test_fallback_on_deny(self):
        tl = _make_tool_layer()
        broker = _make_broker(tl)
        proxy = PolicyEnforcedToolLayer(
            tl, broker=broker, worker_id='researcher',
            fallback_on_broker_error=True,
        )
        # researcher не имеет 'terminal', но fallback включён
        result = proxy.use('terminal', command='whoami')
        self.assertEqual(result, {'success': True, 'output': 'hello'})
        s = proxy.get_stats()
        self.assertEqual(s['fallback'], 1)
        self.assertEqual(s['denied'], 1)

    def test_no_fallback_raises(self):
        tl = _make_tool_layer()
        broker = _make_broker(tl)
        proxy = PolicyEnforcedToolLayer(
            tl, broker=broker, worker_id='researcher',
            fallback_on_broker_error=False,
        )
        with self.assertRaises(CapabilityDeniedError):
            proxy.use('terminal', command='whoami')


# ═══════════════════════════════════════════════════════════════════════════════
# Worker ID context
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkerIdContext(unittest.TestCase):

    def test_set_worker_id(self):
        tl = _make_tool_layer()
        broker = _make_broker(tl)
        proxy = PolicyEnforcedToolLayer(tl, broker=broker, worker_id='planner')
        self.assertEqual(proxy.worker_id, 'planner')
        proxy.set_worker_id('coder')
        self.assertEqual(proxy.worker_id, 'coder')

    def test_different_worker_different_access(self):
        tl = _make_tool_layer()
        broker = _make_broker(tl)
        proxy = PolicyEnforcedToolLayer(tl, broker=broker, worker_id='researcher')
        # researcher может search
        proxy.use('search', query='test')
        # researcher не может filesystem
        with self.assertRaises(CapabilityDeniedError):
            proxy.use('filesystem', action='read', path='/etc/passwd')

        # Меняем на coder — может filesystem
        proxy.set_worker_id('coder')
        proxy.use('filesystem', action='read', path='/etc/passwd')


# ═══════════════════════════════════════════════════════════════════════════════
# _is_broker_error helper
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsBrokerError(unittest.TestCase):

    def test_broker_error(self):
        self.assertTrue(_is_broker_error(BrokerError('x')))

    def test_capability_denied(self):
        self.assertTrue(_is_broker_error(CapabilityDeniedError('x')))

    def test_approval_required(self):
        self.assertTrue(_is_broker_error(ApprovalRequiredError('x')))

    def test_prohibited(self):
        self.assertTrue(_is_broker_error(ProhibitedActionError('x')))

    def test_runtime_error_not_broker(self):
        self.assertFalse(_is_broker_error(RuntimeError('x')))

    def test_value_error_not_broker(self):
        self.assertFalse(_is_broker_error(ValueError('x')))


# ═══════════════════════════════════════════════════════════════════════════════
# Audit — deny events logged
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditDenyLogged(unittest.TestCase):
    """§12: deny events логируются так же обязательно, как allow events."""

    def test_deny_logged_in_audit(self):
        tl = _make_tool_layer()
        journal = MagicMock()
        broker = _make_broker(tl, audit_journal=journal)
        proxy = PolicyEnforcedToolLayer(tl, broker=broker, worker_id='researcher')
        try:
            proxy.use('terminal', command='ls')
        except CapabilityDeniedError:
            pass
        # ToolBroker вызывает _deny → _audit_event → journal.log
        journal.log.assert_called()
        event = journal.log.call_args[0][0]
        self.assertEqual(event['event_type'], 'TOOL_CALL_DENIED')

    def test_allow_logged_in_audit(self):
        tl = _make_tool_layer()
        journal = MagicMock()
        broker = _make_broker(tl, audit_journal=journal)
        proxy = PolicyEnforcedToolLayer(tl, broker=broker, worker_id='researcher')
        proxy.use('search', query='test')
        journal.log.assert_called()
        event = journal.log.call_args[0][0]
        self.assertEqual(event['event_type'], 'TOOL_CALL_OK')


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: ToolBroker grant/revoke через proxy
# ═══════════════════════════════════════════════════════════════════════════════

class TestGrantRevoke(unittest.TestCase):

    def test_grant_unlocks_tool(self):
        tl = _make_tool_layer()
        broker = _make_broker(tl)
        proxy = PolicyEnforcedToolLayer(tl, broker=broker, worker_id='planner')
        # planner не имеет terminal
        with self.assertRaises(CapabilityDeniedError):
            proxy.use('terminal', command='echo hi')
        # Grant terminal to planner
        broker.grant_capability('planner', 'terminal')
        # Всё ещё dangerous → ApprovalRequired (но уже не Capability)
        with self.assertRaises(ApprovalRequiredError):
            proxy.use('terminal', command='echo hi')

    def test_revoke_blocks_tool(self):
        tl = _make_tool_layer()
        broker = _make_broker(tl)
        proxy = PolicyEnforcedToolLayer(tl, broker=broker, worker_id='researcher')
        proxy.use('search', query='test')  # should work
        broker.revoke_capability('researcher', 'search')
        with self.assertRaises(CapabilityDeniedError):
            proxy.use('search', query='test')


# ═══════════════════════════════════════════════════════════════════════════════
# Thread safety
# ═══════════════════════════════════════════════════════════════════════════════

class TestConcurrency(unittest.TestCase):

    def test_concurrent_uses(self):
        import threading
        tl = _make_tool_layer()
        broker = _make_broker(tl)
        proxy = PolicyEnforcedToolLayer(tl, broker=broker, worker_id='researcher')
        errors = []

        def run_search(i):
            try:
                proxy.use('search', query=f'query_{i}')
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=run_search, args=(i,))
                   for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(proxy.get_stats()['total'], 20)
        self.assertEqual(proxy.get_stats()['enforced'], 20)


if __name__ == '__main__':
    unittest.main()
