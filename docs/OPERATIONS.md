# Operations, Troubleshooting & HTTP API

> **Status of this document:** operator runbook for running the agent, driving
> the HTTP API, and diagnosing failures. Container mechanics live in
> `DOCKER.md`; configuration lives in
> [`CONFIGURATION.md`](CONFIGURATION.md); the command surface lives in
> [`COMMANDS_MAP.md`](COMMANDS_MAP.md). Code wins on any disagreement.

## 1. Run modes

| Mode | Command | Notes |
|---|---|---|
| Interactive REPL | `python main.py --workspace <dir>` | Human-operated agent; exit with `:quit`. |
| One-shot | `python main.py --ask "..."` | Single question, no session memory. |
| Unattended tick | `python agent_tick.py --workspace <dir>` | One bounded cycle; `--status` prints state without running. |
| Docker supervisor | `docker compose up -d` | `docker/daemon_loop.py` repeats `agent_tick.py` every `AGENT_TICK_INTERVAL_SECONDS`. See `DOCKER.md`. |
| HTTP API | `uvicorn api.server:app` | JSON API (§2). Optional extra: `pip install fastapi uvicorn[standard]`. |

All modes honour the same approval, budget, kill-switch, memory and dry-run
controls. `AGENT_TICK_DRY_RUN=1` (the default) means no real effects.

## 2. HTTP API operator guide

The API exposes the agent as JSON so dashboards and tools can drive it without a
CLI session (`api/server.py`).

### 2.1 Start & authenticate

```bash
pip install fastapi "uvicorn[standard]"
export AGENT_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uvicorn api.server:app --host 0.0.0.0 --port 8000
```

- **`AGENT_API_TOKEN` is required.** If it is unset the server **refuses to
  start** — an unauthenticated agent API on a network interface is a security
  risk.
- Auth is a **Bearer token** in the `Authorization` header, compared in
  constant time. A missing/wrong token returns **401**.
- **Token lifecycle:** to rotate, change `AGENT_API_TOKEN` and restart the
  process. There is no in-band token endpoint; tokens live only in the
  environment.

### 2.2 Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | none | Liveness (`{"status":"ok"}`). |
| `GET` | `/usage` | Bearer | Cumulative LLM token usage since start. |
| `POST` | `/ask` | Bearer | Run one agent cycle; returns `answer`, `trace_id`, `token_usage`. |
| `GET` | `/docs`, `/redoc` | none | Swagger UI / ReDoc (schema browsing). |

### 2.3 Examples

```bash
# Liveness (no token)
curl -s http://localhost:8000/health

# Ask a question
curl -s -X POST http://localhost:8000/ask \
  -H "Authorization: Bearer $AGENT_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "What does core/loop.py do?", "file_hint": "core/loop.py"}'

# Token usage
curl -s http://localhost:8000/usage -H "Authorization: Bearer $AGENT_API_TOKEN"
```

`/ask` accepts `{"question": str, "file_hint": str|null}` and returns
`{"answer": str, "trace_id": str, "token_usage": {...}}`. `question` and
`file_hint` are length-bounded (`AGENT_API_MAX_QUESTION` / `AGENT_API_MAX_FILE_HINT`).

### 2.4 Shared-memory semantics (important)

The server holds **one shared agent instance** with **cross-request working
memory** — a `/ask` call sees previous turns. `/ask` runs are **serialized** by a
lock: concurrent calls queue rather than run in parallel, so they cannot
interleave the shared trace log, working memory and usage ledger. Consequences:

- The API is **session-stateful**, not stateless. It is not horizontally
  shardable as-is (two workers = two divergent memories).
- Throughput is one in-flight `/ask` at a time by design.

### 2.5 Error responses

| Status | Meaning | Fix |
|---|---|---|
| `401` | Missing/invalid Bearer token | Send the correct `AGENT_API_TOKEN`. |
| `422` | Request validation (e.g. over length) | Shorten `question`/`file_hint`. |
| `500` | Agent error | Body carries only `Agent error: <Type>`; the full trace is in the JSONL log, correlate by `trace_id`. |

## 3. Troubleshooting

| Symptom | Likely cause | Check / fix |
|---|---|---|
| API exits: "AGENT_API_TOKEN … must be set" | Token unset | Set `AGENT_API_TOKEN` (§2.1). |
| API returns 401 | Token mismatch | Confirm the `Authorization: Bearer` value matches the env. |
| Tick "does nothing" / no effects | `AGENT_TICK_DRY_RUN=1` (default) | Expected in dry-run. Set `0` only after clean dry-run ticks. |
| Docker container `unhealthy` | Stale `data/daemon_heartbeat.json` | `docker compose logs --tail 200 agent`; confirm ticks run and the interval is sane. |
| Model calls fail / "no key" | Provider/key not set | Set `AGENT_PROVIDER` + the matching key; `mock` needs none. |
| Local LLM unreachable from Docker | `127.0.0.1` points at the container | Use `http://host.docker.internal:<port>/v1`. |
| Autonomy stops early | Day-budget kill-switch tripped | `:budget-kill-switch` to inspect, `--clear` to reset; review `:budget-window-status`. |
| Memory looks wrong while investigating | Live writes contaminating the object | `:audit on` freezes all durable writes for the session. |

Logs are JSONL under `logs/`; every run and API response carries a `trace_id`
for correlation.

## 4. Recovery & incident basics

- **Stop fast:** `docker compose stop` (SIGTERM → the supervisor exits after the
  current tick, `stop_grace_period` 30s). For runaway spend, trip/inspect the
  budget kill-switch.
- **State recovery:** JSONL stores are quarantine/recover-safe — prove a repair
  on an isolated copy first with `:state-store-drill`.
- **Undo a self-applied change:** `:rollback` applies the latest compensation
  plan (`:rollback list` to see registered plans).
- **Diagnose after the fact:** find the failing `trace_id` in `logs/`; the API
  `500` body and the tick log both carry it.

_Source of facts: `api/server.py`, `docker/daemon_loop.py`, `compose.yaml`, and
`docs/DOCKER.md` on `main`. Code wins on any disagreement._
