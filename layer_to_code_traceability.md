
# Трассировка слои → сервисы → код → тесты → security gates

## Назначение

Этот документ переводит архитектурные слои в инженерную трассировку:

- архитектурный слой
- сервис / контейнер
- модуль в коде
- ключевые интерфейсы
- security gate
- обязательные тесты
- operational owner

---

## 1. Communication Layer

### Контейнеры / сервисы

- `api-gateway`

### Кодовые модули

- `src/gateway/http_api.py`
- `src/gateway/auth.py`
- `src/gateway/rate_limit.py`
- `src/gateway/request_normalizer.py`
- `src/gateway/response_mapper.py`

### Контракты

- `POST /v1/tasks`
- `GET /v1/tasks/{task_id}`
- `POST /v1/approval/{approval_id}`

### Security gates

- authentication
- authorization
- request size limit
- schema validation
- idempotency key
- structured audit logging

### Тесты

- unauthorized request denied
- malformed payload denied
- duplicate request with same idempotency key is safe
- response never leaks secrets
- rate limit behavior

---

## 2. State & Session Management

### Контейнеры / сервисы (2)

- `session-state-service`
- `postgres`
- `redis`

### Кодовые модули (2)

- `src/state/session_store.py`
- `src/state/checkpoint_store.py`
- `src/state/locks.py`
- `src/state/task_state_machine.py`

### Security / correctness

- optimistic locking
- per-task lock
- idempotent resume
- immutable transition log
- no direct state mutation outside service boundary

### Тесты (2)

- resume after crash
- concurrent update rejected or serialized
- invalid state transition blocked
- checkpoint restore deterministic

---

## 3. Orchestration System

### Контейнеры / сервисы (3)

- `orchestrator`
- `task-engine`

### Кодовые модули (3)

- `src/orchestrator/orchestrator.py`
- `src/orchestrator/dispatch.py`
- `src/orchestrator/workflow_engine.py`
- `src/orchestrator/task_decomposition.py`

### Security / behavior

- workers cannot self-escalate privileges
- orchestration decisions are auditable
- task DAG validation before dispatch
- bounded retries and dead-letter queue routing

### Тесты (3)

- invalid DAG rejected
- stuck task escalates
- retry caps enforced
- dead-letter queue path works

---

## 4. Cognitive Core / Planner

### Контейнеры / сервисы (4)

- `planner`
- `model-router`

### Кодовые модули (4)

- `src/planner/plan_builder.py`
- `src/planner/reasoning.py`
- `src/planner/risk_annotations.py`
- `src/models/router.py`

### Security / behavior (4)

- planner cannot call tools directly
- every plan step must carry:
  - intent
  - required tool
  - risk class
  - expected output schema
  - rollback hint

- no opaque critical decisions without rationale

### Тесты (4)

- dangerous action marked dangerous
- unknown action defaults to guarded or denied
- model fallback works
- planner output schema validation

---

## 5. Knowledge System

### Контейнеры / сервисы (5)

- `qdrant`
- `neo4j`
- `minio`
- `knowledge-service`

### Кодовые модули (5)

- `src/knowledge/retrieval.py`
- `src/knowledge/embedding_pipeline.py`
- `src/knowledge/graph_sync.py`
- `src/knowledge/artifact_index.py`
- `src/knowledge/verification.py`

### Security / behavior (5)

- no write to semantic memory without verification
- provenance required on every record
- quarantine bucket for unverified knowledge
- delete operations audited and reversible where possible

### Тесты (5)

- unverified fact not promoted
- provenance missing → reject write
- graph sync idempotent
- vector/object metadata consistency

---

## 6. Agent System / Workers

### Контейнеры / сервисы (6)

- `research-worker`
- `coder-worker`
- `analyst-worker`
- `browser-worker`
- `executor-worker`
- `evaluation-worker`

### Кодовые модули (6)

- `src/workers/base.py`
- `src/workers/research_worker.py`
- `src/workers/coder_worker.py`
- `src/workers/analyst_worker.py`
- `src/workers/browser_worker.py`
- `src/workers/executor_worker.py`
- `src/workers/evaluation_worker.py`

### Security / behavior (6)

- workers receive scoped capabilities only
- no direct network / filesystem / shell access except through broker
- all mutable actions tagged with approval requirement
- worker identity included in every audit record

### Тесты (6)

- worker denied when using non-scoped tool
- broker bypass impossible
- executor cannot run dangerous mutation without approval token

---

## 7. Tool Layer / Tool Broker

### Контейнеры / сервисы (7)

- `tool-broker`
- `sandbox-runner`

