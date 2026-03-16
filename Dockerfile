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

# Switch to non-root user
USER agent
# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8765/health', timeout=5)" || exit 1
# Expose dashboard port
EXPOSE 8765
# Run application
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8765"]
