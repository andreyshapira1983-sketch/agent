# Multi-stage build for Python AI Agent
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Runtime stage
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies (ffmpeg for audio processing, curl for health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd -m -u 1000 agent

# Copy Python dependencies from builder
COPY --from=builder /root/.local /home/agent/.local

# Copy application code
COPY --chown=agent:agent src/ /app/src/
COPY --chown=agent:agent config/ /app/config/
COPY --chown=agent:agent templates/ /app/templates/
COPY --chown=agent:agent .env.example /app/.env.example

# Create data directory for persistence
RUN mkdir -p /app/data /app/logs && chown -R agent:agent /app/data /app/logs

# Set environment variables
ENV PATH=/home/agent/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user
USER agent

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8765/health || exit 1 || python -c "import sys; sys.exit(0)"

# Run the application
ENTRYPOINT ["python", "-m", "src.main"]
