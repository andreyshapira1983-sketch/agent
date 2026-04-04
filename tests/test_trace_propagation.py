# Tests: Structured audit trace_id propagation — formal_contracts_spec §10, §2
# Covers: TraceContext, StructuredAuditEvent, Actor, build_event,
#         AuditJournal.log(), get_events_by_trace_id(),
#         ToolBroker trace_id propagation and audit event emission

import json
import os
import tempfile
import unittest
from pathlib import Path

from evaluation.trace_context import (
    TraceContext,
    StructuredAuditEvent,
    Actor,
    build_event,
    generate_trace_id,
    generate_event_id,
)
from evaluation.audit_journal import AuditJournal
from tools.tool_broker import (
    ToolBroker, ToolRequest, ToolResponse,
    CapabilityDeniedError,
)


# ── Minimal stubs ─────────────────────────────────────────────────────────────

class FakeTool:
    def __init__(self, name, description=''):
        self.name = name
        self.description = description

    def __call__(self, **kwargs):
        return {'tool': self.name, 'params': kwargs}


class FakeToolLayer:
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


# ═══════════════════════════════════════════════════════════════════════════════
# TraceContext
# ═══════════════════════════════════════════════════════════════════════════════

class TestTraceContext(unittest.TestCase):

    def test_auto_generate_trace_id(self):
        ctx = TraceContext()
        self.assertTrue(ctx.trace_id.startswith('trc_'))
        self.assertEqual(len(ctx.trace_id), 16)  # trc_ + 12 hex

    def test_explicit_trace_id(self):
        ctx = TraceContext(trace_id='trc_custom12345')
        self.assertEqual(ctx.trace_id, 'trc_custom12345')

    def test_child_inherits_trace_id(self):
        parent = TraceContext(task_id='task_1')
        child = parent.child(task_id='task_2')
        self.assertEqual(child.trace_id, parent.trace_id)
        self.assertEqual(child.task_id, 'task_2')

    def test_child_inherits_task_id_if_not_overridden(self):
        parent = TraceContext(task_id='task_1')
        child = parent.child()
        self.assertEqual(child.task_id, 'task_1')

    def test_event_factory(self):
        ctx = TraceContext(task_id='t1')
        actor = Actor(type='worker', id='coder')
        evt = ctx.event(step_id='s1', actor=actor,
                        event_type='TOOL_CALL_OK', details={'key': 'val'})
        self.assertEqual(evt.trace_id, ctx.trace_id)
        self.assertEqual(evt.task_id, 't1')
        self.assertEqual(evt.step_id, 's1')
        self.assertEqual(evt.event_type, 'TOOL_CALL_OK')
        self.assertTrue(evt.event_id.startswith('evt_'))
        self.assertIn('key', evt.details)

    def test_elapsed_ms(self):
        ctx = TraceContext()
        self.assertGreaterEqual(ctx.elapsed_ms(), 0)

    def test_repr(self):
        ctx = TraceContext(trace_id='trc_abc', task_id='t1')
        r = repr(ctx)
        self.assertIn('trc_abc', r)
        self.assertIn('t1', r)


# ═══════════════════════════════════════════════════════════════════════════════
# StructuredAuditEvent
# ═══════════════════════════════════════════════════════════════════════════════

class TestStructuredAuditEvent(unittest.TestCase):

    def test_to_dict_roundtrip(self):
        actor = Actor(type='worker', id='exec')
        evt = build_event(
            trace_id='trc_123',
            task_id='t1',
            step_id='s1',
            actor=actor,
            event_type='TOOL_CALL_DENIED',
            details={'reason': 'capability_denied'},
        )
        d = evt.to_dict()
        self.assertEqual(d['trace_id'], 'trc_123')
        self.assertEqual(d['task_id'], 't1')
        self.assertEqual(d['actor']['type'], 'worker')
        self.assertIn('timestamp', d)

        # roundtrip
        restored = StructuredAuditEvent.from_dict(d)
        self.assertEqual(restored.trace_id, 'trc_123')
        self.assertEqual(restored.actor.id, 'exec')

    def test_build_event_autogenerates_ids(self):
        evt = build_event(
            trace_id='trc_x',
            task_id='t1',
            step_id='s1',
            actor=Actor(type='system', id='loop'),
            event_type='PHASE_START',
        )
        self.assertTrue(evt.event_id.startswith('evt_'))
        self.assertNotEqual(evt.timestamp, '')


class TestActor(unittest.TestCase):

    def test_to_dict(self):
        a = Actor(type='human', id='admin')
        self.assertEqual(a.to_dict(), {'type': 'human', 'id': 'admin'})

    def test_from_dict_defaults(self):
        a = Actor.from_dict({})
        self.assertEqual(a.type, 'system')
        self.assertEqual(a.id, 'unknown')


