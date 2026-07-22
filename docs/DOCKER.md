# Docker runtime

This deployment keeps the agent in a long-lived Docker container while preserving
its complete workspace on the Windows host.

## What runs

`agent_tick.py` is deliberately a one-shot bounded process: it performs one tick
and exits. `docker/daemon_loop.py` is a thin process supervisor that runs one tick
immediately and repeats it at `AGENT_TICK_INTERVAL_SECONDS`. It does not bypass
approval, budget, kill-switch, memory, or dry-run controls.

The repository is bind-mounted at `/workspace`, so these remain durable on the
host and survive image rebuilds or container replacement:

- source code and self-build changes;
- `data/` memory, queues, approvals, budgets, heartbeat and registry files;
- `logs/`;
- local configuration files.

The Docker image never copies `.env`, credentials, memory, logs, caches, or Git
metadata into an image layer; `.dockerignore` excludes them.

## Requirements

- Docker Desktop with Linux containers;
- Docker Compose v2;
- the repository checked out locally;
- a local `.env` when a real model provider is used.

Docker Desktop must be running. `restart: unless-stopped` restarts the agent after
a Docker restart or container failure, but it cannot run while the Windows PC or
Docker Desktop is powered off.

## Safe first start

From PowerShell in the repository root:

```powershell
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f agent
```

The default is safe unattended mode:

```text
AGENT_TICK_DRY_RUN=1
```

The first tick runs immediately; later ticks run every 1800 seconds by default.

## Check status

```powershell
docker compose ps
docker compose exec agent python agent_tick.py --workspace /workspace --status
docker compose logs --tail 200 agent
```

The container healthcheck requires a fresh `data/daemon_heartbeat.json`.

## Use the interactive agent

Run a separate temporary container attached to the same workspace:

```powershell
docker compose run --rm agent python main.py --workspace /workspace --auto-approve deny
```

This opens the normal REPL. Exit with `:quit`.

## Provider and secrets

Keep API keys only in the local `.env`; do not put them in `compose.yaml` or the
Dockerfile. Example:

```dotenv
AGENT_PROVIDER=openai
OPENAI_API_KEY=...
AGENT_TICK_DRY_RUN=1
AGENT_TICK_INTERVAL_SECONDS=1800
AGENT_DOCKER_TICK_TIMEOUT_SECONDS=1500
```

For a model server running on Windows, `127.0.0.1` inside the container points to
the container itself. Use Docker Desktop's host alias instead:

```dotenv
AGENT_PROVIDER=local
LOCAL_LLM_BASE_URL=http://host.docker.internal:1234/v1
```

## Enable effects only after observation

After several clean dry-run ticks, real effects can be enabled explicitly in
`.env`:

```dotenv
AGENT_TICK_DRY_RUN=0
```

Then recreate the service:

```powershell
docker compose up -d --force-recreate
```

This does not disable approval gates. It only allows the tick to enter its normal
live path. Persistent budget limits and the kill-switch should be configured
before enabling it.

## Stop, restart, rebuild

```powershell
docker compose stop
docker compose start
docker compose restart
docker compose down
docker compose build --pull
docker compose up -d
```

`docker compose down` removes the container and network but not the bind-mounted
workspace. Do not add `-v` unless named volumes are introduced later and their
data is intentionally disposable.

## Update after Git changes

```powershell
git pull
docker compose build
docker compose up -d
```

Because the repository is bind-mounted, pure Python source changes are visible
immediately. Rebuild whenever dependencies, the Dockerfile, or system packages
change.

## Security boundary

The container receives only the repository bind mount. It does not mount the
Docker socket or the rest of the Windows filesystem. `no-new-privileges` is
enabled. The process can still modify anything inside the repository when live
effects and the agent's own policy permit it.
