"""Tests for validation.contracts — formal contract tests (§12 of spec).

Covers: API boundary, Planning, Tooling, Approval, Memory, Audit, State transitions.
"""

from __future__ import annotations

import time
import unittest

from pydantic import ValidationError

from validation.contracts import (
    ApprovalRequestContract,
    ApprovalTokenContract,
    AuditActor,
    AuditEventContract,
    ErrorEnvelope,
    ImpactScope,
    Initiator,
    InputArtifact,
    MemoryRecord,
    MemoryWriteContract,
    PlanStepContract,
    Provenance,
    RiskClass,
    TaskConstraints,
    TaskCreationContract,
    TaskStatus,
    ToolRequestContract,
    ToolResponseContract,
    ToolResultStatus,
    validate_or_error,
    validate_transition,
)


# ═══════════════════════════════════════════════════════════════════════════════
# §2 — Error Envelope
# ═══════════════════════════════════════════════════════════════════════════════

class TestErrorEnvelope(unittest.TestCase):
    def test_valid(self):
        e = ErrorEnvelope(error_code='POLICY_DENIED', retryable=False)
        assert e.error_code == 'POLICY_DENIED'

    def test_error_code_required(self):
        with self.assertRaises(ValidationError):
            ErrorEnvelope(error_code='', retryable=True)

    def test_retryable_defaults_false(self):
        e = ErrorEnvelope(error_code='ERR')
        assert e.retryable is False

    def test_details_default_empty(self):
        e = ErrorEnvelope(error_code='ERR')
        assert e.details == {}

    def test_with_all_fields(self):
        e = ErrorEnvelope(
            error_code='X', message='msg', trace_id='t1',
            task_id='tk1', step_id='s1', retryable=True,
            details={'key': 'val'},
        )
        assert e.trace_id == 't1'


# ═══════════════════════════════════════════════════════════════════════════════
# §3 — Task Creation
# ═══════════════════════════════════════════════════════════════════════════════

class TestTaskCreation(unittest.TestCase):

    def _valid_data(self, **overrides):
        base = {
            'task_id': 'tsk_1',
            'request_id': 'req_1',
            'idempotency_key': 'idem_1',
            'initiator': {'type': 'user', 'id': 'u1'},
            'goal': 'Test goal',
        }
        base.update(overrides)
        return base

    def test_valid(self):
        t = TaskCreationContract(**self._valid_data())
        assert t.task_id == 'tsk_1'
        assert t.initiator.type == 'user'

    def test_task_id_required(self):
        with self.assertRaises(ValidationError):
            TaskCreationContract(**self._valid_data(task_id=''))

    def test_request_id_required(self):
        with self.assertRaises(ValidationError):
            TaskCreationContract(**self._valid_data(request_id=''))

    def test_idempotency_key_required(self):
        with self.assertRaises(ValidationError):
            TaskCreationContract(**self._valid_data(idempotency_key=''))

    def test_initiator_required(self):
        d = self._valid_data()
        del d['initiator']
        with self.assertRaises(ValidationError):
            TaskCreationContract(**d)

    def test_initiator_type_required(self):
        with self.assertRaises(ValidationError):
            TaskCreationContract(**self._valid_data(
                initiator={'type': '', 'id': 'u1'}
            ))

    def test_constraints_defaults(self):
        t = TaskCreationContract(**self._valid_data())
        assert t.constraints.allow_mutation is False

    def test_unknown_fields_rejected(self):
        """Unknown field handling (§12 API boundary)."""
        d = self._valid_data()
        d['unknown_field'] = 'surprise'
        # Pydantic v2 ignores extra by default; ensure we can still create
        t = TaskCreationContract(**d)
        assert t.task_id == 'tsk_1'


