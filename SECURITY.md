# Security Policy

This is a proprietary project (see [`LICENSE`](LICENSE)). This document explains
how to report a vulnerability and the security controls the project relies on.

## Reporting a vulnerability

**Do not open a public issue for a security problem.** Report it privately:

- Use GitHub's **private vulnerability reporting** for this repository, or
- Contact the repository owner directly.

> Maintainers: set a dedicated security contact address here.

Please include a description, affected paths/versions, and reproduction steps.

## Supported surface

Only the `main` branch is maintained. There is no separate LTS or backport line.

## Secret handling

- **`.env` is never committed.** Copy [`.env.example`](.env.example) to `.env`
  and keep keys there. `.env` is git-ignored and excluded from Docker images.
- `config/credentials.json` and `config/token.json` are local secret files —
  git-ignored; never commit them or bake them into an image.
- CI runs **gitleaks** over history and diffs; a leaked credential fails the
  build. `core/secret_scanner` scans runtime content, and `scripts/audit_release.py`
  checks the release manifest for local secrets. These are independent gates.

## HTTP API

- `AGENT_API_TOKEN` is **required**; the server refuses to start without it.
- Authentication is a Bearer token compared in constant time; missing/invalid
  tokens get `401` before any agent logic runs.
- Do not expose the API on a public interface without a token and network
  controls. See [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

## Runtime safety controls

- **Approval gates**, **budget limits** and a **day-budget kill-switch** bound
  autonomous action; `AGENT_TICK_DRY_RUN=1` (the default) blocks real effects.
- **Memory audit mode** (`:audit on`) freezes all durable writes during
  investigation.
- Under Docker, the container gets only the repository bind mount — **no Docker
  socket, no host filesystem** — and runs with `no-new-privileges`. See
  [`docs/DOCKER.md`](docs/DOCKER.md).

## Dependency integrity

Dependencies are hash-locked in `requirements.lock` and installed with
`pip install --require-hashes`. CI additionally runs `pip check`, verifies the
SBOM is in sync (`scripts/generate_sbom.py --check`), and runs the
supply-chain/release gate.

_Source of facts: `.github/workflows/ci.yml`, `api/server.py`, `compose.yaml`,
and the repository's ignore/secret configuration on `main`._