# ═══════════════════════════════════════════════════════════════════════════════
# generate_* helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestGenerators(unittest.TestCase):

    def test_trace_id_format(self):
        tid = generate_trace_id()
        self.assertTrue(tid.startswith('trc_'))
        self.assertEqual(len(tid), 16)

    def test_event_id_format(self):
        eid = generate_event_id()
        self.assertTrue(eid.startswith('evt_'))
        self.assertEqual(len(eid), 16)

    def test_ids_unique(self):
        ids = {generate_trace_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)


# ═══════════════════════════════════════════════════════════════════════════════
# AuditJournal.log() — structured events
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditJournalLog(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.journal = AuditJournal(memory_dir=self._tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_log_dict_event(self):
        self.journal.log({
            'event_id': 'evt_1',
            'trace_id': 'trc_abc',
            'event_type': 'TEST',
        })
        events = self.journal.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['trace_id'], 'trc_abc')

    def test_log_structured_event(self):
        evt = build_event(
            trace_id='trc_xyz',
            task_id='t1',
            step_id='s1',
            actor=Actor(type='worker', id='coder'),
            event_type='TOOL_CALL_OK',
        )
        self.journal.log(evt)
        events = self.journal.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['trace_id'], 'trc_xyz')
        self.assertEqual(events[0]['event_type'], 'TOOL_CALL_OK')

    def test_get_events_by_trace_id(self):
        self.journal.log({'trace_id': 'trc_a', 'event_type': 'E1'})
        self.journal.log({'trace_id': 'trc_b', 'event_type': 'E2'})
        self.journal.log({'trace_id': 'trc_a', 'event_type': 'E3'})

        result = self.journal.get_events_by_trace_id('trc_a')
        self.assertEqual(len(result), 2)
        types = [e['event_type'] for e in result]
        self.assertIn('E1', types)
        self.assertIn('E3', types)

    def test_get_events_by_trace_id_empty(self):
        result = self.journal.get_events_by_trace_id('trc_nonexistent')
        self.assertEqual(result, [])

    def test_log_ignores_unsupported_types(self):
        self.journal.log(42)
        self.journal.log(None)
        events = self.journal.get_events()
        self.assertEqual(len(events), 0)

    def test_events_persisted_to_jsonl(self):
        self.journal.log({'trace_id': 'trc_p', 'event_type': 'P1'})
        path = Path(self._tmpdir) / 'audit_events.jsonl'
        self.assertTrue(path.exists())
        lines = path.read_text(encoding='utf-8').strip().split('\n')
        self.assertEqual(len(lines), 1)
        data = json.loads(lines[0])
        self.assertEqual(data['trace_id'], 'trc_p')

    def test_multiple_events_append(self):
        for i in range(5):
            self.journal.log({'trace_id': f'trc_{i}', 'event_type': f'E{i}'})
        events = self.journal.get_events()
        self.assertEqual(len(events), 5)

    def test_get_events_max_count(self):
        for i in range(10):
            self.journal.log({'trace_id': 'trc_x', 'n': i})
        events = self.journal.get_events(max_count=3)
        self.assertEqual(len(events), 3)
        # Последние 3
        self.assertEqual(events[0]['n'], 7)


# ═══════════════════════════════════════════════════════════════════════════════
# ToolBroker trace_id propagation
# ═══════════════════════════════════════════════════════════════════════════════

def _make_broker_with_journal(**overrides):
    tl = FakeToolLayer()
    tl.register(FakeTool('search'))
    tl.register(FakeTool('terminal'))

    tmpdir = tempfile.mkdtemp()
    journal = AuditJournal(memory_dir=tmpdir)

    defaults = {
        'tool_layer': tl,
        'capability_matrix': {
            'researcher': {'search'},
            'coder': {'terminal', 'search'},
        },
        'tool_risk': {
            'search': 'safe',
            'terminal': 'dangerous',
        },
        'audit_journal': journal,
    }
    defaults.update(overrides)
    broker = ToolBroker(**defaults)
    return broker, journal, tmpdir