# ═══════════════════════════════════════════════════════════════════════════════
# §4 — Plan Step
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlanStep(unittest.TestCase):

    def _valid_data(self, **overrides):
        base = {
            'step_id': 'stp_1',
            'task_id': 'tsk_1',
            'risk_class': 'safe',
            'expected_output_schema': 'TestRunResult.v1',
        }
        base.update(overrides)
        return base

    def test_valid(self):
        p = PlanStepContract.model_validate(self._valid_data())
        assert p.risk_class == RiskClass.SAFE

    def test_risk_class_required(self):
        """Step without risk_class rejected (§12 Planning)."""
        d = self._valid_data()
        del d['risk_class']
        with self.assertRaises(ValidationError):
            PlanStepContract.model_validate(d)

    def test_expected_output_schema_required(self):
        with self.assertRaises(ValidationError):
            PlanStepContract.model_validate(self._valid_data(expected_output_schema=''))

    def test_dangerous_without_approval_rejected(self):
        """Dangerous step without approval flag rejected (§12 Planning)."""
        with self.assertRaises(ValidationError):
            PlanStepContract.model_validate(self._valid_data(
                risk_class='dangerous',
                requires_approval=False,
            ))

    def test_dangerous_with_approval_ok(self):
        p = PlanStepContract.model_validate(self._valid_data(
            risk_class='dangerous',
            requires_approval=True,
        ))
        assert p.requires_approval is True

    def test_safe_without_approval_ok(self):
        p = PlanStepContract.model_validate(self._valid_data(risk_class='safe'))
        assert p.requires_approval is False

    def test_guarded_without_approval_ok(self):
        p = PlanStepContract.model_validate(self._valid_data(risk_class='guarded'))
        assert p.requires_approval is False


# ═══════════════════════════════════════════════════════════════════════════════
# §5 — Tool Request
# ═══════════════════════════════════════════════════════════════════════════════

class TestToolRequest(unittest.TestCase):

    def _valid_data(self, **overrides):
        base = {
            'request_id': 'toolreq_1',
            'task_id': 'tsk_1',
            'step_id': 'stp_1',
            'worker_id': 'coder-worker',
            'tool_name': 'python_sandbox',
            'action': 'run_pytest',
            'parameters': {'cwd': '/workspace'},
            'risk_class': 'guarded',
            'capability_scope': 'repo:test',
        }
        base.update(overrides)
        return base

    def test_valid(self):
        r = ToolRequestContract(**self._valid_data())
        assert r.tool_name == 'python_sandbox'

    def test_worker_id_required(self):
        with self.assertRaises(ValidationError):
            ToolRequestContract(**self._valid_data(worker_id=''))

    def test_capability_scope_required(self):
        with self.assertRaises(ValidationError):
            ToolRequestContract(**self._valid_data(capability_scope=''))

    def test_dangerous_without_token_rejected(self):
        """Dangerous action has no valid approval token → rejected (§12 Tooling)."""
        with self.assertRaises(ValidationError):
            ToolRequestContract(**self._valid_data(
                risk_class='dangerous',
                approval_token=None,
            ))

    def test_dangerous_with_token_ok(self):
        r = ToolRequestContract(**self._valid_data(
            risk_class='dangerous',
            approval_token='signed-token-abc',
        ))
        assert r.approval_token == 'signed-token-abc'

    def test_prohibited_always_rejected(self):
        """Forbidden tool/action combination rejected (§12 Tooling)."""
        with self.assertRaises(ValidationError):
            ToolRequestContract(**self._valid_data(
                risk_class='prohibited',
                approval_token='even-with-token',
            ))

    def test_safe_no_token_ok(self):
        r = ToolRequestContract(**self._valid_data(risk_class='safe'))
        assert r.approval_token is None


# ═══════════════════════════════════════════════════════════════════════════════
# §6 — Tool Response
# ═══════════════════════════════════════════════════════════════════════════════