### Кодовые модули (7)

- `src/tools/broker.py`
- `src/tools/policy_adapter.py`
- `src/tools/secrets_injection.py`
- `src/tools/terminal_runner.py`
- `src/tools/python_runner.py`
- `src/tools/browser_adapter.py`
- `src/tools/github_adapter.py`
- `src/tools/db_adapter.py`
- `src/tools/cloud_adapter.py`

### Security / behavior (7)

- single choke point for tool calls
- timeout / retry / backoff enforced centrally
- network egress policy
- path allowlist for filesystem
- command allowlist or command classes
- output redaction before logs
- broker signs execution receipts

### Тесты (7)

- forbidden command blocked
- forbidden path blocked
- network egress restricted
- secret never appears in logs
- timeout kills child process
- sandbox resource limits enforced

---

## 8. Human Approval Layer

### Контейнеры / сервисы (8)

- `approval-service`

### Кодовые модули (8)

- `src/approval/service.py`
- `src/approval/policy.py`
- `src/approval/token.py`

### Security / behavior (8)

- approval token is single-use
- approval bound to:
  - task_id
  - step_id
  - actor
  - action hash
  - expiry

- dangerous action without valid token denied

### Тесты (8)

- expired approval denied
- reused token denied
- mutated action after approval denied
- approval audit complete

---

## 9. Data Validation & Contracts

### Контейнеры / сервисы (9)

- used by all runtime services

### Кодовые модули (9)

- `src/contracts/schemas.py`
- `src/contracts/validators.py`
- `src/contracts/error_model.py`

### Security / behavior (9)

- strict schema validation at every boundary
- reject unknown critical fields where appropriate
- typed errors only
- no best-effort mutation on dangerous inputs

### Тесты (9)

- invalid contract rejected
- unknown enum rejected
- partial payload rejected on critical endpoints

---

## 10. Evaluation / Reflection / Self-Repair

### Контейнеры / сервисы (10)

- `evaluation-worker`

### Кодовые модули (10)

- `src/eval/evaluator.py`
- `src/eval/postmortem.py`
- `src/eval/regression_guard.py`
- `src/repair/propose_fix.py`

### Security / behavior (10)

- self-repair never writes directly to production path
- proposed fixes must pass tests in sandbox
- evaluation records confidence and evidence

### Тесты (10)

- failing patch not promoted
- postmortem generated after repeated failure
- regression block works

---

## 11. Security System

### Контейнеры / сервисы (11)

- `iam-service`
- `secrets-vault`
- `policy-engine`

### Кодовые модули (11)

- `src/security/iam.py`
- `src/security/policy_engine.py`
- `src/security/secrets.py`
- `src/security/redaction.py`
- `src/security/audit.py`

### Security / behavior (11)

- deny by default
- least privilege
- explicit capability scope
- immutable audit events
- secret redaction library mandatory in logs/exceptions

### Тесты (11)

- default deny on missing policy
- capability escalation denied
- secret redaction works on nested payloads
- audit event emitted for deny and allow

---

## 12. Monitoring & Logging / Reliability

### Контейнеры / сервисы (12)

- `observability-stack`

### Кодовые модули (12)

- `src/obs/logging.py`
- `src/obs/metrics.py`
- `src/obs/tracing.py`
- `src/reliability/retry.py`
- `src/reliability/dlq.py`

### Security / behavior (12)

- logs structured and redact-sensitive
- traces correlated by `trace_id`, `task_id`, `step_id`
- no infinite retry
- failure class determines fallback or escalation

### Тесты (12)

- trace propagation
- DLQ routing
- retry cap enforcement
- redaction in logs and traces

---

## 13. Минимальная матрица ownership

| Область | Owner |
| --- | --- |
| API boundary | api-gateway |
| Task lifecycle | orchestrator |
| Planning semantics | planner |
| Tool invocation safety | tool-broker + policy-engine |
| Secrets handling | secrets-vault |
| Session correctness | session-state-service |
| Knowledge integrity | knowledge-service + verification |
| Dangerous action control | approval-service |
| Auditability | audit subsystem |
| Resilience / retries / DLQ | reliability subsystem |

---

## 14. Что уже видно по текущему состоянию

Тестовый прогон показывает `1560 passed`, то есть базовая автоматизированная проверка у проекта есть. Но сам по себе этот факт не доказывает достаточность security-гейтов, корректность privilege boundaries, отсутствие утечек секретов и соответствие runtime-поведения архитектурным ограничениям. Эти свойства нужно покрывать отдельными контрактными, security и integration тестами.
