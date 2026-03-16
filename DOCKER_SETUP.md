# Docker Setup Guide

This project has been containerized following Docker best practices. Here's how to use it.

## Quick Start

### 1. Build the image

```bash
docker build -t ai-agent-12systems:latest .
```

### 2. Run with Docker Compose

Create a `.env` file with your configuration:

```bash
cp .env.example .env
# Edit .env and add your TELEGRAM and OPENAI_API_KEY tokens
```

Then start the agent:

```bash
docker compose up -d
```

View logs:

```bash
docker compose logs -f agent
```

Stop the agent:

```bash
docker compose down
```

## Environment Variables

Key variables (read from `.env`):
- `OPENAI_API_KEY` - Your OpenAI API key
- `TELEGRAM` - Telegram bot token
- `TELEGRAM_ALERTS_CHAT_ID` - Chat ID for alerts
- `AUTONOMOUS_START` - Set to 1 to enable autonomous mode on startup
- `DASHBOARD` - Set to 1 to enable web dashboard (port 8765)

See `.env.example` for all available options.

## Data Persistence

All data is persisted in Docker volumes:
- `agent_data` - Agent memory and state
- `agent_logs` - Application logs
- `agent_backups` - Backup files
- `agent_config` - Configuration files

## Development Mode

For local development with hot-reload:

```bash
# Option 1: Uncomment bind mounts in docker-compose.yml, then:
docker compose up -d

# Option 2: Use watch mode (Docker Desktop 4.20+)
docker compose watch
```

Edit files in `src/`, `config/`, or `templates/` — changes appear in the container instantly.

## Debugging

```bash
# View logs
docker compose logs agent

# Follow logs in real-time
docker compose logs -f agent

# Check container status
docker compose ps

# Execute commands in running container
docker compose exec agent python -c "import sys; print(sys.version)"

# View resource usage
docker stats ai-agent-12systems
```

## Production Deployment

### Resource Limits

Current limits (edit `docker-compose.yml`):
- CPU: 2 cores (reserved: 1 core)
- Memory: 2 GB (reserved: 1 GB)

Adjust based on your workload.

### Networking

The agent runs on an isolated Docker network `agent_network`. To access the dashboard:
- Local: `http://localhost:8765`
- From other containers: `http://agent:8765`

### Backup

Backup volumes:

```bash
docker run --rm \
  -v agent_data:/data \
  -v /backup/path:/backup \
  busybox tar czf /backup/agent_data.tar.gz -C / data
```

### Update

```bash
# Pull latest code
git pull

# Rebuild image
docker build -t ai-agent-12systems:latest .

# Stop and restart
docker compose down
docker compose up -d
```

## Architecture

### Multi-stage Dockerfile

The Dockerfile uses multi-stage builds to minimize image size:

1. **Builder stage** - Installs build tools and compiles dependencies
2. **Runtime stage** - Copies only compiled dependencies, omitting build tools

Final image: ~1.4 GB (minimal for a Python AI agent with all dependencies)

### Security

- Non-root user (`agent:agent` UID 1000)
- No new privileges escalation
- Read-only root filesystem option (commented out to allow config writes)
- Health check implemented

### Logging

- Docker JSON file driver with rotation
- Max file size: 10 MB
- Max files: 3 (30 MB total)

## Troubleshooting

### Container won't start

```bash
# Check logs
docker compose logs agent

# Common issues:
# - Missing .env file: copy .env.example .env
# - Invalid API keys: check OPENAI_API_KEY and TELEGRAM
# - Port 8765 in use: change DASHBOARD_PORT in docker-compose.yml
```

### High memory usage

```bash
# Check current usage
docker stats ai-agent-12systems

# Increase memory limit in docker-compose.yml:
# memory: 4G  # Change from 2G
```

### Disk space issues

```bash
# Clean up unused images/volumes
docker system prune -a

# Check volume usage
docker system df
```

## Next Steps

- **Monitor dashboard**: `http://localhost:8765` (if DASHBOARD=1)
- **Read logs**: `docker compose logs -f agent`
- **Customize config**: Edit `.env` and rebuild if needed
- **Deploy to production**: Use Docker Swarm or Kubernetes with the provided image