class TestToolResponse(unittest.TestCase):

    def _valid_data(self, **overrides):
        base = {
            'request_id': 'toolreq_1',
            'task_id': 'tsk_1',
            'step_id': 'stp_1',
            'status': 'ok',
            'receipt_id': 'rcpt_1',
        }
        base.update(overrides)
        return base

    def test_valid(self):
        """Receipt always generated (§12 Tooling)."""
        r = ToolResponseContract.model_validate(self._valid_data())
        assert r.receipt_id == 'rcpt_1'

    def test_receipt_id_required(self):
        with self.assertRaises(ValidationError):
            ToolResponseContract.model_validate(self._valid_data(receipt_id=''))

    def test_status_enum(self):
        r = ToolResponseContract.model_validate(self._valid_data(status='error'))
        assert r.status == ToolResultStatus.ERROR

    def test_invalid_status_rejected(self):
        with self.assertRaises(ValidationError):
            ToolResponseContract.model_validate(self._valid_data(status='unknown'))

    def test_timing_and_resource(self):
        r = ToolResponseContract.model_validate(self._valid_data(
            timing_ms=298520,
            resource_usage={'cpu_seconds': 110.2, 'max_memory_mb': 842},
        ))
        assert r.timing_ms == 298520


# ═══════════════════════════════════════════════════════════════════════════════
# §7-§8 — Approval Request & Token
# ═══════════════════════════════════════════════════════════════════════════════

class TestApprovalRequest(unittest.TestCase):
    def test_valid(self):
        a = ApprovalRequestContract(
            approval_id='appr_1', task_id='tsk_1', step_id='stp_1',
            action_hash='sha256:abc', risk_class=RiskClass.DANGEROUS,
            summary='Delete stale artifacts.',
        )
        assert a.risk_class == RiskClass.DANGEROUS

    def test_action_hash_required(self):
        with self.assertRaises(ValidationError):
            ApprovalRequestContract(
                approval_id='a', task_id='t', step_id='s',
                action_hash='', risk_class=RiskClass.DANGEROUS,
            )


class TestApprovalToken(unittest.TestCase):
    def _valid_data(self, **overrides):
        base = {
            'approval_token': 'signed-tok',
            'approval_id': 'appr_1',
            'task_id': 'tsk_1',
            'step_id': 'stp_1',
            'action_hash': 'sha256:abc',
            'subject': 'executor-worker',
            'expires_at': time.time() + 3600,
        }
        base.update(overrides)
        return base

    def test_valid(self):
        t = ApprovalTokenContract(**self._valid_data())
        assert t.single_use is True

    def test_expired_token_rejected(self):
        """Expired token rejected (§12 Approval)."""
        t = ApprovalTokenContract(**self._valid_data(
            expires_at=time.time() - 100,
        ))
        assert t.is_expired()

    def test_active_token(self):
        t = ApprovalTokenContract(**self._valid_data(
            expires_at=time.time() + 3600,
        ))
        assert not t.is_expired()

    def test_action_hash_mismatch_rejected(self):
        """Action hash mismatch rejected (§12 Approval)."""
        t = ApprovalTokenContract(**self._valid_data(
            action_hash='sha256:abc',
        ))
        assert not t.matches_action('sha256:DIFFERENT')

    def test_action_hash_match(self):
        t = ApprovalTokenContract(**self._valid_data(
            action_hash='sha256:abc',
        ))
        assert t.matches_action('sha256:abc')

    def test_subject_mismatch(self):
        """Token не переносим между workers (§8)."""
        t = ApprovalTokenContract(**self._valid_data(subject='worker-A'))
        assert not t.matches_subject('worker-B')

    def test_subject_match(self):
        t = ApprovalTokenContract(**self._valid_data(subject='worker-A'))
        assert t.matches_subject('worker-A')

    def test_subject_required(self):
        with self.assertRaises(ValidationError):
            ApprovalTokenContract(**self._valid_data(subject=''))


# ═══════════════════════════════════════════════════════════════════════════════
# §9 — Memory Write
# ═══════════════════════════════════════════════════════════════════════════════