class TestBrokerTraceIdPropagation(unittest.TestCase):

    def setUp(self):
        self.broker, self.journal, self._tmpdir = _make_broker_with_journal()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_trace_id_in_response(self):
        resp = self.broker.request(
            task_id='t1', step_id='s1', worker_id='researcher',
            tool_name='search', trace_id='trc_test123456',
        )
        self.assertEqual(resp.trace_id, 'trc_test123456')

    def test_trace_id_in_audit_event(self):
        self.broker.request(
            task_id='t1', step_id='s1', worker_id='researcher',
            tool_name='search', trace_id='trc_audit12345',
        )
        events = self.journal.get_events_by_trace_id('trc_audit12345')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['event_type'], 'TOOL_CALL_OK')
        self.assertEqual(events[0]['trace_id'], 'trc_audit12345')

    def test_deny_event_has_trace_id(self):
        try:
            self.broker.request(
                task_id='t1', step_id='s1', worker_id='researcher',
                tool_name='terminal', trace_id='trc_deny123456',
            )
        except CapabilityDeniedError:
            pass
        events = self.journal.get_events_by_trace_id('trc_deny123456')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['event_type'], 'TOOL_CALL_DENIED')

    def test_audit_event_has_actor(self):
        self.broker.request(
            task_id='t1', step_id='s1', worker_id='researcher',
            tool_name='search', trace_id='trc_actor12345',
        )
        events = self.journal.get_events_by_trace_id('trc_actor12345')
        self.assertEqual(len(events), 1)
        actor = events[0].get('actor', {})
        self.assertEqual(actor['type'], 'worker')
        self.assertEqual(actor['id'], 'researcher')

    def test_audit_event_has_event_id(self):
        self.broker.request(
            task_id='t1', step_id='s1', worker_id='researcher',
            tool_name='search', trace_id='trc_evtid12345',
        )
        events = self.journal.get_events_by_trace_id('trc_evtid12345')
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]['event_id'].startswith('evt_'))

    def test_trace_correlation_across_multiple_calls(self):
        """Два вызова с одним trace_id коррелируются."""
        tid = 'trc_corr1234567'
        self.broker.request(
            task_id='t1', step_id='s1', worker_id='researcher',
            tool_name='search', trace_id=tid,
        )
        self.broker.request(
            task_id='t1', step_id='s2', worker_id='researcher',
            tool_name='search', trace_id=tid,
        )
        events = self.journal.get_events_by_trace_id(tid)
        self.assertEqual(len(events), 2)
        step_ids = {e['step_id'] for e in events}
        self.assertEqual(step_ids, {'s1', 's2'})

    def test_empty_trace_id_default(self):
        """Без trace_id — ответ содержит пустую строку (обратная совместимость)."""
        resp = self.broker.request(
            task_id='t1', step_id='s1', worker_id='researcher',
            tool_name='search',
        )
        self.assertEqual(resp.trace_id, '')


# ═══════════════════════════════════════════════════════════════════════════════
# ToolRequest / ToolResponse trace_id in to_dict
# ═══════════════════════════════════════════════════════════════════════════════

class TestRequestResponseTraceId(unittest.TestCase):

    def test_request_to_dict_has_trace_id(self):
        req = ToolRequest(
            request_id='r1', task_id='t1', step_id='s1',
            worker_id='w1', tool_name='search', action='query',
            parameters={'q': 'x'}, risk_class='safe',
            approval_token=None, capability_scope='search:read',
            trace_id='trc_req12345678',
        )
        d = req.to_dict()
        self.assertEqual(d['trace_id'], 'trc_req12345678')

    def test_response_to_dict_has_trace_id(self):
        resp = ToolResponse(
            request_id='r1', task_id='t1', step_id='s1',
            status='ok', receipt_id='rcpt_1',
            structured_result={'data': 1}, timing_ms=42.5,
            trace_id='trc_resp1234567',
        )
        d = resp.to_dict()
        self.assertEqual(d['trace_id'], 'trc_resp1234567')


# ═══════════════════════════════════════════════════════════════════════════════
# TraceContext → ToolBroker integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestTraceContextBrokerIntegration(unittest.TestCase):

    def setUp(self):
        self.broker, self.journal, self._tmpdir = _make_broker_with_journal()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_trace_context_drives_broker(self):
        """TraceContext.trace_id → broker.request(trace_id=...) → audit event."""
        ctx = TraceContext(task_id='t1')
        resp = self.broker.request(
            task_id=ctx.task_id, step_id='s1', worker_id='researcher',
            tool_name='search', trace_id=ctx.trace_id,
        )
        self.assertEqual(resp.trace_id, ctx.trace_id)

        events = self.journal.get_events_by_trace_id(ctx.trace_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['task_id'], 't1')

    def test_child_context_same_trace(self):
        """Child context сохраняет trace_id → события коррелируются."""
        parent = TraceContext(task_id='t1')
        child = parent.child(task_id='t2')

        self.broker.request(
            task_id=parent.task_id, step_id='s1', worker_id='researcher',
            tool_name='search', trace_id=parent.trace_id,
        )
        self.broker.request(
            task_id=child.task_id, step_id='s2', worker_id='researcher',
            tool_name='search', trace_id=child.trace_id,
        )

        events = self.journal.get_events_by_trace_id(parent.trace_id)
        self.assertEqual(len(events), 2)
        task_ids = {e['task_id'] for e in events}
        self.assertEqual(task_ids, {'t1', 't2'})


if __name__ == '__main__':
    unittest.main()
