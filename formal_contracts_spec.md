
# Формальные контракты: runtime, API, approvals, tool calls, memory writes

## 1. Базовые принципы

Все ключевые границы в системе должны быть формализованы контрактами:

- API request/response
- task object
- plan step
- tool request/response
- approval token
- memory write record
- audit event
- error envelope

Ниже — рекомендованный минимум.

---

## 2. Error envelope

```json
{
  "error_code": "POLICY_DENIED",
  "message": "Action is not allowed by policy.",
  "trace_id": "trc_123",
  "task_id": "tsk_123",
  "step_id": "stp_004",
  "retryable": false,
  "details": {
    "policy_rule": "no_direct_filesystem_delete"
  }
}
```

### Правила

- `error_code` обязателен
- `retryable` обязателен
- секреты и внутренние токены в `details` запрещены

---

## 3. Task creation contract

```json
{
  "task_id": "tsk_123",
  "request_id": "req_123",
  "idempotency_key": "idem_abc",
  "initiator": {
    "type": "user",
    "id": "user_1"
  },
  "goal": "Analyze a repository and produce a safe refactoring plan.",
  "constraints": {
    "budget_usd_max": 5.0,
    "allow_network": true,
    "allow_mutation": false
  },
  "input_artifacts": [
    {
      "artifact_id": "art_1",
      "type": "repository"
    }
  ]
}
```

### Инварианты создания задачи

- `task_id`, `request_id`, `idempotency_key` обязательны
- task без `initiator` недопустим
- `allow_mutation=false` запрещает any mutable tool step unless explicit re-approval

---

## 4. Plan step contract

```json
{
  "step_id": "stp_004",
  "task_id": "tsk_123",
  "worker_type": "coder",
  "intent": "run_tests",
  "required_tool": "python_sandbox",
  "risk_class": "guarded",
  "inputs": {
    "repo_path": "/workspace/repo"
  },
  "expected_output_schema": "TestRunResult.v1",
  "rollback_hint": null,
  "requires_approval": false,
  "rationale": "Run tests before proposing changes."
}
```

### Инварианты плана

- каждый шаг имеет `risk_class`
- каждый шаг имеет `expected_output_schema`
- dangerous step без `requires_approval=true` недопустим
- planner не может выпускать step без `required_tool`, если step tool-using

---

## 5. Tool request contract

```json
{
  "request_id": "toolreq_1",
  "task_id": "tsk_123",
  "step_id": "stp_004",
  "worker_id": "coder-worker",
  "tool_name": "python_sandbox",
  "action": "run_pytest",
  "parameters": {
    "cwd": "/workspace/repo",
    "args": ["-q"]
  },
  "risk_class": "guarded",
  "approval_token": null,
  "capability_scope": "repo:test"
}
```

### Инварианты tool request

- broker rejects request if:
  - `worker_id` missing
  - `capability_scope` missing
  - dangerous action has no valid approval token
  - action not allowed for tool
  - parameters violate policy

---

## 6. Tool response contract

```json
{
  "request_id": "toolreq_1",
  "task_id": "tsk_123",
  "step_id": "stp_004",
  "status": "ok",
  "receipt_id": "rcpt_1",
  "stdout_ref": "obj://runs/tsk_123/stp_004/stdout.txt",
  "stderr_ref": "obj://runs/tsk_123/stp_004/stderr.txt",
  "structured_result": {
    "tests_passed": 1560,
    "tests_failed": 0
  },
  "timing_ms": 298520,
  "resource_usage": {
    "cpu_seconds": 110.2,
    "max_memory_mb": 842
  }
}
```

### Правило

- любой реальный execution результат должен иметь `receipt_id`
- stdout/stderr возвращаются по ссылке на артефакт, а не сырым неограниченным текстом

---

## 7. Approval request contract

```json
{
  "approval_id": "appr_1",
  "task_id": "tsk_123",
  "step_id": "stp_010",
  "action_hash": "sha256:abc",
  "risk_class": "dangerous",
  "summary": "Delete 14 stale artifacts from scoped storage path.",
  "impact_scope": {
    "resource_type": "object_storage",
    "resource_ids": ["obj_1", "obj_2"]
  },
  "rollback_plan": "Restore from versioned bucket snapshot.",
  "expires_at": "2026-04-03T12:00:00Z"
}
```

---

## 8. Approval token contract

```json
{
  "approval_token": "signed-token",
  "approval_id": "appr_1",
  "task_id": "tsk_123",
  "step_id": "stp_010",
  "action_hash": "sha256:abc",
  "subject": "executor-worker",
  "expires_at": "2026-04-03T12:00:00Z",
  "single_use": true
}
```

### Инварианты approval token

- token привязан к action hash
- token одноразовый
- token не переносим между workers
- token с истёкшим сроком недействителен

---

## 9. Memory write contract

```json
{
  "write_id": "memw_1",
  "task_id": "tsk_123",
  "source_step_id": "stp_011",
  "memory_target": "semantic",
  "record": {
    "content": "Repository test suite passed with 1560 tests.",
    "provenance": {
      "source_type": "tool_result",
      "receipt_id": "rcpt_1",
      "timestamp": "2026-04-03T11:00:00Z"
    },
    "verification_status": "verified",
    "confidence": 0.98
  }
}
```

### Инварианты memory write

- semantic memory write без `provenance` запрещён
- semantic/long-term write без `verification_status` запрещён
- dangerous or external claims require verification workflow

---

## 10. Audit event contract

```json
{
  "event_id": "evt_1",
  "trace_id": "trc_123",
  "task_id": "tsk_123",
  "step_id": "stp_010",
  "actor": {
    "type": "worker",
    "id": "executor-worker"
  },
  "event_type": "TOOL_CALL_DENIED",
  "timestamp": "2026-04-03T10:58:00Z",
  "details": {
    "tool_name": "filesystem",
    "action": "delete_path",
    "reason": "approval_missing"
  }
}
```

### Инварианты audit event

- deny events логируются так же обязательно, как allow events
- audit event append-only

---

## 11. State transition contract

Допустимые статусы task:

- `created`
- `planned`
- `awaiting_approval`
- `running`
- `blocked`
- `failed`
- `completed`
- `cancelled`

Недопустимые переходы:

- `created -> completed`
- `awaiting_approval -> running` без valid approval
- `failed -> completed` без explicit recovery path

---

## 12. Contract tests, которые должны существовать

### API boundary

- request schema rejection
- unknown field handling
- idempotency behavior

### Planning

- step without risk_class rejected
- dangerous step without approval flag rejected

### Tooling

- forbidden tool/action combination rejected
- secret redaction contract enforced
- receipt always generated

### Approval

- expired token rejected
- reused token rejected
- action hash mismatch rejected

### Memory

- semantic write without provenance rejected
- unverified record quarantined

### Audit

- deny path emits event
- allow path emits event
- trace correlation survives retries
