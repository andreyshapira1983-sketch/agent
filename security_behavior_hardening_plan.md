
# План доведения security и поведения кода до уровня архитектуры

## 1. Принципиальный вывод

Тексты архитектуры уже требуют:

- policy-gates для инструментов, сети, файлов и команд
- сквозного аудита
- sandbox для исполнения
- запрета bypass-механизмов
- верификации знаний перед долгосрочной записью
- bounded retries
- Human Approval для критичных действий fileciteturn1file1

Наличие большого числа проходящих тестов (`1560 passed`) показывает рабочую тестовую базу, но не гарантирует, что эти требования реально обеспечены в коде и runtime-поведении. fileciteturn1file0

---

## 2. Что нужно сделать в коде в первую очередь

### Приоритет P0 — без этого архитектура не соответствует текстам

#### 2.1. Запретить прямой доступ worker-ов к опасным возможностям

##### Сделать

- удалить прямые вызовы shell/filesystem/network/db/cloud из worker-кода
- оставить только broker-mediated path
- в base worker классе запретить dangerous imports / adapters

##### Критерий готовности

- любой mutable или external action возможен только через Tool Broker

#### 2.2. Ввести deny-by-default policy engine

##### Сделать (2.2)

- capability map на worker × tool × action
- default deny
- явные allow rules
- rule evaluation logged

##### Критерий (2.2)

- отсутствие policy = отказ, а не silent allow

#### 2.3. Approval-токены для dangerous actions

##### Сделать (2.3)

- single-use signed token
- bind to task_id + step_id + action_hash + expiry
- broker validates before execution

##### Критерий (2.3)

- dangerous action невозможно выполнить без токена

#### 2.4. Secrets redaction и vault-only access

##### Сделать (2.4)

- все секреты читать через secrets abstraction
- запретить передачу секретов напрямую в workers
- лог- и exception-redaction middleware

##### Критерий (2.4)

- секреты не видны ни в логах, ни в stdout/stderr, ни в audit details

#### 2.5. Sandbox hardening

##### Сделать (2.5)

- каждый job в отдельном контейнере
- CPU/RAM/time limits
- read-only root fs
- scoped workspace
- network off by default

##### Критерий (2.5)

- arbitrary code execution не выходит в хост по умолчанию

---

## 3. Приоритет P1 — нужно для реального эксплуатационного доверия

### 3.1. State machine и идемпотентность

- формальный task/step state machine
- idempotency keys на вход
- checkpoint/resume only from valid states
- lock protection against concurrent mutation

### 3.2. Structured audit

- task_id
- step_id
- trace_id
- actor_id
- action
- result
- policy decision
- approval decision
- receipt_id

### 3.3. Retry discipline

- retry only for known transient classes
- no infinite retry
- DLQ after threshold
- escalation for repeated unknown failure

### 3.4. Memory write verification

- provenance mandatory
- quarantine unverified knowledge
- semantic writes only after validation
- graph updates idempotent and reversible where possible

---

## 4. Приоритет P2 — для зрелого поведения системы

### 4.1. Formal contracts everywhere

- pydantic / json-schema / protobuf, но единообразно
- no untyped dict passing across service boundaries
- versioned contracts

### 4.2. Security-focused CI

- secret scanning
- dependency scanning
- static checks for dangerous imports
- policy regression suite
- contract test suite
- sandbox escape regression tests

### 4.3. Behavioral guardrails

- planner must emit risk class
- planner must emit rationale
- executor refuses ambiguous mutation
- unknown action class -> deny or guarded review

---

## 5. Конкретный backlog на 4 итерации

## Итерация 1

- Tool Broker mandatory path
- deny-by-default policy engine
- approval token validation
- secret redaction middleware
- worker capability scopes

## Итерация 2

- formal task / step / tool / approval contracts
- structured audit
- trace propagation
- retry caps + DLQ
- task state machine

## Итерация 3

- sandbox hardening
- network egress restrictions
- filesystem path allowlists
- command class allowlists
- memory verification gates

## Итерация 4

- contract tests
- security integration tests
- incident runbooks
- postmortem automation
- regression dashboards

---

## 6. Обязательные security tests, которых обычно не хватает

### Tooling

- worker cannot bypass broker
- forbidden shell command rejected
- forbidden path rejected
- direct db mutation denied
- network disabled in sandbox by default

### Approval

- no dangerous action without token
- expired token denied
- token reuse denied
- action hash mismatch denied

### Secrets

- secrets never appear in logs
- secrets never appear in exceptions
- secrets not exposed in audit payloads

### Memory

- unverified fact not promoted
- provenance missing → reject
- quarantine path works

### Resilience

- retry cap enforced
- DLQ path works
- resumed task preserves state invariant

---

## 7. Definition of Done для security/behavior alignment

Считать код доведённым до уровня архитектуры можно только когда одновременно выполнено всё ниже:

1. **Все dangerous actions проходят через approval + broker + audit**
2. **Нет прямого обхода policy/broker из worker-кода**
3. **Secrets никогда не логируются**
4. **Все runtime границы описаны формальными контрактами**
5. **Semantic/long-term memory пишет только verified pipeline**
6. **Sandbox реально изолирован и ограничен**
7. **Retry/fallback bounded и наблюдаемы**
8. **Есть runbook на основные инциденты**
9. **Есть security integration tests, а не только unit tests**
10. **Traceability layer→service→module→test поддерживается в репозитории**

---

## 8. Практический вывод

Сейчас архитектура уже описывает нужный уровень строгости и безопасности. Следующая стадия — не придумывать новые слои, а жёстко заставить код obey этим слоям:

- через mandatory broker path,
- formal contracts,
- approval tokens,
- verification-before-write,
- deny-by-default policy,
- sandbox isolation,
- auditability by construction.

Именно это превращает красивый архитектурный текст в реально надёжную систему.