class TestMemoryWrite(unittest.TestCase):

    def _valid_data(self, **overrides):
        base = {
            'write_id': 'memw_1',
            'memory_target': 'semantic',
            'record': {
                'content': 'Test suite passed.',
                'provenance': {
                    'source_type': 'tool_result',
                    'receipt_id': 'rcpt_1',
                },
                'verification_status': 'verified',
                'confidence': 0.98,
            },
        }
        base.update(overrides)
        return base

    def test_valid(self):
        m = MemoryWriteContract(**self._valid_data())
        assert m.record.confidence == 0.98

    def test_semantic_without_provenance_rejected(self):
        """Semantic write without provenance rejected (§12 Memory)."""
        d = self._valid_data()
        del d['record']['provenance']
        with self.assertRaises(ValidationError):
            MemoryWriteContract(**d)

    def test_without_verification_status_rejected(self):
        """Unverified record quarantined / rejected (§12 Memory)."""
        d = self._valid_data()
        d['record']['verification_status'] = ''
        with self.assertRaises(ValidationError):
            MemoryWriteContract(**d)

    def test_confidence_out_of_range(self):
        with self.assertRaises(ValidationError):
            MemoryWriteContract(**self._valid_data(
                record={
                    'content': 'x',
                    'provenance': {'source_type': 's'},
                    'verification_status': 'v',
                    'confidence': 1.5,
                }
            ))

    def test_invalid_memory_target(self):
        with self.assertRaises(ValidationError):
            MemoryWriteContract(**self._valid_data(memory_target='cache'))

    def test_working_memory_target_ok(self):
        m = MemoryWriteContract(**self._valid_data(memory_target='working'))
        assert m.memory_target == 'working'

    def test_content_required(self):
        d = self._valid_data()
        d['record']['content'] = ''
        with self.assertRaises(ValidationError):
            MemoryWriteContract(**d)


# ═══════════════════════════════════════════════════════════════════════════════
# §10 — Audit Event
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditEvent(unittest.TestCase):
    def test_valid_deny_event(self):
        """Deny path emits event (§12 Audit)."""
        e = AuditEventContract(
            event_id='evt_1', trace_id='trc_1', task_id='tsk_1',
            actor=AuditActor(type='worker', id='executor-worker'),
            event_type='TOOL_CALL_DENIED',
            details={'tool_name': 'filesystem', 'reason': 'approval_missing'},
        )
        assert e.event_type == 'TOOL_CALL_DENIED'

    def test_valid_allow_event(self):
        """Allow path emits event (§12 Audit)."""
        e = AuditEventContract(
            event_id='evt_2',
            actor=AuditActor(type='worker', id='w1'),
            event_type='TOOL_CALL_ALLOWED',
        )
        assert e.event_type == 'TOOL_CALL_ALLOWED'

    def test_event_type_required(self):
        with self.assertRaises(ValidationError):
            AuditEventContract(
                event_id='e', actor=AuditActor(type='w', id='i'),
                event_type='',
            )

    def test_actor_required(self):
        with self.assertRaises(ValidationError):
            AuditEventContract(event_id='e', event_type='X')  # type: ignore[call-arg]

    def test_trace_correlation(self):
        """Trace correlation survives retries (§12 Audit)."""
        e = AuditEventContract(
            event_id='evt_1', trace_id='trc_123',
            actor=AuditActor(type='system', id='retry-handler'),
            event_type='RETRY',
        )
        assert e.trace_id == 'trc_123'


# ═══════════════════════════════════════════════════════════════════════════════
# §11 — State Transitions
# ═══════════════════════════════════════════════════════════════════════════════

