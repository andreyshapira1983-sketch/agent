# Repository Entry Point and Navigation

This README is the entry point and short navigation map for the repository. It
does **not** own doctrine or architecture itself; it points at the documents
that do. When a document below conflicts with this README, that document wins.

## Source-of-truth hierarchy

Read these in priority order; a higher entry overrides a lower one:

1. **`docs/AGENT_DOCTRINE.md`** — authoritative rules for how agents must behave.
2. **Target architecture and component boundaries** — the intended design and
   the responsibilities/limits of each component.
3. **`docs/ROADMAP.md`** — the order in which capabilities are developed.
4. **`docs/daemon-progress.md`** — the actual implementation state (which
   sub-items are merged, in review, or not started).
5. **`README.md`** (this file) — entry point and brief navigation only.

## What actually runs on `main`

| Path | Entry | Role |
| ---- | ----- | ---- |
| Interactive / one-shot | `python main.py` or `python main.py --ask "..."` | Primary human-operated agent (REPL or single question). |
| Unattended tick | `python agent_tick.py --workspace <dir>` | Bounded one-shot autonomous cycle (production unattended path). |
| Docker long-lived | `docker compose up` → `docker/daemon_loop.py` | Process supervisor that **repeatedly runs `agent_tick.py`**. Not `app.daemon.DaemonLoop`. |
| Optional HTTP API | `uvicorn api.server:app` | FastAPI `/ask`, `/health`, `/usage` (requires `AGENT_API_TOKEN`). |

**Not production-composed:** `app/daemon.py` (`DaemonLoop`) and related async
building blocks (`WorkerPool`, `FileWatcher`, priority queue, etc.) exist and
are tested, but are **not** the process Compose or `agent_tick` starts. See
`docs/daemon-progress.md` and `docs/DOCKER.md`.

Routing map for documents: [`docs/INDEX.md`](docs/INDEX.md).

## Quickstart

**Requirements:** Python 3.11.

```bash
# 1. Install (hash-locked core dependencies)
python -m pip install --upgrade pip
pip install --require-hashes -r requirements.lock

# 2. Configure — copy .env.example to .env (safe default: mock provider, no keys)

# 3. Run — interactive REPL (exit with :quit)
python main.py --workspace .

# ...or ask a single question:
python main.py --ask "What does this project do?"
```

- **Unattended one-shot:** `python agent_tick.py --workspace .` — `AGENT_TICK_DRY_RUN=1` by default, so no real effects.
- **Long-lived (Docker):** `docker compose up -d` — see [`docs/DOCKER.md`](docs/DOCKER.md).
- **HTTP API:** `pip install fastapi "uvicorn[standard]"`, then `uvicorn api.server:app` — see [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

Configuration reference: [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

## Purpose

The goal of this project is to provide a clear, maintainable foundation with a small, low-risk surface area for change.

## Principles

- Prefer simplicity over cleverness.
- Make behavior explicit and easy to verify.
- Keep changes small and localized.
- Preserve compatibility unless a change is intentionally breaking.
- Update documentation alongside code changes.

## Architecture

- The repository should remain easy to understand at a glance.
- Core behavior should be defined in one primary place whenever possible.
- Supporting code should be organized to reduce duplication and ambiguity.

## Change Process

1. Identify the smallest safe change.
2. Implement the change with minimal impact.
3. Add or update tests when behavior changes.
4. Review for clarity, consistency, and maintainability.

## Testing

Run the project's tests after making changes, and focus on the specific area affected when possible.

## Maintenance

When in doubt, keep this README limited to navigation, and update the
authoritative document (doctrine, architecture, roadmap, or progress) that
actually owns the decision.

## License

Proprietary — **All rights reserved.** This project is not open source. Use,
copying, modification, and distribution require the copyright holder's prior
written permission. See [`LICENSE`](LICENSE).
