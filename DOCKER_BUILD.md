# Docker Build Guide

## Quick Start

### Production Build
```bash
docker compose up -d
```

### Development Build with Hot Reload
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

### One-off Build
```bash
docker build -t telegram-agent:latest .
```

## Files Overview

- **Dockerfile**: Multi-stage build (builder + runtime)
  - Python 3.10-slim base image
  - Virtual environment for dependency isolation
  - Non-root user (appuser) for security
  - Separate build stage to minimize runtime image size

- **docker-compose.yml**: Production configuration
  - Named volumes for persistence (data, logs, backups, config)
  - Environment variables from .env
  - Resource limits (CPU, memory)
  - Logging driver (json-file with rotation)
  - Network isolation
  - Security options

- **docker-compose.dev.yml**: Development configuration
  - Hot reload with file sync (`develop.watch`)
  - Bind mounts for source code
  - Automatic rebuild on requirements.txt changes
  - Extends production configuration

- **.dockerignore**: Reduces build context
  - Excludes cache, venv, .env, test results
  - Reduces layer size and build time

## Configuration

### Environment Variables
Copy `.env.example` to `.env` and configure:
```bash
cp .env.example .env
# Edit .env with your TELEGRAM token and OPENAI_API_KEY
```

### Key Variables for Docker
- `OPENAI_API_KEY`: LLM/TTS provider
- `TELEGRAM`: Bot token (required)
- `TELEGRAM_ALERTS_CHAT_ID`: Alert destination (optional)
- `DASHBOARD`: Enable web dashboard (default: 1)
- `AUTONOMOUS_START`: Auto-enable autonomous mode (default: 0)

## Data Persistence

Volumes are created automatically:
- `/app/data` → `agent_data`
- `/app/logs` → `agent_logs`
- `/app/backups` → `agent_backups`
- `/app/config` → `agent_config`

To inspect volumes:
```bash
docker volume ls
docker volume inspect agent_data
```

## Monitoring

### View Logs
```bash
docker compose logs -f agent
```

### Dashboard
Access at `http://localhost:8765/dashboard/` (if enabled)

### Container Stats
```bash
docker stats telegram-agent
```

## Cleanup

Remove containers and volumes:
```bash
docker compose down -v
```

## Best Practices Applied

✓ Multi-stage builds for smaller images
✓ Layer caching optimization
✓ Non-root user execution
✓ Named volumes for data persistence
✓ Resource limits defined
✓ Logging configured with rotation
✓ Network isolation
✓ Security options enabled
✓ .dockerignore excludes unnecessary files
✓ PYTHONUNBUFFERED for real-time logs
✓ Environment variable management via .env
✓ Hot reload support for development