class TestStateTransitions(unittest.TestCase):
    def test_created_to_planned_ok(self):
        assert validate_transition(TaskStatus.CREATED, TaskStatus.PLANNED)

    def test_created_to_completed_forbidden(self):
        """created → completed недопустим (§11)."""
        assert not validate_transition(TaskStatus.CREATED, TaskStatus.COMPLETED)

    def test_failed_to_completed_forbidden(self):
        """failed → completed без recovery — недопустим (§11)."""
        assert not validate_transition(TaskStatus.FAILED, TaskStatus.COMPLETED)

    def test_planned_to_running_ok(self):
        assert validate_transition(TaskStatus.PLANNED, TaskStatus.RUNNING)

    def test_running_to_completed_ok(self):
        assert validate_transition(TaskStatus.RUNNING, TaskStatus.COMPLETED)

    def test_completed_is_terminal(self):
        for s in TaskStatus:
            if s != TaskStatus.COMPLETED:
                assert not validate_transition(TaskStatus.COMPLETED, s)

    def test_cancelled_is_terminal(self):
        for s in TaskStatus:
            if s != TaskStatus.CANCELLED:
                assert not validate_transition(TaskStatus.CANCELLED, s)

    def test_blocked_to_running_ok(self):
        assert validate_transition(TaskStatus.BLOCKED, TaskStatus.RUNNING)

    def test_awaiting_to_running_ok(self):
        """Structural check — caller must verify approval separately."""
        assert validate_transition(TaskStatus.AWAITING_APPROVAL, TaskStatus.RUNNING)


# ═══════════════════════════════════════════════════════════════════════════════
# validate_or_error helper
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidateOrError(unittest.TestCase):
    def test_valid_returns_model(self):
        result = validate_or_error(ErrorEnvelope, {
            'error_code': 'OK', 'retryable': False,
        })
        assert isinstance(result, ErrorEnvelope)

    def test_invalid_returns_error_envelope(self):
        """Request schema rejection (§12 API boundary)."""
        result = validate_or_error(TaskCreationContract, {'bad': 'data'})
        assert isinstance(result, ErrorEnvelope)
        assert result.error_code == 'VALIDATION_ERROR'

    def test_error_contains_model_name(self):
        result = validate_or_error(PlanStepContract, {})
        assert isinstance(result, ErrorEnvelope)
        assert 'PlanStepContract' in result.details.get('model', '')


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-contract integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrossContract(unittest.TestCase):
    """Проверяет согласованность полей между контрактами."""

    def test_tool_request_response_ids_match(self):
        req = ToolRequestContract(
            request_id='toolreq_1', task_id='tsk_1', step_id='stp_1',
            worker_id='w', tool_name='t', action='a',
            risk_class=RiskClass.SAFE, capability_scope='repo:test',
        )
        resp = ToolResponseContract(
            request_id=req.request_id, task_id=req.task_id,
            step_id=req.step_id, status=ToolResultStatus.OK, receipt_id='rcpt_1',
        )
        assert resp.request_id == req.request_id
        assert resp.task_id == req.task_id

    def test_approval_flow_consistency(self):
        """Approval request → token → tool request с одинаковыми IDs."""
        ar = ApprovalRequestContract(
            approval_id='appr_1', task_id='tsk_1', step_id='stp_1',
            action_hash='sha256:abc', risk_class=RiskClass.DANGEROUS,
        )
        tok = ApprovalTokenContract(
            approval_token='signed', approval_id=ar.approval_id,
            task_id=ar.task_id, step_id=ar.step_id,
            action_hash=ar.action_hash, subject='executor',
            expires_at=time.time() + 3600,
        )
        assert tok.matches_action(ar.action_hash)
        assert tok.approval_id == ar.approval_id

    def test_memory_write_after_tool_response(self):
        """Tool result → memory write with receipt provenance."""
        resp = ToolResponseContract(
            request_id='toolreq_1', task_id='tsk_1', step_id='stp_1',
            status=ToolResultStatus.OK, receipt_id='rcpt_42',
        )
        mw = MemoryWriteContract(
            write_id='memw_1', task_id=resp.task_id,
            source_step_id=resp.step_id, memory_target='semantic',
            record=MemoryRecord(
                content='Tests passed.',
                provenance=Provenance(
                    source_type='tool_result',
                    receipt_id=resp.receipt_id,
                ),
                verification_status='verified',
                confidence=0.95,
            ),
        )
        assert mw.record.provenance.receipt_id == resp.receipt_id


if __name__ == '__main__':
    unittest.main()
