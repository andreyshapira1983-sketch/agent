
# Runbook: эксплуатация, инциденты, approvals, sandbox и security-поведение

## 1. Цель

Этот runbook фиксирует, как система должна вести себя в норме и при сбоях, чтобы фактическое поведение совпадало с архитектурой.

---

## 2. Нормальный запуск

### Предусловия

- контейнеры подняты
- Postgres, Redis, Qdrant, Neo4j, MinIO доступны
- policy-engine healthy
- secrets-vault healthy
- tool-broker healthy
- sandbox-runner healthy

### Проверка

1. Проверить `api-gateway /health`
2. Проверить `orchestrator /health`
3. Проверить соединение с Postgres
4. Проверить Redis locks/queues
5. Проверить vector DB
6. Проверить graph DB
7. Проверить object storage
8. Проверить broker can execute test-safe sandbox command
9. Проверить tracing and logs

### Норма

- все health-check зелёные
- нет backlog в DLQ
- нет зависших approvals
- нет невалидных миграций
- все секреты читаются только broker/policy-authorized services

---

## 3. Классы действий

### Safe

- чтение данных
- retrieval
- локальная аналитика в sandbox
- создание черновиков
- non-mutating browser fetch

**Approval:** не нужен

### Guarded

- ограниченные записи во внутренние хранилища
- создание временных артефактов
- безопасные sandbox jobs
- controlled repo reads/writes в allowed scope

**Approval:** обычно не нужен, но обязателен policy check

### Dangerous

- удаление важных данных
- мутирующие DB write в critical tables
- изменение конфигурации
- внешние действия от имени пользователя
- финансовые действия
- cloud mutations
- необратимые batch operations

**Approval:** обязателен

### Prohibited

- bypass policy
- bypass broker
- доступ вне scopes
- вывод секретов
- небезопасный произвольный shell без sandbox/guardrails
- автоприменение risky patch в prod

**Approval:** не помогает; действие запрещено

---

## 4. Процедура dangerous action

1. Planner маркирует step как dangerous.
2. Policy engine подтверждает класс опасности.
3. Approval service генерирует approval request.
4. Человек получает:
   - цель действия
   - затрагиваемые ресурсы
   - риск
   - ожидаемый эффект
   - rollback plan

5. После одобрения создаётся single-use approval token.
6. Executor / broker проверяет:
   - token signature
   - expiry
   - action hash
   - scope

7. Только после этого действие исполняется.
8. Все детали пишутся в audit log.

Если действие изменилось после одобрения хотя бы на один параметр — approval недействителен.

---

## 5. Инцидент: tool broker bypass attempt

### Симптомы

- worker пытается обратиться к shell/network/filesystem напрямую
- audit gap
- missing broker receipt

### Немедленные действия

1. Заблокировать worker identity.
2. Остановить связанную задачу.
3. Снять trace и последние события.
4. Проверить, было ли реальное воздействие наружу.
5. Открыть security incident.

### Root cause check

- ошибка capability map
- прямой импорт запрещённого adapter
- тестовый обход оставлен в production code
- misconfigured environment variable

### Исправление

- hard fail in worker base class
- static guard / linter rule
- integration test “worker cannot bypass broker”
- review all mutable adapters

---

## 6. Инцидент: утечка секрета в лог

### Симптомы утечки

- токен / key обнаружен в логах или ошибке

### Немедленные действия при утечке

1. Ротировать секрет.
2. Ограничить доступ к логам.
3. Пометить incident severity high.
4. Найти источник:
   - exception path
   - debug print
   - adapter response dump

5. Перезапустить затронутые сервисы после фикса redaction.

### Профилактика

- mandatory redaction wrapper
- ban raw exception dumps in adapters
- test fixtures with synthetic secrets
- CI check for secret-like patterns in logs

---

## 7. Инцидент: зависший approval

### Симптомы зависшего approval

- dangerous task blocked longer than SLA
- approval pending without owner

### Действия при зависшем approval

1. Проверить approval queue.
2. Проверить expiration policy.
3. Если approval expired — mark task as `awaiting_replan` or `cancelled`.
4. Не выполнять action автоматически.
5. Уведомить orchestrator to replan or await new human action.

---

## 8. Инцидент: inconsistency между Postgres и object storage

### Симптомы inconsistency

- metadata exists but object missing
- object exists but metadata missing

### Действия при inconsistency

1. Остановить promotion to semantic memory.
2. Найти affected artifact ids.
3. Выполнить reconciliation job.
4. Если artifact критичен — quarantine record.
5. Зафиксировать postmortem.

---

## 9. Инцидент: repeated task failure

### Условие

- step failed N times
- retry cap exceeded

### Действия при repeated failure

1. Переместить событие в DLQ.
2. Сохранить checkpoint.
3. Создать postmortem entry.
4. Передать evaluation-worker.
5. Если failure class dangerous or unknown — human review required.

---

## 10. Sandbox run rules

- каждый job исполняется в отдельном изолированном контейнере
- ограничение CPU / RAM / execution time
- read-only root fs по возможности
- отдельный рабочий volume
- сеть выключена по умолчанию
- egress включается только по policy
- stdout/stderr проходят redaction
- артефакты экспортируются только через broker

---

## 11. Recovery после рестарта машины

1. Поднять storage services
2. Поднять secrets / policy / IAM
3. Поднять broker
4. Поднять orchestrator
5. Восстановить pending tasks из Postgres
6. Проверить locks / zombie jobs
7. Resume only from valid checkpoints
8. Re-run approval validation for any dangerous pending step

---

## 12. Ежедневные операционные проверки

- backlog в очередях
- DLQ size
- failed approvals count
- secret access anomalies
- number of policy denies
- sandbox timeout count
- redaction failures
- graph/vector sync failures
- budget threshold breaches

---

## 13. Еженедельный security review

- просмотреть top denied actions
- просмотреть all dangerous approvals
- проверить несоответствия capability scopes
- проверить секреты и их rotation age
- проверить лог-редакцию
- проверить новые tool adapters
- проверить рост privileged code paths

---

## 14. Что запрещено оператору и коду

- временно отключать policy ради удобства
- запускать mutable action вручную в обход approval-service
- писать секреты в `.env.example`, тестовые дампы, логи
- давать worker-ам shared superuser credentials
- отключать sandbox limits для “быстрого теста”
