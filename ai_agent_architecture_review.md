
# Архитектура автономного AI-агента — инженерный разбор и целевая схема

## 1. Что уже хорошо в исходной архитектуре

Исходный документ задаёт очень широкий и зрелый охват системы: восприятие, знания, reasoning, агентные роли, исполнение, безопасность, governance, HITL, оценку качества, бюджет, верификацию знаний и безопасную эволюцию. Это сильная база для **логической архитектуры**.

Но в текущем виде документ остаётся в основном **каталогом слоёв**, а не **исполняемой технической архитектурой**. Ключевая проблема: перечислены блоки, но слабо зафиксированы:

- направления вызовов;
- границы доверия;
- права на чтение/запись;
- форматы обмена между блоками;
- порядок исполнения задачи;
- техническое размещение сервисов на одной машине.

## 2. Что в архитектуре не хватает

### 2.1. Центральный управляющий контур

Сейчас есть Cognitive Core, Agent System, Orchestration, Execution, Task Decomposition, Goal Management, Continuous Loop.  
Не определено, кто из них — главный runtime-координатор.

Нужно явно ввести:

- **API Gateway**
- **Orchestrator / Manager Agent**
- **Workflow / Task Engine**
- **Policy Engine**
- **Executor**
- **Result Collector**

### 2.2. Явный pipeline одной задачи

Нужен конкретный маршрут исполнения:

1. Входящий API-запрос  
2. Session creation / restore  
3. Context retrieval  
4. Task decomposition  
5. Plan generation  
6. Risk / policy check  
7. Human approval only for dangerous actions  
8. Dispatch to worker  
9. Tool call via Tool Broker  
10. Result validation  
11. State update  
12. Evaluation / reflection  
13. Memory write-back  
14. API response

### 2.3. Разделение памяти по режимам записи

Нужно не просто перечислить short-term / long-term / episodic / semantic memory, а зафиксировать:

- **Short-term / Session State**  

  Хранение текущего контекста и статуса шагов.

- **Episodic Memory**  

  История выполненных действий и outcomes.

- **Semantic Memory / RAG**  

  Проверенные знания и факты.

- **Knowledge Graph**  

  Связи между сущностями, проектами, задачами, источниками.

- **Artifacts Storage**  

  Документы, файлы, результаты, отчёты.

### 2.4. Tool Broker как обязательный шлюз

Сейчас инструменты просто перечислены. Нужно выделить отдельный сервис:

- authorizes tool usage
- нормализует вызовы
- добавляет timeout / retry
- ведёт audit
- injects secrets
- проверяет budget/policy
- маршрутизирует в sandbox

Без этого agent workers получат слишком прямой и опасный доступ к ОС, сети и внешним API.

### 2.5. IAM / Secret Vault

Security System в документе есть, но для реальной системы нужно отдельно выделить:

- **Identity & Access Manager**
- **Secrets Vault**
- policy scopes per worker
- signing/approval metadata
- access boundaries for tools

### 2.6. Event Bus / Queue

Для мультиагентной системы с worker-процессами нужна очередь / событийная шина.

Минимум:

- task queue
- step queue
- result queue
- dead-letter queue
- approval events
- monitoring events

### 2.7. Технический deployment-уровень

Требуется отдельная инженерная схема:

- какие контейнеры запущены локально;
- какие БД локальны;
- где sandbox;
- как workers общаются с broker;
- где логирование и health-checks;
- как storage связано с executor.

## 3. Целевая техническая архитектура под твои требования

Ниже — целевой вариант для **одного локального ПК**, **Docker-based**, **multi-worker architecture**, **API-first**, **Human Approval только для опасных действий**.

## 4. Состав сервисов

### 4.1. Edge / Control Plane

- **API Gateway**  

  Единая точка входа. Принимает внешний API-запрос. Делает auth, rate limit, request normalization.

- **Session & State Service**  

  Держит состояние задач, checkpoints, статусы шагов, resume metadata.

- **Orchestrator / Manager Agent**  

  Главный координатор. Принимает задачу, строит маршрут, создаёт workflow.

- **Task Decomposition Engine**  

  Разбивает сложную задачу на steps / DAG.

- **Planner / Cognitive Core**  

  Делает reasoning, plan synthesis, strategy selection.

- **Policy Engine**  

  Проверяет допустимость действий.

- **Risk & Budget Engine**  

  Оценивает стоимость, опасность, право на исполнение без человека.

- **Human Approval Service**  

  Срабатывает только для опасных/необратимых действий.

### 4.2. Worker Plane

- **Research Worker**
- **Coder Worker**
- **Analyst Worker**
- **Browser Worker**
- **Executor Worker**
- **Reflection / Evaluation Worker**

Workers не ходят напрямую во внешний мир без Tool Broker.

### 4.3. Tool / Execution Plane

- **Tool Broker**
- **Terminal Runner**
- **Filesystem Adapter**
- **Python Sandbox**
- **Browser Automation Runtime**
- **Docker Sandbox Executor**
- **GitHub Adapter**
- **DB Adapter**
- **Cloud API Adapter**

### 4.4. Data Plane

- **Postgres** — system state, sessions, tasks, approvals, audit metadata, run history
- **Redis** — short-lived cache, locks, coordination, queue acceleration
- **Qdrant / Weaviate / Chroma** — vector memory
- **Neo4j** — knowledge graph
- **S3-compatible local object storage** — files, artifacts, parsed docs, outputs
- **Logs / Metrics Store** — observability

