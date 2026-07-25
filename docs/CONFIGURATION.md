# Configuration, Data & State

> **Status of this document:** operator reference for how the agent is
> configured and where it keeps durable state. The authoritative source for any
> default is the code that reads the variable; when this file and the code
> disagree, the code wins. The canonical template is
> [`.env.example`](../.env.example) — copy it to `.env` and edit locally.

## 1. How configuration is resolved

Configuration comes from three places, in order:

1. **Process environment variables** (highest).
2. **`.env`** in the repository root — loaded via `python-dotenv` at startup.
   Never commit `.env`; it is git-ignored and excluded from the Docker image.
3. **JSON config files** under [`config/`](../config) for structured settings
   (budgets, model registry).

`AGENT_PROVIDER=mock` is the safe default: no network, no keys, deterministic.
Set a real provider only when you intend live model calls.

## 2. Environment variables

### Provider & models

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_PROVIDER` | `mock` | Active provider: `mock` \| `openai` \| `anthropic` \| `huggingface` \| `local`. |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `HF_TOKEN` | — | Provider credentials (required when `AGENT_PROVIDER` is not `mock`). |
| `AGENT_MODEL` | registry | Default model when the registry does not override. |
| `AGENT_MODEL_POLICY` | `balanced` | Routing policy: `offline` \| `balanced` \| `quality` (see `:models`). |
| `AGENT_MEMORY_PROVIDER` / `AGENT_MEMORY_MODEL` | — | Route the memory role to a cheaper/local model. |
| `AGENT_VERIFIER_PROVIDER` / `AGENT_VERIFIER_MODEL` | — | Route the verifier role. |
| `AGENT_REPAIR_PROVIDER` / `AGENT_REPAIR_MODEL` | — | Route the self-repair role. |

### Local LLM (OpenAI-compatible server: LM Studio / vLLM / llama.cpp)

| Variable | Example | Purpose |
|---|---|---|
| `LOCAL_LLM_BASE_URL` | `http://127.0.0.1:1234/v1` | Local server endpoint. In Docker use `http://host.docker.internal:1234/v1`. |
| `LOCAL_LLM_API_KEY` | `lm-studio` | Token the local server expects (often a placeholder). |
| `LOCAL_LLM_MODEL` | `qwen-local` | Served model id. |
| `LOCAL_LLM_LOAD_KEY` | `qwen/qwen3-4b-2507` | Model to autoload. |
| `LOCAL_LLM_TIMEOUT` | `60` | Per-request timeout (seconds). |

### Budgets & per-session caps

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_MODEL_MAX_CALLS_PER_SESSION` | `0` (off) | Hard cap on LLM calls per session. |
| `AGENT_MODEL_MAX_TOKENS_PER_SESSION` | `0` (off) | Hard cap on tokens per session. |
| `AGENT_MODEL_MAX_COST_UNITS_PER_SESSION` | `0` (off) | Hard cap on cost units per session. |
| `AGENT_BUDGET_HOUR_LLM_CALLS` | see `config/` | Persistent hourly call budget. |
| `AGENT_BUDGET_DAY_LLM_CALLS` | see `config/` | Persistent daily call budget. |
| `AGENT_BUDGET_DAY_MODEL_TOKENS` | see `config/` | Persistent daily token budget. |
| `AGENT_BUDGET_DAY_MODEL_COST_UNITS` | see `config/` | Persistent daily cost-unit budget. |
| `AGENT_BUDGET_CONFIG_PATH` | `config/budget_limits.json` | Override the budget-config file location. |

Structured budgets live in [`config/budget_limits.json`](../config/budget_limits.example.json)
(template: `budget_limits.example.json`). Inspect at runtime with `:budget-status`
/ `:budget-window-status`; the day-budget kill-switch is `:budget-kill-switch`.

### Unattended tick & Docker supervisor

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_WORKSPACE` | `.` | Workspace root the tick/API operate on. |
| `AGENT_TICK_DRY_RUN` | `1` (safe) | `1` = no real effects; `0` = live path (approval gates still apply). |
| `AGENT_TICK_INTERVAL_SECONDS` | `1800` | Seconds between ticks under `docker/daemon_loop.py`. |
| `AGENT_DOCKER_TICK_TIMEOUT_SECONDS` | ≈ `interval - 60` | Per-tick subprocess timeout; on timeout the supervisor records exit code 124. |
| `AGENT_AUTO_HYGIENE` | `shadow` | Unattended memory hygiene: `shadow` (log only) \| `on` (delete) \| `off`. |

### HTTP API (`api/server.py`)

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_API_TOKEN` | — (**required**) | Bearer token; the server refuses to start if unset. |
| `AGENT_API_MAX_QUESTION` | `8000` | Max characters for `question`. |
| `AGENT_API_MAX_FILE_HINT` | `512` | Max characters for `file_hint`. |

### Advanced / tuning

These are read by the code; the authoritative default lives at the read site.
Find them with:

```bash
grep -rn "AGENT_MAX_TOKENS\|AGENT_MAX_CONTINUATIONS\|AGENT_AUTO_CONTINUE\|AGENT_EVIDENCE_FILE_CHARS\|AGENT_EVIDENCE_TOTAL_CHARS\|AGENT_SELF_BUILD_COOLDOWN_HOURS\|AGENT_MODEL_CATALOG_PATH\|AGENT_MODEL_CATALOG_TTL_DAYS\|AGENT_TEST_TIMEOUT_SECONDS" core/ main.py agent_tick.py
```

`AGENT_SERVICE_*` variables belong to the Windows-service shell contract
(`app/windows_service.py`); per `docs/ROADMAP.md` that service is not
implemented, so they configure a contract only.

## 3. Config files (`config/`)

| File | Committed? | Purpose |
|---|---|---|
| `budget_limits.json` | local | Persistent hour/day budget limits. Template: `budget_limits.example.json`. |
| `model_registry.json` | local | Active model routing table. Template: `model_registry.example.json`. |
| `model_catalog.json` | local | Cached provider model catalog (refresh: `:refresh-models`). |
| `credentials.json` / `token.json` | **never — secrets** | Local provider credentials / tokens. Git-ignored; keep out of images. |

## 4. Data & State (`data/`)

Durable agent state is JSONL under `data/`. Each store has a matching `.lock`
file (single-writer guard). Under Docker the repository is bind-mounted, so these
survive image rebuilds and container replacement.

| Store | Holds |
|---|---|
| `episodic_memory.jsonl` | Episodic memory (observations of past runs). |
| `procedural_memory.jsonl` | Learned procedures. |
| `memory_consolidation.jsonl` | Consolidated/summarised memory. |
| `source_registry.jsonl` | Ingested sources + extracted claims. |
| `daemon_heartbeat.json` | Last-tick heartbeat the Docker healthcheck reads. |

`data/` also holds runtime queues, approvals and budget-window state. The live
`*.jsonl` payloads are git-ignored (runtime data); the `.lock` markers are
tracked so the directory shape is versioned.

**Recovery:** JSONL stores are quarantine/recover-safe — prove it on an isolated
copy with `:state-store-drill` before trusting a repair. Inspect memory with
`:mem` / `:smart-memory`; freeze all durable writes during investigation with
`:audit on`.

_Source of facts: `.env.example`, `api/server.py`, `docker/daemon_loop.py`, and
the `config/` + `data/` layout on `main`. Code wins on any disagreement._
