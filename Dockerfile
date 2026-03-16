FROM python:3.11-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    libssl-dev \
    libffi-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install to virtual environment
COPY requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies only (not build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

<<<<<<< HEAD
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

=======
# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Create non-root user for security
RUN useradd -m -u 1000 agent && \
    mkdir -p /app/data /app/backups /app/logs /app/config && \
    chown -R agent:agent /app

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONOPTIMIZE=2

# Copy application code
COPY --chown=agent:agent src /app/src
COPY --chown=agent:agent config /app/config
COPY --chown=agent:agent templates /app/templates

>>>>>>> 5869125f4e8b7ace43817310bca88fb224da3fa4
# Switch to non-root user
USER agent

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
<<<<<<< HEAD
    CMD curl -f http://localhost:8765/health || exit 1 || python -c "import sys; sys.exit(0)"

# Run the application
ENTRYPOINT ["python", "-m", "src.main"]
=======
    CMD python -c "import requests; requests.get('http://localhost:8765/health', timeout=5)" || exit 1

# Expose dashboard port
EXPOSE 8765

# Run application
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8765"]
>>>>>>> 5869125f4e8b7ace43817310bca88fb224da3fa4