### 4.5. Model Plane

- **Model Router / Inference Gateway**  

  Выбор модели по задаче, цене, latency, fallback policy.

## 5. Предлагаемая локальная контейнерная раскладка

### Контейнеры

1. api-gateway  
2. orchestrator  
3. planner  
4. policy-engine  
5. approval-service  
6. task-engine  
7. tool-broker  
8. research-worker  
9. coder-worker  
10. analyst-worker  
11. executor-worker  
12. browser-worker  
13. evaluation-worker  
14. postgres  
15. redis  
16. qdrant  
17. neo4j  
18. minio (S3 local)  
19. sandbox-runner  
20. observability stack

## 6. Связи между сервисами

### Главный маршрут

API Gateway  
→ Session & State Service  
→ Orchestrator  
→ Task Engine / Decomposition  
→ Planner / Cognitive Core  
→ Memory Retrieval (Vector DB + Neo4j + Object Store metadata)  
→ Policy Engine  
→ Risk & Budget Engine  
→ Human Approval (если action dangerous)  
→ Worker dispatch  
→ Tool Broker  
→ конкретный adapter / sandbox  
→ Result Collector  
→ Evaluation / Reflection  
→ Memory Write-back  
→ API Response

### Ключевые правила связи

- Workers не вызывают ОС и внешние API напрямую.
- Все опасные tool calls идут через Policy + Tool Broker.
- Все результаты исполнения проходят Validation.
- Long-term / semantic write-back идёт только после verification/classification.
- Approval нужен только для опасных действий:
  - удаление важных данных
  - запись во внешние прод-системы
  - финансовые действия
  - изменение конфигурации
  - массовые внешние запросы
  - необратимые filesystem / database mutations

## 7. Что обязательно добавить в сам документ архитектуры

### Раздел A. Runtime Sequence

Добавить в документ последовательность для одной задачи:

- receive
- classify
- decompose
- plan
- check policy
- approve if needed
- execute
- validate
- evaluate
- store
- respond

### Раздел B. Interface Contracts

Для каждого сервиса описать:

- input
- output
- sync/async
- idempotency
- retry policy
- timeout
- error model

### Раздел C. Tool Permission Matrix

Матрица:

- Manager: orchestration only
- Researcher: browser / read-only data tools
- Coder: python sandbox / repo / filesystem scoped
- Analyst: db read / python / vector retrieval
- Executor: only approved mutable actions
- Browser worker: outbound web with policy restrictions

### Раздел D. Memory Write Policy

Кто пишет:

- session state → Session Service
- episodic memory → Result Collector / Reflection
- semantic memory → Knowledge Acquisition + Verification
- graph updates → Graph updater after entity/relation extraction
- artifacts → object storage

### Раздел E. Danger Classification

Нужно явно описать классы действий:

- Safe
- Guarded
- Dangerous
- Prohibited

### Раздел F. Failure Handling

Для каждого шага:

- detect
- retry?
- fallback?
- rollback?
- escalate?
- write postmortem?

## 8. Самые критичные недостающие блоки

1. **API Gateway**
2. **Model Router / Inference Gateway**
3. **Tool Broker**
4. **Result Collector**
5. **Identity & Access Manager**
6. **Secrets Vault**
7. **Task / Workflow Engine**
8. **Validation Layer as runtime gate**
9. **Local S3 artifact storage**
10. **Redis for coordination and locks**
11. **Approval Service**
12. **Observability stack**
13. **Dead-letter queue / failure channel**
14. **Memory write policy service**
15. **Knowledge verification pipeline**

## 9. Рекомендуемый стек под локальный ПК

### Базы / хранилища

- **Postgres** — основная операционная БД
- **Redis** — быстрый coordination layer
- **Qdrant** — самый практичный вариант для локального vector store
- **Neo4j** — knowledge graph
- **MinIO** — локальный S3-compatible object storage

### Очереди

- На минимуме: Redis Streams / RQ / Celery with Redis
- Лучше: NATS / RabbitMQ, если хочешь более чистую событийную модель

### Sandbox

- Docker-in-Docker не рекомендую
- Лучше: отдельный sandbox-runner container, который запускает ограниченные job containers
- Python, terminal и browser исполнять в изолированных контейнерах с лимитами CPU/RAM/network

### Browser

- Playwright container

### Observability

- Prometheus + Grafana + Loki
- или упрощённо: structured logs + local dashboard

## 10. Итоговая инженерная позиция

Твой исходный документ — сильный **концептуальный фундамент**.  
Чтобы он стал **рабочей инженерной архитектурой**, нужно перевести его из списка слоёв в:

1. карту сервисов;  
2. карту потоков данных;  
3. карту потоков управления;  
4. матрицу прав;  
5. схему deployment на одной машине;  
6. схемы runtime/error/memory.

Именно это отражено в приложенной диаграмме.

## 11. Что лежит в приложенной схеме

Диаграмма показывает:

- API вход
- control plane
- worker plane
- tool/execution plane
- data plane
- model plane
- observability
- safety gates
- маршруты чтения/записи
- места, где нужен Human Approval
- путь обновления памяти и графа знаний
